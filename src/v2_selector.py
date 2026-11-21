"""V2-Lite shadow rule selector.

This module is a standalone candidate. It is not wired into the app.
"""
from __future__ import annotations

import re
from typing import Any

SCOPE_RANK = {"word": 6, "phrase": 5, "sentence": 4, "paragraph": 3, "section": 2, "document": 1}
# 句级改写类规则最解决问题，其次是段落；词/短语卡多是术语类，不该抢句法卡的位置
SCOPE_WEIGHT = {"sentence": 1.0, "paragraph": 0.75, "section": 0.5, "phrase": 0.15, "word": 0.05, "document": 0.1}
VALID_STAGES = {"preflight", "translate", "qa", "repair", "delivery"}

# 这些触发词几乎每篇都有，命中它们只能说明“文体大致对路”，
# 不能说明这条规则现在真的有用（例如每篇叙事都有专名，不代表要讲地名旧译）。
GENERIC_TRIGGERS = {
    "proper_noun", "cultural", "terminology", "clarity", "quality", "review",
    "process", "workflow", "literary", "narrative", "expressive", "informative",
    "mixed", "operative", "sentence", "paragraph", "style", "tone",
}

# 同义/旧触发词归一到 canonical key，避免同一现象被拆成多个 key 分叉。
TRIGGER_ALIASES = {
    "chinglish": "translationese",
    "word_salad": "translationese",
}


def _canonical_trigger(key: Any) -> str:
    value = str(key)
    return TRIGGER_ALIASES.get(value, value)


def _trigger_entries(triggers: list[Any] | None) -> list[tuple[str, float]]:
    """兼容 v2 的对象触发器和旧版字符串触发器。"""
    out: list[tuple[str, float]] = []
    for t in triggers or []:
        if isinstance(t, dict):
            key = t.get("key")
            if not key:
                continue
            try:
                weight = float(t.get("weight", 1))
            except (TypeError, ValueError):
                weight = 1.0
            out.append((_canonical_trigger(key), weight))
        elif t is not None:
            out.append((_canonical_trigger(t), 1.0))
    return out


def _hard_conditions_pass(card: dict[str, Any], text: str) -> bool:
    """硬条件：全部条件通过才允许进入候选。

    结构：[{"type": "regex", "pattern": "..."}, ...]
    仅支持 regex；文本为空时，有条件卡直接不选，避免误触发。
    """
    conditions = card.get("hard_conditions") or []
    if not conditions:
        return True
    if not text:
        return False
    for condition in conditions:
        if not isinstance(condition, dict) or condition.get("type") != "regex":
            return False
        pattern = str(condition.get("pattern") or "")
        if not pattern:
            return False
        try:
            if re.search(pattern, text, re.IGNORECASE | re.MULTILINE) is None:
                return False
        except re.error:
            return False
    return True


def _trigger_match(card: dict[str, Any], active: set[str]) -> tuple[bool, float]:
    """Return (passes_hard_gate, soft_score)."""
    mode = card.get("trigger_mode", "soft")
    match = card.get("trigger_match", "any")
    entries = _trigger_entries(card.get("triggers"))
    keys = {key for key, _weight in entries}
    hit = keys & active
    if mode == "hard":
        passed = bool(keys) and (len(hit) == len(keys) if match == "all" else bool(hit))
        return passed, 0.0
    if not keys:
        return True, 0.0
    if match == "all" and len(hit) != len(keys):
        return True, 0.0
    weight = sum(weight for key, weight in entries if key in hit)
    return True, weight


def _is_specific(card: dict[str, Any]) -> bool:
    return card.get("domain") not in (None, "", "general") or bool(card.get("triggers"))


def select(
    cards: list[dict[str, Any]],
    *,
    direction: str,
    domain: str = "general",
    stage: str | None = None,
    active_triggers: set[str] | None = None,
    limit: int = 20,
    relevance: dict[str, float] | None = None,
    text_type: str | None = None,
    text: str = "",
    kb_topk: dict[str, int] | None = None,
    parent_topk: int = 4,
) -> dict[str, Any]:
    """Select cards deterministically and return selected + conflict markers.

    与上一版的区别：不再“谁 precedence 高就选谁”。命中具体触发词（长句、被动、
    代词、翻译腔…）的卡优先；只命中 generic 触发词的卡降权；再叠加与原文的
    BM25 相关度。这样“地名旧译”“作品名”这类卡不会因为 precedence 高而挤掉
    真正要用的句法卡。
    """
    active = active_triggers or set()
    if direction not in {"en-zh", "zh-en", "both"}:
        raise ValueError("bad direction")
    if stage and stage not in VALID_STAGES:
        raise ValueError("bad stage")

    rel = relevance or {}
    rel_peak = max(rel.values()) if rel else 0.0
    masters: list[dict[str, Any]] = []
    candidates: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    for card in cards:
        if not card.get("enabled", True):
            continue
        if card.get("availability", "A") != "A":
            continue
        if not _hard_conditions_pass(card, text):
            continue
        card_direction = card.get("direction", "both")
        if card_direction not in {"both", direction}:
            continue
        card_domain = card.get("domain", "general")
        is_global = str(card.get("id", "")).startswith("G-") or card.get("kb") == 0
        if domain in {"general", "default"}:
            if card_domain != "general" and not is_global:
                continue
        elif card_domain not in {"general", domain} and not is_global:
            continue
        if stage and stage not in set(card.get("stage", [])):
            continue
        if card.get("activation", "triggered") == "triggered":
            passed, soft = _trigger_match(card, active)
            if not passed:
                continue
        else:
            soft = 0.0
        is_branch = card.get("card_type") == "branch"
        keys = {key for key, _weight in _trigger_entries(card.get("triggers"))}
        hit = keys & active
        specific_hits = hit - GENERIC_TRIGGERS
        generic_hits = hit & GENERIC_TRIGGERS
        # 具体触发命中 > 泛化命中；完全没命中但和原文关键词相关也能进来
        specificity = SCOPE_RANK.get(card.get("scope", "sentence"), 0)
        bm25 = rel.get(str(card.get("id", "")), 0.0) / rel_peak if rel_peak > 0 else 0.0
        type_bonus = 1.0 if text_type and text_type in (card.get("text_types") or []) else 0.0
        scope = str(card.get("scope", "sentence"))
        score = (
            3.0 * len(specific_hits)
            + 1.0 * len(generic_hits)
            + float(soft)
            + 2.2 * bm25
            + 0.6 * type_bonus
            + 0.9 * SCOPE_WEIGHT.get(scope, 0.3)
            + 1.2 * int(card.get("priority", 2))
            + 0.1 * int(card.get("precedence", 0)) / 100.0
        )
        # 只命中泛化触发词（每篇都有的 proper_noun/expressive…）又不相关的卡，
        # 排到真正命中的卡后面，避免“标题卡”“地名旧译卡”占掉句法卡的位置
        untargeted = 1 if (not specific_hits and bm25 < 0.35) else 0
        key = (
            untargeted,
            -round(score, 6),
            0 if is_branch else 1,
            str(card.get("id", "")),
        )
        if is_branch:
            candidates.append((key, card))
        else:
            masters.append(card)

    candidates.sort(key=lambda x: x[0])
    selected: list[dict[str, Any]] = []
    per_parent: dict[str, int] = {}
    per_kb: dict[str, int] = {}
    used: set[str] = set()

    def take(card: dict[str, Any]) -> bool:
        cid = str(card.get("id", ""))
        parent = str(card.get("parent_id") or "")
        kb = str(card.get("kb", ""))
        if cid in used:
            return False
        cap_kb = int((kb_topk or {}).get(kb, 5))
        if per_parent.get(parent, 0) >= max(1, parent_topk) or per_kb.get(kb, 0) >= max(1, cap_kb):
            return False
        selected.append(card)
        used.add(cid)
        per_parent[parent] = per_parent.get(parent, 0) + 1
        per_kb[kb] = per_kb.get(kb, 0) + 1
        return True

    for _, card in candidates:
        if len(selected) >= max(0, limit):
            break
        take(card)

    # 兜底：命中的卡太少时，补几张相关度最高的，保证提示词里至少有 4 条
    if len(selected) < min(4, limit):
        for _, card in candidates:
            if len(selected) >= min(4, limit):
                break
            take(card)

    # C: explicit conflict groups only. Cards without a group are never
    # guessed to conflict; ambiguous equal-priority pairs are reported.
    groups: dict[str, list[dict[str, Any]]] = {}
    for c in selected:
        group = str(c.get("conflict_group") or "").strip()
        if group:
            groups.setdefault(group, []).append(c)
    conflicts = []
    for group, group_cards in groups.items():
        if len(group_cards) < 2:
            continue
        keep = max(
            group_cards,
            key=lambda c: (int(c.get("priority", 2)), int(c.get("precedence", 0)), str(c.get("id", ""))),
        )
        dropped = [c["id"] for c in group_cards if c["id"] != keep["id"]]
        selected[:] = [
            c for c in selected
            if str(c.get("conflict_group") or "").strip() != group or c["id"] == keep["id"]
        ]
        conflicts.append({
            "group": group,
            "cards": [c["id"] for c in group_cards],
            "kept": keep["id"],
            "dropped": dropped,
            "action": "dedupe",
            "message": "同一冲突组同时命中；保留优先级更高的卡，其余移出本次选择。",
        })
    return {"masters": masters, "selected": selected, "conflicts": conflicts}
