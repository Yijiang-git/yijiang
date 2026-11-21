from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
DATA = Path(os.getenv("YIJIANG_DATA", str(ROOT / "data"))).expanduser()
DATA.mkdir(parents=True, exist_ok=True)
# 开发者模式：构建期 dev 档，或“源码直跑 + YIJIANG_DEV=1”。
# PyInstaller 打包后 sys.frozen 为 True，运行期环境变量不再能解锁发布包。
try:
    from edition import EDITION as _EDITION
except Exception:  # pragma: no cover - edition.py 总是随包存在
    _EDITION = "free"
_SOURCE_RUN = not getattr(sys, "frozen", False)
DEV_MODE = _EDITION == "dev" or (_SOURCE_RUN and os.getenv("YIJIANG_DEV") == "1")
# v0.2 起，体验期只由 entitlements 的 first_launch_at 控制，不再用「测试版标记文件」。
# 旧的 resources/test_build.json 可能是历史残留，必须失效，否则新旧包行为不一致。
TEST_BUILD = os.getenv("YIJIANG_TEST_BUILD") == "1"
TEST_EXPIRY_HOURS = 72
CONFIG_PATH = Path(os.getenv("YIJIANG_CONFIG", str(ROOT / "config.json"))).expanduser()
EXAMPLE_PATH = ROOT / "config.example.json"
SECURE_KEY_PATH = DATA / "config.secure"


@dataclass
class AppConfig:
    provider: str = "deepseek"
    base_url: str = "https://api.deepseek.com"
    api_key: str = ""
    model: str = "deepseek-flash"
    quality_model: str = "deepseek-v4-pro"
    # P2.2 模型分级：draft 用于正文翻译，review 用于审校/修复；留空则跟随 model
    draft_model: str = ""
    review_model: str = ""
    # 关闭“思考模式”。DeepSeek V3.2 这类混合推理模型的 reasoning_content
    # 与正文共用 max_tokens，长文会因此拿到空译文或半截译文；翻译、审校都是
    # 确定性任务，关掉思考更快更稳。
    disable_thinking: bool = True
    temperature: float = 0.5
    qa_temperature: float = 0.0
    max_tokens: int = 8192
    timeout: int = 180
    default_mode: str = "standard"
    default_reader: str = "general"
    auto_open: bool = True
    port: int = 8765
    host: str = "127.0.0.1"
    # 成本/token 参考：你现有 Dify 工作流在 483 词样本上实测 32036 tokens
    baseline_per_word: float = 66.3
    baseline_fixed: int = 0
    # 每个分块的最大英文词数。段落优先，不切断句子。
    max_words_per_chunk: int = 900
    # 送给翻译模型的规则卡数量与 QA 检查项数量
    top_rules: int = 5
    qa_checks: int = 10
    extra: dict[str, Any] = field(default_factory=dict)

    def public(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("extra", None)
        payload["has_key"] = bool(self.api_key)
        payload["api_key_masked"] = mask_key(self.api_key)
        payload.pop("api_key", None)
        return payload

    @property
    def chat_url(self) -> str:
        return self.base_url.rstrip("/") + "/chat/completions"


def mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 10:
        return "*" * len(key)
    return key[:4] + "*" * 10 + key[-4:]


def load_config() -> AppConfig:
    cfg = AppConfig()
    if EXAMPLE_PATH.exists():
        try:
            raw = json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))
            for k, v in raw.items():
                if hasattr(cfg, k):
                    setattr(cfg, k, v)
        except Exception:
            pass
    if CONFIG_PATH.exists():
        try:
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            for k, v in raw.items():
                if hasattr(cfg, k):
                    setattr(cfg, k, v)
        except Exception:
            pass

    # API keys are not kept in config.json. Windows DPAPI protects the user key.
    try:
        from entitlements import load_protected, save_protected

        if SECURE_KEY_PATH.exists():
            secure = load_protected(SECURE_KEY_PATH, {})
            if secure.get("api_key"):
                cfg.api_key = str(secure["api_key"]).strip()
        elif cfg.api_key:
            save_protected(SECURE_KEY_PATH, {"api_key": cfg.api_key})
    except Exception:
        pass

    cfg.api_key = os.getenv("DEEPSEEK_API_KEY", cfg.api_key).strip()
    cfg.base_url = os.getenv("DEEPSEEK_BASE_URL", cfg.base_url).strip()
    cfg.model = os.getenv("TRANSLATION_MODEL", cfg.model).strip()

    # `deepseek-pro` 不是该接口的真实模型名（正确的是 deepseek-v4-pro），
    # 保留旧值会让“额外精修”直接 400。
    cfg.draft_model = os.getenv("DRAFT_MODEL", getattr(cfg, "draft_model", "") or "").strip()
    cfg.review_model = os.getenv("REVIEW_MODEL", getattr(cfg, "review_model", "") or "").strip()
    if str(cfg.quality_model).strip() in {"deepseek-pro", "", "None"}:
        cfg.quality_model = "deepseek-v4-pro"

    # migrate an old plaintext key out of config.json
    try:
        if CONFIG_PATH.exists():
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if raw.get("api_key"):
                save_config(cfg)
    except Exception:
        pass

    # 首次运行时，从构建阶段导入的 Dify 密钥初始化
    key_file = DATA / ".deepseek_key"
    if not cfg.api_key and key_file.exists():
        cfg.api_key = key_file.read_text(encoding="ascii").strip()
        save_config(cfg)
        try:
            key_file.unlink()
        except OSError:
            pass
    return cfg


def save_config(cfg: AppConfig) -> None:
    payload = asdict(cfg)
    payload.pop("api_key", None)  # never write the key to plaintext config.json
    tmp = CONFIG_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(CONFIG_PATH)
    if cfg.api_key:
        try:
            from entitlements import save_protected

            save_protected(SECURE_KEY_PATH, {"api_key": cfg.api_key})
        except Exception:
            pass


def update_config(cfg: AppConfig, patch: dict[str, Any]) -> AppConfig:
    for key, value in patch.items():
        if key in {"api_key", "base_url", "model", "quality_model", "default_mode", "default_reader", "host"}:
            if key == "api_key" and (value is None or value == "" or value == "__KEEP__"):
                continue
            text = str(value).strip()
            if key in {"base_url", "model", "host"} and not text:
                continue
            setattr(cfg, key, text)
        elif key in {"temperature", "qa_temperature", "baseline_per_word"}:
            setattr(cfg, key, float(value))
        elif key in {"max_tokens", "timeout", "port", "baseline_fixed", "max_words_per_chunk", "top_rules", "qa_checks"}:
            setattr(cfg, key, int(value))
        elif key in {"auto_open", "disable_thinking"}:
            setattr(cfg, key, bool(value))
    save_config(cfg)
    return cfg
