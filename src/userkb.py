"""User-owned knowledge layer.

The built-in `data/kb.json` is read-only. Everything the user adds lives in a
separate file/namespace and is merged only in memory for retrieval. This keeps
the built-in and user KB logically isolated and makes export/backup boundaries
explicit.
"""
from __future__ import annotations

import copy
import json
import os
import re
import shutil
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from entitlements import (
    FREE_CASE_EN_WORDS,
    FREE_CASE_LIMIT,
    FREE_CASE_ZH_CHARS,
    load_protected,
    save_protected,
)

ROOT = Path(__file__).resolve().parent
DATA = Path(os.getenv("YIJIANG_DATA", str(ROOT / "data"))).expanduser()
DATA.mkdir(parents=True, exist_ok=True)
USER_KB = DATA / "user_kb.enc"
BACKUP_DIR = DATA / "backups"
# 内置卡沿用历史编号（KB1-M00 / G-FACT-M01 / CAND-A-001），用户自建规则用 KBx-001；
# 两种都要能通过校验，否则“导出付费版规则再导入免费版”会被整批拒绝。
RULE_ID = re.compile(
    r"^(?:KB(?:[1-9]|[1-9][0-9])-(?:\d{3}|M\d{2})|(?:G|CAND)-[A-Za-z0-9_\-]{1,24})$"
)
CASE_ID = re.compile(r"^CASE(?:-[A-Z]{2})?-\d{2,3}$")
EN_WORD = re.compile(r"[A-Za-z][A-Za-z'\u2019\-]*")
ZH_CHAR = re.compile(r"[\u4e00-\u9fff]")


class UserKBError(Exception):
    pass


def empty_payload() -> dict[str, Any]:
    return {
        "version": 1,
        "revision": 0,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "rules": [],
        "cases": [],
        "glossary": [],
        "kb_defs": {},
    }


def _validate_rule(rule: dict[str, Any]) -> None:
    rid = str(rule.get("id", "")).strip()
    if not RULE_ID.match(rid):
        raise UserKBError(f"规则 ID 不合法：{rid}")
    if not str(rule.get("title", "")).strip():
        raise UserKBError(f"{rid} 缺标题")
    if not str(rule.get("principle", "")).strip():
        raise UserKBError(f"{rid} 缺原则说明")
    if int(rule.get("kb", 0)) not in range(0, 100):
        raise UserKBError(f"{rid} 的知识库编号应为 0-99")
    card_type = str(rule.get("card_type") or "master")
    if card_type not in ("master", "branch"):
        raise UserKBError(f"{rid} 的卡类型只能是 master 或 branch")
    if card_type == "branch" and not str(rule.get("parent_id") or "").strip():
        raise UserKBError(f"{rid} 是分支卡，必须选择父卡")
    if int(rule.get("priority", 2)) not in range(1, 6):
        raise UserKBError("优先级应为 1-5")
    examples = rule.get("examples", [])
    if examples and not isinstance(examples, list):
        raise UserKBError(f"{rid} 的 examples 必须是数组")
    for example in examples:
        if not isinstance(example, dict) or not example.get("en") or not example.get("zh"):
            raise UserKBError(f"{rid} 的例句必须包含 en 和 zh")


def _validate_case(case: dict[str, Any]) -> None:
    cid = str(case.get("id", "")).strip()
    if not CASE_ID.match(cid):
        raise UserKBError(f"案例 ID 不合法：{cid}")
    if not case.get("en") or not case.get("zh"):
        raise UserKBError(f"{cid} 缺原文或译文")


def case_size(case: dict[str, Any]) -> tuple[int, int]:
    """(英文字数, 中文字数)。中文按汉字计，不含标点。"""
    en = str(case.get("en") or "")
    zh = str(case.get("zh") or "")
    return len(EN_WORD.findall(en)), len(ZH_CHAR.findall(zh))


def _check_free_case(case: dict[str, Any]) -> None:
    en_words, zh_chars = case_size(case)
    if en_words > FREE_CASE_EN_WORDS:
        raise UserKBError(
            f"免费版单个案例英文最多 {FREE_CASE_EN_WORDS} 词，当前 {en_words} 词；"
            "激活付费版后可导入更长、更多案例。"
        )
    if zh_chars > FREE_CASE_ZH_CHARS:
        raise UserKBError(
            f"免费版单个案例中文最多 {FREE_CASE_ZH_CHARS} 字，当前 {zh_chars} 字；"
            "激活付费版后可导入更长、更多案例。"
        )


class UserKB:
    def __init__(self, path: Path = USER_KB, backup_dir: Path = BACKUP_DIR):
        self.path = path
        self.backup_dir = backup_dir
        self.lock = threading.RLock()
        self.persisted = True
        fresh = not self.path.exists()
        self.payload = self._load()
        self.revision = int(self.payload.get("revision", 0))
        if fresh:
            # Create the encrypted user store immediately so startup can be
            # verified even before the first edit.
            self._save()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return empty_payload()
        try:
            data = load_protected(self.path, empty_payload())
        except Exception as exc:
            raise UserKBError(f"用户知识库损坏：{exc}") from exc
        if not isinstance(data, dict):
            return empty_payload()
        data.setdefault("revision", 0)
        data.setdefault("rules", [])
        data.setdefault("cases", [])
        data.setdefault("glossary", [])
        # Repair legacy files with duplicate IDs: keeping duplicates made a
        # single delete remove every copy from the UI.
        seen: set[str] = set()
        unique = []
        for rule in data["rules"]:
            rid = str(rule.get("id", ""))
            if rid and rid not in seen:
                seen.add(rid)
                unique.append(rule)
        data["rules"] = unique
        return data

    def reload(self) -> dict[str, Any]:
        with self.lock:
            self.payload = self._load()
            self.revision = int(self.payload.get("revision", 0))
            return copy.deepcopy(self.payload)

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return copy.deepcopy(self.payload)

    def _backup(self) -> None:
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        name = f"user-{datetime.now().strftime('%Y%m%d-%H%M%S')}.enc"
        save_protected(self.backup_dir / name, self.payload)
        for old in sorted(self.backup_dir.glob("user-*.enc"))[:-30]:
            old.unlink(missing_ok=True)

    def reset(self) -> None:
        """清空自建知识库（恢复出厂的一部分）。"""
        with self.lock:
            self.payload = empty_payload()
            self._save()

    def _save(self) -> None:
        try:
            self._backup()
        except Exception:
            # Automatic backup is best effort; a read-only backups folder must
            # not prevent the primary encrypted store from being written.
            pass
        self.payload["revision"] = int(self.payload.get("revision", 0)) + 1
        self.payload["updated_at"] = datetime.now().isoformat(timespec="seconds")
        self.payload["meta"] = self._meta()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            save_protected(self.path, self.payload)
            self.revision = int(self.payload["revision"])
            self.persisted = True
        except Exception:
            # Keep the in-memory KB usable when AppData is read-only; the UI
            # can still translate, but edits are not durable for this run.
            self.persisted = False

    def _meta(self) -> dict[str, Any]:
        rules = self.payload.get("rules", [])
        return {
            "rule_count": len(rules),
            "case_count": len(self.payload.get("cases", [])),
            "glossary_count": len(self.payload.get("glossary", [])),
            "by_kb": {str(k): sum(1 for r in rules if int(r.get("kb", 0)) == k) for k in range(0, 9)},
        }

    @property
    def rule_count(self) -> int:
        return len(self.payload.get("rules", []))

    def kb_defs(self) -> dict[str, str]:
        with self.lock:
            raw = self.payload.get("kb_defs") or {}
            return {str(k): str(v) for k, v in raw.items() if str(k).isdigit() and str(v).strip()}

    def add_kb(self, name: str, kb: int | None = None) -> dict[str, str]:
        name = str(name or "").strip()
        if not name:
            raise UserKBError("知识库名称不能为空")
        if len(name) > 40:
            raise UserKBError("知识库名称太长（最多 40 字）")
        with self.lock:
            defs = dict(self.payload.get("kb_defs") or {})
            used = set(range(1, 16)) | {int(k) for k in defs if str(k).isdigit()}
            if kb is None:
                kb = max(used) + 1 if used else 16
            kb = int(kb)
            if kb < 16 or kb > 99:
                raise UserKBError("自定义知识库编号需在 16–99 之间")
            if kb in used:
                raise UserKBError(f"KB{kb} 已存在")
            defs[str(kb)] = name
            self.payload["kb_defs"] = defs
            self._save()
            return {str(kb): name}

    def upsert_rule(
        self,
        rule: dict[str, Any],
        free_limit: int = 3,
        is_pro: bool = False,
        original_id: str | None = None,
    ) -> dict[str, Any]:
        with self.lock:
            _validate_rule(rule)
            if int(rule.get("priority", 2)) >= 5 and not is_pro:
                raise UserKBError("优先级“最高”仅付费版可用")
            rid = rule["id"]
            if str(rule.get("card_type") or "master") == "branch":
                pid = str(rule.get("parent_id") or "").strip()
                user_parent = next((r for r in self.payload.get("rules", []) if r.get("id") == pid), None)
                if user_parent is not None:
                    if str(user_parent.get("card_type") or "master") != "master":
                        raise UserKBError("父卡必须是一张总卡")
                    if int(user_parent.get("kb", 0)) != int(rule.get("kb", 0)):
                        raise UserKBError("分支卡与父卡的知识库必须一致")
            else:
                rule["parent_id"] = None
            old_id = str(original_id or rid).strip()
            existing = {r["id"]: r for r in self.payload.get("rules", [])}
            has_children = any(str(r.get("parent_id") or "") == old_id for r in self.payload["rules"])
            if has_children and str(rule.get("card_type") or "master") != "master":
                raise UserKBError("该卡下还有子卡，不能改成分支卡；请先迁移或删除子卡")
            if old_id != rid:
                if old_id not in existing:
                    raise UserKBError(f"原规则 {old_id} 不存在")
                if rid in existing:
                    raise UserKBError(f"规则 ID {rid} 已存在；请先删除或合并另一条规则")
                if free_limit is not None and self.rule_count > free_limit:
                    raise UserKBError(f"当前版本最多保存 {free_limit} 条自定义规则")
                index = next(i for i, r in enumerate(self.payload["rules"]) if r["id"] == old_id)
                has_children = any(str(r.get("parent_id") or "") == old_id for r in self.payload["rules"])
                if has_children and str(rule.get("card_type") or "master") != "master":
                    raise UserKBError("该卡下还有子卡，不能改成分支卡；请先迁移或删除子卡")
                self.payload["rules"][index] = rule
                # Keep source references and child cards usable after a rename.
                for item in self.payload["rules"]:
                    src = item.get("source") or {}
                    if str(src.get("section", "")) == old_id:
                        src["section"] = rid
                    if str(item.get("parent_id") or "") == old_id:
                        item["parent_id"] = rid
            else:
                if rid not in existing and free_limit is not None and self.rule_count >= free_limit:
                    raise UserKBError(f"当前版本最多保存 {free_limit} 条自定义规则；删除一条后可再添加，或升级版本。")
                self.payload["rules"] = [r for r in self.payload["rules"] if r["id"] != rid] + [rule]
            self.payload["rules"].sort(key=lambda r: (int(r.get("kb", 0)), r["id"]))
            self._save()
            return rule

    def resolve_builtin_collisions(self, builtin_ids: set[str]) -> None:
        """Renumber legacy user rules that collide with renumbered built-ins."""
        with self.lock:
            rules = self.payload.get("rules", [])
            changed = False
            used = {str(r["id"]) for r in rules} | set(builtin_ids)
            for rule in rules:
                rid = str(rule.get("id", ""))
                if rid not in builtin_ids:
                    continue
                kb = int(rule.get("kb", 1))
                candidates = []
                for value in used:
                    m = re.match(r"^KB([1-6])-(\d{3})$", value)
                    if m and int(m.group(1)) == kb:
                        candidates.append(int(m.group(2)))
                new_id = f"KB{kb}-{max(candidates + [0]) + 1:03d}"
                old_id = rid
                rule["id"] = new_id
                used.add(new_id)
                src = rule.get("source") or {}
                if str(src.get("section", "")) == old_id:
                    src["section"] = new_id
                for item in rules:
                    if str(item.get("parent_id") or "") == old_id:
                        item["parent_id"] = new_id
                changed = True
            if changed:
                self._save()

    def delete_rule(self, rid: str) -> None:
        with self.lock:
            children = [r["id"] for r in self.payload["rules"] if str(r.get("parent_id") or "") == rid]
            if children:
                raise UserKBError(
                    f"该总卡下还有 {len(children)} 张分支卡（{', '.join(children[:3])}…），"
                    "请先删除或迁移子卡后再删总卡。"
                )
            before = len(self.payload["rules"])
            self.payload["rules"] = [r for r in self.payload["rules"] if r["id"] != rid]
            if len(self.payload["rules"]) == before:
                raise UserKBError(f"用户知识库中没有规则 {rid}")
            self._save()

    def upsert_glossary(self, entry: dict[str, Any], max_count: int | None = None) -> dict[str, Any]:
        with self.lock:
            en = str(entry.get("en", "")).strip()
            zh = str(entry.get("zh", "")).strip()
            if not en or not zh:
                raise UserKBError("术语必须同时包含 en 和 zh")
            exists = any(g["en"].lower() == en.lower() for g in self.payload["glossary"])
            if not exists and max_count is not None and len(self.payload["glossary"]) >= max_count:
                raise UserKBError(f"当前版本最多保存 {max_count} 条术语；删除一条后可再添加。")
            if exists:
                self.payload["glossary"] = [g for g in self.payload["glossary"] if g["en"].lower() != en.lower()]
            # 保留 UI 传来的领域、启用状态和备注；之前只保留 en/zh/source/note，
            # 导致“领域归属”保存后丢失、术语筛选失效。
            stored = dict(entry)
            stored["en"] = en
            stored["zh"] = zh
            stored["source"] = str(entry.get("source") or "用户")
            stored["note"] = str(entry.get("note") or "")
            stored["domain"] = str(entry.get("domain") or "general")
            stored["enabled"] = entry.get("enabled", True) is not False
            self.payload["glossary"].append(stored)
            self.payload["glossary"].sort(key=lambda g: g["en"].lower())
            self._save()
            return stored

    def delete_glossary(self, en: str) -> None:
        with self.lock:
            before = len(self.payload["glossary"])
            self.payload["glossary"] = [g for g in self.payload["glossary"] if g["en"].lower() != en.lower()]
            if len(self.payload["glossary"]) == before:
                raise UserKBError(f"用户知识库中没有术语 {en}")
            self._save()

    def upsert_case(self, case: dict[str, Any], max_count: int | None = None) -> dict[str, Any]:
        with self.lock:
            _validate_case(case)
            cid = case["id"]
            exists = any(c["id"] == cid for c in self.payload["cases"])
            if not exists and max_count is not None and len(self.payload["cases"]) >= max_count:
                raise UserKBError(f"当前版本最多保存 {max_count} 个案例；删除一个后可再添加。")
            self.payload["cases"] = [c for c in self.payload["cases"] if c["id"] != cid] + [case]
            self.payload["cases"].sort(key=lambda c: c["id"])
            self._save()
            return case

    def delete_case(self, cid: str) -> None:
        with self.lock:
            before = len(self.payload["cases"])
            self.payload["cases"] = [c for c in self.payload["cases"] if c["id"] != cid]
            if len(self.payload["cases"]) == before:
                raise UserKBError(f"用户知识库中没有案例 {cid}")
            self._save()

    def import_payload(self, incoming: dict[str, Any], is_pro: bool, free_limit: int | None = 3, max_cases: int | None = FREE_CASE_LIMIT, free_tier: bool = True, max_terms: int | None = None) -> dict[str, int]:
        with self.lock:
            if not isinstance(incoming, dict):
                raise UserKBError("导入文件不是知识库 JSON")
            rules = incoming.get("rules", [])
            cases = incoming.get("cases", [])
            glossary = incoming.get("glossary", [])
            if not isinstance(rules, list) or not isinstance(cases, list) or not isinstance(glossary, list):
                raise UserKBError("rules / cases / glossary 必须是数组")
            # 免费版：3 条规则 + 1 个案例（英文 ≤600 词 / 中文 ≤300 字），术语导入不开放
            if not is_pro and glossary:
                raise UserKBError("免费版不支持导入术语；激活付费版后可批量导入术语。")
            if free_limit is not None and len(rules) > free_limit:
                raise UserKBError(f"当前版本最多导入 {free_limit} 条规则。")
            for rule in rules:
                _validate_rule(rule)
            ids = [str(rule["id"]) for rule in rules]
            if len(ids) != len(set(ids)):
                raise UserKBError("导入文件里有重复的规则 ID；每条规则必须使用唯一 ID。")
            for case in cases:
                _validate_case(case)
                if free_tier:
                    _check_free_case(case)
            existing = {r["id"] for r in self.payload.get("rules", [])}
            new_ids = [r["id"] for r in rules if r["id"] not in existing]
            if free_limit is not None and len(existing) + len(new_ids) > free_limit:
                raise UserKBError(f"当前版本最多保存 {free_limit} 条自定义规则；当前已有 {len(existing)} 条。")
            existing_cases = {c["id"] for c in self.payload.get("cases", [])}
            new_case_ids = [c["id"] for c in cases if c["id"] not in existing_cases]
            if max_cases is not None and len(self.payload.get("cases", [])) + len(new_case_ids) > max_cases:
                raise UserKBError(
                    f"当前版本最多保存 {max_cases} 个用户案例；当前已有 "
                    f"{len(self.payload.get('cases', []))} 个，请先删除旧案例再导入。"
                )
            merged_rules = {r["id"]: r for r in self.payload["rules"]}
            merged_rules.update({r["id"]: r for r in rules})
            self.payload["rules"] = sorted(merged_rules.values(), key=lambda r: (int(r.get("kb", 0)), r["id"]))
            merged_cases = {c["id"]: c for c in self.payload["cases"]}
            merged_cases.update({c["id"]: c for c in cases})
            self.payload["cases"] = sorted(merged_cases.values(), key=lambda c: c["id"])
            if is_pro:
                existing_terms = {g["en"].lower() for g in self.payload["glossary"]}
                new_terms = [g for g in glossary if g.get("en") and g.get("zh") and g["en"].lower() not in existing_terms]
                if max_terms is not None and len(existing_terms) + len(new_terms) > max_terms:
                    raise UserKBError(
                        f"当前版本最多保存 {max_terms} 条术语；当前已有 {len(existing_terms)} 条。"
                    )
                merged_glossary = {g["en"].lower(): g for g in self.payload["glossary"]}
                merged_glossary.update({g["en"].lower(): g for g in glossary if g.get("en") and g.get("zh")})
                self.payload["glossary"] = sorted(merged_glossary.values(), key=lambda g: g["en"].lower())
            self._save()
            return {"rules": len(rules), "cases": len(cases), "glossary": len(glossary) if is_pro else 0}

    def export_payload(self) -> dict[str, Any]:
        data = copy.deepcopy(self.snapshot())
        data["meta"] = self._meta()
        data["exported_at"] = datetime.now().isoformat(timespec="seconds")
        data["scope"] = "user-only"
        return data

    def backup(self) -> str:
        with self.lock:
            self._backup()
            names = sorted(self.backup_dir.glob("user-*.enc"))
            return names[-1].name if names else ""

    def list_backups(self) -> list[dict[str, Any]]:
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        return [
            {"name": p.name, "size": p.stat().st_size, "created_at": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")}
            for p in sorted(self.backup_dir.glob("user-*.enc"), reverse=True)
        ]

    def restore_backup(
        self,
        name: str,
        max_rules: int | None = None,
        max_cases: int | None = None,
        max_terms: int | None = None,
    ) -> None:
        if not re.match(r"^user-[\d-]+\.enc$", name):
            raise UserKBError("备份文件名不合法")
        path = self.backup_dir / name
        if not path.exists():
            raise UserKBError("备份不存在")
        data = load_protected(path, {})
        if not isinstance(data, dict):
            raise UserKBError("备份内容损坏")
        # 恢复同样要受当前版本额度约束，避免用买断版的备份绕过免费/轻量上限
        checks = (
            (max_rules, len(data.get("rules", [])), "规则"),
            (max_cases, len(data.get("cases", [])), "案例"),
            (max_terms, len(data.get("glossary", [])), "术语"),
        )
        for limit, count, label in checks:
            if limit is not None and count > limit:
                raise UserKBError(f"当前版本最多保存 {limit} 条{label}，该备份有 {count} 条，无法恢复。")
        self.payload = data
        self._save()

    def merged(self, builtin: dict[str, Any], builtin_editable: bool = True) -> dict[str, Any]:
        with self.lock:
            user = copy.deepcopy(self.payload)
            builtin_rules = [{**r, "origin": "builtin", "editable": builtin_editable} for r in builtin.get("rules", [])]
            user_rules = [{**r, "origin": "user", "editable": True} for r in user.get("rules", [])]
            builtin_ids = {r["id"] for r in builtin_rules}
            user_rules = [r for r in user_rules if r["id"] not in builtin_ids]
            rules = builtin_rules + user_rules
            if builtin.get("schema") == "translation-rule-card-v2":
                builtin_cards = [{**c, "origin": "builtin", "editable": builtin_editable} for c in builtin.get("cards", builtin_rules)]
                user_cards = []
                for r in user_rules:
                    ctype = str(r.get("card_type") or "master")
                    user_cards.append({
                        **r,
                        "card_type": ctype,
                        "parent_id": (r.get("parent_id") or None) if ctype == "branch" else None,
                        "scope": "document" if ctype == "master" else "sentence",
                        "stage": ["preflight", "translate", "qa", "delivery"] if ctype == "master" else ["translate", "qa"],
                        "direction": r.get("direction") or r.get("lang_pair") or "both",
                        "trigger_mode": "soft", "trigger_match": "any",
                        "activation": "always" if ctype == "master" else "triggered",
                        "triggers": r.get("triggers") or [],
                        "precedence": int(r.get("priority", 2)) * 100,
                        "exceptions": [], "children": [],
                        "conflict_policy": {"policy": "specific_over_general", "on_conflict": "flag"},
                        "conflict_group": "", "availability": "A",
                    })
                cards = builtin_cards + user_cards
            else:
                cards = rules
            builtin_cases = [{**c, "origin": "builtin", "editable": builtin_editable} for c in builtin.get("cases", [])]
            builtin_case_ids = {c["id"] for c in builtin_cases}
            user_cases = [{**c, "origin": "user", "editable": True} for c in user.get("cases", []) if c["id"] not in builtin_case_ids]
            cases = builtin_cases + user_cases
            builtin_terms = [{**g, "origin": "builtin", "editable": builtin_editable} for g in builtin.get("glossary", [])]
            user_terms = [{**g, "origin": "user", "editable": True} for g in user.get("glossary", [])]
            # 用户术语覆盖同名词条，避免内置与自建重复
            user_keys = {str(g.get("en", "")).lower() for g in user_terms}
            builtin_terms = [g for g in builtin_terms if str(g.get("en", "")).lower() not in user_keys]
            # user terms are placed first so they win in prompt formatting
            glossary = user_terms + builtin_terms
            by_kb = {str(k): sum(1 for r in rules if int(r.get("kb", 0)) == k) for k in range(0, 16)}
            meta = {
                "source": "内置知识库 + 用户知识库",
                "rule_count": len(rules),
                "case_count": len(cases),
                "glossary_count": len(glossary),
                "by_kb": by_kb,
                "builtin_rule_count": len(builtin_rules),
                "user_rule_count": len(user_rules),
                "builtin_case_count": len(builtin_cases),
                "user_case_count": len(user_cases),
                "builtin_glossary_count": len(builtin_terms),
                "user_glossary_count": len(user_terms),
            }
            return {
                "kb_defs": self.kb_defs(),
                "schema": builtin.get("schema", ""),
                "version": 3,
                "revision": int(builtin.get("revision", 1)) * 100000 + int(user.get("revision", 0)),
                "updated_at": user.get("updated_at") or builtin.get("updated_at", ""),
                "meta": meta,
                "rules": rules,
                "cards": cards if builtin.get("schema") == "translation-rule-card-v2" else rules,
                "masters": builtin.get("masters", []),
                "cases": cases,
                "glossary": glossary,
            }

