"""规则资产保护回归：白名单、总卡脱敏、历史/导出不落正文、开发者模式。

这些用例只读内存与既有开发库，不写用户历史库，也不依赖 FastAPI/网络。
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import builtin_kb
import config
import kb_guard

ALLOWED = {"id", "kb", "card_type", "parent_id", "title", "enabled", "origin", "locked", "editable"}


class PublicRuleTests(unittest.TestCase):
    def test_public_rule_drops_body_and_examples(self):
        rule = {
            "id": "KB2-801",
            "kb": 2,
            "card_type": "branch",
            "parent_id": "KB2-M01",
            "title": "测试规则",
            "enabled": True,
            "principle": "SECRET-PRINCIPLE",
            "examples": [{"en": "SECRET-EXAMPLE", "zh": "秘密例句"}],
            "triggers": ["SECRET-TRIGGER"],
        }
        out = kb_guard.public_rule(rule)
        self.assertLessEqual(set(out), ALLOWED)
        self.assertNotIn("SECRET", json.dumps(out, ensure_ascii=False))

    def test_builtin_masters_become_skeletons(self):
        payload = json.loads(builtin_kb.V2_PLAIN_PATH.read_text(encoding="utf-8"))
        masters = kb_guard.public_masters(payload.get("masters") or [])
        self.assertEqual(len(masters), len(payload.get("masters") or []))
        for master in masters:
            self.assertLessEqual(set(master), ALLOWED)
            self.assertNotIn("principle", master)
            self.assertNotIn("examples", master)


class PersistenceTests(unittest.TestCase):
    def test_rule_refs_only_carry_id_and_title(self):
        refs = kb_guard.rule_refs(
            [{"id": "KB2-801", "title": "T", "gist": "SECRET", "examples": [{"en": "X"}]}]
        )
        self.assertEqual(refs, [{"id": "KB2-801", "title": "T"}])

    def test_export_sanitizer_clears_legacy_payload(self):
        meta = {
            "rules": [{"id": "KB2-801", "gist": "SECRET-GIST", "examples": [{"en": "SECRET-EX"}]}],
            "source": "x",
        }
        cleaned = kb_guard.sanitize_history_meta(meta, can_view_rules=False)
        self.assertEqual(cleaned["rules"], [])
        self.assertTrue(cleaned["rules_hidden"])
        self.assertNotIn("SECRET", json.dumps(cleaned, ensure_ascii=False))
        self.assertIs(kb_guard.sanitize_history_meta(meta, can_view_rules=True), meta)

    def test_hydrate_restores_text_only_from_memory(self):
        rules = [{"id": "KB2-801", "title": "T", "gist": "G", "examples": [{"en": "E"}]}]
        refs = [{"id": "KB2-801", "title": "T"}, {"id": "KB2-999"}]
        out = kb_guard.hydrate_rule_refs(refs, rules)
        self.assertEqual(out[0]["gist"], "G")
        self.assertEqual(out[1]["gist"], "")


class DevModeTests(unittest.TestCase):
    def test_env_var_cannot_unlock_frozen_release(self):
        self.assertFalse(config.DEV_MODE)
        with patch.object(sys, "frozen", True, create=True), patch.dict(os.environ, {"YIJIANG_DEV": "1"}):
            reloaded = importlib.reload(config)
            try:
                self.assertFalse(reloaded.DEV_MODE)
            finally:
                os.environ.pop("YIJIANG_DEV", None)
                importlib.reload(config)

    def test_release_kb_is_editable_but_not_auto_dumped(self):
        target = builtin_kb.V2_PLAIN_PATH
        before = target.stat().st_mtime_ns if target.exists() else None
        kb = builtin_kb.BuiltinKB()
        self.assertTrue(kb.editable)
        after = target.stat().st_mtime_ns if target.exists() else None
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
