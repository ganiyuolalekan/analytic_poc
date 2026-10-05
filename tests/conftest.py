"""Shared fixtures. Tests use a stub LLM and temporary databases by default (no network, no cost)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path, monkeypatch):
    """Every test gets its own DB/cache/data dir and the LLM forced offline unless a test opts in."""
    monkeypatch.setenv("NSW_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("NSW_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("NSW_LLM_CACHE", str(tmp_path / "cache.sqlite"))
    monkeypatch.setenv("NSW_OFFLINE", "1")
    from nsw_sim import db
    from nsw_sim.llm import cache as llm_cache
    db.close_reader()
    llm_cache._conn = None
    yield
    db.close_reader()
    llm_cache._conn = None


@pytest.fixture
def conn():
    from nsw_sim import db
    c = db.connect()
    db.init_db(c)
    yield c
    c.close()
