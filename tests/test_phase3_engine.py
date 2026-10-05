"""Engine + ledger invariants on a short offline simulation (module-scoped fixture; ~15 s)."""
import os
import tempfile

import pytest

from nsw_sim import clock, db
from nsw_sim.llm.client import StubLLM
from nsw_sim.sim import backfill, ids

DAYS = 34


@pytest.fixture(scope="module")
def sim():
    d = tempfile.mkdtemp()
    old = {k: os.environ.get(k) for k in ("NSW_DB_PATH", "NSW_LLM_CACHE", "NSW_DATA_DIR", "NSW_OFFLINE")}
    os.environ.update(NSW_DB_PATH=d + "/s.db", NSW_LLM_CACHE=d + "/c.sqlite", NSW_DATA_DIR=d, NSW_OFFLINE="1")
    conn = db.connect()
    db.init_db(conn)
    llm = StubLLM(offline=True)
    llm.concurrency = 2
    until = clock.engine_start().timestamp() + DAYS * 86400
    eng = backfill.run_backfill(conn, llm, until)
    yield conn, eng, until
    conn.close()
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def q(conn, sql, *a):
    return conn.execute(sql, a).fetchall()


def test_every_journal_entry_balances_and_trial_balance_is_zero(sim):
    conn, *_ = sim
    assert q(conn, "SELECT SUM(debit_minor)-SUM(credit_minor) FROM journal_lines")[0][0] == 0
    assert q(conn, "SELECT COUNT(*) FROM (SELECT entry_id FROM journal_lines GROUP BY entry_id HAVING SUM(debit_minor)!=SUM(credit_minor))")[0][0] == 0
    assert q(conn, "SELECT COUNT(*) FROM journal_lines WHERE debit_minor<0 OR credit_minor<0")[0][0] == 0


def test_balance_sheet_balances_per_entity(sim):
    conn, *_ = sim
    rows = q(conn, """SELECT l.entity_id, c.class, SUM(l.debit_minor)-SUM(l.credit_minor) FROM journal_lines l
                      JOIN chart_of_accounts c ON c.entity_id=l.entity_id AND c.account_code=l.account_code GROUP BY 1,2""")
    net: dict[str, dict] = {}
    for ent, cls, v in rows:
        net.setdefault(ent, {})[cls] = v
    for ent, d in net.items():
        # Assets = Liabilities + Equity + (Revenue - Expense)  <=>  sum of all debit-credit = 0
        assert sum(d.values()) == 0, (ent, d)
        assert d.get("Asset", 0) >= 0 or ent in ("MOF-FA",), (ent, d)


def test_no_future_dated_facts(sim):
    conn, eng, until = sim
    wm = clock.iso(until)
    for tbl, col in [("stage_events", "occurred_at"), ("fee_assessments", "occurred_at"), ("payments", "occurred_at"),
                     ("payment_allocations", "paid_at"), ("payment_allocations", "settled_at"), ("settlements", "occurred_at"),
                     ("settlement_batches", "closed_at"), ("settlement_batches", "completed_at"), ("journal_entries", "occurred_at"),
                     ("expenses", "occurred_at"), ("live_events", "occurred_at"), ("remittances", "occurred_at"), ("remittances", "paid_at"),
                     ("consignments", "manifested_at"), ("consignments", "arrived_at"), ("consignments", "gate_out_at")]:
        assert q(conn, f"SELECT COUNT(*) FROM {tbl} WHERE {col} > ?", wm)[0][0] == 0, (tbl, col)
    assert db.kv_get(conn, "watermark_utc") == wm


def test_watermark_and_idempotent_rerun(sim):
    conn, eng, until = sim
    before = {t: q(conn, f"SELECT COUNT(*) FROM {t}")[0][0] for t in ("consignments", "journal_lines", "payments", "stage_events")}
    llm = StubLLM(offline=True)
    llm.concurrency = 2
    backfill.run_backfill(conn, llm, until)             # nothing to do: watermark already at `until`
    after = {t: q(conn, f"SELECT COUNT(*) FROM {t}")[0][0] for t in before}
    assert before == after


def test_every_entity_has_data_every_day_after_ramp_up(sim):
    conn, *_ = sim
    days = [r[0] for r in q(conn, "SELECT DISTINCT day FROM rollup_day WHERE metric='paid' AND day>=date(?,'+12 day') ORDER BY day",
                            clock.wat_day(clock.engine_start()))]
    assert len(days) >= 18
    for ent in ("NCS", "NRS", "NPA", "NSW", "NIMASA", "SON", "NAFDAC"):
        got = {r[0] for r in q(conn, "SELECT day FROM rollup_day WHERE metric='paid' AND dim='ALL' AND entity_id=?", ent)}
        assert set(days) <= got, (ent, sorted(set(days) - got))


def test_timestamps_monotonic_per_consignment(sim):
    conn, *_ = sim
    bad = q(conn, """SELECT COUNT(*) FROM consignments WHERE (arrived_at IS NOT NULL AND arrived_at < manifested_at)
                     OR (declared_at IS NOT NULL AND declared_at < arrived_at) OR (released_at IS NOT NULL AND released_at < declared_at)
                     OR (gate_out_at IS NOT NULL AND gate_out_at < released_at)""")
    assert bad[0][0] == 0
    # per stage: ready <= started <= occurred (for complete rows)
    assert q(conn, "SELECT COUNT(*) FROM stage_events WHERE data_complete=1 AND status IN ('done','manual') AND (started_at<ready_at OR occurred_at<started_at)")[0][0] == 0


def test_dwell_decomposes_exactly_into_digital_plus_physical(sim):
    conn, *_ = sim
    assert q(conn, "SELECT MAX(ABS(dwell_h-digital_h-physical_h)) FROM consignments WHERE gate_out_at IS NOT NULL")[0][0] < 1e-6
    assert q(conn, "SELECT COUNT(*) FROM consignments WHERE gate_out_at IS NOT NULL")[0][0] > 1000


def test_money_chain_invariants(sim):
    conn, *_ = sim
    # sum of allocations = payment amount (for applied payments)
    assert q(conn, """SELECT COUNT(*) FROM payments p WHERE p.status='confirmed' AND p.nsw_ref NOT LIKE 'UNMATCHED%' AND p.amount_ngn_minor !=
                      (SELECT SUM(amount_ngn_minor) FROM payment_allocations a WHERE a.payment_id=p.payment_id)""")[0][0] == 0
    # settled <= paid per allocation; settled + cost == paid
    assert q(conn, "SELECT COUNT(*) FROM payment_allocations WHERE settled_at IS NOT NULL AND settled_ngn_minor+collection_cost_ngn_minor != amount_ngn_minor")[0][0] == 0
    # NGN-denominated fees: paid == assessed; foreign currency: within 5%
    assert q(conn, """SELECT COUNT(*) FROM payment_allocations a JOIN fee_assessments f ON f.assessment_id=a.assessment_id
                      WHERE a.currency='NGN' AND a.amount_ngn_minor != f.amount_ngn_minor""")[0][0] == 0
    assert q(conn, """SELECT COUNT(*) FROM payment_allocations a JOIN fee_assessments f ON f.assessment_id=a.assessment_id
                      WHERE a.currency!='NGN' AND ABS(a.amount_ngn_minor-f.amount_ngn_minor) > 0.05*f.amount_ngn_minor""")[0][0] == 0
    # settlement rows tie to allocations
    assert q(conn, """SELECT COUNT(*) FROM settlements s WHERE s.amount_ngn_minor != (SELECT SUM(amount_ngn_minor) FROM payment_allocations a
                      WHERE a.payment_id=s.payment_id AND a.entity_id=s.entity_id)""")[0][0] == 0
    # fee assessments never exceed what has been paid + outstanding: paid per assessment <= assessed*1.05
    assert q(conn, """SELECT COUNT(*) FROM (SELECT a.assessment_id, SUM(a.amount_ngn_minor) p, f.amount_ngn_minor am FROM payment_allocations a
                      JOIN fee_assessments f USING(assessment_id) GROUP BY a.assessment_id HAVING p > am*1.05)""")[0][0] == 0


def test_ids_unique_and_formats(sim):
    conn, *_ = sim
    assert q(conn, "SELECT COUNT(*)-COUNT(DISTINCT payment_ref) FROM payments")[0][0] == 0
    assert q(conn, "SELECT COUNT(*)-COUNT(DISTINCT container_no) FROM consignments WHERE container_no IS NOT NULL")[0][0] == 0
    for (c,) in q(conn, "SELECT container_no FROM consignments WHERE container_no IS NOT NULL ORDER BY RANDOM() LIMIT 300"):
        assert ids.valid_container(c), c
    for (v,) in q(conn, "SELECT DISTINCT vessel_imo FROM consignments WHERE vessel_imo IS NOT NULL"):
        assert ids.valid_imo(v), v
    import re
    for (r,) in q(conn, "SELECT nsw_ref FROM consignments LIMIT 200"):
        assert re.fullmatch(r"NSW-\d{6}-[A-Z]{3,5}-\d{7}", r)
    for (e,) in q(conn, "SELECT entry_id FROM journal_entries ORDER BY RANDOM() LIMIT 100"):
        assert re.fullmatch(r"JE-[A-Z\-]+-\d{8}-\d{6}", e)
    for (p,) in q(conn, "SELECT payment_ref FROM payments LIMIT 100"):
        assert re.fullmatch(r"\d{12}", p)


def test_each_beat_that_has_occurred_is_in_the_data_or_deferred(sim):
    """Within the 34-day window only the warm-up exists (beats start 28 days later); B1 progress starts at 0."""
    conn, eng, until = sim
    assert eng.cond.progress(until) < 0.1


def test_resume_from_inflight_matches_uninterrupted_run():
    """Stop mid-run, rebuild the engine from the database, continue: the result equals one uninterrupted run."""
    results = []
    for split in (False, True):
        d = tempfile.mkdtemp()
        os.environ.update(NSW_DB_PATH=d + "/r.db", NSW_LLM_CACHE=d + "/c.sqlite", NSW_DATA_DIR=d, NSW_OFFLINE="1")
        conn = db.connect()
        db.init_db(conn)
        llm = StubLLM(offline=True)
        llm.concurrency = 2
        t0 = clock.engine_start().timestamp()
        if split:
            backfill.run_backfill(conn, llm, t0 + 9 * 86400)
            conn.close()
            conn = db.connect()
        backfill.run_backfill(conn, llm, t0 + 14 * 86400)
        results.append((q(conn, "SELECT COUNT(*), SUM(amount_ngn_minor) FROM fee_assessments")[0],
                        q(conn, "SELECT COUNT(*), SUM(amount_ngn_minor) FROM payments")[0],
                        q(conn, "SELECT COUNT(*) FROM stage_events")[0],
                        q(conn, "SELECT COUNT(*), SUM(debit_minor) FROM journal_lines")[0]))
        conn.close()
    assert results[0] == results[1]


def test_determinism_same_seed_same_facts():
    outs = []
    for _ in range(2):
        d = tempfile.mkdtemp()
        os.environ.update(NSW_DB_PATH=d + "/d.db", NSW_LLM_CACHE=d + "/c.sqlite", NSW_DATA_DIR=d, NSW_OFFLINE="1")
        conn = db.connect()
        db.init_db(conn)
        llm = StubLLM(offline=True)
        llm.concurrency = 2
        backfill.run_backfill(conn, llm, clock.engine_start().timestamp() + 8 * 86400)
        outs.append(q(conn, "SELECT nsw_ref, cif_value_ngn_minor, origin_country FROM consignments ORDER BY nsw_ref LIMIT 300")
                    + q(conn, "SELECT SUM(amount_ngn_minor) FROM fee_assessments"))
        conn.close()
    assert outs[0] == outs[1]
