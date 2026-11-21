"""知识库可见性兼容层。

当前发布口径：免费开源版只随包带 1 条身份卡 KB1-M00；
169 / 260 知识库通过导入 JSON 决定规则、案例与术语范围。
旧的分档常量仅用于兼容历史构建与数据，不再作为功能门槛。
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
TIERS_PATH = ROOT / "resources" / "kb_tiers.json"

TIER_CATEGORIES: dict[str, set[str]] = {
    "free": {"A", "B", "C1"},
    "lite": {"A", "B", "C1"},
    "buyout": {"A", "B", "C1", "C2", "D"},
    "enterprise": {"A", "B", "C1", "C2", "D"},
    "dev": {"A", "B", "C1", "C2", "D"},
}


@lru_cache(maxsize=1)
def categories() -> dict[str, str]:
    try:
        payload = json.loads(TIERS_PATH.read_text(encoding="utf-8"))
        raw = payload.get("categories") or {}
        return {str(key): str(value) for key, value in raw.items()}
    except Exception:
        return {}


def allowed_ids(tier: str) -> set[str]:
    wanted = TIER_CATEGORIES.get(str(tier), TIER_CATEGORIES["free"])
    mapping = categories()
    if not mapping:
        return set()
    return {rule_id for rule_id, category in mapping.items() if category in wanted}


def visible_payload(payload: dict[str, Any], tier: str) -> dict[str, Any]:
    """返回该档位可见的规则/总卡副本；案例与术语不分档。

    免费版是“空白知识库”：只保留最高优先级身份卡 KB1-M00，
    规则/案例/术语全部由用户自己导入或新建。
    """
    if str(tier) == "free":
        rules = [r for r in payload.get("rules") or [] if str(r.get("id")) == "KB1-M00"]
        return {**payload, "rules": rules, "masters": rules, "cards": rules, "cases": [], "glossary": []}
    allowed = allowed_ids(tier)
    if not allowed:
        return payload
    rules = [rule for rule in payload.get("rules") or [] if str(rule.get("id")) in allowed]
    masters = [rule for rule in payload.get("masters") or [] if str(rule.get("id")) in allowed]
    cards = [rule for rule in payload.get("cards") or [] if str(rule.get("id")) in allowed]
    return {**payload, "rules": rules, "masters": masters, "cards": cards}


def category_of(rule_id: str) -> str:
    return categories().get(str(rule_id), "")
