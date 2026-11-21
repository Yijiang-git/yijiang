"""分档规则集与最高优先级身份规则。"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import kb_tiers
import prompts


class TierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payload = json.loads((ROOT / "data" / "kb_v2.json").read_text(encoding="utf-8"))

    def test_every_rule_has_a_category(self):
        mapping = kb_tiers.categories()
        self.assertEqual(len(mapping), len(self.payload["rules"]))
        self.assertTrue(set(mapping.values()) <= {"A", "B", "C1", "C2", "D"})

    def test_tier_counts_are_ordered(self):
        free = kb_tiers.visible_payload(self.payload, "free")
        lite = kb_tiers.visible_payload(self.payload, "lite")
        full = kb_tiers.visible_payload(self.payload, "buyout")
        self.assertLess(len(free["rules"]), len(lite["rules"]))
        self.assertLess(len(lite["rules"]), len(full["rules"]))
        self.assertEqual(len(full["rules"]), len(self.payload["rules"]))

    def test_free_has_no_domain_rules(self):
        free = kb_tiers.visible_payload(self.payload, "free")
        for rule in free["rules"]:
            self.assertLess(int(rule.get("kb", 0)), 7, rule["id"])

    def test_complete_has_domain_rules(self):
        full = kb_tiers.visible_payload(self.payload, "buyout")
        self.assertTrue(any(int(r.get("kb", 0)) >= 7 for r in full["rules"]))

    def test_identity_rule_is_in_every_tier(self):
        for tier in ("free", "lite", "buyout"):
            ids = {r["id"] for r in kb_tiers.visible_payload(self.payload, tier)["rules"]}
            self.assertIn("KB1-M00", ids)

    def test_identity_rule_is_priority_five(self):
        rule = next(r for r in self.payload["rules"] if r["id"] == "KB1-M00")
        self.assertEqual(int(rule["priority"]), 5)
        self.assertIn("顶级的专业英汉译员", rule["principle"])


class PromptTests(unittest.TestCase):
    def test_identity_prefix_is_first_in_system_prompt(self):
        messages = prompts.build_translation_messages("Hello", "", [], [], lang_pair="en-zh")
        system = messages[0]["content"]
        self.assertTrue(system.startswith(prompts.IDENTITY_PREFIX))
        self.assertEqual(prompts.IDENTITY_PREFIX, (
            "你是一名顶级的专业英汉译员，不仅能精准地进行英汉互译，更能深度理解上下文语境和专业术语，"
            "确保翻译结果流畅、地道且符合行业规范。"
        ))


if __name__ == "__main__":
    unittest.main()
