"""内置规则可编辑 + 恢复出厂。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import builtin_kb


class BuiltinEditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "kb_v2.json"
        self.patch = patch.object(builtin_kb, "DEV_KB_PATH", self.path)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_edit_writes_copy_and_reset_restores_baseline(self):
        kb = builtin_kb.BuiltinKB()
        baseline_count = len(kb.snapshot()["rules"])
        rule = dict(kb.snapshot()["rules"][0])
        rule["title"] = "改过的标题"
        kb.upsert_rule(rule)
        self.assertTrue(self.path.exists())

        reopened = builtin_kb.BuiltinKB()
        self.assertEqual(reopened.snapshot()["rules"][0]["title"], "改过的标题")

        reopened.reset()
        self.assertFalse(self.path.exists())
        self.assertEqual(len(reopened.snapshot()["rules"]), baseline_count)

    def test_identity_rule_cannot_be_deleted_by_accident_but_is_editable(self):
        kb = builtin_kb.BuiltinKB()
        rule = next(r for r in kb.snapshot()["rules"] if r["id"] == "KB1-M00")
        rule["principle"] = "改过的身份规则"
        kb.upsert_rule(rule)
        again = builtin_kb.BuiltinKB()
        self.assertEqual(again.snapshot()["rules"][0]["principle"], "改过的身份规则")


if __name__ == "__main__":
    unittest.main()
