"""翻译记忆（TM）：精确命中复用旧译文，改稿时只翻改动段落。"""
from __future__ import annotations

import difflib
import hashlib
import sqlite3
import time
from pathlib import Path
from typing import Any


class TranslationMemory:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS tm (
                   hash TEXT PRIMARY KEY,
                   source TEXT NOT NULL,
                   target TEXT NOT NULL,
                   lang_pair TEXT NOT NULL DEFAULT 'en-zh',
                   model TEXT NOT NULL DEFAULT '',
                   created_at REAL NOT NULL
               )"""
        )
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_tm_lang ON tm(lang_pair)")
        self._conn.commit()

    @staticmethod
    def _hash(text: str, lang_pair: str) -> str:
        norm = " ".join(text.split())
        return hashlib.sha1(f"{lang_pair}|{norm}".encode("utf-8")).hexdigest()

    def get(self, source: str, lang_pair: str = "en-zh") -> str | None:
        row = self._conn.execute(
            "SELECT target FROM tm WHERE hash = ?", (self._hash(source, lang_pair),)
        ).fetchone()
        return row[0] if row else None

    def put(self, source: str, target: str, lang_pair: str = "en-zh", model: str = "") -> None:
        if not source.strip() or not target.strip():
            return
        self._conn.execute(
            "INSERT OR REPLACE INTO tm(hash, source, target, lang_pair, model, created_at) VALUES (?,?,?,?,?,?)",
            (self._hash(source, lang_pair), source, target, lang_pair, model, time.time()),
        )
        self._conn.commit()

    def fuzzy(self, source: str, lang_pair: str = "en-zh", limit: int = 1, threshold: float = 0.72) -> str | None:
        """近似匹配：取同语言对最近的 2000 条做相似度比对，返回最相似且过阈值的译文。"""
        rows = self._conn.execute(
            "SELECT source, target FROM tm WHERE lang_pair = ? ORDER BY created_at DESC LIMIT 2000",
            (lang_pair,),
        ).fetchall()
        best, best_ratio = None, 0.0
        for src, tgt in rows:
            ratio = difflib.SequenceMatcher(None, src, source).ratio()
            if ratio > best_ratio:
                best, best_ratio = tgt, ratio
        return best if best_ratio >= threshold else None

    def stats(self) -> dict[str, Any]:
        total = self._conn.execute("SELECT COUNT(*) FROM tm").fetchone()[0]
        return {"entries": total, "path": str(self.path)}

    def close(self) -> None:
        self._conn.close()
