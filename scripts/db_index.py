#!/usr/bin/env python
"""Two flavours of the database: INDEXED (fast to read: what you share) and UNINDEXED (about half the size, quick to copy, restore and write into: what you test with locally).
  make db-status                 show whether a database is indexed, its size and whether planner statistics are present
  make db-unindexed              copy data/nsw.db to data/nsw_unindexed.db without its indexes (an online backup: the app can keep running)
  make db-index                  (re)build the indexes on data/nsw.db and refresh its statistics (the app should not be generating at the time)
  make run-local                 start the app on the unindexed copy (port 8599, full controls)
Direct use: ``.venv/bin/python scripts/db_index.py {status,slim,index} [--db PATH] [--src PATH] [--out PATH]``."""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nsw_sim.config import data_dir, db_path  # noqa: E402
from nsw_sim.dbtools import index, slim, status  # noqa: E402

UNINDEXED = data_dir() / "nsw_unindexed.db"


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
