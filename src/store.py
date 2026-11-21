from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from config import DATA
from entitlements import protect_text, unprotect_text

DB_PATH = DATA / "app.db"
_LOCK = threading.Lock()
_ACTIVE_DB = DB_PATH
_MEMORY_DB: sqlite3.Connection | None = None


def _connect() -> sqlite3.Connection:
    """Open the local DB without killing startup if the file is locked/corrupt.

    A failed WAL/sidecar can leave a zero-byte app.db on a user machine. Try the
    primary path, a local alternate, then an in-memory DB so translation still
    starts even if history persistence is unavailable.
    """
    global _ACTIVE_DB, _MEMORY_DB
    if _MEMORY_DB is not None:
        return _MEMORY_DB
    candidates = [_ACTIVE_DB, DB_PATH, DATA / "app.local.db", DATA / "app.fallback.db"]
    seen = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        try:
            candidate.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(candidate), timeout=15)
            conn.row_factory = sqlite3.Row
            try:
                conn.execute("PRAGMA journal_mode=WAL")
            except sqlite3.OperationalError:
                conn.execute("PRAGMA journal_mode=DELETE")
            conn.execute("PRAGMA synchronous=NORMAL")
            _ACTIVE_DB = candidate
            return conn
        except (sqlite3.Error, OSError):
            continue
    _MEMORY_DB = sqlite3.connect(":memory:")
    _MEMORY_DB.row_factory = sqlite3.Row
    return _MEMORY_DB


def init_db() -> None:
    with _LOCK, _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS results (
                key TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                source TEXT NOT NULL,
                translation TEXT NOT NULL,
                meta TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                title TEXT NOT NULL,
                source TEXT NOT NULL,
                translation TEXT NOT NULL,
                tokens INTEGER NOT NULL DEFAULT 0,
                baseline INTEGER NOT NULL DEFAULT 0,
                mode TEXT NOT NULL DEFAULT 'standard',
                meta TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_history_created ON history(created_at DESC);
            """
        )


def get_result(key: str) -> dict[str, Any] | None:
    with _LOCK, _connect() as conn:
        row = conn.execute("SELECT * FROM results WHERE key = ?", (key,)).fetchone()
        if not row:
            return None
        payload = dict(row)
        payload["source"] = unprotect_text(payload.get("source", ""))
        payload["translation"] = unprotect_text(payload.get("translation", ""))
        payload["meta"] = json.loads(unprotect_text(payload["meta"] or "{}") or "{}")
        return payload


def put_result(key: str, source: str, translation: str, meta: dict[str, Any]) -> None:
    with _LOCK, _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO results(key, created_at, source, translation, meta) VALUES (?,?,?,?,?)",
            (key, datetime.now().isoformat(timespec="seconds"), protect_text(source), protect_text(translation), protect_text(json.dumps(meta, ensure_ascii=False))),
        )


def add_history(
    source: str,
    translation: str,
    tokens: int,
    baseline: int,
    mode: str,
    meta: dict[str, Any],
) -> int:
    title = " ".join(source.strip().split())[:48] or "未命名"
    with _LOCK, _connect() as conn:
        cur = conn.execute(
            "INSERT INTO history(created_at, title, source, translation, tokens, baseline, mode, meta) VALUES (?,?,?,?,?,?,?,?)",
            (
                datetime.now().isoformat(timespec="seconds"),
                protect_text(title),
                protect_text(source),
                protect_text(translation),
                tokens,
                baseline,
                mode,
                protect_text(json.dumps(meta, ensure_ascii=False)),
            ),
        )
        return int(cur.lastrowid)


def list_history(limit: int = 30) -> list[dict[str, Any]]:
    with _LOCK, _connect() as conn:
        rows = conn.execute(
            "SELECT id, created_at, title, tokens, baseline, mode FROM history ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["title"] = unprotect_text(item.get("title", ""))
            out.append(item)
        return out


def get_history(item_id: int) -> dict[str, Any] | None:
    with _LOCK, _connect() as conn:
        row = conn.execute("SELECT * FROM history WHERE id = ?", (item_id,)).fetchone()
        if not row:
            return None
        payload = dict(row)
        payload["title"] = unprotect_text(payload.get("title", ""))
        payload["source"] = unprotect_text(payload.get("source", ""))
        payload["translation"] = unprotect_text(payload.get("translation", ""))
        payload["meta"] = json.loads(unprotect_text(payload["meta"] or "{}") or "{}")
        return payload


def delete_history(item_id: int) -> None:
    with _LOCK, _connect() as conn:
        conn.execute("DELETE FROM history WHERE id = ?", (item_id,))


def clear_history() -> None:
    with _LOCK, _connect() as conn:
        conn.execute("DELETE FROM history")


def clear_results() -> int:
    """清空翻译结果缓存，返回被删条数。"""
    with _LOCK, _connect() as conn:
        try:
            count = int(conn.execute("SELECT count(*) FROM results").fetchone()[0])
        except Exception:
            count = 0
        conn.execute("DELETE FROM results")
        return count


def stats() -> dict[str, int]:
    with _LOCK, _connect() as conn:
        count = conn.execute("SELECT count(*) FROM history").fetchone()[0]
        total = conn.execute("SELECT coalesce(sum(tokens),0) FROM history").fetchone()[0]
        return {"count": int(count), "tokens": int(total)}


init_db()

