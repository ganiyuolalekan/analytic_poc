#!/usr/bin/env python
"""Two flavours of the database: INDEXED (fast to read: what you share) and UNINDEXED (about half the size, quick to copy, restore and write into: what you test with locally).
  make db-status                 show whether a database is indexed, its size and whether planner statistics are present
  make db-unindexed              copy data/nsw.db to data/nsw_unindexed.db without its indexes (an online backup: the app can keep running)
  make db-index                  (re)build the indexes on data/nsw.db and refresh its statistics (the app should not be generating at the time)
  make run-local                 start the app on the unindexed copy (port 8599, full controls)
Direct use: ``.venv/bin/python scripts/db_index.py {status,slim,index} [--db PATH] [--src PATH] [--out PATH]``."""
import argparse
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nsw_sim import db  # noqa: E402
from nsw_sim.config import data_dir, db_path  # noqa: E402

UNINDEXED = data_dir() / "nsw_unindexed.db"
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


def show(info: dict) -> None:
    print(f"{info['path']}: {info['kind']} ({info['indexes']} of {info['expected']} chosen indexes), {info['size_mb']:,} MB, planner statistics {'present' if info['statistics'] else 'absent'}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Indexed and unindexed copies of the database.")
    ap.add_argument("action", choices=["status", "slim", "index"])
    ap.add_argument("--db", type=Path, default=None, help="database for status/index (default: the app's database)")
    ap.add_argument("--src", type=Path, default=None, help="slim: source database (default: the app's database)")
    ap.add_argument("--out", type=Path, default=UNINDEXED, help="slim: where to write the unindexed copy")
    a = ap.parse_args()
    t0 = time.time()
    if a.action == "status":
        for p in ([a.db] if a.db else [db_path(), UNINDEXED]):
            if p.exists():
                show(status(p))
        return 0
    if a.action == "slim":
        src = a.src or db_path()
        if not src.exists():
            sys.exit(f"{src} does not exist")
        show(slim(src, a.out))
    else:
        target = a.db or db_path()
        if not target.exists():
            sys.exit(f"{target} does not exist")
        show(index(target))
    print(f"done in {time.time() - t0:.0f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
