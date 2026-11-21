from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import requests


class LLMError(RuntimeError):
    pass


class LLMTruncatedError(LLMError):
    """输出被截断或为空。

    推理模型（deepseek-flash / deepseek-pro 等）会先产生 reasoning_content，
    这些思考 token 与正文共用同一个 max_tokens 预算。预算偏小时思考会吃掉全部
    额度，正文为空或只剩开头一截，而接口仍可能返回 finish_reason="stop"。
    这类结果不能当成功处理。
    """


@dataclass
class Usage:
    prompt: int = 0
    completion: int = 0
    total: int = 0
    cached: int = 0

    def add(self, other: "Usage") -> None:
        self.prompt += other.prompt
        self.completion += other.completion
        self.total += other.total
        self.cached += other.cached

    def as_dict(self) -> dict[str, int]:
        return {"prompt": self.prompt, "completion": self.completion, "total": self.total, "cached": self.cached}


@dataclass
class LLMResult:
    text: str
    usage: Usage = field(default_factory=Usage)
    elapsed: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)
    finish_reason: str = ""
    reasoning_tokens: int = 0


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    words = len(re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", text))
    digits = len(re.findall(r"\d+", text))
    covered = cjk + len(re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", text)) + digits
    other = max(0, len(text) - covered)
    return max(1, int(cjk * 1.02 + words * 1.32 + digits * 1.0 + other * 0.25))


def strip_think(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"<think\b[^>]*>.*?</think\s*>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"^\s*<think\b[^>]*>.*", "", text, flags=re.DOTALL | re.IGNORECASE)
    return text.strip()


def extract_json(text: str) -> dict[str, Any]:
    text = strip_think(text).strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise LLMError("模型没有返回 JSON")
    candidate = text[start : end + 1]
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError as exc:
        # 常见问题：中文引号、尾逗号
        candidate = candidate.replace("“", '"').replace("”", '"')
        candidate = re.sub(r",\s*([}\]])", r"\1", candidate)
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            raise LLMError(f"JSON 解析失败: {exc}") from exc
    if not isinstance(data, dict):
        raise LLMError("模型返回的 JSON 不是对象")
    return data


class LLMClient:
    def __init__(self, cfg, session: requests.Session | None = None):
        self.cfg = cfg
        # requests.Session 不是线程安全的：每个线程一个会话，避免并发时连接池损坏
        self._fixed_session = session
        self._local = threading.local()
        self._headers = {
            "Authorization": f"Bearer {cfg.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if session is not None:
            session.headers.update(self._headers)
        # 有的兼容网关不认 thinking / reasoning_effort，400 之后自动降级
        self._thinking_rejected = False

    @property
    def session(self) -> requests.Session:
        if self._fixed_session is not None:
            return self._fixed_session
        s = getattr(self._local, "session", None)
        if s is None:
            s = requests.Session()
            s.headers.update(self._headers)
            self._local.session = s
        return s

    def _thinking_hint(self) -> dict[str, Any]:
        """关掉思考模式，或至少把思考压到最短。"""
        if not getattr(self.cfg, "disable_thinking", True) or self._thinking_rejected:
            return {}
        if "deepseek" in str(self.cfg.base_url).lower():
            # DeepSeek V3.2：thinking 与正文共用 max_tokens，长文会被思考吃光
            return {"thinking": {"type": "disabled"}}
        return {"reasoning_effort": "minimal"}

    def _payload(
        self,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int,
        response_format: dict[str, str] | None,
        stream: bool,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.cfg.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": min(max_tokens, self.cfg.max_tokens),
            "stream": stream,
        }
        if stream:
            payload["stream_options"] = {"include_usage": True}
        if response_format:
            payload["response_format"] = response_format
        payload.update(self._thinking_hint())
        return payload

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict[str, str] | None = None,
        on_delta: Callable[[str], None] | None = None,
    ) -> LLMResult:
        if not self.cfg.api_key:
            raise LLMError("还没有配置 API Key，请点右上角「设置」填写。")
        stream = on_delta is not None
        payload = self._payload(
            messages,
            self.cfg.temperature if temperature is None else temperature,
            max_tokens or 2048,
            response_format,
            stream,
        )
        started = time.time()
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                if stream:
                    return self._checked(self._stream(payload, on_delta, started))
                return self._checked(self._once(payload, started))
            except LLMTruncatedError:
                # 预算不够是确定性的，原样重试没有意义，交给上层加大预算
                raise
            except (requests.Timeout, requests.ConnectionError) as exc:
                last_error = exc
            except LLMError as exc:
                last_error = exc
                # 网关不支持 thinking / reasoning_effort 时去掉参数重试一次
                if "400" in str(exc) and not self._thinking_rejected and getattr(self.cfg, "disable_thinking", True):
                    self._thinking_rejected = True
                    payload.update(self._thinking_hint())
                    for key in ("thinking", "reasoning_effort"):
                        if key not in self._thinking_hint():
                            payload.pop(key, None)
                    continue
                if "401" in str(exc) or "403" in str(exc) or "余额" in str(exc):
                    break
            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else 0
                last_error = exc
                if status in (400, 401, 403, 404):
                    break
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
        raise LLMError(f"模型调用失败：{last_error}")

    @staticmethod
    def _checked(result: LLMResult) -> LLMResult:
        """把“被截断/空正文”当成错误而不是译文。"""
        text = (result.text or "").strip()
        truncated = result.finish_reason == "length"
        starved = not text and result.reasoning_tokens > 0
        if truncated or starved:
            detail = (
                f"推理占用 {result.reasoning_tokens} tokens，正文 {len(text)} 字"
                if result.reasoning_tokens
                else f"正文 {len(text)} 字"
            )
            raise LLMTruncatedError(f"模型输出被截断（{detail}，finish_reason={result.finish_reason or 'stop'}）")
        return result

    def _once(self, payload: dict[str, Any], started: float) -> LLMResult:
        resp = self.session.post(self.cfg.chat_url, json=payload, timeout=(10, self.cfg.timeout))
        if resp.status_code >= 400:
            raise self._http_error(resp)
        data = resp.json()
        choice = (data.get("choices") or [{}])[0]
        text = strip_think(((choice.get("message") or {}).get("content")) or "")
        usage = self._usage(data.get("usage"))
        if not usage.total:
            usage.prompt = estimate_tokens("\n".join(m.get("content", "") for m in payload["messages"]))
            usage.completion = estimate_tokens(text)
            usage.total = usage.prompt + usage.completion
        return LLMResult(
            text=text,
            usage=usage,
            elapsed=time.time() - started,
            raw=data,
            finish_reason=str(choice.get("finish_reason") or ""),
            reasoning_tokens=self._reasoning_tokens(data.get("usage")),
        )

    def _stream(
        self, payload: dict[str, Any], on_delta: Callable[[str], None] | None, started: float
    ) -> LLMResult:
        parts: list[str] = []
        usage = Usage()
        finish_reason = ""
        reasoning_used = 0
        with self.session.post(
            self.cfg.chat_url, json=payload, timeout=(10, self.cfg.timeout), stream=True
        ) as resp:
            if resp.status_code >= 400:
                raise self._http_error(resp)
            for raw_line in resp.iter_lines(decode_unicode=True):
                if not raw_line:
                    continue
                line = raw_line.strip() if isinstance(raw_line, str) else raw_line.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                chunk = line[5:].strip()
                if chunk == "[DONE]":
                    break
                try:
                    data = json.loads(chunk)
                except json.JSONDecodeError:
                    continue
                if data.get("usage"):
                    usage = self._usage(data["usage"])
                    reasoning_used = self._reasoning_tokens(data.get("usage")) or reasoning_used
                choices = data.get("choices") or []
                if not choices:
                    continue
                choice = choices[0]
                if choice.get("finish_reason"):
                    finish_reason = str(choice["finish_reason"])
                delta = choice.get("delta") or {}
                piece = delta.get("content") or ""
                if piece:
                    parts.append(piece)
                    if on_delta:
                        on_delta(piece)
        text = strip_think("".join(parts))
        if not usage.total:
            usage.prompt = estimate_tokens("\n".join(m.get("content", "") for m in payload["messages"]))
            usage.completion = estimate_tokens(text)
            usage.total = usage.prompt + usage.completion
        return LLMResult(
            text=text,
            usage=usage,
            elapsed=time.time() - started,
            finish_reason=finish_reason,
            reasoning_tokens=reasoning_used,
        )

    @staticmethod
    def _reasoning_tokens(raw: dict[str, Any] | None) -> int:
        raw = raw or {}
        details = raw.get("completion_tokens_details")
        if isinstance(details, dict):
            return int(details.get("reasoning_tokens") or 0)
        return int(raw.get("reasoning_tokens") or 0)

    def _usage(self, raw: dict[str, Any] | None) -> Usage:
        raw = raw or {}
        prompt = int(raw.get("prompt_tokens") or 0)
        completion = int(raw.get("completion_tokens") or 0)
        total = int(raw.get("total_tokens") or prompt + completion)
        cached = int(raw.get("prompt_cache_hit_tokens") or raw.get("cached_tokens") or 0)
        if not cached and isinstance(raw.get("prompt_tokens_details"), dict):
            cached = int(raw["prompt_tokens_details"].get("cached_tokens") or 0)
        return Usage(prompt=prompt, completion=completion, total=total, cached=cached)

    def _http_error(self, resp: requests.Response) -> LLMError:
        detail = ""
        try:
            body = resp.json()
            detail = (
                (body.get("error") or {}).get("message")
                if isinstance(body.get("error"), dict)
                else body.get("message") or body.get("error") or ""
            )
        except Exception:
            detail = resp.text[:300]
        if resp.status_code == 401:
            detail = "API Key 无效或已失效。" + str(detail)
        elif resp.status_code == 402 or "balance" in str(detail).lower():
            detail = "账户余额不足。" + str(detail)
        elif resp.status_code == 429:
            detail = "请求过于频繁，请稍后再试。" + str(detail)
        return LLMError(f"HTTP {resp.status_code}: {detail}")

    def test(self) -> LLMResult:
        return self.chat(
            [{"role": "user", "content": "只回复 OK"}],
            temperature=0,
            max_tokens=4,
        )
