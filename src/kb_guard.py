"""规则资产的出口白名单。

内置规则正文是付费资产。所有对外的规则数据都必须经过这里，避免某个
接口漏过滤把 principle/examples 带出去。
"""
from __future__ import annotations

from typing import Any

PUBLIC_RULE_FIELDS = ("id", "kb", "card_type", "parent_id", "title", "enabled")


def public_rule(rule: dict[str, Any]) -> dict[str, Any]:
    """内置规则只下发骨架：编号、归属、父子关系、名称、启用状态。"""
    out = {key: rule.get(key) for key in PUBLIC_RULE_FIELDS}
    out["origin"] = "builtin"
    out["locked"] = True
    out["editable"] = False
    return out


def public_masters(masters: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """总卡与普通规则同一口径；自建规则原样保留。"""
    return [
        item if str(item.get("origin")) == "user" else public_rule(item)
        for item in (masters or [])
    ]


def rule_refs(rules: list[dict[str, Any]]) -> list[dict[str, str]]:
    """历史/缓存只保留规则引用，不落正文与例句。"""
    return [{"id": str(rule.get("id", "")), "title": str(rule.get("title", ""))} for rule in rules]


def sanitize_history_meta(meta: Any, can_view_rules: bool) -> Any:
    """历史/导出里的规则引用：无权限时直接清空，绝不带出正文。"""
    if not isinstance(meta, dict) or can_view_rules:
        return meta
    return {**meta, "rules": [], "rules_hidden": True}


def hydrate_rule_refs(refs: list[dict[str, Any]] | None, rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把历史里的 {id,title} 用当前内存知识库补全；仅对有权档位调用。"""
    index = {str(rule.get("id")): rule for rule in rules}
    out: list[dict[str, Any]] = []
    for ref in refs or []:
        rule_id = str(ref.get("id") or "")
        source = index.get(rule_id)
        if source is None:
            out.append({"id": rule_id, "title": str(ref.get("title") or ""), "gist": "", "examples": []})
            continue
        out.append(
            {
                "id": rule_id,
                "title": source.get("title") or ref.get("title") or "",
                "gist": source.get("gist") or source.get("principle") or "",
                "examples": source.get("examples") or [],
            }
        )
    return out
