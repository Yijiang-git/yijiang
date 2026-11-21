from __future__ import annotations

import asyncio
import copy
import hmac
import json
import logging
import os
import queue
import re
import secrets
import socket
import subprocess
import threading
import time
import uuid
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import store
from builtin_kb import BuiltinKB
from edition import EDITION, IS_OFFLINE
from config import DATA, DEV_MODE, ROOT, TEST_BUILD, AppConfig, load_config, update_config
from entitlements import Entitlements
import kb_tiers
from kb_guard import hydrate_rule_refs, public_rule, sanitize_history_meta
from llm import LLMClient
from pipeline import Pipeline, PipelineError, TranslationRequest, normalize_text
from retrieval import KnowledgeBase, detect_profile, estimate_baseline, words_of
from userkb import UserKB, UserKBError

_handlers: list[logging.Handler] = [logging.StreamHandler()]
try:
    from logging.handlers import RotatingFileHandler
    _log_dir = DATA / "logs"
    _log_dir.mkdir(parents=True, exist_ok=True)
    _handlers.insert(0, RotatingFileHandler(_log_dir / "app.log", maxBytes=2_000_000,
                                            backupCount=3, encoding="utf-8"))
except Exception:
    pass
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=_handlers,
)
log = logging.getLogger("translation-studio")

APP_VERSION = "0.2"
STARTED_AT = time.time()
PRODUCT_NAME = "译匠"

cfg: AppConfig = load_config()

# 企业离线版：地址锁在本机模型，避免误连外网
if IS_OFFLINE:
    cfg.base_url = "http://127.0.0.1:11434/v1"
    cfg.provider = "local"
    cfg.model = "qwen2.5:7b"
    cfg.quality_model = "qwen2.5:7b"
    edtion_lock = True
kb_store = BuiltinKB()  # 基线 + 用户改动副本（data/kb_v2.json）
user_kb = UserKB()
entitlements = Entitlements()
BUILTIN_RULE_IDS = {r["id"] for r in kb_store.snapshot().get("rules", [])}
user_kb.resolve_builtin_collisions(BUILTIN_RULE_IDS)


def visible_builtin() -> dict[str, Any]:
    """当前档位可见的内置知识库（A/B/C1/C2/D 按档裁剪）。"""
    return kb_tiers.visible_payload(kb_store.snapshot(), entitlements.tier_name())


kb = KnowledgeBase(user_kb.merged(visible_builtin(), builtin_editable=True))
client = LLMClient(cfg)
pipeline = Pipeline(cfg, kb, client)
KB_LOCK = threading.RLock()
BUILTIN_CASE_IDS = {c["id"] for c in kb_store.snapshot().get("cases", [])}
BUILTIN_TERM_KEYS = {g["en"].lower() for g in kb_store.snapshot().get("glossary", [])}


def load_languages() -> dict[str, Any]:
    path = DATA / "languages.json"
    if not path.exists():
        path = ROOT / "resources" / "languages.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"languages": [], "pairs": []}


def pair_status(source: str, target: str) -> dict[str, Any]:
    for pair in load_languages().get("pairs", []):
        if pair.get("source") == source and pair.get("target") == target:
            return pair
    return {"id": f"{source}-{target}", "source": source, "target": target, "status": "placeholder", "label": f"{source} → {target}"}


def reload_kb() -> None:
    global kb, pipeline, kb_store, BUILTIN_RULE_IDS, BUILTIN_CASE_IDS, BUILTIN_TERM_KEYS
    with KB_LOCK:
        kb_store = BuiltinKB()
        snapshot = kb_store.snapshot()
        BUILTIN_RULE_IDS = {r["id"] for r in snapshot.get("rules", [])}
        BUILTIN_CASE_IDS = {c["id"] for c in snapshot.get("cases", [])}
        BUILTIN_TERM_KEYS = {g["en"].lower() for g in snapshot.get("glossary", [])}
        user_kb.resolve_builtin_collisions(BUILTIN_RULE_IDS)
        kb = KnowledgeBase(user_kb.merged(visible_builtin(), builtin_editable=True))
        pipeline = Pipeline(cfg, kb, LLMClient(cfg))
    log.info("kb reloaded revision=%s rules=%s tier=%s", kb.revision, len(kb.rules), entitlements.tier_name())


app = FastAPI(title="译匠 · 本地翻译工作台", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

# 本地 HTTP 接口的会话 token。desktop.py 在启动前生成，并写进 server.json；
# 前端从窗口 URL 的 ?token= 读取，再用 X-Yijiang-Token 头访问 /api/*。
# 这样同机的普通网页（含 DNS rebinding）不能改 base_url / 读授权信息。
API_TOKEN = os.getenv("YIJIANG_API_TOKEN", "").strip()


@dataclass
class Job:
    id: str
    queue: "queue.Queue[dict[str, Any] | None]" = field(default_factory=queue.Queue)
    created: float = field(default_factory=time.time)
    thread: threading.Thread | None = None
    pro: bool = False


JOBS: dict[str, Job] = {}
JOBS_LOCK = threading.Lock()


class TranslateBody(BaseModel):
    text: str = Field(min_length=1)
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


class ProfileBody(BaseModel):
    text: str
    mode: str = "standard"
    source_lang: str = "en"
    target_lang: str = "zh"
    strategy: int = 0
    poetry: bool = False
    genre: str = "default"


class ConfigBody(BaseModel):
    api_key: str | None = None
    provider: str | None = None
    base_url: str | None = None
    model: str | None = None
    quality_model: str | None = None
    disable_thinking: bool | None = None
    temperature: float | None = None
    qa_temperature: float | None = None
    default_mode: str | None = None
    default_reader: str | None = None
    auto_open: bool | None = None
    top_rules: int | None = None
    qa_checks: int | None = None


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html", headers={"Cache-Control": "no-store"})


@app.middleware("http")
async def _api_token_guard(request: Request, call_next):
    if API_TOKEN and request.url.path.startswith("/api/") and request.url.path != "/api/health":
        supplied = (
            request.headers.get("x-yijiang-token")
            or request.query_params.get("token")
            or ""
        )
        if not hmac.compare_digest(supplied, API_TOKEN):
            return JSONResponse(
                status_code=403,
                content={"detail": "本地接口需要授权；请从译匠应用窗口打开，不要直接访问端口。"},
            )
    return await call_next(request)


@app.middleware("http")
async def _timing(request: Request, call_next):
    started = time.time()
    try:
        response = await call_next(request)
    except Exception:
        log.exception("request failed: %s %s", request.method, request.url.path)
        raise
    cost = time.time() - started
    if cost > 3:
        log.warning("slow %.1fs %s %s", cost, request.method, request.url.path)
    return response


@app.get("/api/health")
async def health():
    """轻量健康检查：不占线程池，线程池被打满时也能回应。"""
    info = {"ok": True, "uptime": round(time.time() - STARTED_AT, 1), "version": APP_VERSION}
    try:
        import anyio.to_thread
        limiter = anyio.to_thread.current_default_thread_limiter()
        info["threads_taken"] = limiter.borrowed_tokens
        info["threads_total"] = limiter.total_tokens
    except Exception:
        pass
    return info


@app.post("/api/heartbeat")
def heartbeat():
    try:
        (DATA / "heartbeat").write_text(str(time.time()), encoding="ascii")
    except Exception:
        pass
    return {"ok": True}


@app.get("/api/state")
def state():
    return {
        "config": cfg.public(),
        "kb": kb.meta,
        "kb_revision": kb.revision,
        "languages": load_languages(),
        "history": store.stats(),
        "baseline_per_word": cfg.baseline_per_word,
        "license": entitlements.public(),
        "version": APP_VERSION,
        "edition": EDITION,
    }


@app.get("/api/test/export")
def test_export():
    """导出用户数据（任何时候可用，不加密、不含 API Key）。"""
    records = []
    for item in store.list_history(100000):
        full = store.get_history(int(item["id"]))
        if full:
            # 历史里只存规则引用；导出前再按当前档位兜底清一次，
            # 防止旧版本遗留的记录把正文/例句带出去。
            meta = full.get("meta")
            if isinstance(meta, dict):
                full["meta"] = sanitize_history_meta(meta, entitlements.can_view_rules)
            records.append(full)
    payload = {
        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "history": records,
        "user_kb": user_kb.export_payload(),
        "settings": cfg.public(),
        "note": "不包含 API Key；内置知识库不导出",
    }
    body = json.dumps(payload, ensure_ascii=False, indent=1)
    return Response(
        content=body,
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="yijiang-user-data.json"'},
    )


@app.post("/api/test/uninstall")
def test_uninstall_help():
    try:
        os.startfile("ms-settings:appsfeatures")
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc
    return {"ok": True}


@app.post("/api/profile")
def profile(body: ProfileBody):
    pair = pair_status(body.source_lang, body.target_lang)
    if pair.get("status") != "ready":
        return {"ok": False, "unsupported": True, "pair": pair}
    text = normalize_text(body.text)
    lang = body.source_lang
    if (len(words_of(text)) if lang == "en" else len(re.findall(r"[\u4e00-\u9fff]", text))) < 5:
        return {"ok": False}
    lang_pair = f"{body.source_lang}-{body.target_lang}"
    p = detect_profile(text, lang)
    rules = kb.select(p, cfg.top_rules, lang_pair, body.strategy, body.poetry, body.genre, text)
    case = kb.select_case(p, text, 1, lang_pair, body.poetry, body.genre)
    glossary = kb.build_glossary(text, "", 14, lang_pair)
    words = max(1, p.word_count)
    mode = body.mode if body.mode in {"fast", "standard", "strict"} else "standard"
    if mode == "fast":
        low, high = int(words * 2.4 + 400), int(words * 4.2 + 900)
    elif mode == "strict":
        low, high = int(words * 3.6 + 900), int(words * 6.2 + 1500)
    else:
        low, high = int(words * 3.0 + 700), int(words * 5.4 + 1200)
    preview = entitlements.is_pro or not entitlements.has_translated()
    # 规则实体只对企业版/开发者版下发；其他版本只告诉“召回了多少条”，
    # 否则调用一次 /api/profile 就能把命中规则整段抓走。
    show_rule_text = entitlements.can_view_rules
    return {
        "ok": True,
        "profile": p.public(),
        "brief": p.brief("general", body.strategy, lang_pair) if preview else "",
        "rules": [{"id": r["id"], "title": r["title"], "gist": r["gist"], "why": r.get("why", ""), "examples": r.get("examples", [])} for r in rules] if (preview and show_rule_text) else [],
        "rules_count": len(rules),
        "rules_hidden": not show_rule_text,
        "glossary": glossary if preview else [],
        "case_example": case if preview else None,
        "kb_revision": kb.revision,
        "estimate": {"low": low, "high": high, "baseline": estimate_baseline(p, cfg.baseline_per_word)},
    }


@app.get("/api/languages")
def languages():
    return load_languages()


@app.post("/api/config")
def set_config(body: ConfigBody):
    global cfg, client, pipeline
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    if IS_OFFLINE:
        patch["base_url"] = "http://127.0.0.1:11434/v1"
        patch["provider"] = "local"
    if patch.get("api_key") and "****" in str(patch["api_key"]):
        patch.pop("api_key", None)
    cfg = update_config(cfg, patch)
    client = LLMClient(cfg)
    pipeline = Pipeline(cfg, kb, client)
    log.info("config updated: model=%s quality=%s base=%s key=%s", cfg.model, cfg.quality_model, cfg.base_url, "yes" if cfg.api_key else "no")
    return {"ok": True, "edition": EDITION, "config": cfg.public()}


@app.post("/api/config/test")
def test_config():
    started = time.time()
    box: dict = {}

    def worker():
        try:
            box["result"] = LLMClient(cfg).test()
        except Exception as exc:  # noqa: BLE001
            box["error"] = exc

    th = threading.Thread(target=worker, daemon=True)
    th.start()
    th.join(timeout=35)
    if th.is_alive():
        log.warning("config/test timeout")
        return JSONResponse(status_code=504, content={"ok": False, "error": "网络无响应（35 秒超时），请检查代理或网络"})
    try:
        if "error" in box:
            raise box["error"]
        result = box["result"]
        return {
            "ok": True,
            "model": cfg.model,
            "elapsed": round(time.time() - started, 2),
            "usage": result.usage.as_dict(),
            "reply": result.text[:40],
        }
    except Exception as exc:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(exc)})


# ---------------- knowledge base ----------------
_RULE_ID_RE = re.compile(r"^KB(\d+)-(\d+)$")


def _next_rule_id(kb_no: int, rules: list[dict[str, Any]]) -> str:
    """只统计 KBx-纯数字，跳过 KB2-M01 之类的总卡编号。"""
    nums: list[int] = []
    for rule in rules:
        match = _RULE_ID_RE.match(str(rule.get("id", "")))
        if match and int(match.group(1)) == int(kb_no):
            nums.append(int(match.group(2)))
    return f"KB{int(kb_no)}-{max(nums, default=0) + 1:03d}"


# 公开版只下发内置规则的「骨架」：编号、名称、所属 KB、父子关系、启用状态。
# 正文（principle / check / examples / triggers 等）绝不下发；企业版不受此限。
# 白名单实现在 kb_guard，所有对外出口共用同一份口径。
_PUBLIC_RULE_FIELDS = ("id", "kb", "card_type", "parent_id", "title", "enabled")
_public_rule = public_rule


@app.get("/api/kb/full")
def kb_full():
    # 规则库不再保密：本档位可用的规则卡全部原样下发，且可编辑。
    snap = kb_store.snapshot()
    merged = user_kb.merged(visible_builtin(), builtin_editable=True)
    all_rules = list(merged.get("rules", []))
    merged["rules_hidden"] = False
    merged["builtin_rule_count"] = sum(1 for r in all_rules if str(r.get("origin")) != "user")
    builtin_rules = snap.get("rules", [])
    merged["next_rule_ids"] = {
        str(kb_no): _next_rule_id(kb_no, builtin_rules) for kb_no in range(1, 100)
    }
    lic = entitlements.public()
    return {
        **merged,
        "builtin_revision": snap.get("revision", 1),
        "user_revision": user_kb.revision,
        "user_rule_count": user_kb.rule_count,
        "user_case_count": len(user_kb.snapshot().get("cases", [])),
        "edition": lic.get("edition"),
        "tier": entitlements.tier_name(),
        "license": lic,
    }


def _builtin_has_rule(rule_id: str) -> bool:
    return rule_id in BUILTIN_RULE_IDS


def _ensure_visible(rule_id: str) -> None:
    allowed = kb_tiers.allowed_ids(entitlements.tier_name())
    if allowed and rule_id not in allowed:
        raise HTTPException(403, "该规则卡不属于当前档位")


@app.post("/api/kb/rules")
def kb_save_rule(rule: dict[str, Any], original_id: str | None = None):
    rid = str(rule.get("id", "")).strip()
    target = str(original_id or rid).strip()
    if _builtin_has_rule(target) or str(rule.get("origin") or "") == "builtin":
        _ensure_visible(target)
        if original_id and original_id != rid:
            _ensure_visible(rid)
        try:
            saved = kb_store.upsert_rule(rule, original_id)
        except Exception as exc:
            raise HTTPException(400, str(exc)) from exc
        reload_kb()
        return {"ok": True, "rule": saved, "revision": kb.revision, "origin": "builtin"}
    card_type = str(rule.get("card_type") or "master")
    if card_type == "branch":
        pid = str(rule.get("parent_id") or "").strip()
        candidates = list(user_kb.snapshot().get("rules", [])) + list(kb.rules)
        parent = next((r for r in candidates if r.get("id") == pid), None)
        if parent is None:
            raise HTTPException(400, "父卡不存在，请先建立或选择一张总卡。")
        if str(parent.get("card_type") or "master") != "master":
            raise HTTPException(400, "父卡必须是一张总卡。")
        if int(parent.get("kb", 0)) != int(rule.get("kb", 0)):
            rule["kb"] = int(parent.get("kb", rule.get("kb", 0)))
    else:
        rule["card_type"] = "master"
        rule["parent_id"] = None
    try:
        saved = user_kb.upsert_rule(rule, None, True, original_id)
    except UserKBError as exc:
        raise HTTPException(400, str(exc)) from exc
    reload_kb()
    return {"ok": True, "rule": saved, "revision": kb.revision}


@app.delete("/api/kb/rules/{rule_id}")
def kb_delete_rule(rule_id: str):
    if _builtin_has_rule(rule_id):
        _ensure_visible(rule_id)
        try:
            kb_store.delete_rule(rule_id)
        except Exception as exc:
            raise HTTPException(400, str(exc)) from exc
        reload_kb()
        return {"ok": True, "revision": kb.revision, "origin": "builtin"}
    try:
        user_kb.delete_rule(rule_id)
    except UserKBError as exc:
        raise HTTPException(400, str(exc)) from exc
    reload_kb()
    return {"ok": True, "revision": kb.revision}


@app.post("/api/kb/glossary")
def kb_save_glossary(entry: dict[str, Any]):
    en = str(entry.get("en", "")).strip()
    builtin = str(entry.get("origin") or "") == "builtin" or en.lower() in BUILTIN_TERM_KEYS
    try:
        if builtin:
            saved = kb_store.upsert_glossary(entry)
        else:
            saved = user_kb.upsert_glossary(entry, max_count=None)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    reload_kb()
    return {"ok": True, "entry": saved, "revision": kb.revision}


@app.delete("/api/kb/glossary")
def kb_delete_glossary(en: str):
    try:
        if en.lower() in BUILTIN_TERM_KEYS:
            kb_store.delete_glossary(en)
        else:
            user_kb.delete_glossary(en)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    reload_kb()
    return {"ok": True, "revision": kb.revision}


@app.post("/api/kb/cases")
def kb_save_case(case: dict[str, Any]):
    cid = str(case.get("id", "")).strip()
    builtin = str(case.get("origin") or "") == "builtin" or cid in BUILTIN_CASE_IDS
    try:
        if builtin:
            saved = kb_store.upsert_case(case)
        else:
            saved = user_kb.upsert_case(case, max_count=None)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    reload_kb()
    return {"ok": True, "case": saved, "revision": kb.revision}


@app.delete("/api/kb/cases/{case_id}")
def kb_delete_case(case_id: str):
    try:
        if case_id in BUILTIN_CASE_IDS:
            kb_store.delete_case(case_id)
        else:
            user_kb.delete_case(case_id)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    reload_kb()
    return {"ok": True, "revision": kb.revision}


@app.post("/api/kb/factory-reset")
def kb_factory_reset():
    """恢复出厂：删除内置库改动、清空自建库，再重载。"""
    try:
        kb_store.reset()
        user_kb.reset()
    except Exception as exc:
        raise HTTPException(500, f"恢复出厂失败：{exc}") from exc
    reload_kb()
    return {"ok": True, "revision": kb.revision}


@app.post("/api/kb/import")
def kb_import(body: dict[str, Any]):
    incoming = body.get("data") or {}
    if not isinstance(incoming, dict):
        raise HTTPException(400, "导入文件不是译匠知识库模板")
    if incoming.get("template") not in (None, "yijiang-kb-v1"):
        raise HTTPException(400, "不是译匠可识别的知识库模板（template 应为 yijiang-kb-v1）")
    rules = incoming.get("rules") or []
    cases = incoming.get("cases") or []
    glossary = incoming.get("glossary") or []
    if not isinstance(rules, list) or not isinstance(cases, list) or not isinstance(glossary, list):
        raise HTTPException(400, "rules / cases / glossary 必须是数组")

    existing = {str(r.get("id")): r for r in user_kb.snapshot().get("rules", [])}
    existing_ids = set(existing)
    imported_map = {str(r.get("imported_from")): str(r.get("id")) for r in existing.values() if r.get("imported_from")}
    used = set(BUILTIN_RULE_IDS) | set(existing)
    next_rule_no: dict[int, int] = {}

    def unique_rule_id(kb_no: int) -> str:
        no = next_rule_no.get(kb_no, 900)
        while f"KB{kb_no}-{no:03d}" in used:
            no += 1
        next_rule_no[kb_no] = no + 1
        return f"KB{kb_no}-{no:03d}"

    # 第一遍：先给每条规则确定目标 ID，再处理父卡引用。
    # 之前只改自身 ID、不改 parent_id，导致导入 260 条后 231 张分支卡
    # 全部指向不存在的旧 ID（孤儿卡）。
    id_map: dict[str, str] = {}
    source_by_id: dict[str, dict[str, Any]] = {}
    skipped_builtin = 0
    for raw in rules:
        if not isinstance(raw, dict):
            raise HTTPException(400, "rules 里有非对象条目")
        orig = str(raw.get("id") or "").strip()
        if not orig:
            raise HTTPException(400, "有一条规则缺少 id")
        if orig in source_by_id:
            raise HTTPException(400, f"导入文件里有重复的规则 ID：{orig}")
        source_by_id[orig] = raw
        # 免费版只有 KB1-M00 一张身份卡；导入整库时沿用内置身份卡，
        # 不复制成 KB1-9xx，否则 169/260 会多出一条重复身份卡。
        if orig == "KB1-M00" and orig in BUILTIN_RULE_IDS:
            id_map[orig] = orig
            skipped_builtin += 1
            continue
        target = imported_map.get(orig)
        if not target:
            if orig in BUILTIN_RULE_IDS or orig in used:
                target = unique_rule_id(int(raw.get("kb", 1) or 1))
            else:
                target = orig
        id_map[orig] = target
        used.add(target)

    saved_rules = 0
    for raw in rules:
        orig = str(raw.get("id") or "").strip()
        target = id_map[orig]
        if target == orig and orig in BUILTIN_RULE_IDS:
            continue  # 内置身份卡已存在，不重复写入
        rule = dict(raw)
        rule["id"] = target
        rule["imported_from"] = orig
        parent = str(rule.get("parent_id") or "").strip()
        if parent:
            if parent in id_map:
                rule["parent_id"] = id_map[parent]
            elif parent in BUILTIN_RULE_IDS or parent in existing_ids:
                rule["parent_id"] = parent
            else:
                raise HTTPException(
                    400,
                    f"{orig} 的父卡 {parent} 不在导入文件中；请同时导入父卡，或先导入父卡。",
                )
        elif str(rule.get("card_type") or "master") == "branch":
            raise HTTPException(400, f"{orig} 是分支卡但缺少父卡")
        else:
            rule["parent_id"] = None
        try:
            user_kb.upsert_rule(rule, None, True)
        except UserKBError as exc:
            raise HTTPException(400, f"{orig} 导入失败：{exc}") from exc
        imported_map[orig] = target
        saved_rules += 1

    case_used = {str(c.get("id")) for c in user_kb.snapshot().get("cases", [])} | set(BUILTIN_CASE_IDS)
    case_map = {str(c.get("imported_from")): str(c.get("id")) for c in user_kb.snapshot().get("cases", []) if c.get("imported_from")}
    saved_cases = 0
    for raw in cases:
        if not isinstance(raw, dict):
            raise HTTPException(400, "cases 里有非对象条目")
        case = dict(raw)
        orig = str(case.get("id") or "").strip()
        if not orig:
            raise HTTPException(400, "有一个案例缺少 id")
        target = case_map.get(orig)
        if not target:
            if orig in case_used:
                no = 900
                while f"CASE-IM-{no:03d}" in case_used:
                    no += 1
                target = f"CASE-IM-{no:03d}"
            else:
                target = orig
        case["id"] = target
        case["imported_from"] = orig
        try:
            user_kb.upsert_case(case, max_count=None)
        except UserKBError as exc:
            raise HTTPException(400, f"{orig} 导入失败：{exc}") from exc
        case_used.add(target)
        case_map[orig] = target
        saved_cases += 1

    saved_terms = 0
    for raw in glossary:
        if not isinstance(raw, dict):
            raise HTTPException(400, "glossary 里有非对象条目")
        try:
            user_kb.upsert_glossary(raw, max_count=None)
        except UserKBError as exc:
            raise HTTPException(400, f"{raw.get('en', '')} 导入失败：{exc}") from exc
        saved_terms += 1

    reload_kb()
    return {
        "ok": True,
        "rules": saved_rules + skipped_builtin,
        "saved_rules": saved_rules,
        "skipped_builtin": skipped_builtin,
        "cases": saved_cases,
        "glossary": saved_terms,
        "revision": kb.revision,
    }


BUILTIN_DOMAINS = [
    ("political", "政经"),
    ("legal", "法律"),
    ("advertising", "广告"),
    ("business", "商务"),
    ("tech_medical", "科技 / 医学"),
    ("news_media", "新闻 / 传媒"),
    ("audiovisual", "影视 / 字幕"),
    ("tourism", "旅游"),
    ("academic", "学术"),
    ("children", "儿童文学"),
    ("poetry", "诗歌"),
]


@app.get("/api/domains")
def domains_list():
    payload = json.loads((DATA / "domains.json").read_text(encoding="utf-8")) if (DATA / "domains.json").exists() else {}
    custom = [str(x) for x in (payload.get("custom") or []) if str(x).strip()]
    options = [{"value": "default", "label": "默认（不启用以下领域任何知识库）"}]
    options += [{"value": value, "label": label} for value, label in BUILTIN_DOMAINS]
    options += [{"value": name, "label": name} for name in custom]
    return {"domains": options, "custom": custom}


class DomainBody(BaseModel):
    name: str


@app.post("/api/domains")
def domains_add(body: DomainBody):
    name = body.name.strip()
    if not name or len(name) > 24:
        raise HTTPException(400, "领域名称需为 1–24 个字符")
    path = DATA / "domains.json"
    payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    custom = [str(x) for x in (payload.get("custom") or [])]
    if name not in custom:
        custom.append(name)
    path.write_text(json.dumps({"custom": custom}, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"ok": True, "custom": custom}


@app.get("/api/kb/defs")
def kb_defs():
    return {"kb_defs": user_kb.kb_defs()}


class KbDefBody(BaseModel):
    name: str
    kb: int | None = None


@app.post("/api/kb/defs")
def kb_def_add(body: KbDefBody):
    try:
        saved = user_kb.add_kb(body.name, body.kb)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "kb_defs": saved, "all": user_kb.kb_defs()}


@app.get("/api/kb/export")
def kb_export(kind: str = ""):
    merged = user_kb.merged(visible_builtin(), builtin_editable=True)
    payload = {
        "template": "yijiang-kb-v1",
        "version": 1,
        "app": "yijiang",
        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "tier": entitlements.tier_name(),
        "rules": merged.get("rules") or [],
        "cases": merged.get("cases") or [],
        "glossary": merged.get("glossary") or [],
    }
    if kind in {"rules", "cases", "terms"}:
        key = {"rules": "rules", "cases": "cases", "terms": "glossary"}[kind]
        part = {
            "template": "yijiang-kb-v1",
            "version": 1,
            "app": "yijiang",
            "kind": kind,
            "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            key: payload.get(key) or [],
        }
        return Response(
            content=json.dumps(part, ensure_ascii=False, indent=1),
            media_type="application/json; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="yijiang-{kind}.json"'},
        )
    body = json.dumps(payload, ensure_ascii=False, indent=1)
    return Response(
        content=body,
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="yijiang-kb-template.json"'},
    )


@app.get("/api/kb/backups")
def kb_backups():
    return {"items": user_kb.list_backups(), "scope": "user-only", "exportable": entitlements.is_pro}


@app.post("/api/kb/backup")
def kb_backup():
    name = user_kb.backup()
    return {"ok": True, "name": name}


@app.post("/api/kb/restore/{name}")
def kb_restore(name: str):
    try:
        limits = entitlements.limits()
        user_kb.restore_backup(name, limits.get("rules"), limits.get("cases"), limits.get("terms"))
    except UserKBError as exc:
        raise HTTPException(400, str(exc)) from exc
    reload_kb()
    return {"ok": True, "revision": kb.revision}


@app.post("/api/kb/reload")
def kb_reload():
    user_kb.reload()
    reload_kb()
    return {"ok": True, "revision": kb.revision}


@app.get("/api/kb/stats")
def kb_stats():
    groups: dict[str, list[dict[str, Any]]] = {}
    if entitlements.can_view_rules:
        for rule in kb.rules:
            groups.setdefault(f'知识库{rule["kb"]}', []).append(
                {"id": rule["id"], "title": rule["title"], "gist": rule.get("gist") or rule.get("principle", "")[:80]}
            )
    return {
        "meta": kb.meta,
        "groups": groups,
        "rules_hidden": not entitlements.can_view_rules,
        "glossary_count": len(kb.glossary),
        "revision": kb.revision,
    }


@app.get("/api/kb/search")
def kb_search(q: str = ""):
    if not q.strip():
        return {"rules": []}
    hits = kb.search(q, 15)
    if not entitlements.can_view_rules:
        hits = [h if str(h.get("origin")) == "user" else _public_rule(h) for h in hits]
    return {"rules": hits, "rules_hidden": not entitlements.can_view_rules}


# ---------------- history ----------------
def _hydrate_rule_refs(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把历史里的 {id,title} 补成当前内存知识库中的完整规则。

    仅企业版/开发者版会走到这里；公开版在 history_item 里直接清空 rules。
    """
    return hydrate_rule_refs(refs, kb.rules)


@app.get("/api/history")
def history():
    return {"items": store.list_history(30)}


@app.get("/api/history/{item_id}")
def history_item(item_id: int):
    item = store.get_history(item_id)
    if not item:
        raise HTTPException(404, "记录不存在")
    if isinstance(item.get("meta"), dict):
        if entitlements.can_view_rules:
            # 历史只存了 {id,title}，正文按当前内存知识库补全。
            item["meta"] = {
                **item["meta"],
                "rules": _hydrate_rule_refs(item["meta"].get("rules") or []),
            }
        elif not entitlements.is_pro:
            item["meta"] = _gate_result(item["meta"])
        else:
            item["meta"] = {**item["meta"], "rules": [], "rules_hidden": True}
    return item


@app.delete("/api/history/{item_id}")
def history_delete(item_id: int):
    store.delete_history(item_id)
    return {"ok": True}


@app.delete("/api/history")
def history_clear():
    store.clear_history()
    return {"ok": True}


def _gate_context(event: dict[str, Any]) -> dict[str, Any]:
    """Free users may translate, but rule cards/brief/case are paid previews."""
    return {
        **event,
        "rules": [],
        "brief": "",
        "glossary": [],
        "case_example": None,
        "locked": {"review": True, "result_rules": True},
    }


def _gate_result(result: dict[str, Any]) -> dict[str, Any]:
    total = (result.get("usage") or {}).get("total") or {}
    return {
        **result,
        "rules": [],
        "brief": "",
        "glossary": [],
        "case_example": None,
        "issues": [],
        "qa": {"locked": True, "passed": None, "high": 0, "medium": 0, "low": 0},
        "usage": {"total": total},
        "baseline": 0,
        "saved_pct": 0,
        "locked": {"review": True, "result_rules": True, "usage_detail": True},
        "plan": "free",
    }


def _strip_rule_payload(event: dict[str, Any]) -> dict[str, Any]:
    """公开版一律不把规则正文/例句下发到前端。"""
    if entitlements.can_view_rules:
        return event
    if event.get("type") == "context":
        # 公开版：内置规则只给名字，正文不下发
        return {
            **event,
            "rules": [_public_rule(r) for r in (event.get("rules") or [])],
            "rules_hidden": True,
        }
    if event.get("type") == "done" and isinstance(event.get("result"), dict):
        result = dict(event["result"])
        result["rules"] = [_public_rule(r) for r in (result.get("rules") or [])]
        result["rules_hidden"] = True
        return {**event, "result": result}
    return event


def _run_job(job: Job, req: TranslationRequest) -> None:
    def emit(event: dict[str, Any]) -> None:
        event = _strip_rule_payload(event)
        if not job.pro:
            if event.get("type") == "context":
                event = _gate_context(event)
            elif event.get("type") == "usage":
                # Free users only get a total; do not stream stage breakdowns.
                usage = event.get("usage") or event.get("total") or {}
                event = {"type": "usage", "stage": "total", "usage": {"total": usage}}
            elif event.get("type") == "done" and isinstance(event.get("result"), dict):
                event = {**event, "result": _gate_result(event["result"])}
        job.queue.put(event)

    try:
        job_cfg = copy.deepcopy(cfg)
        Pipeline(job_cfg, kb, LLMClient(job_cfg)).run(req, emit)
    except PipelineError as exc:
        job.queue.put({"type": "error", "message": str(exc)})
    except Exception as exc:  # noqa: BLE001
        log.exception("job failed")
        job.queue.put({"type": "error", "message": f"运行失败：{exc}"})
    finally:
        job.queue.put(None)


@app.post("/api/translate")
def translate(body: TranslateBody):
    if not cfg.api_key:
        raise HTTPException(400, "还没有配置 API Key，请先在右上角「设置」中填写。")
    pair = pair_status(body.source_lang, body.target_lang)
    if pair.get("status") != "ready":
        raise HTTPException(400, f"{pair.get('label', '该语言对')} 的语言包尚未接入，目前支持英语 ↔ 汉语。")
    if body.quality and not entitlements.is_pro:
        raise HTTPException(403, "额外精修是付费版功能；免费版请取消勾选后使用标准模式。")
    req = TranslationRequest(
        text=body.text,
        mode=body.mode,
        reader=body.reader,
        text_type=body.text_type,
        glossary=body.glossary,
        deep_brief=body.deep_brief,
        use_cache=body.use_cache,
        source_lang=body.source_lang,
        target_lang=body.target_lang,
        quality=body.quality,
        strategy=body.strategy,
        poetry=body.poetry,
        genre=body.genre,
        review_enabled=entitlements.is_pro,
    )
    try:
        req.text = normalize_text(req.text)
        from pipeline import validate

        validate(req)
    except PipelineError as exc:
        raise HTTPException(400, str(exc)) from exc

    if body.mode == "strict":
        ok, message = entitlements.can_use_strict()
        if not ok:
            raise HTTPException(403, message)
        entitlements.consume_strict()

    entitlements.mark_translated()
    job = Job(id=uuid.uuid4().hex[:12], pro=entitlements.is_pro)
    with JOBS_LOCK:
        for key, old in list(JOBS.items()):
            if time.time() - old.created > 3600:
                JOBS.pop(key, None)
        JOBS[job.id] = job
    thread = threading.Thread(target=_run_job, args=(job, req), daemon=True)
    job.thread = thread
    thread.start()
    return {"job_id": job.id}


@app.get("/api/jobs/{job_id}/events")
async def job_events(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "任务不存在或已过期")

    async def stream():
        while True:
            try:
                event = await asyncio.to_thread(job.queue.get, True, 20)
            except queue.Empty:
                yield ": ping\n\n"
                continue
            if event is None:
                break
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/cleanup")
def cleanup():
    """应用内“清理内存”：清历史、结果缓存与日志；不动知识库、设置和 Key。"""
    removed = {"history": 0, "cache": 0, "logs": 0, "bytes": 0}
    try:
        removed["history"] = int(store.stats().get("count") or 0)
        store.clear_history()
    except Exception:
        log.exception("cleanup: history")
    try:
        removed["cache"] = int(store.clear_results() or 0)
    except Exception:
        log.exception("cleanup: results")
    try:
        for path in sorted((DATA / "logs").glob("*.log")):
            try:
                removed["bytes"] += path.stat().st_size
                path.write_text("", encoding="utf-8")
                removed["logs"] += 1
            except OSError:
                continue
    except Exception:
        log.exception("cleanup: logs")
    return {"ok": True, **removed}


@app.post("/api/open-folder")
def open_folder():
    """打开译匠本地数据文件夹（主页面「打开文件位置」）。"""
    try:
        os.startfile(str(DATA))
    except Exception as exc:
        raise HTTPException(500, f"无法打开文件夹：{exc}") from exc
    return {"ok": True, "path": str(DATA)}


@app.exception_handler(PipelineError)
async def pipeline_error_handler(_request: Request, exc: PipelineError):
    return JSONResponse(status_code=400, content={"error": str(exc)})


def _free_port(host: str, preferred: int) -> int:
    for port in range(preferred, preferred + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, port))
                return port
            except OSError:
                continue
    raise RuntimeError("找不到空闲端口")


def main() -> None:
    global cfg, client, pipeline, API_TOKEN
    import argparse

    if not API_TOKEN:
        API_TOKEN = secrets.token_urlsafe(24)
        os.environ["YIJIANG_API_TOKEN"] = API_TOKEN
    parser = argparse.ArgumentParser(description="译匠 · 本地翻译工作台")
    parser.add_argument("--no-browser", action="store_true", help="启动后不自动打开浏览器")
    parser.add_argument("--port", type=int, default=0, help="指定端口")
    args = parser.parse_args()

    cfg = load_config()
    client = LLMClient(cfg)
    pipeline = Pipeline(cfg, kb, client)
    if args.port:
        cfg.port = args.port
    port = _free_port(cfg.host, cfg.port)
    url = f"http://{cfg.host}:{port}"
    log.info("starting on %s", url)
    print(f"\n  译匠 · 本地翻译工作台\n  打开： {url}/?token={API_TOKEN}\n  模型： {cfg.model}    规则： {kb.meta.get('rule_count')} 条\n")
    if cfg.auto_open and not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url + "/?token=" + API_TOKEN)).start()
    uvicorn.run(app, host=cfg.host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
