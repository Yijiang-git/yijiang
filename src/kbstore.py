from __future__ import annotations

import copy
import json
import re
import shutil
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
KB_PATH = DATA / "kb.json"
BACKUP_DIR = DATA / "backups"


class KBError(ValueError):
    pass


class KBStore:
    def __init__(self, path: Path = KB_PATH):
        self.path = path
        self.lock = threading.RLock()
        self.payload = self._load()
        self._normalize()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            raise KBError("data/kb.json 不存在")
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _normalize(self) -> None:
        data = self.payload
        data.setdefault("version", 2)
        data.setdefault("revision", 1)
        data.setdefault("updated_at", datetime.now().isoformat(timespec="seconds"))
        data.setdefault("cases", [])
        data.setdefault("glossary", [])
        for rule in data.get("rules", []):
            rule.setdefault("principle", rule.get("gist") or rule.get("body", "")[:160])
            rule.setdefault("check", f'检查「{rule.get("title", "规则")}」相关问题。')
            rule.setdefault("examples", [])
            rule.setdefault("triggers", [])
            rule.setdefault("text_types", [])
            rule.setdefault("priority", 2)
            rule.setdefault("source", {"file": "Dify 知识库", "section": rule.get("id", "")})
            rule.setdefault("enabled", True)
            rule.setdefault("lang_pair", "en-zh")
        self._recount()

    def _recount(self) -> None:
        data = self.payload
        rules = data.get("rules", [])
        data["meta"] = {
            "source": "Dify KB1-5 + 北外笔译课程材料 + 中译英通用规则",
            "rule_count": len(rules),
            "case_count": len(data.get("cases", [])),
            "glossary_count": len(data.get("glossary", [])),
            "by_kb": {str(k): sum(1 for r in rules if int(r.get("kb", 0)) == k) for k in range(1, 9)},
        }

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return copy.deepcopy(self.payload)

    def reload(self) -> dict[str, Any]:
        with self.lock:
            self.payload = self._load()
            self._normalize()
            return self.snapshot()

    def _validate(self, data: dict[str, Any]) -> None:
        rules = data.get("rules")
        if not isinstance(rules, list) or not rules:
            raise KBError("规则列表不能为空")
        seen = set()
        for rule in rules:
            rid = str(rule.get("id", "")).strip()
            if not re.match(r"^KB[1-6]-\d{3}$", rid):
                raise KBError(f"规则 ID 不合法：{rid}（应形如 KB2-101）")
            if rid in seen:
                raise KBError(f"规则 ID 重复：{rid}")
            seen.add(rid)
            if not str(rule.get("title", "")).strip():
                raise KBError(f"{rid} 缺标题")
            if not str(rule.get("principle", "")).strip():
                raise KBError(f"{rid} 缺原则说明")
            if int(rule.get("kb", 0)) not in range(1, 9):
                raise KBError(f"{rid} 的知识库编号应为 1-8")
            examples = rule.get("examples", [])
            if examples and not isinstance(examples, list):
                raise KBError(f"{rid} 的 examples 必须是数组")
            for ex in examples:
                if not isinstance(ex, dict) or not ex.get("en") or not ex.get("zh"):
                    raise KBError(f"{rid} 的例句必须包含 en 和 zh")
        cases = data.get("cases", [])
        case_ids = set()
        for case in cases:
            cid = str(case.get("id", "")).strip()
            if not re.match(r"^CASE(?:-[A-Z]{2})?-\d{2,3}$", cid):
                raise KBError(f"案例 ID 不合法：{cid}")
            if cid in case_ids:
                raise KBError(f"案例 ID 重复：{cid}")
            case_ids.add(cid)
            if not case.get("en") or not case.get("zh"):
                raise KBError(f"{cid} 缺原文或译文")
        glossary = data.get("glossary", [])
        terms = set()
        for entry in glossary:
            en = str(entry.get("en", "")).strip()
            zh = str(entry.get("zh", "")).strip()
            if not en or not zh:
                raise KBError("术语条目不能为空")
            if en.lower() in terms:
                raise KBError(f"术语重复：{en}")
            terms.add(en.lower())

    def _backup(self) -> str:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = BACKUP_DIR / f"kb-{stamp}.json"
        if self.path.exists():
            shutil.copy2(self.path, target)
        backups = sorted(BACKUP_DIR.glob("kb-*.json"))
        for old in backups[:-30]:
            try:
                old.unlink()
            except OSError:
                pass
        return target.name

    def _write(self, data: dict[str, Any]) -> None:
        self._validate(data)
        data["version"] = 2
        data["revision"] = int(self.payload.get("revision", 1)) + 1
        data["updated_at"] = datetime.now().isoformat(timespec="seconds")
        self._recount()
        self._backup()
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)
        self.payload = data

    def upsert_rule(self, rule: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            data = self.snapshot()
            rules = data["rules"]
            rule = dict(rule)
            rule.setdefault("enabled", True)
            rule.setdefault("lang_pair", "en-zh")
            rule.setdefault("examples", [])
            rule.setdefault("triggers", [])
            rule.setdefault("text_types", [])
            index = next((i for i, r in enumerate(rules) if r.get("id") == rule.get("id")), -1)
            if index >= 0:
                rules[index] = rule
            else:
                rules.append(rule)
            self._write(data)
            return rule

    def delete_rule(self, rule_id: str) -> None:
        with self.lock:
            data = self.snapshot()
            before = len(data["rules"])
            data["rules"] = [r for r in data["rules"] if r.get("id") != rule_id]
            if len(data["rules"]) == before:
                raise KBError("规则不存在")
            self._write(data)

    def upsert_glossary(self, entry: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            data = self.snapshot()
            en = str(entry.get("en", "")).strip()
            zh = str(entry.get("zh", "")).strip()
            if not en or not zh:
                raise KBError("术语的英文和中文都不能为空")
            item = {"en": en, "zh": zh, "source": str(entry.get("source", "手动")), "note": str(entry.get("note", ""))}
            existing = next((i for i, g in enumerate(data["glossary"]) if g.get("en", "").lower() == en.lower()), -1)
            if existing >= 0:
                data["glossary"][existing] = item
            else:
                data["glossary"].append(item)
            self._write(data)
            return item

    def delete_glossary(self, en: str) -> None:
        with self.lock:
            data = self.snapshot()
            before = len(data["glossary"])
            data["glossary"] = [g for g in data["glossary"] if g.get("en", "").lower() != en.lower()]
            if len(data["glossary"]) == before:
                raise KBError("术语不存在")
            self._write(data)

    def upsert_case(self, case: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            data = self.snapshot()
            item = dict(case)
            item.setdefault("enabled", True)
            item.setdefault("tags", [])
            existing = next((i for i, c in enumerate(data["cases"]) if c.get("id") == item.get("id")), -1)
            if existing >= 0:
                data["cases"][existing] = item
            else:
                data["cases"].append(item)
            self._write(data)
            return item

    def delete_case(self, case_id: str) -> None:
        with self.lock:
            data = self.snapshot()
            before = len(data["cases"])
            data["cases"] = [c for c in data["cases"] if c.get("id") != case_id]
            if len(data["cases"]) == before:
                raise KBError("案例不存在")
            self._write(data)

    def import_payload(self, incoming: dict[str, Any], mode: str = "merge") -> dict[str, Any]:
        with self.lock:
            if mode == "replace":
                data = {"version": 2, "revision": 1, "rules": [], "cases": [], "glossary": []}
            else:
                data = self.snapshot()
            if not isinstance(incoming, dict):
                raise KBError("导入内容必须是 JSON 对象")
            if mode == "replace":
                data["rules"] = list(incoming.get("rules", []))
                data["cases"] = list(incoming.get("cases", []))
                data["glossary"] = list(incoming.get("glossary", []))
            else:
                rules = {r["id"]: r for r in data.get("rules", [])}
                for r in incoming.get("rules", []):
                    if r.get("id"):
                        rules[r["id"]] = r
                data["rules"] = list(rules.values())
                cases = {c["id"]: c for c in data.get("cases", [])}
                for c in incoming.get("cases", []):
                    if c.get("id"):
                        cases[c["id"]] = c
                data["cases"] = list(cases.values())
                glossary = {g["en"].lower(): g for g in data.get("glossary", [])}
                for g in incoming.get("glossary", []):
                    if g.get("en"):
                        glossary[g["en"].lower()] = g
                data["glossary"] = list(glossary.values())
            data["rules"].sort(key=lambda r: (int(r.get("kb", 0)), str(r.get("id", ""))))
            data["cases"].sort(key=lambda c: str(c.get("id", "")))
            data["glossary"].sort(key=lambda g: str(g.get("en", "")).lower())
            self._write(data)
            return {"revision": data["revision"], "rules": len(data["rules"]), "cases": len(data["cases"]), "glossary": len(data["glossary"])}

    def export(self) -> dict[str, Any]:
        return self.snapshot()

    def backup(self) -> str:
        with self.lock:
            return self._backup()

    def list_backups(self) -> list[dict[str, Any]]:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        out = []
        for p in sorted(BACKUP_DIR.glob("kb-*.json"), reverse=True)[:30]:
            out.append({"name": p.name, "size": p.stat().st_size, "mtime": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")})
        return out

    def restore(self, name: str) -> dict[str, Any]:
        with self.lock:
            if not re.match(r"^kb-[\d-]+\.json$", name):
                raise KBError("备份文件名不合法")
            path = BACKUP_DIR / name
            if not path.exists():
                raise KBError("备份不存在")
            data = json.loads(path.read_text(encoding="utf-8"))
            self._write(data)
            return {"revision": self.payload["revision"]}

