"""Indexed and unindexed copies of the database (scripts/db_index.py): the unindexed copy holds exactly the same data, and indexes can be rebuilt on it."""
import importlib.util
import sqlite3
from pathlib import Path

import pytest

from nsw_sim import db

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def tool():
    spec = importlib.util.spec_from_file_location("db_index", ROOT / "scripts" / "db_index.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def indexed_db(tmp_path):
    p = tmp_path / "full.db"
    c = db.connect(p)
    db.init_db(c)                                                    # tables and every chosen index
    db.kv_set(c, "watermark_utc", "2026-10-05T19:09:35Z")
    c.execute("ANALYZE")                                             # a real database carries planner statistics
    c.close()
    return p


def test_status_reports_an_indexed_database(tool, indexed_db):
    s = tool.status(indexed_db)
    assert s["kind"] == "indexed" and s["indexes"] == s["expected"] == len(db.INDEXES) and s["statistics"]


def test_the_unindexed_copy_has_no_chosen_indexes_the_same_rows_and_leaves_the_source_alone(tool, indexed_db, tmp_path):
    before = tool.status(indexed_db)
    s = tool.slim(indexed_db, tmp_path / "unindexed.db")
    assert s["kind"] == "unindexed" and s["indexes"] == 0 and not s["statistics"]
    assert tool.status(indexed_db) == before                         # the source (a running app's file) is untouched
    a, b = sqlite3.connect(indexed_db), sqlite3.connect(tmp_path / "unindexed.db")
    assert a.execute("SELECT key, value FROM sim_state ORDER BY key").fetchall() == b.execute("SELECT key, value FROM sim_state ORDER BY key").fetchall()
    assert ("watermark_utc", "2026-10-05T19:09:35Z") in b.execute("SELECT key, value FROM sim_state").fetchall()
    tables = lambda c: sorted(r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))      # noqa: E731
    assert tables(a) == tables(b)
    a.close()
    b.close()


def test_indexes_can_be_rebuilt_on_the_unindexed_copy(tool, indexed_db, tmp_path):
    out = tmp_path / "unindexed.db"
    tool.slim(indexed_db, out)
    s = tool.index(out)
    assert s["kind"] == "indexed" and s["statistics"]


def test_slim_replaces_an_existing_copy(tool, indexed_db, tmp_path):
    out = tmp_path / "unindexed.db"
    out.write_bytes(b"not a database")
    assert tool.slim(indexed_db, out)["kind"] == "unindexed"


def test_index_count_tells_the_two_kinds_of_database_apart(tool, indexed_db, tmp_path):
    tool.slim(indexed_db, tmp_path / "unindexed.db")
    for path, want in ((indexed_db, len(db.INDEXES)), (tmp_path / "unindexed.db", 0)):
        c = sqlite3.connect(path)
        assert db.index_count(c) == want
        c.close()
