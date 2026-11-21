from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

import store
from config import AppConfig
from llm import LLMClient, LLMError, LLMResult, LLMTruncatedError, Usage, estimate_tokens, extract_json, strip_think
from prompts import (
    build_brief_messages,
    build_qa_messages,
    build_repair_messages,
    build_translation_messages,
    format_rule_cards,
)
from retrieval import (
    KnowledgeBase,
    TextProfile,
    build_qa_checks,
    detect_profile,
    estimate_baseline,
    split_sentences,
    split_sentences_zh,
    words_of,
)

PROMPT_VERSION = "2026-10-26d"
VALID_MODES = {"fast", "standard", "strict"}
VALID_READERS = {"general", "professional", "student", "child"}
VALID_TYPES = {"informative", "expressive", "operative", "mixed"}
TYPE_LABEL = {"informative": "信息型", "expressive": "表情型", "operative": "感染型", "mixed": "混合型"}
SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}


class PipelineError(RuntimeError):
    pass


@dataclass
class TranslationRequest:
    text: str
    mode: str = "standard"
    reader: str = "general"
    text_type: str = "auto"
    glossary: str = ""
    deep_brief: bool = False
    use_cache: bool = True
    source_lang: str = "en"
    target_lang: str = "zh"
    quality: bool = False
    strategy: int = 0
    poetry: bool = False
    genre: str = "default"
    review_enabled: bool = True


@dataclass
class PipelineResult:
    translation: str
    source: str
    profile: dict[str, Any]
    brief: str
    rules: list[dict[str, Any]]
    glossary: list[dict[str, str]]
    issues: list[dict[str, Any]]
    usage: dict[str, Any]
    baseline: int
    saved_pct: int
    elapsed: float
    mode: str
    model: str
    cached: bool = False
    chunks: int = 1
    qa: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    source_lang: str = "en"
    target_lang: str = "zh"
    quality: bool = False
    kb_revision: int = 1
    case_example: dict[str, Any] | None = None
    strategy: int = 0
    poetry: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "translation": self.translation,
            "source": self.source,
            "profile": self.profile,
            "brief": self.brief,
            "rules": self.rules,
            "glossary": self.glossary,
            "issues": self.issues,
            "usage": self.usage,
            "baseline": self.baseline,
            "saved_pct": self.saved_pct,
            "elapsed": round(self.elapsed, 2),
            "mode": self.mode,
            "model": self.model,
            "cached": self.cached,
            "chunks": self.chunks,
            "qa": self.qa,
            "created_at": self.created_at,
            "source_lang": self.source_lang,
            "target_lang": self.target_lang,
            "quality": self.quality,
            "kb_revision": self.kb_revision,
            "case_example": self.case_example,
            "strategy": self.strategy,
            "poetry": self.poetry,
        }


def _rule_refs(rules: list[dict[str, Any]]) -> list[dict[str, str]]:
    """历史/缓存里只保留规则引用，不落正文与例句。

    规则正文属于付费资产：历史记录会长期留在 SQLite 和导出文件里，若把
    gist/examples 一起写进去，免费用户用一条 SQL 或一次导出就能批量收集。
    读取时由 app.py 按当前档位从内存知识库补全。
    """
    from kb_guard import rule_refs

    return rule_refs(rules)


def normalize_text(text: str) -> str:
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u200b", "").replace("\ufeff", "")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text.strip()


def tidy_cjk_spacing(text: str) -> str:
    """清理译文里中文字符之间、标点前后的多余空格（模型偶发按 token 拼空格）。"""
    if not text:
        return text
    t = text.replace("\u00a0", " ")
    t = re.sub(r"(?<=[\u4e00-\u9fff])[ \t]+(?=[\u4e00-\u9fff])", "", t)
    t = re.sub(r"[ \t]+([，。；：！？、）】」』”’])", r"\1", t)
    t = re.sub(r"([（【「『“‘])[ \t]+", r"\1", t)
    t = re.sub(r"[ \t]{2,}", " ", t)
    t = re.sub(r"[ \t]+\n", "\n", t)
    return t


def text_units(text: str) -> int:
    return len(words_of(text)) + len(re.findall(r"[\u4e00-\u9fff]", text))


def validate(req: TranslationRequest) -> None:
    text = normalize_text(req.text)
    if len(text) < 30:
        raise PipelineError("原文太短，至少粘贴 30 个字符。")
    if len(text) > 50000:
        raise PipelineError("原文过长（超过 5 万字符），请分段处理。")
    words = words_of(text)
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    pair = (req.source_lang, req.target_lang)
    if pair not in {("en", "zh"), ("zh", "en")}:
        raise PipelineError("该语言对尚未接入；目前支持英语 → 汉语、汉语 → 英语。")
    if pair == ("en", "zh"):
        if len(words) < 8:
            raise PipelineError("没有检测到足够的英文内容，请粘贴英文原文。")
        if cjk > len(text) * 0.35 and len(words) < 40:
            raise PipelineError("这看起来是中文文本。当前方向是英译中，请粘贴英文原文。")
    else:
        if cjk < 20:
            raise PipelineError("没有检测到足够的中文内容，请粘贴中文原文。")
        if len(words) > cjk * 1.5:
            raise PipelineError("这看起来是英文文本。当前方向是中译英，请粘贴中文原文。")
    if req.mode not in VALID_MODES:
        raise PipelineError("未知模式。")
    if req.reader not in VALID_READERS:
        raise PipelineError("未知目标读者。")
    if req.strategy not in {-1, 0, 1}:
        raise PipelineError("未知翻译倾向。")


_DUP_SINCE = re.compile(r"自从([^，。；！？]{0,14}?)从")


def dedup_fix(text: str) -> str:
    """兜底清洗：去掉“自从……从……”这类近距离重复虚词。"""
    return _DUP_SINCE.sub(r"\1从", text)


def split_long_paragraph(paragraph: str, max_words: int, lang: str = "en") -> list[str]:
    sentences = split_sentences_zh(paragraph) if lang == "zh" else split_sentences(paragraph)
    groups: list[str] = []
    current: list[str] = []
    count = 0
    joiner = "" if lang == "zh" else " "
    for sentence in sentences:
        n = text_units(sentence)
        if current and count + n > max_words:
            groups.append(joiner.join(current))
            current, count = [sentence], n
        else:
            current.append(sentence)
            count += n
    if current:
        groups.append(joiner.join(current))
    # A paragraph with no usable sentence punctuation (dense prose, lists,
    # malformed text) can otherwise become one oversized API request.
    if len(groups) == 1 and text_units(groups[0]) > max_words * 1.5:
        raw = groups[0]
        hard: list[str] = []
        if lang == "zh":
            step = max(1, max_words)
            for i in range(0, len(raw), step):
                hard.append(raw[i:i + step])
        else:
            words = raw.split()
            step = max(1, max_words)
            for i in range(0, len(words), step):
                hard.append(" ".join(words[i:i + step]))
        return [x for x in hard if x.strip()] or groups
    return groups or [paragraph]


def split_chunks(text: str, max_words: int = 900, lang: str = "en") -> list[str]:
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paras:
        return [text]
    chunks: list[str] = []
    current: list[str] = []
    count = 0
    for para in paras:
        n = text_units(para)
        if n > max_words * 1.5:
            if current:
                chunks.append("\n\n".join(current))
                current, count = [], 0
            for group in split_long_paragraph(para, max_words, lang):
                chunks.append(group)
            continue
        if current and count + n > max_words:
            chunks.append("\n\n".join(current))
            current, count = [], 0
        current.append(para)
        count += n
    if current:
        chunks.append("\n\n".join(current))
    return chunks or [text]


def clean_translation(text: str) -> str:
    text = strip_think(text or "")
    text = re.sub(r"^```(?:markdown|md|text)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    lines = text.splitlines()
    while lines and re.match(r"^\s*(以下是|下面是|这是|以下为).{0,24}(译文|翻译)[：:。]?\s*$", lines[0]):
        lines.pop(0)
    while lines and re.match(r"^\s*(here is|here's|below is).{0,30}(translation|english)[:：]?\s*$", lines[0], re.IGNORECASE):
        lines.pop(0)
    text = "\n".join(lines).strip()
    text = re.sub(r"^(译文|翻译|english translation|translation)[：:]\s*", "", text, flags=re.IGNORECASE)
    return text.strip()


def _type_brief(text_type: str, secondary: str | None) -> str:
    main = TYPE_LABEL.get(text_type, text_type)
    if secondary and secondary != text_type:
        return f"{main}（次：{TYPE_LABEL.get(secondary, secondary)}）"
    return main


class Pipeline:
    def __init__(self, cfg: AppConfig, kb: KnowledgeBase, client: LLMClient | None = None):
        self.cfg = cfg
        self.kb = kb
        self.client = client or LLMClient(cfg)
        self._usage_local = threading.local()

    def cache_key(self, req: TranslationRequest, text: str) -> str:
        raw = "|".join(
            [
                PROMPT_VERSION,
                self.cfg.quality_model if req.quality else self.cfg.model,
                req.mode,
                req.reader,
                req.text_type,
                req.glossary.strip(),
                str(req.deep_brief),
                req.source_lang,
                req.target_lang,
                str(req.quality),
                str(req.strategy),
                str(req.poetry),
                req.genre,
                str(req.review_enabled),
                str(self.cfg.temperature),
                str(self.cfg.qa_temperature),
                str(self.cfg.top_rules),
                str(self.cfg.qa_checks),
                str(self.cfg.max_words_per_chunk),
                str(self.kb.revision),
                text,
            ]
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def run(
        self,
        req: TranslationRequest,
        emit: Callable[[dict[str, Any]], None] | None = None,
        global_terms: list[dict[str, str]] | None = None,
        context_note: str = "",
        qa_enabled: bool = True,
    ) -> PipelineResult:
        emit = emit or (lambda _e: None)
        started = time.time()
        text = normalize_text(req.text)
        req.text = text
        validate(req)
        run_model = self.cfg.quality_model if (req.quality and self.cfg.quality_model) else self.cfg.model
        # P2.2 模型分级：正文可用更快的草稿模型，审校/修复用更强（或更省）的模型
        draft_model = (getattr(self.cfg, "draft_model", "") or run_model).strip()
        review_model = (getattr(self.cfg, "review_model", "") or run_model).strip()

        def _client_for(model_name: str) -> LLMClient:
            if model_name == self.cfg.model:
                return self.client
            cfg2 = copy.deepcopy(self.cfg)
            cfg2.model = model_name
            return LLMClient(cfg2)

        self.client = _client_for(draft_model)
        run_model = draft_model
        qa_client = _client_for(review_model)

        key = self.cache_key(req, text)
        if req.use_cache:
            cached = store.get_result(key)
            if cached:
                meta = cached["meta"]
                emit({"type": "phase", "phase": "cache", "message": "命中本地缓存，未消耗 Token"})
                result = PipelineResult(
                    translation=cached["translation"],
                    source=text,
                    profile=meta.get("profile", {}),
                    brief=meta.get("brief", ""),
                    rules=meta.get("rules", []),
                    glossary=meta.get("glossary", []),
                    issues=meta.get("issues", []),
                    usage=meta.get("usage", {}),
                    baseline=int(meta.get("baseline") or 0),
                    saved_pct=int(meta.get("saved_pct") or 0),
                    elapsed=time.time() - started,
                    mode=req.mode,
                    model=run_model,
                    cached=True,
                    chunks=int(meta.get("chunks") or 1),
                    qa=meta.get("qa", {}),
                    created_at=cached.get("created_at", ""),
                    source_lang=req.source_lang,
                    target_lang=req.target_lang,
                    quality=req.quality,
                    kb_revision=self.kb.revision,
                    case_example=meta.get("case_example"),
                    strategy=req.strategy,
                    poetry=req.poetry,
                )
                emit({"type": "done", "result": result.as_dict()})
                return result

        emit({"type": "phase", "phase": "analyzing", "message": "本地文本分析中（不消耗 Token）"})
        lang_pair = f"{req.source_lang}-{req.target_lang}"
        profile = detect_profile(text, req.source_lang)
        reader = req.reader
        if req.text_type in VALID_TYPES:
            if req.text_type != profile.text_type:
                profile.secondary_type = profile.text_type if profile.text_type != req.text_type else None
            profile.text_type = req.text_type
            profile.type_source = "user"
        elif profile.text_type == "mixed" and not profile.secondary_type:
            profile.secondary_type = "informative"

        rules = self.kb.select(profile, self.cfg.top_rules, lang_pair, req.strategy, req.poetry, req.genre, text)
        case_example = self.kb.select_case(profile, text, 1, lang_pair, req.poetry, req.genre)
        glossary = self.kb.build_glossary(text, req.glossary, 20, lang_pair, req.genre)
        brief = profile.brief(reader, req.strategy, lang_pair)
        if context_note:
            brief = brief + "\n【章节摘要】" + context_note
        brief_usage = Usage()

        if req.mode == "strict" or req.deep_brief:
            emit({"type": "phase", "phase": "brief", "message": "生成深度分析卡（1 次小请求）"})
            try:
                data = self._json_call(
                    build_brief_messages(text, brief, profile.public()),
                    temperature=0.1,
                    max_tokens=1600 if req.quality else 1300,
                    client=qa_client,
                )
                brief_usage = self._last_usage
                brief, profile, extra_terms = self._merge_brief(data, profile, brief)
                if extra_terms:
                    existing = {g["en"].lower() for g in glossary}
                    for term in extra_terms:
                        en, zh = term.get("en", "").strip(), term.get("zh", "").strip()
                        if en and zh and en.lower() not in existing:
                            glossary.append({"en": en, "zh": zh, "source": "LLM"})
                            existing.add(en.lower())
                rules = self.kb.select(profile, self.cfg.top_rules, lang_pair, req.strategy, req.poetry, req.genre, text)
                case_example = self.kb.select_case(profile, text, 1, lang_pair, req.poetry, req.genre)
            except Exception:
                # 深度分析失败不影响主流程
                pass

        emit(
            {
                "type": "context",
                "profile": profile.public(),
                "brief": brief,
                "rules": [{"id": r["id"], "title": r["title"], "gist": r["gist"], "why": r.get("why", ""), "examples": r.get("examples", [])} for r in rules],
                "glossary": glossary,
                "case_example": case_example,
                "kb_revision": self.kb.revision,
                "lang_pair": lang_pair,
                "strategy": req.strategy,
            }
        )

        chunks = split_chunks(text, self.cfg.max_words_per_chunk, req.source_lang)
        emit({"type": "phase", "phase": "translating", "message": f"翻译中（{len(chunks)} 段）"})
        usage = {
            "analysis": brief_usage,
            "translate": Usage(),
            "qa": Usage(),
            "repair": Usage(),
        }
        translations: list[str] = []
        all_issues: list[dict[str, Any]] = []
        context_tail = ""
        checks = build_qa_checks(rules, self.cfg.qa_checks, lang_pair)

        for index, chunk in enumerate(chunks):
            if index:
                emit({"type": "phase", "phase": "translating", "message": f"翻译第 {index + 1}/{len(chunks)} 段"})
            messages = build_translation_messages(
                chunk,
                brief,
                rules,
                glossary,
                profile.proper_nouns,
                context_tail,
                case_example,
                lang_pair,
                req.strategy,
            )
            max_out = self._translate_budget(chunk)
            try:
                translated, result = self._translate_chunk(chunk, messages, index, emit, max_out)
            except LLMError as exc:
                raise PipelineError(str(exc)) from exc

            usage["translate"].add(result.usage)
            translations.append(translated)
            context_tail = translated[-180:]
            emit(
                {
                    "type": "usage",
                    "stage": "translate",
                    "usage": result.usage.as_dict(),
                    "total": usage["translate"].as_dict(),
                    "chunk": index,
                }
            )

            if qa_enabled and req.mode != "fast" and req.review_enabled and translated.strip():
                emit({"type": "phase", "phase": "checking", "message": f"审校第 {index + 1}/{len(chunks)} 段"})
                draft = translated
                try:
                    qa = self._json_call(
                        build_qa_messages(chunk, draft, checks, glossary, lang_pair),
                        temperature=self.cfg.qa_temperature,
                        max_tokens=2400 if req.quality else 2000,
                        client=qa_client,
                    )
                    qa_usage = self._last_usage
                    usage["qa"].add(qa_usage)
                    issues = self._normalize_issues(qa.get("issues"))
                    patched, applied, skipped = self._apply_fixes(draft, issues)
                    total_applied = applied
                    total_skipped = skipped
                    high_left = [i for i in issues if i["severity"] == "high" and not i.get("applied")]
                    if req.mode == "strict" and high_left:
                        emit({"type": "phase", "phase": "repairing", "message": "定点修复未能自动替换的硬伤"})
                        try:
                            repair = self._json_call(
                                build_repair_messages(high_left, patched),
                                temperature=0,
                                max_tokens=2000 if req.quality else 1200,
                                client=qa_client,
                            )
                            usage["repair"].add(self._last_usage)
                            fixes = repair.get("fixes") or []
                            for item in fixes:
                                q, f = str(item.get("quote", "")).strip(), str(item.get("fix", "")).strip()
                                if q and q in patched and q != f:
                                    patched = patched.replace(q, f, 1)
                                    total_applied += 1
                            for issue in high_left:
                                if issue["quote"] not in patched:
                                    issue["applied"] = True
                        except Exception:
                            pass
                    translations[-1] = patched
                    for issue in issues:
                        issue["chunk"] = index
                    all_issues.extend(issues)
                    high = sum(1 for i in issues if i["severity"] == "high" and not i.get("applied"))
                    medium = sum(1 for i in issues if i["severity"] == "medium" and not i.get("applied"))
                    low = sum(1 for i in issues if i["severity"] == "low")
                    emit(
                        {
                            "type": "qa",
                            "chunk": index,
                            "issues": issues,
                            "applied": total_applied,
                            "skipped": total_skipped,
                            "passed": high == 0 and medium == 0,
                            "counts": {"high": high, "medium": medium, "low": low},
                        }
                    )
                except Exception as exc:
                    emit(
                        {
                            "type": "qa",
                            "chunk": index,
                            "issues": [],
                            "applied": 0,
                            "skipped": 0,
                            "passed": True,
                            "warning": f"审校请求失败，已保留译文：{exc}",
                            "counts": {"high": 0, "medium": 0, "low": 0},
                        }
                    )

        translation = "\n\n".join(t.strip() for t in translations if t.strip()).strip()
        total = Usage(
            prompt=sum(u.prompt for u in usage.values()),
            completion=sum(u.completion for u in usage.values()),
            total=sum(u.total for u in usage.values()),
            cached=sum(u.cached for u in usage.values()),
        )
        baseline = estimate_baseline(profile, self.cfg.baseline_per_word, self.cfg.baseline_fixed)
        saved = max(0, min(99, round((1 - total.total / baseline) * 100))) if baseline > 0 and total.total > 0 else 0
        high_left = sum(1 for i in all_issues if i["severity"] == "high" and not i.get("applied"))
        medium_left = sum(1 for i in all_issues if i["severity"] == "medium" and not i.get("applied"))
        qa_summary = {
            "passed": high_left == 0 and medium_left == 0,
            "high": high_left,
            "medium": medium_left,
            "low": sum(1 for i in all_issues if i["severity"] == "low"),
            "applied": sum(1 for i in all_issues if i.get("applied")),
            "skipped": sum(1 for i in all_issues if i.get("skipped")),
        }
        translation = tidy_cjk_spacing(translation)
        translation = dedup_fix(translation)
        result = PipelineResult(
            translation=translation,
            source=text,
            profile=profile.public(),
            brief=brief,
            rules=[{"id": r["id"], "title": r["title"], "gist": r["gist"], "why": r.get("why", ""), "examples": r.get("examples", [])} for r in rules],
            glossary=glossary,
            issues=all_issues,
            usage={
                "analysis": usage["analysis"].as_dict(),
                "translate": usage["translate"].as_dict(),
                "qa": usage["qa"].as_dict(),
                "repair": usage["repair"].as_dict(),
                "total": total.as_dict(),
                "by_stage_total": total.total,
            },
            baseline=baseline,
            saved_pct=saved,
            elapsed=time.time() - started,
            mode=req.mode,
            model=run_model,
            chunks=len(chunks),
            qa=qa_summary,
            created_at=datetime.now().isoformat(timespec="seconds"),
            source_lang=req.source_lang,
            target_lang=req.target_lang,
            quality=req.quality,
            kb_revision=self.kb.revision,
            case_example=case_example,
            strategy=req.strategy,
            poetry=req.poetry,
        )
        meta = result.as_dict()
        # 持久化/缓存只留规则引用，正文由读取端按档位补全。
        meta["rules"] = _rule_refs(rules)
        store.put_result(key, text, translation, meta)
        try:
            store.add_history(text, translation, total.total, baseline, req.mode, meta)
        except Exception:
            pass
        emit({"type": "phase", "phase": "done", "message": "完成"})
        emit({"type": "done", "result": result.as_dict()})
        return result

    def _translate_budget(self, chunk: str) -> int:
        """单段译文的输出预算。

        推理模型（deepseek-flash / deepseek-pro 等）先写 reasoning_content，
        这些思考 token 和正文共用同一个 max_tokens。只按原文长度给预算时，长文
        的思考会把预算吃光，接口返回空正文或半截译文——这正是 v0.2 里“长文
        无法返还译文”的原因。这里给思考单独留出余量。
        """
        units = text_units(chunk)
        needed = int(units * 3.2) + 400
        return max(2600, min(self.cfg.max_tokens, needed + 2600))

    @staticmethod
    def _plausible_length(chunk: str, translated: str) -> bool:
        """粗判译文是否被截断：中文约 1.5 字/英文词，英文约 0.6 词/汉字。"""
        units = text_units(chunk)
        if units < 40:
            return True
        if re.search(r"[\u4e00-\u9fff]", translated):
            return len(re.findall(r"[\u4e00-\u9fff]", translated)) >= units * 0.55
        words = len(re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", translated))
        return words >= units * 0.35

    def _translate_chunk(
        self,
        chunk: str,
        messages: list[dict[str, str]],
        index: int,
        emit: Callable[[dict[str, Any]], None],
        budget: int,
    ) -> tuple[str, Any]:
        last_error = ""
        nudge = False
        for attempt in range(2):
            stream = attempt == 0
            msgs = messages
            if nudge and len(messages) >= 2:
                msgs = [
                    messages[0],
                    {
                        "role": "user",
                        "content": messages[1]["content"]
                        + "\n\n【额外要求】上一版译文出现了同一个名词或短语连续重复。"
                        "请重写这一段：不要复制同一个名词或短句，必要时用省略、指代或换句式。",
                    },
                ]
            try:
                result = self.client.chat(
                    msgs,
                    temperature=0 if nudge else self.cfg.temperature,
                    max_tokens=budget,
                    on_delta=(
                        lambda piece, idx=index: emit({"type": "delta", "text": piece, "chunk": idx})
                    )
                    if stream
                    else None,
                )
            except LLMTruncatedError as exc:
                last_error = str(exc)
                budget = self.cfg.max_tokens
                if attempt == 0:
                    emit(
                        {
                            "type": "phase",
                            "phase": "translating",
                            "message": f"输出预算被思考占用，已加大预算重试第 {index + 1} 段",
                        }
                    )
                continue
            except LLMError as exc:
                raise LLMError(str(exc)) from exc
            translated = clean_translation(result.text)
            # 只有在“这次请求确实产生了思考 token”时才怀疑被截断：
            # 关闭思考模式后 reasoning_tokens=0，正常的短译文不会被误判。
            if translated and (not result.reasoning_tokens or self._plausible_length(chunk, translated)):
                return translated, result
            last_error = "译文明显短于原文，疑似被思考过程挤占"
            budget = self.cfg.max_tokens
            if attempt == 0:
                emit(
                    {
                        "type": "phase",
                        "phase": "translating",
                        "message": f"检测到译文不完整，已加大预算重试第 {index + 1} 段",
                    }
                )
        if last_error:
            raise PipelineError(
                "模型没有返回完整译文：输出的思考过程占满了 token 预算。"
                "请在设置里勾选“关闭思考模式”，或把“最大输出 token”调到 8192 后重试。"
                f"（{last_error}）"
            )
        raise PipelineError("模型返回了空译文，请检查模型设置后重试。")

    def _json_call(
        self,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int,
        client: LLMClient | None = None,
    ) -> dict[str, Any]:
        caller = client or self.client
        # 推理 token 与 JSON 正文共用预算，预算太小会拿到空 JSON
        budget = max(1200, int(max_tokens))
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                try:
                    result = caller.chat(
                        messages,
                        temperature=temperature,
                        max_tokens=budget,
                        response_format={"type": "json_object"},
                    )
                except LLMTruncatedError:
                    raise
                except LLMError:
                    # 少数兼容接口不支持 response_format，退回纯文本解析
                    result = caller.chat(messages, temperature=temperature, max_tokens=budget)
            except LLMTruncatedError as exc:
                last_error = exc
                budget = min(self.cfg.max_tokens, max(2048, budget * 2))
                continue
            self._usage_local.usage = result.usage
            return extract_json(result.text)
        raise LLMError(str(last_error))

    @property
    def _last_usage(self) -> Usage:
        return getattr(self._usage_local, "usage", Usage())

    def _merge_brief(
        self, data: dict[str, Any], profile: TextProfile, local_brief: str
    ) -> tuple[str, TextProfile, list[dict[str, str]]]:
        text_type = str(data.get("text_type") or "").lower()
        secondary = data.get("secondary")
        secondary = str(secondary).lower() if secondary and str(secondary).lower() in VALID_TYPES else None
        if text_type in VALID_TYPES:
            profile.text_type = text_type
            profile.secondary_type = secondary if secondary != text_type else None
            profile.type_source = "llm"
        for attr in ("genre", "tone"):
            value = data.get(attr)
            if isinstance(value, str) and value.strip():
                setattr(profile, attr, value.strip()[:40])
        strategy = str(data.get("strategy") or "").strip()
        raw_pitfalls = data.get("pitfalls")
        pitfalls = (
            [str(x).strip() for x in raw_pitfalls if str(x).strip()][:3]
            if isinstance(raw_pitfalls, list)
            else []
        )
        reader = str(data.get("reader") or "").strip()
        if strategy or pitfalls:
            lines = [
                f"文本类型：{_type_brief(profile.text_type, profile.secondary_type)}；文体：{profile.genre}；"
                f"读者：{reader or '普通中文读者'}；语气：{profile.tone}。",
                f"策略：{strategy or profile._strategy()}",
            ]
            if pitfalls:
                lines.append("注意：" + "；".join(pitfalls) + "。")
            brief = "\n".join(lines)
        else:
            brief = local_brief
        terms = []
        for term in data.get("terms") or []:
            if isinstance(term, dict) and term.get("en") and term.get("zh"):
                terms.append({"en": str(term["en"]).strip(), "zh": str(term["zh"]).strip()})
        return brief, profile, terms

    def _normalize_issues(self, raw: Any) -> list[dict[str, Any]]:
        if not isinstance(raw, list):
            return []
        out: list[dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            severity = str(item.get("severity", "medium")).lower()
            if severity not in SEVERITY_RANK:
                severity = "medium"
            quote = str(item.get("quote", "")).strip().strip("“”\"'")
            fix = str(item.get("fix", "")).strip().strip("“”\"'")
            rule = str(item.get("rule", "")).strip()
            if not re.match(r"^KB\d-\d{3}$", rule):
                rule = ""
            why = str(item.get("why", "")).strip()[:40]
            if not quote or quote == fix:
                continue
            if len(quote) > 120:
                quote = quote[:120]
            if len(fix) > 160:
                fix = fix[:160]
            out.append(
                {
                    "severity": severity,
                    "rule": rule,
                    "quote": quote,
                    "fix": fix,
                    "why": why,
                    "applied": False,
                    "skipped": False,
                }
            )
        out.sort(key=lambda x: SEVERITY_RANK[x["severity"]])
        return out[:8]

    @staticmethod
    def _has_adjacent_repeat(text: str) -> bool:
        """检测“动词/名词紧挨着重复”的补丁事故，例如“点火，用煤油点火”。

        在 10 字窗口内找重复的双字组，跳过自我重叠（“一个个”“常常”不算）。
        """
        for i in range(0, len(text) - 2):
            chunk = text[i : i + 2]
            if not re.match(r"[\u4e00-\u9fff]{2}", chunk):
                continue
            j = text.find(chunk, i + 2)
            if j != -1 and j - i <= 10:
                return True
        return False

    @staticmethod
    def _fix_repeats_prefix(text: str, quote: str, fix: str) -> bool:
        """补丁里出现的双字组，是否刚好在插入点前面已经出现过。"""
        idx = text.find(quote)
        if idx < 0:
            return False
        prefix = text[max(0, idx - 12) : idx]
        for i in range(0, len(fix) - 1):
            chunk = fix[i : i + 2]
            if re.match(r"[\u4e00-\u9fff]{2}", chunk) and chunk in prefix:
                return True
        return False

    def _apply_fixes(self, draft: str, issues: list[dict[str, Any]]) -> tuple[str, int, int]:
        applied = 0
        skipped = 0
        text = draft
        for issue in issues:
            quote, fix = issue["quote"], issue["fix"]
            fix = re.sub(r"^(?:改为|建议改为|应改为|可改为)[：:]\s*", "", fix or "").strip()
            issue["fix"] = fix
            if not quote or quote == fix or len(quote) < 2:
                issue["skipped"] = True
                skipped += 1
                continue
            # 低级别问题只做小幅替换，避免借“润色”重写整句
            if issue["severity"] == "low" and (not fix or len(quote) > 60 or len(fix) > len(quote) * 1.35):
                issue["skipped"] = True
                skipped += 1
                continue
            # 模型有时把“下一小句”也塞进 fix，造成同一句重复；这种补丁直接拒绝
            if fix.startswith(quote) and quote.rstrip().endswith((",", "，", ";", "：")) and len(fix) > len(quote) + 8:
                issue["skipped"] = True
                skipped += 1
                continue
            if quote in text:
                if quote == text.strip() and not fix:
                    issue["skipped"] = True
                    skipped += 1
                    continue
                candidate = text.replace(quote, fix, 1)
                # 补丁不得制造“相邻重复”，也不得把已有的词再插一遍
                if (self._has_adjacent_repeat(candidate) and not self._has_adjacent_repeat(text)) or self._fix_repeats_prefix(text, quote, fix):
                    issue["skipped"] = True
                    skipped += 1
                    continue
                text = candidate
                issue["applied"] = True
                applied += 1
                continue
            # 容错：去掉空白后再找一次
            compact = re.sub(r"\s+", "", quote)
            if compact:
                pattern = re.compile(r"\s*".join(re.escape(ch) for ch in compact))
                match = pattern.search(text)
                if match:
                    candidate = text[: match.start()] + fix + text[match.end() :]
                    if (self._has_adjacent_repeat(candidate) and not self._has_adjacent_repeat(text)) or self._fix_repeats_prefix(text, quote, fix):
                        issue["skipped"] = True
                        skipped += 1
                        continue
                    text = candidate
                    issue["applied"] = True
                    applied += 1
                    continue
            issue["skipped"] = True
            skipped += 1
        return text, applied, skipped
