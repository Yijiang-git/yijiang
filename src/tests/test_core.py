from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import AppConfig
from llm import LLMResult, Usage, estimate_tokens, strip_think
from pipeline import Pipeline, PipelineError, TranslationRequest, clean_translation, split_chunks, validate
from retrieval import KnowledgeBase, build_qa_checks, detect_profile

KB = KnowledgeBase(json.loads((ROOT / "data" / "kb_v2.json").read_text(encoding="utf-8")))
SAMPLE = (ROOT / "data" / "sample_en.txt").read_text(encoding="utf-8")
SAMPLE_ZH = (ROOT / "data" / "sample_zh.txt").read_text(encoding="utf-8")


class MockLLM:
    def __init__(self, qa_issues=None):
        self.calls = []
        self.qa_issues = qa_issues or []

    def chat(self, messages, temperature=None, max_tokens=None, response_format=None, on_delta=None):
        user = messages[-1]["content"]
        if "【必须逐条检查】" in user:
            self.calls.append("qa")
            text = json.dumps({"passed": not self.qa_issues, "issues": self.qa_issues}, ensure_ascii=False)
        elif "请只针对这些问题" in user:
            self.calls.append("repair")
            text = json.dumps({"fixes": []}, ensure_ascii=False)
        elif "请输出：" in user and "text_type" in user:
            self.calls.append("brief")
            text = json.dumps(
                {
                    "text_type": "informative", "secondary": "expressive", "genre": "历史纪实",
                    "tone": "正式", "reader": "普通读者", "strategy": "语意翻译，长句拆分。",
                    "pitfalls": ["少用被字句"], "terms": [],
                },
                ensure_ascii=False,
            )
        else:
            self.calls.append("translate")
            text = "这段被测试的译文。"
            if on_delta:
                for i in range(0, len(text), 6):
                    on_delta(text[i : i + 6])
        return LLMResult(text=text, usage=Usage(100, 50, 150))


class KnowledgeBaseTest(unittest.TestCase):
    def test_rules_are_complete(self):
        self.assertGreaterEqual(len(KB.rules), 110)
        self.assertEqual(len({r["id"] for r in KB.rules}), len(KB.rules))
        self.assertGreaterEqual(len(KB.cases), 8)
        for rule in KB.rules:
            self.assertTrue(rule["principle"])
            self.assertTrue(rule["check"])
            self.assertNotIn("知识库2 英汉", rule["principle"])
            self.assertNotIn("知识库3 文本", rule["principle"])

    def test_glossary(self):
        self.assertGreaterEqual(len(KB.glossary), 180)
        sample = "The application programming interface (API) is documented."
        sample_glossary = KB.build_glossary(sample)
        self.assertFalse(any(g["en"].upper() == "WHO" for g in sample_glossary))
        self.assertTrue(any(g["en"] == "application programming interface (API)" for g in sample_glossary))

    def test_profile(self):
        profile = detect_profile(SAMPLE)
        self.assertEqual(profile.text_type, "informative")
        self.assertEqual(profile.secondary_type, "expressive")
        self.assertIn("Long Bow Village", profile.proper_nouns)
        self.assertNotIn("The South Shanxi", profile.proper_nouns)

    def test_rule_selection(self):
        profile = detect_profile(SAMPLE)
        rules = KB.select(profile, 10)
        ids = [r["id"] for r in rules]
        self.assertEqual(len(ids), 10)
        self.assertEqual(len(set(ids)), 10)
        self.assertTrue(all(int(r.get("kb", 0)) > 0 for r in rules))
        self.assertGreaterEqual(len({int(r.get("kb", 0)) for r in rules}), 2)
        self.assertTrue(all("why" in r for r in rules))

    def test_case_selection(self):
        profile = detect_profile(SAMPLE)
        case = KB.select_case(profile, SAMPLE)
        self.assertIsNotNone(case)
        self.assertTrue(case["zh"])

    def test_qa_checks(self):
        profile = detect_profile(SAMPLE)
        checks = build_qa_checks(KB.select(profile, 10), 10)
        ids = [c["id"] for c in checks]
        self.assertIn("CHK-FID", ids)
        self.assertIn("CHK-FLU", ids)
        self.assertLessEqual(len(checks), 10)


class PipelineTest(unittest.TestCase):
    def test_chunking(self):
        para = ("word " * 400).strip()
        chunks = split_chunks("\n\n".join([para, para, para]), 900)
        self.assertGreaterEqual(len(chunks), 2)
        self.assertLessEqual(max(len(c.split()) for c in chunks), 1300)

    def test_clean_translation(self):
        self.assertEqual(clean_translation("以下是我的译文：\n\n你好。"), "你好。")
        self.assertEqual(clean_translation("<think>内部</think>你好。"), "你好。")
        self.assertEqual(strip_think("<think>x</think>ok"), "ok")

    def test_estimate(self):
        self.assertGreater(estimate_tokens("这是一个测试。"), 0)
        self.assertGreater(estimate_tokens("This is a test."), 0)

    def test_standard_pipeline(self):
        llm = MockLLM()
        pipe = Pipeline(AppConfig(api_key="test"), KB, llm)
        events = []
        with patch("store.get_result", return_value=None), patch("store.put_result"), patch("store.add_history"):
            result = pipe.run(TranslationRequest(text=SAMPLE, mode="standard", use_cache=False), events.append)
        self.assertEqual(llm.calls.count("translate"), 1)
        self.assertEqual(llm.calls.count("qa"), 1)
        self.assertEqual(result.usage["total"]["total"], 300)
        self.assertTrue(result.translation)
        self.assertEqual(result.chunks, 1)
        self.assertTrue(any(e["type"] == "done" for e in events))

    def test_fast_skips_qa(self):
        llm = MockLLM()
        pipe = Pipeline(AppConfig(api_key="test"), KB, llm)
        with patch("store.get_result", return_value=None), patch("store.put_result"), patch("store.add_history"):
            pipe.run(TranslationRequest(text=SAMPLE, mode="fast", use_cache=False), lambda _e: None)
        self.assertEqual(llm.calls.count("translate"), 1)
        self.assertEqual(llm.calls.count("qa"), 0)

    def test_strict_calls_brief(self):
        llm = MockLLM()
        pipe = Pipeline(AppConfig(api_key="test"), KB, llm)
        with patch("store.get_result", return_value=None), patch("store.put_result"), patch("store.add_history"):
            pipe.run(TranslationRequest(text=SAMPLE, mode="strict", use_cache=False), lambda _e: None)
        self.assertIn("brief", llm.calls)

    def test_patch_high_issue(self):
        issue = {"severity": "high", "rule": "KB2-005", "quote": "被测试", "fix": "经测试", "why": "被动"}
        llm = MockLLM(qa_issues=[issue])
        pipe = Pipeline(AppConfig(api_key="test"), KB, llm)
        with patch("store.get_result", return_value=None), patch("store.put_result"), patch("store.add_history"):
            result = pipe.run(TranslationRequest(text=SAMPLE, mode="standard", use_cache=False), lambda _e: None)
        self.assertIn("经测试", result.translation)
        self.assertTrue(result.issues[0]["applied"])
        self.assertEqual(result.qa["high"], 0)

    def test_unsupported_language(self):
        with self.assertRaises(PipelineError):
            validate(TranslationRequest(text=SAMPLE, source_lang="ja", target_lang="zh"))
        with self.assertRaises(PipelineError):
            validate(TranslationRequest(text=SAMPLE, source_lang="en", target_lang="en"))

    def test_ce_profile_and_rules(self):
        profile = detect_profile(SAMPLE_ZH, "zh")
        self.assertEqual(profile.lang_pair, "zh-en")
        rules = KB.select(profile, 10, "zh-en", 0)
        self.assertGreaterEqual(len(rules), 8)
        self.assertTrue(any(int(r.get("kb", 0)) in (6, 7) for r in rules))
        compact = KB.select(profile, 10, "zh-en", -1)
        free = KB.select(profile, 10, "zh-en", 1)
        self.assertEqual(len(compact), 10)
        self.assertEqual(len(free), 10)
        terms = KB.build_glossary("合同适用准据法。", "", 20, "zh-en")
        self.assertTrue(any(t["en"] == "governing law" for t in terms))
        checks = build_qa_checks(rules, 10, "zh-en")
        self.assertTrue(any(c["id"] == "CHK-ENG" for c in checks))

    def test_ce_pipeline(self):
        llm = MockLLM()
        pipe = Pipeline(AppConfig(api_key="test"), KB, llm)
        with patch("store.get_result", return_value=None), patch("store.put_result"), patch("store.add_history"):
            result = pipe.run(
                TranslationRequest(text=SAMPLE_ZH, source_lang="zh", target_lang="en", use_cache=False),
                lambda _e: None,
            )
        self.assertTrue(result.translation)
        self.assertEqual(result.source_lang, "zh")
        self.assertEqual(result.target_lang, "en")
        self.assertIsInstance(result.rules, list)


if __name__ == "__main__":
    unittest.main()
