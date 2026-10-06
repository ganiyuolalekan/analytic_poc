"""Resilience (network loss -> degraded mode -> recovery, offline end to end) and performance budgets (Section 11.5)."""
import time
from pathlib import Path

import pytest

from nsw_sim import clock, db
from nsw_sim.llm.client import LLM, STATUS, LLMConfig, StubLLM
from nsw_sim.sim import backfill, director

ROOT = Path(__file__).resolve().parent.parent
REAL = ROOT / "data" / "nsw.db"


def test_network_loss_degrades_then_recovers_and_director_falls_back(monkeypatch):
    monkeypatch.delenv("NSW_OFFLINE", raising=False)
    from nsw_sim.config import settings
    monkeypatch.setitem(settings()["llm"], "retry_max", 1)
    STATUS.state, STATUS.consec_fail, STATUS.forced_offline = "live", 0, False
    events = []
    STATUS.on_change.append(lambda o, n, r: events.append((o, n)))
    llm = LLM(LLMConfig(api_key="k" * 20, base_url="http://127.0.0.1:9/v1", model_data="m", model_chat="m"), offline=False)
    for _ in range(3):                                           # network is down: connection refused
        r = llm.json_call("director", "s", "u", timeout=2)
        assert not r.ok
    assert STATUS.state == "degraded" and ("live", "degraded") in events
    ctx = {"active_incidents": [], "open_alerts": {}, "time_wat": "Mon 10:00"}
    res = director.call_director(llm, ctx, 45, "seed-1")           # the feed keeps being produced by the deterministic fallback
    assert res.source == "fallback" and len(res.obj.feed_items) >= 3
    STATUS.ok(120)                                               # network restored
    assert STATUS.state == "live" and ("degraded", "live") in events
    STATUS.on_change.clear()


def test_offline_mode_runs_the_whole_pipeline_with_fallback_content(tmp_path, monkeypatch):
    monkeypatch.setenv("NSW_OFFLINE", "1")
    conn = db.connect()
    db.init_db(conn)
    llm = LLM()
    assert llm.offline
    llm.concurrency = 2
    t0 = time.time()
    backfill.run_backfill(conn, llm, clock.engine_start().timestamp() + 12 * 86400)
    per_day = (time.time() - t0) / 12
    assert per_day < 5, f"backfill budget: 1 simulated day under 5 s, got {per_day:.1f}"
    assert conn.execute("SELECT COUNT(DISTINCT source) FROM entity_profiles").fetchone()[0] == 1
    assert conn.execute("SELECT source FROM entity_profiles LIMIT 1").fetchone()[0] == "fallback"
    assert conn.execute("SELECT COUNT(*) FROM live_events").fetchone()[0] > 0


def test_offline_assistant_still_answers_from_tools(monkeypatch):
    if not REAL.exists():
        pytest.skip("run `make seed` first")
    monkeypatch.setenv("NSW_DB_PATH", str(REAL))
    from nsw_sim.assistant.agent import Agent
    db.close_reader()
    res = Agent(StubLLM(offline=True), as_of="2026-10-05T12:00:00Z").ask("How are we doing?")
    assert res.source == "offline" and res.status == "verified" and "offline" in res.text


@pytest.mark.slow
def test_cached_page_loads_meet_the_two_second_budget(monkeypatch):
    if not REAL.exists():
        pytest.skip("run `make seed` first")
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv("NSW_DB_PATH", str(REAL))
    db.close_reader()
    slow = {}
    for page in ("01_command_center", "02_clearance", "03_entities", "06_supervision"):
        path = str(ROOT / "app" / "screens" / f"{page}.py")
        AppTest.from_file(path, default_timeout=180).run()          # warm the data cache
        dt = 1e9
        for _ in range(2):                                              # best of two: the 30 s data-time bucket may roll over between runs
            t0 = time.time()
            at = AppTest.from_file(path, default_timeout=180).run()
            dt = min(dt, time.time() - t0)
            assert not at.exception
        if dt > 2.0:
            slow[page] = round(dt, 1)
    assert not slow, f"pages over the 2 s cached-load budget: {slow}"


@pytest.mark.slow
def test_report_compute_is_fast_enough_for_a_live_demo(monkeypatch):
    if not REAL.exists():
        pytest.skip("run `make seed` first")
    monkeypatch.setenv("NSW_DB_PATH", str(REAL))
    db.close_reader()
    from nsw_sim.analytics import reports
    as_of = "2026-10-05T12:00:00Z"
    p = reports.ReportParams(start=clock.iso(clock.sim_start()), end=as_of, entities=("NCS", "NPA", "NAFDAC"),
                             sections=("kpis", "statements", "consolidated", "reconciliation", "exceptions"))
    t0 = time.time()
    rep = reports.ReportEngine.compute(p, as_of)
    assert rep["fingerprint"] and time.time() - t0 < 60


def test_presenter_incident_injection_changes_engine_inputs_and_feed(tmp_path, monkeypatch):
    """Injected incidents are real engine inputs: conditions change, an incident row and a feed event are written, and a duplicate burst creates duplicates."""
    from nsw_sim.sim.conditions import Incident
    monkeypatch.setenv("NSW_OFFLINE", "1")
    conn = db.connect()
    db.init_db(conn)
    llm = StubLLM(offline=True)
    llm.concurrency = 2
    eng = backfill.run_backfill(conn, llm, clock.engine_start().timestamp() + 9 * 86400)
    t = eng.t
    base = eng.cond.scanner_mult("NGAPP", t)
    eng.inject(Incident("INC-TEST-1", "scanner_outage", t, t + 8 * 3600, port="NGAPP", params={"scanners_offline": 2}, label="Scanner outage at Apapa"))
    eng.inject(Incident("INC-TEST-2", "duplicate_burst", t, t + 3600, params={"count": 5}, label="Duplicate payments burst"))
    assert eng.cond.scanner_mult("NGAPP", t + 60) >= base * 1.9 and eng.cond.scanners_offline("NGAPP", t + 60) == 2
    eng.advance(t + 3 * 3600)
    eng.flush(t + 3 * 3600)
    assert conn.execute("SELECT COUNT(*) FROM incidents WHERE incident_id='INC-TEST-1'").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM live_events WHERE type='ops.scanner.offline' AND subject='INC-TEST-1'").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM payments WHERE is_duplicate=1 AND occurred_at>?", (clock.iso(t),)).fetchone()[0] >= 4
