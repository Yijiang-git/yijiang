"""长文批处理：分块、并发翻译、断点续跑、翻译记忆、章节摘要、分级审校。

用法（CLI）：
    python tools/batch_translate.py 输入.txt 输出.json --workers 4 --mode standard
"""
from __future__ import annotations

import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

import qa_policy
from llm import LLMClient
from outline import SectionSummarizer, chunk_section_index, split_sections
from pipeline import Pipeline, TranslationRequest
from tm import TranslationMemory


def split_chunks(text: str, max_chars: int = 1400) -> list[str]:
    text = (text or "").replace("\r\n", "\n").strip()
    if len(text) <= max_chars:
        return [text] if text else []
    paras = re.split(r"\n{2,}", text)
    chunks: list[str] = []
    buf = ""

    def flush() -> None:
        nonlocal buf
        if buf.strip():
            chunks.append(buf.strip())
        buf = ""

    for p in paras:
        if len(p) > max_chars:
            flush()
            for sent in re.split(r"(?<=[.!?。！？；;])\s*", p):
                if len(buf) + len(sent) > max_chars:
                    flush()
                buf += sent
            flush()
            continue
        if len(buf) + len(p) + 2 > max_chars:
            flush()
        buf += ("\n\n" if buf else "") + p
    flush()
    return chunks


class BatchTranslator:
    def __init__(
        self,
        pipe: Pipeline,
        workers: int = 4,
        mode: str = "standard",
        reader: str = "professional",
        genre: str = "default",
        lang_pair: str = "en-zh",
        checkpoint: Path | None = None,
        tm_path: Path | None = None,
        use_sections: bool = True,
        qa_mode: str = "always",
        qa_threshold: float = 0.35,
        qa_sample_rate: float = 0.25,
    ) -> None:
        self.pipe = pipe
        self.workers = max(1, workers)
        self.mode = mode
        self.reader = reader
        self.genre = genre
        self.lang_pair = lang_pair
        self.checkpoint = checkpoint
        self.tm = TranslationMemory(tm_path) if tm_path else None
        self.use_sections = use_sections
        self.qa_mode = qa_mode
        self.qa_threshold = qa_threshold
        self.qa_sample_rate = qa_sample_rate
        self._lock = threading.Lock()

    def run(
        self,
        text: str,
        on_progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        chunks = split_chunks(text)
        total = len(chunks)
        done: dict[int, dict[str, Any]] = {}
        started = time.time()

        if self.checkpoint and self.checkpoint.exists():
            try:
                prev = json.loads(self.checkpoint.read_text(encoding="utf-8"))
                for item in prev.get("chunks", []):
                    if item.get("translation"):
                        done[int(item["index"])] = item
            except Exception:
                done = {}

        global_terms = self.pipe.kb.global_glossary(text, limit=200, lang_pair=self.lang_pair)

        # P1.4 章节摘要
        section_summaries: list[str] = []
        chunk_sections: list[int] = [0] * total
        if self.use_sections and len(text) > 4000:
            try:
                sections = split_sections(text)
                client = LLMClient(self.pipe.cfg)
                section_summaries = SectionSummarizer(client).summarize(sections, limit=12)
                chunk_sections = chunk_section_index(chunks, text, sections)
            except Exception:
                section_summaries = []

        def work(idx: int) -> dict[str, Any]:
            chunk = chunks[idx]
            t0 = time.time()

            # P1.2 翻译记忆：精确命中直接复用
            if self.tm is not None:
                hit = self.tm.get(chunk, self.lang_pair)
                if hit:
                    return {"index": idx, "translation": hit, "tokens": 0,
                            "elapsed": round(time.time() - t0, 2), "error": "", "cached": True,
                            "qa": "skip", "risk": 0.0}

            # P2.1 分级审校
            qa_ok, risk = qa_policy.should_review(
                chunk, mode=self.qa_mode, threshold=self.qa_threshold,
                sample_rate=self.qa_sample_rate, index=idx,
            )
            note = ""
            if section_summaries:
                si = chunk_sections[idx] if idx < len(chunk_sections) else 0
                if 0 <= si < len(section_summaries):
                    note = section_summaries[si]

            req = TranslationRequest(
                text=chunk, mode=self.mode, reader=self.reader, use_cache=False,
                review_enabled=True,
                source_lang="en" if self.lang_pair == "en-zh" else "zh",
                target_lang="zh" if self.lang_pair == "en-zh" else "en",
                genre=self.genre,
            )
            try:
                res = self.pipe.run(
                    req, global_terms=global_terms, context_note=note, qa_enabled=qa_ok,
                )
                if self.tm is not None and res.translation.strip():
                    self.tm.put(chunk, res.translation, self.lang_pair)
                return {
                    "index": idx, "translation": res.translation,
                    "tokens": res.usage.get("total", {}).get("total", 0),
                    "elapsed": round(time.time() - t0, 1), "error": "",
                    "cached": False, "qa": "run" if qa_ok else "skip", "risk": risk.score,
                }
            except Exception as exc:  # noqa: BLE001
                return {"index": idx, "translation": "", "tokens": 0,
                        "elapsed": round(time.time() - t0, 1), "error": str(exc)[:300],
                        "cached": False, "qa": "error", "risk": risk.score}

        todo = [i for i in range(total) if i not in done]
        if self.workers == 1:
            for i in todo:
                done[i] = work(i)
                self._save(done, total, started, global_terms, section_summaries)
                if on_progress:
                    on_progress(self._status(done, total, started))
        else:
            with ThreadPoolExecutor(max_workers=self.workers) as pool:
                futures = {pool.submit(work, i): i for i in todo}
                for fut in as_completed(futures):
                    rec = fut.result()
                    with self._lock:
                        done[rec["index"]] = rec
                        self._save(done, total, started, global_terms, section_summaries)
                        if on_progress:
                            on_progress(self._status(done, total, started))

        ordered = [done[i] for i in sorted(done)]
        return {
            "translation": "\n\n".join(x.get("translation", "") for x in ordered),
            "chunks": ordered,
            "total_chunks": total,
            "tokens": sum(x.get("tokens", 0) for x in ordered),
            "cached_chunks": sum(1 for x in ordered if x.get("cached")),
            "qa_skipped": sum(1 for x in ordered if x.get("qa") == "skip"),
            "sections": len(section_summaries),
            "elapsed": round(time.time() - started, 1),
            "global_terms": len(global_terms),
        }

    def _status(self, done: dict[int, dict[str, Any]], total: int, started: float) -> dict[str, Any]:
        return {
            "done": len(done), "total": total,
            "elapsed": round(time.time() - started, 1),
            "tokens": sum(x.get("tokens", 0) for x in done.values()),
            "failed": sum(1 for x in done.values() if x.get("error")),
            "cached": sum(1 for x in done.values() if x.get("cached")),
        }

    def _save(
        self,
        done: dict[int, dict[str, Any]],
        total: int,
        started: float,
        terms: list[dict[str, str]],
        summaries: list[str],
    ) -> None:
        if not self.checkpoint:
            return
        payload = {
            "status": self._status(done, total, started),
            "global_terms": [f"{t['en']}={t['zh']}" for t in terms][:200],
            "sections": summaries,
            "chunks": [done[i] for i in sorted(done)],
        }
        self.checkpoint.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
