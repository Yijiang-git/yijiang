"""KB 结构契约：Plan A 的 core/priority/precedence/触发词归一。"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import v2_selector


class KbContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads((ROOT / "data" / "kb_v2.json").read_text(encoding="utf-8"))
        cls.rules = cls.data["rules"]

    def test_core_priority_is_three_and_no_level_four(self):
        priorities = [int(r.get("priority", 0)) for r in self.rules]
        self.assertEqual(priorities.count(3), 40)
        self.assertEqual(priorities.count(4), 0)
        self.assertEqual(priorities.count(5), 1)
        self.assertEqual([r["id"] for r in self.rules if int(r.get("priority", 0)) == 5], ["KB1-M00"])

    def test_precedence_is_derived_from_priority(self):
        for rule in self.rules:
            self.assertEqual(int(rule["precedence"]), int(rule["priority"]) * 100, rule["id"])

    def test_hard_conditions_are_structured(self):
        expected = {
            "G-FORMAT-B01", "G-FORMAT-B02", "G-FORMAT-B03",
            "G-NUMBER-B01", "G-NUMBER-B02", "G-NUMBER-B03", "G-NUMBER-B04",
            "G-PUNCT-B01", "G-PUNCT-B02", "G-PUNCT-B03",
            "CAND-B-023", "CAND-B-024", "CAND-C-002", "CAND-C-004",
            "CAND-C-007", "CAND-H-015", "CAND-H-020",
        }
        actual = {r["id"] for r in self.rules if r.get("hard_conditions")}
        self.assertEqual(actual, expected)
        for rule in self.rules:
            for condition in rule.get("hard_conditions") or []:
                self.assertEqual(condition.get("type"), "regex")
                self.assertTrue(condition.get("pattern"))

    def test_hard_condition_blocks_when_text_does_not_match(self):
        card = {
            "id": "G-FORMAT-B01", "card_type": "branch", "parent_id": "G-FORMAT-M01", "kb": 1,
            "priority": 3, "precedence": 300, "trigger_mode": "hard", "direction": "both",
            "scope": "sentence", "triggers": [{"key": "inline_markup", "weight": 3}],
            "hard_conditions": [{"type": "regex", "pattern": r"\*\*"}], "availability": "A",
        }
        blocked = v2_selector.select([card], direction="en-zh", active_triggers={"inline_markup"}, limit=1, text="plain text")
        self.assertEqual(blocked["selected"], [])
        passed = v2_selector.select([card], direction="en-zh", active_triggers={"inline_markup"}, limit=1, text="**bold**")
        self.assertEqual([c["id"] for c in passed["selected"]], ["G-FORMAT-B01"])

    def test_every_check_is_one_question(self):
        bad = [r["id"] for r in self.rules if str(r.get("check", "")).count("？") != 1]
        self.assertEqual(bad, [])

    def test_obvious_trigger_aliases_are_normalized(self):
        keys = {
            t.get("key") if isinstance(t, dict) else str(t)
            for rule in self.rules for t in (rule.get("triggers") or [])
        }
        self.assertNotIn("chinglish", keys)
        self.assertNotIn("word_salad", keys)
        self.assertEqual(v2_selector._trigger_entries([{"key": "chinglish", "weight": 2}]), [("translationese", 2.0)])

    def test_duplicate_groups_are_declared(self):
        groups = {r.get("conflict_group") for r in self.rules}
        self.assertIn("passive-en-zh", groups)
        self.assertIn("suo-guanyu", groups)

    def test_conflict_group_keeps_higher_priority_card(self):
        def card(cid, priority):
            return {
                "id": cid, "card_type": "branch", "parent_id": "P1", "kb": 2,
                "priority": priority, "precedence": priority * 100,
                "trigger_mode": "soft", "direction": "en-zh", "scope": "sentence",
                "triggers": [{"key": "passive", "weight": 2}],
                "conflict_group": "passive-en-zh", "availability": "A",
            }
        result = v2_selector.select(
            [card("LOW", 2), card("HIGH", 3)],
            direction="en-zh", active_triggers={"passive"}, limit=2,
        )
        self.assertEqual([c["id"] for c in result["selected"]], ["HIGH"])
        self.assertEqual(result["conflicts"][0]["dropped"], ["LOW"])


if __name__ == "__main__":
    unittest.main()
