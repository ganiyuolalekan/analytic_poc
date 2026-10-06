"""Indexed and unindexed copies of the database: how indexed a file is, an unindexed copy made from a running one, and (re)building the indexes.
Used by ``scripts/db_index.py``, by publishing the database (``dbpublish``) and by the first-start download on a new host (``dbfetch``)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from nsw_sim import db

STATS = ("sqlite_stat1", "sqlite_stat4")


def status(path: Path) -> dict:
    """How indexed a database file is: chosen indexes present out of the expected ones, planner statistics, and size."""
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=60)
    try:
        present = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='index' AND sql IS NOT NULL")}
        stats = any(c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in STATS if c.execute("SELECT 1 FROM sqlite_master WHERE name=?", (t,)).fetchone())
    finally:
        c.close()
    have = len(present & set(db.INDEXES))
    return {"path": str(path), "size_mb": round(path.stat().st_size / 1e6), "indexes": have, "expected": len(db.INDEXES), "statistics": stats,
            "kind": "indexed" if have == len(db.INDEXES) else "unindexed" if have == 0 else "partly indexed"}


def slim(src: Path, out: Path) -> dict:
    """An online backup of ``src`` (read-only: a running app is not disturbed) with the chosen indexes and their statistics removed, then compacted."""
    for ext in ("", "-wal", "-shm"):
        Path(str(out) + ext).unlink(missing_ok=True)
    s = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=60)
    d = sqlite3.connect(out)
    with d:
        s.backup(d)
    s.close()
    db.drop_indexes(d)
    for t in STATS:                                           # statistics describe indexes that no longer exist
        if d.execute("SELECT 1 FROM sqlite_master WHERE name=?", (t,)).fetchone():
            d.execute(f"DELETE FROM {t}")
    d.commit()
    d.execute("VACUUM")
    d.close()
    return status(out)


def index(path: Path) -> dict:
    """Create any missing chosen index and refresh the planner statistics, so every page and the assistant read quickly."""
    c = db.connect(path)
    db.create_indexes(c)
    c.execute("ANALYZE")
    c.execute("PRAGMA optimize")
    c.close()
    return status(path)
