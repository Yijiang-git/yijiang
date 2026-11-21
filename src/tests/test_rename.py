from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from userkb import UserKB, UserKBError


def rule(rid: str, title: str = "测试规则") -> dict:
    return {
        "id": rid,
        "kb": 1,
        "title": title,
        "principle": "原则说明",
        "check": "检查项",
        "examples": [],
        "triggers": [],
        "text_types": [],
        "priority": 2,
        "source": {"file": "test", "section": rid},
        "enabled": True,
        "lang_pair": "en-zh",
    }


class RenameRuleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=str(ROOT / "data"))
        base = Path(self.tmp.name)
        self.kb = UserKB(base / "user_kb.enc", base / "backups")

    def tearDown(self):
        self.tmp.cleanup()

    def test_rename_moves_rule_and_source(self):
        self.kb.upsert_rule(rule("KB1-001", "旧标题"), is_pro=True)
        self.kb.upsert_rule(rule("KB1-002", "另一条"), is_pro=True)
        self.kb.upsert_rule(rule("KB1-009", "新标题"), is_pro=True, original_id="KB1-001")
        ids = [r["id"] for r in self.kb.payload["rules"]]
        self.assertIn("KB1-009", ids)
        self.assertNotIn("KB1-001", ids)
        moved = next(r for r in self.kb.payload["rules"] if r["id"] == "KB1-009")
        self.assertEqual(moved["title"], "新标题")
        self.assertEqual(moved["source"]["section"], "KB1-009")

    def test_rename_collision_is_rejected(self):
        self.kb.upsert_rule(rule("KB1-001"), is_pro=True)
        self.kb.upsert_rule(rule("KB1-002"), is_pro=True)
        with self.assertRaises(UserKBError):
            self.kb.upsert_rule(rule("KB1-002", "冲突"), is_pro=True, original_id="KB1-001")

    def test_title_only_change_keeps_id(self):
        self.kb.upsert_rule(rule("KB1-001", "旧标题"), is_pro=True)
        self.kb.upsert_rule(rule("KB1-001", "新标题"), is_pro=True, original_id="KB1-001")
        self.assertEqual(self.kb.payload["rules"][0]["title"], "新标题")
        self.assertEqual(self.kb.payload["rules"][0]["id"], "KB1-001")


if __name__ == "__main__":
    unittest.main()
