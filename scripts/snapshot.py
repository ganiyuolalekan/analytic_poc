#!/usr/bin/env python
"""Frozen snapshot of the database for evaluation / emergency restore.
  make snapshot       -> data/demo_snapshot.db  (consistent online backup via SQLite's backup API)
  make restore-demo   -> restores the snapshot over data/nsw.db (stop the app first)"""
import argparse
import os
import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nsw_sim.config import data_dir, db_path  # noqa: E402

SNAP = data_dir() / "demo_snapshot.db"


def snapshot() -> None:
    src = sqlite3.connect(str(db_path()))
    dst = sqlite3.connect(str(SNAP))
    with dst:
        src.backup(dst)
    wm = dst.execute("SELECT value FROM sim_state WHERE key='watermark_utc'").fetchone()
    dst.close()
    src.close()
    print(f"snapshot written: {SNAP} ({SNAP.stat().st_size / 1e9:.2f} GB), data as of {wm[0] if wm else '?'}")


def restore() -> None:
    if not SNAP.exists():
        sys.exit("no snapshot found: run `make snapshot` first")
    lock = data_dir() / ".sim.lock"
    for ext in ("", "-wal", "-shm"):
        p = Path(str(db_path()) + ext)
        if p.exists():
            p.unlink()
    shutil.copy2(SNAP, db_path())
    print(f"restored {db_path()} from {SNAP}. Start the app with `make run` (it catches up from the snapshot time to now).")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--restore", action="store_true")
    a = ap.parse_args()
    restore() if a.restore else snapshot()
