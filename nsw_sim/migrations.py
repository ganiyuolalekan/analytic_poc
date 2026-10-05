"""Forward-only schema migrations: add columns introduced after a database was first created (CREATE IF NOT EXISTS
does not alter existing tables). Safe to run on every start."""
from __future__ import annotations

import sqlite3

COLUMN_ADDITIONS = [
    ("stage_events", "doc_no", "TEXT"),
    ("consignments", "declared_at", "TEXT"),
    ("alerts", "dedup_key", "TEXT"),
    ("alerts", "cleared_at", "TEXT"),
    ("live_events", "speed", "REAL"),
    ("llm_calls", "cached", "INT DEFAULT 0"),
]


def migrate(conn: sqlite3.Connection) -> list[str]:
    applied = []
    for table, col, decl in COLUMN_ADDITIONS:
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if have and col not in have:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
            applied.append(f"{table}.{col}")
    return applied
