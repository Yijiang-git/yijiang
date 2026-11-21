"""触发特征兼容：v2 对象触发器与旧版字符串触发器都要能匹配。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import v2_selector


class TriggerTests(unittest.TestCase):
    def test_entries_accept_dict_and_string(self):
        self.assertEqual(
            v2_selector._trigger_entries([{"key": "long_sentence", "weight": 3}, "passive"]),
            [("long_sentence", 3.0), ("passive", 1.0)],
        )

    def test_string_trigger_matches(self):
        card = {"triggers": ["long_sentence"], "trigger_mode": "hard", "trigger_match": "any"}
        self.assertEqual(v2_selector._trigger_match(card, {"long_sentence"}), (True, 0.0))

    def test_object_trigger_keeps_weight(self):
        card = {"triggers": [{"key": "long_sentence", "weight": 3}], "trigger_mode": "soft"}
        self.assertEqual(v2_selector._trigger_match(card, {"long_sentence"}), (True, 3.0))


if __name__ == "__main__":
    unittest.main()
