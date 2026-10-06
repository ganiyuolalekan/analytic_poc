"""R-TGT-01 (dwell projection misses the end-2026 target) only fires on a real projection, and its metric is the fitted median dwell in days."""
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from nsw_sim import clock
from nsw_sim.analytics import clearance
from nsw_sim.supervision import rules

REAL = Path(__file__).resolve().parent.parent / "data" / "nsw.db"
T = clock.to_epoch("2026-10-05T12:00:00Z")


def weekly(medians):
    """Weekly medians as ``clearance.weekly_dwell`` returns them (the rule drops the last, still incomplete, week)."""
    start = date(2026, 7, 6)
    return pd.DataFrame({"week": [(start + timedelta(weeks=i)).isoformat() for i in range(len(medians))], "median_days": medians, "p90_days": medians, "n": 300})


@pytest.fixture
def series(monkeypatch):
    def use(medians):
        monkeypatch.setattr(clearance, "weekly_dwell", lambda *a, **k: weekly(medians))
    return use


def test_too_little_data_to_project_raises_no_alert(series):
    series([14.0, 13.0, 12.0])                       # 3 weeks (2 once the incomplete week is dropped): nothing to project
    assert rules.r_tgt(None, T, {}, False) == []


def test_a_flat_trend_fires_with_the_fitted_dwell_as_the_metric_never_zero(series):
    series([11.0, 11.1, 10.9, 11.0, 11.05, 10.95, 11.0, 11.0, 11.0])
    (f,) = rules.r_tgt(None, T, {}, False)
    assert f["metric"] == pytest.approx(11.0, abs=0.2) and f["threshold"] == 7.0
    assert "not at all within the projection horizon" in f["details"]["summary"] and "11.0 days now" in f["details"]["summary"]


def test_a_slow_improvement_fires_with_the_projected_date(series):
    series([14.0, 13.8, 13.6, 13.4, 13.2, 13.0, 12.8, 12.6, 12.4])           # about -0.2 days a week: reaches 7 days long after 31 Dec
    (f,) = rules.r_tgt(None, T, {}, False)
    assert 12.5 < f["metric"] < 13.0 and "after the 2026-12-31 target date" in f["details"]["summary"]


def test_a_trend_that_reaches_the_target_in_time_raises_no_alert(series):
    series([15.0, 14.2, 13.4, 12.6, 11.8, 11.0, 10.2, 9.4, 8.6])             # -0.8 days a week: crosses 7 days well before 31 Dec
    assert rules.r_tgt(None, T, {}, False) == []


@pytest.mark.slow
def test_seeded_database_has_no_target_alert_with_a_placeholder_metric(monkeypatch):
    if not REAL.exists():
        pytest.skip("run `make seed` first")
    import sqlite3
    c = sqlite3.connect(f"file:{REAL}?mode=ro", uri=True)
    rows = c.execute("SELECT alert_id, metric_value, status FROM alerts WHERE rule_code='R-TGT-01'").fetchall()
    assert rows and all(m > 0 for _, m, st in rows if st != "dismissed"), rows      # a dismissed record keeps its original placeholder as history
    assert not [r for r in rows if r[2] == "open"], rows                            # the only open one was a transient no-data firing and has been dismissed
    audit = c.execute("SELECT decision, reviewer FROM reviews WHERE target_id='ALT-20261005-00001'").fetchall()
    assert audit == [("dismissed", "System (data correction)")]


@pytest.mark.slow
def test_supervision_panel_shows_na_not_zero_for_the_placeholder_metric(monkeypatch):
    if not REAL.exists():
        pytest.skip("run `make seed` first")
    from streamlit.testing.v1 import AppTest

    from nsw_sim import db
    monkeypatch.setenv("NSW_DB_PATH", str(REAL))
    db.close_reader()
    at = AppTest.from_file(str(REAL.parent.parent / "app" / "screens" / "06_supervision.py"), default_timeout=180)
    at.query_params["alert"] = "ALT-20261005-00001"
    at.run()
    assert not at.exception, [e.value[:200] for e in at.exception]
    text = " ".join(m.value for m in at.markdown)
    assert "n/a (no projection was possible)" in text and "Metric 0 vs" not in text and "dismissed" in text
