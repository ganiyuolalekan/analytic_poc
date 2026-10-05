"""Persistent LLM response cache (SQLite). Replays are free and make the demo reproducible."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from typing import Any

from nsw_sim import clock
from nsw_sim.config import cache_path

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None
_conn_path: str | None = None


def _get() -> sqlite3.Connection:
    global _conn, _conn_path
    p = str(cache_path())
    if _conn is None or _conn_path != p:
        _conn = sqlite3.connect(p, check_same_thread=False, timeout=30, isolation_level=None)
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA synchronous=NORMAL")
        _conn.execute("CREATE TABLE IF NOT EXISTS llm_cache(key TEXT PRIMARY KEY, role TEXT, model TEXT, "
                      "response TEXT, tokens_in INT, tokens_out INT, created_at TEXT)")
        _conn_path = p
    return _conn


def make_key(role: str, model: str, system: str, user: str, version: str, extra: Any = None) -> str:
    h = hashlib.sha256()
    for part in (role, model, system, user, version, json.dumps(extra, sort_keys=True, default=str)):
        h.update(part.encode("utf-8"))
        h.update(b"\x1f")
    return h.hexdigest()


def get(key: str) -> dict | None:
    with _lock:
        row = _get().execute("SELECT response, tokens_in, tokens_out FROM llm_cache WHERE key=?", (key,)).fetchone()
    if not row:
        return None
    return {"response": row[0], "tokens_in": row[1] or 0, "tokens_out": row[2] or 0}


def put(key: str, role: str, model: str, response: str, tokens_in: int, tokens_out: int) -> None:
    with _lock:
        _get().execute("INSERT OR REPLACE INTO llm_cache(key,role,model,response,tokens_in,tokens_out,created_at) "
                       "VALUES(?,?,?,?,?,?,?)",
                       (key, role, model, response, tokens_in, tokens_out, clock.iso(clock.utcnow())))


def stats() -> dict:
    with _lock:
        n, = _get().execute("SELECT COUNT(*) FROM llm_cache").fetchone()
    return {"entries": n}


def clear() -> None:
    with _lock:
        _get().execute("DELETE FROM llm_cache")
