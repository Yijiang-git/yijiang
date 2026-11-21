"""Read-only built-in knowledge base.

At development time `data/kb.json` is the source. A release build runs
tools/encrypt_builtin.py and ships `data/builtin_kb.enc` instead; the app
decrypts it in memory through win_crypto and never writes plaintext.
"""
from __future__ import annotations

import copy
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from config import DATA

ROOT = Path(__file__).resolve().parent
RESOURCES = ROOT / "resources"
ENC_PATH = RESOURCES / "builtin_kb.enc"
V2_ENC_PATH = RESOURCES / "builtin_kb_v2.enc"
V2_PLAIN_PATH = ROOT / "data" / "kb_v2.json"
# 用户改动副本与开发基线分开：恢复出厂只删用户副本，绝不动 data/kb_v2.json。
DEV_KB_PATH = DATA / "kb_user.json"


class BuiltinKBError(Exception):
    pass


def _builtin_payload() -> dict[str, Any] | None:
    """读取随包发布的内置库。2026-11 起取消加密，直接读明文 JSON。"""
    for path in (RESOURCES / "kb_v2.json", V2_PLAIN_PATH):
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                raise BuiltinKBError(f"内置知识库读取失败：{exc}") from exc
    return None


def load_payload() -> dict[str, Any]:
    # 用户改过的内置库优先；没有改动时直接用随包基线。
    if DEV_KB_PATH.exists():
        try:
            data = json.loads(DEV_KB_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    data = _builtin_payload()
    if data is None:
        raise BuiltinKBError("缺少 V2 内置知识库")
    if not isinstance(data, dict):
        raise BuiltinKBError("内置知识库格式错误")
    return data


class BuiltinKB:
    def __init__(self):
        self.payload = load_payload()
        # 规则库不再只读：所有档位都可以编辑并保存到 data/kb_v2.json。
        self.plain_path = DEV_KB_PATH
        self.revision = int(self.payload.get("revision", 1))
        self.editable = True

    def snapshot(self) -> dict[str, Any]:
        return copy.deepcopy(self.payload)

    def _save(self) -> None:
        self.payload["revision"] = int(self.payload.get("revision", 1)) + 1
        self.payload["updated_at"] = datetime.now().isoformat(timespec="seconds")
        self.revision = int(self.payload["revision"])
        if "cards" in self.payload:
            self.payload["cards"] = self.payload.get("rules", [])
        if "masters" in self.payload:
            self.payload["masters"] = [
                r for r in self.payload.get("rules", []) if r.get("card_type") == "master"
            ]
        try:
            self.plain_path.parent.mkdir(parents=True, exist_ok=True)
            self.plain_path.write_text(
                json.dumps(self.payload, ensure_ascii=False, indent=1), encoding="utf-8"
            )
        except OSError as exc:
            raise BuiltinKBError(
                f"知识库写入失败：{self.plain_path} 不可写（{exc}）。"
                "请确认开发者版 data 目录有写入权限。"
            ) from exc

    def upsert_rule(self, rule: dict[str, Any], original_id: str | None = None) -> dict[str, Any]:
        rid = str(rule.get("id", "")).strip()
        if not rid or not str(rule.get("title", "")).strip():
            raise BuiltinKBError("规则 id 和 title 必填")
        if int(rule.get("priority", 2)) > 5:
            raise BuiltinKBError("优先级最高为 5")
        old_id = str(original_id or rid).strip()
        rules = self.payload.setdefault("rules", [])
        ids = {str(r.get("id")) for r in rules}
        has_children = any(str(r.get("parent_id") or "") == old_id for r in rules)
        if has_children and str(rule.get("card_type") or "master") != "master":
            raise BuiltinKBError("该卡下还有子卡，不能改成分支卡；请先迁移或删除子卡")
        if old_id != rid:
            if old_id not in ids:
                raise BuiltinKBError(f"原规则 {old_id} 不存在")
            if rid in ids:
                raise BuiltinKBError(f"规则 ID {rid} 已存在，请换一个 ID")
            has_children = any(str(r.get("parent_id") or "") == old_id for r in rules)
            if has_children and str(rule.get("card_type") or "master") != "master":
                raise BuiltinKBError("该卡下还有子卡，不能改成分支卡；请先迁移或删除子卡")
            index = next(i for i, r in enumerate(rules) if str(r.get("id")) == old_id)
            rules[index] = copy.deepcopy(rule)
            for item in rules:
                src = item.get("source") or {}
                if str(src.get("section", "")) == old_id:
                    src["section"] = rid
                if str(item.get("parent_id") or "") == old_id:
                    item["parent_id"] = rid
        else:
            for index, existing in enumerate(rules):
                if str(existing.get("id")) == rid:
                    rules[index] = copy.deepcopy(rule)
                    break
            else:
                rules.append(copy.deepcopy(rule))
        self._save()
        return copy.deepcopy(rule)

    def delete_rule(self, rule_id: str) -> None:
        children = [r["id"] for r in self.payload.get("rules", []) if str(r.get("parent_id") or "") == rule_id]
        if children:
            raise BuiltinKBError(
                f"该总卡下还有 {len(children)} 张分支卡（{', '.join(children[:3])}…），"
                "请先删除或迁移子卡后再删总卡。"
            )
        self.payload["rules"] = [r for r in self.payload.get("rules", []) if str(r.get("id")) != rule_id]
        self._save()

    def upsert_glossary(self, entry: dict[str, Any]) -> dict[str, Any]:
        key = str(entry.get("en", "")).strip()
        if not key:
            raise BuiltinKBError("术语 en 必填")
        terms = self.payload.setdefault("glossary", [])
        entry = dict(entry)
        entry.setdefault("enabled", True)
        for index, existing in enumerate(terms):
            if str(existing.get("en", "")).lower() == key.lower():
                terms[index] = copy.deepcopy(entry)
                break
        else:
            terms.append(copy.deepcopy(entry))
        # 内置与用户新增的术语统一按英文字母序排列，便于查找
        terms.sort(key=lambda g: str(g.get("en", "")).casefold())
        self.payload["glossary"] = terms
        self._save()
        return copy.deepcopy(entry)

    def delete_glossary(self, en: str) -> None:
        self.payload["glossary"] = [g for g in self.payload.get("glossary", []) if str(g.get("en", "")).lower() != en.lower()]
        self._save()

    def upsert_case(self, case: dict[str, Any]) -> dict[str, Any]:
        cid = str(case.get("id", "")).strip()
        if not cid:
            raise BuiltinKBError("案例 id 必填")
        cases = self.payload.setdefault("cases", [])
        for index, existing in enumerate(cases):
            if str(existing.get("id")) == cid:
                cases[index] = copy.deepcopy(case)
                break
        else:
            cases.append(copy.deepcopy(case))
        self._save()
        return copy.deepcopy(case)

    def delete_case(self, case_id: str) -> None:
        self.payload["cases"] = [c for c in self.payload.get("cases", []) if str(c.get("id")) != case_id]
        self._save()

    def reset(self) -> None:
        """恢复出厂：删除用户改动副本，重新载入随包基线。"""
        try:
            self.plain_path.unlink(missing_ok=True)
        except OSError as exc:
            raise BuiltinKBError(f"无法删除知识库副本：{exc}") from exc
        data = _builtin_payload()
        if data is None:
            raise BuiltinKBError("缺少随包内置知识库，无法恢复出厂")
        self.payload = data
        self.revision = int(self.payload.get("revision", 1))
