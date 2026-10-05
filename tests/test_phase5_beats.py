"""Story beats B1-B10 (Section 7.7): present in the data AND detected, with effect sizes in tolerance.
Runs against the real seeded database (data/nsw.db); skipped if it has not been seeded."""
from pathlib import Path

import pytest

from nsw_sim import clock, db
from nsw_sim.analytics import clearance, queries
from nsw_sim.analytics.queries import Filters

REAL = Path(__file__).resolve().parent.parent / "data" / "nsw.db"
pytestmark = pytest.mark.slow


@pytest.fixture(autouse=True)
def _real_db(monkeypatch):
    if not REAL.exists():
        pytest.skip("run `make seed` first")
    monkeypatch.setenv("NSW_DB_PATH", str(REAL))
    db.close_reader()
    c = db.reader()
    if not db.kv_get(c, "watermark_utc") or clock.sim_day_offset(db.kv_get(c, "watermark_utc")) < 90:
        pytest.skip("database not seeded through the story beats yet")
    yield
    db.close_reader()


def day(offset: float) -> str:
    return clock.iso(clock.sim_start().timestamp() + offset * 86400)


def alerts(rule, **kw):
    c = db.reader()
    rows = c.execute("SELECT alert_id, entity_id, subject, detected_at, metric_value, threshold FROM v_alerts WHERE rule_code=?", (rule,)).fetchall()
    return [r for r in rows if all(kw.get(k) is None or kw[k] in str(r[i]) for k, i in (("entity", 1), ("subject", 2)))]


def in_window(ts, a, b):
    return day(a) <= ts < day(b)


def test_B1_dwell_trend_and_digital_share():
    f = Filters(start=day(0))
    wk = clearance.weekly_dwell(f)
    assert 13.5 <= wk["median_days"].iloc[0] <= 16.0, wk.head(2)
    assert 9.5 <= wk["median_days"].iloc[-2] <= 12.0
    dp = clearance.digital_physical(f)
    assert 0.33 <= dp["digital_share"].iloc[0] <= 0.46 and 0.14 <= dp["digital_share"].iloc[-2] <= 0.26
    assert dp["digital_share"].iloc[0] > dp["digital_share"].iloc[-2] + 0.12
    assert any(a[2] == "dwell-target" for a in alerts("R-TGT-01"))


def test_B2_scanner_outage_apapa_detected_and_effect():
    a = [x for x in alerts("R-PHYS-01") if "NGAPP" in x[2] and in_window(x[3], 42, 47)]
    assert a and a[0][4] >= 1.8
    during = clearance.stage_percentile("S06", Filters(start=day(42), end=day(46), ports=("NGAPP",)), 0.5, "wait_h", queued=True)
    before = clearance.stage_percentile("S06", Filters(start=day(28), end=day(40), ports=("NGAPP",)), 0.5, "wait_h", queued=True)
    assert 2.0 <= during["median"] / before["median"] <= 3.2        # ~2.5x


def test_B3_nafdac_permit_backlog_detected():
    a = [x for x in alerts("R-SLA-01", entity="NAFDAC") if in_window(x[3], 54, 63)]
    assert a, "no NAFDAC SLA alert in 24-31 Aug"
    c = db.reader()

    def med(s, e):
        return c.execute("SELECT AVG(wait_h+dur_h) FROM v_stage_durations WHERE owner_entity='NAFDAC' AND stage='S02' AND status='queued' AND occurred_at>=? AND occurred_at<?", (day(s), day(e))).fetchone()[0]
    assert 1.7 <= med(55, 61) / med(40, 52) <= 2.9


def test_B4_fx_move_about_six_percent():
    c = db.reader()
    r0 = c.execute("SELECT rate_to_ngn FROM v_fx_rates WHERE ccy='USD' AND ts_utc<? ORDER BY ts_utc DESC LIMIT 1", (day(63),)).fetchone()[0]
    r1 = c.execute("SELECT rate_to_ngn FROM v_fx_rates WHERE ccy='USD' AND ts_utc>=? ORDER BY ts_utc LIMIT 1", (day(69),)).fetchone()[0]
    assert 0.045 <= r1 / r0 - 1 <= 0.075 and r1 <= 1650
    assert any(in_window(x[3], 62, 69) for x in alerts("R-FX-01"))


def test_B5_late_remittance_one_regulator_nine_days():
    c = db.reader()
    row = c.execute("SELECT entity_id, days_late FROM v_remittances WHERE entity_id='SON' AND due_date>=? AND due_date<? AND paid_at IS NOT NULL", (day(70), day(72))).fetchone()
    assert row and 8 <= row[1] <= 10
    late = c.execute("SELECT COUNT(*) FROM v_remittances WHERE days_late>3").fetchone()[0]
    assert late == 1, "only the beat-B5 remittance should be more than 3 days late"
    assert [x for x in alerts("R-REM-01", entity="SON") if in_window(x[3], 72, 82)]


def test_B6_under_assessment_cluster():
    c = db.reader()
    q = ("SELECT SUM(expected_amount_ngn_minor), SUM(amount_ngn_minor) FROM v_assessments WHERE fee_code='NCS-DUTY' AND commodity_group='Electronics' "
         "AND origin_country='CN' AND occurred_at>=? AND occurred_at<?")
    ex, ass = c.execute(q, (day(69), day(75))).fetchone()
    assert 0.09 <= (ex - ass) / ex <= 0.15
    ex2, ass2 = c.execute(q, (day(50), day(66))).fetchone()
    assert abs(ex2 - ass2) / ex2 < 0.02
    assert [x for x in alerts("R-FEE-01") if "Electronics" in x[2] and in_window(x[3], 69, 79)]


def test_B7_bank_c_settlement_lag():
    c = db.reader()
    lag = c.execute("SELECT AVG(lag_hours) FROM v_settlement_batches WHERE bank='C' AND closed_at>=? AND closed_at<?", (day(78), day(81))).fetchone()[0]
    base = c.execute("SELECT AVG(lag_hours) FROM v_settlement_batches WHERE bank='C' AND closed_at>=? AND closed_at<?", (day(60), day(75))).fetchone()[0]
    assert lag > 60 and base < 30
    assert [x for x in alerts("R-SET-01") if "Bank C" in x[2] and in_window(x[3], 78, 86)]


def test_B8_duplicate_burst_on_2_oct():
    c = db.reader()
    n = c.execute("SELECT COUNT(*) FROM v_payments WHERE is_duplicate=1 AND occurred_at>=? AND occurred_at<?", (day(93), day(94))).fetchone()[0]
    assert n == 14
    assert [x for x in alerts("R-DUP-01") if in_window(x[3], 93, 94)]


def test_B9_faan_data_gap_detected_and_score_drops():
    from nsw_sim.analytics import quality
    assert [x for x in alerts("R-DQ-01", entity="FAAN") if in_window(x[3], 89, 92)]
    tr = quality.completeness_trend(clock.iso(day(95).__class__ and clock.sim_start().timestamp() + 95 * 86400), 20, ("FAAN",))
    gap = tr[tr["day"].isin([clock.wat_day(day(89.5)), clock.wat_day(day(90.5))])]["completeness"].mean()
    normal = tr[tr["day"] < clock.wat_day(day(88))]["completeness"].mean()
    assert normal - gap > 0.25


def test_B10_holiday_has_low_arrivals():
    c = db.reader()
    def d(off):
        return c.execute("SELECT COUNT(*) FROM v_consignments WHERE manifested_at>=? AND manifested_at<?", (day(off), day(off + 1))).fetchone()[0]
    assert d(92) < 0.6 * (d(91) + d(94)) / 2          # 1 Oct (Independence Day) vs neighbouring weekdays


def test_calibration_monthly_totals_within_bands():
    import yaml
    tg = yaml.safe_load(open(REAL.parent.parent / "config" / "settings.yaml"))["monthly_targets_ngn_bn"]
    tol = 0.15
    for month, (a, b) in {"Aug": (31 + 0, 62), "Sep": (62, 92)}.items():
        mix = queries.aggregate("assessed", ["entity"], Filters(start=day(a), end=day(b)))
        for ent, (lo, hi) in tg.items():
            v = float(mix[mix["entity"] == ent]["value"].iloc[0]) / 1e11
            assert lo * (1 - tol) <= v <= hi * (1 + tol), (month, ent, v, lo, hi)


def test_every_entity_has_data_every_day_from_sim_start():
    c = db.reader()
    days = [r[0] for r in c.execute("SELECT DISTINCT day FROM v_assessments WHERE day>=date(?,'+1 hour') ORDER BY day", (day(0),))]
    assert len(days) >= 94
    for ent in ("NCS", "NRS", "NPA", "NIMASA", "SON", "NAFDAC", "NAQS", "NESREA", "FAAN", "NSW"):
        got = {r[0] for r in c.execute("SELECT DISTINCT date(paid_at,'+1 hour') FROM v_collections WHERE entity_id=?", (ent,))}
        missing = [d for d in days if d not in got]
        assert len(missing) <= 1, (ent, missing)
