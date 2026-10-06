"""A question that quotes a figure ("assessed 12.1% below expectation") is about a specific episode, not the UI period.
Covers: episode detection, the get_alert / shortfall_episodes tools, the question-figure guard in the agent loop, and the Supervision alert lookup."""
import json
from pathlib import Path

import pytest

from nsw_sim import db
from nsw_sim.analytics import queries, reconcile
from nsw_sim.analytics.queries import Filters
from nsw_sim.assistant import tools, verifier
from nsw_sim.assistant.agent import Agent
from nsw_sim.llm.client import ChatResult, StubLLM

AS_OF = "2026-09-30T00:00:00Z"
START, END = "2026-08-31T23:00:00Z", "2026-09-30T00:00:00Z"
REAL = Path(__file__).resolve().parent.parent / "data" / "nsw.db"
ROOT = Path(__file__).resolve().parent.parent


def add_slice(conn, commodity: str, origin: str, short_days: set[int], pct: float = 0.12, days=range(1, 21), per_day: int = 4, entity="NCS", fee="NCS-DUTY"):
    """Four assessments of 1,000,000 kobo expected per day; on ``short_days`` they are assessed ``pct`` below expectation."""
    rows = []
    for d in days:
        for i in range(per_day):
            exp = 1_000_000
            amt = int(exp * (1 - pct)) if d in short_days else exp
            rows.append((f"FA-{commodity}-{origin}-{d:02d}-{i}", f"NSW-{commodity}-{origin}-{d:02d}-{i}", entity, fee, commodity, origin, amt, exp, f"2026-09-{d:02d}T10:00:00Z"))
    conn.executemany("INSERT INTO fee_assessments(assessment_id,nsw_ref,entity_id,fee_code,commodity_group,origin_country,amount_ngn_minor,expected_amount_ngn_minor,occurred_at) "
                     "VALUES(?,?,?,?,?,?,?,?,?)", rows)


@pytest.fixture
def world(conn):
    add_slice(conn, "Electronics", "CN", set(range(8, 14)))                 # the episode: 8-13 Sep, 12% below expectation
    add_slice(conn, "Apparel", "IN", set(), pct=0.0)                        # healthy slice
    add_slice(conn, "Cement", "AA", {2, 3, 5, 6}, pct=0.10)                 # one quiet day (4th) inside the run: still one episode
    add_slice(conn, "Cement", "BB", {2, 3, 6, 7}, pct=0.10)                 # two quiet days between runs: two episodes
    add_slice(conn, "Paper", "DE", {15}, pct=0.30)                          # a single bad day is not an episode
    conn.execute("INSERT INTO alerts(alert_id,rule_code,severity,entity_id,subject,detected_at,window_start,window_end,metric_value,threshold,details_json,status,cleared_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 ("ALT-20260910-00001", "R-FEE-01", "high", "NCS", "NCS-DUTY:Electronics:CN", "2026-09-10T11:00:00Z", "2026-09-07T11:00:00Z", "2026-09-14T11:00:00Z", 0.102, 0.08,
                  json.dumps({"summary": "NCS-DUTY for Electronics from CN is assessed 10.2% below the fee-rule expectation over 3 days (12 assessments).", "n_supporting": 2,
                              "supporting_rows": ["FA-Electronics-CN-08-0", "FA-Electronics-CN-08-1"],
                              "drill": {"view": "assessments", "entity": "NCS", "fee_code": "NCS-DUTY", "commodity": "Electronics", "origin": "CN"}}), "resolved", "2026-09-14T17:00:00Z"))
    conn.execute("INSERT INTO reviews(review_id,target_type,target_id,reviewer,role,decision,comment,created_at) VALUES('r1','alert','ALT-20260910-00001','x','Analyst','acknowledged','','2026-09-14T18:30:00Z')")
    db.close_reader()
    return conn


# ------------------------------------------------------------------ episode detection
def test_episode_covers_exactly_the_run_of_bad_days_and_is_not_diluted(world):
    ep = reconcile.shortfall_episodes(Filters(start=START, end=END), AS_OF)
    e = ep[(ep["commodity_group"] == "Electronics") & (ep["origin_country"] == "CN")].iloc[0]
    assert (e["start_day"], e["end_day"], int(e["days"]), int(e["n"])) == ("2026-09-08", "2026-09-13", 6, 24)
    assert e["shortfall_pct"] == pytest.approx(0.12)
    assert e["shortfall_minor"] == 24 * 120_000
    whole = reconcile.slice_daily("NCS", "NCS-DUTY", "Electronics", "CN", START, END, AS_OF)
    assert (whole["shortfall_minor"].sum() / whole["expected"].sum()) == pytest.approx(0.036)          # 6 of 20 days: the whole period dilutes it


def test_one_quiet_day_is_bridged_two_are_not_and_single_days_are_ignored(world):
    ep = reconcile.shortfall_episodes(Filters(start=START, end=END), AS_OF)
    cement_aa = ep[ep["origin_country"] == "AA"]
    assert len(cement_aa) == 1 and (cement_aa.iloc[0]["start_day"], cement_aa.iloc[0]["end_day"]) == ("2026-09-02", "2026-09-06")
    assert cement_aa.iloc[0]["shortfall_pct"] == pytest.approx(0.10 * 16 / 20)                       # recomputed over the run, including the quiet day
    assert len(ep[ep["origin_country"] == "BB"]) == 2
    assert ep[ep["origin_country"].isin(["IN", "DE"])].empty                                          # healthy slice and a one-day blip


def test_episodes_respect_filters_and_the_data_time(world):
    f = Filters(start=START, end=END, commodities=("Electronics",))
    assert list(reconcile.shortfall_episodes(f, AS_OF)["origin_country"]) == ["CN"]
    early = reconcile.shortfall_episodes(Filters(start=START, end="2026-09-10T00:00:00Z"), "2026-09-10T00:00:00Z", min_days=2)
    assert early[early["origin_country"] == "CN"].iloc[0]["end_day"] == "2026-09-09"                  # nothing from the future leaks in


# ------------------------------------------------------------------ tools
def test_shortfall_episodes_tool_returns_episode_and_whole_period_figures(world):
    r = tools.call("shortfall_episodes", {"period": {"start": START, "end": END}, "filters": {"commodities": ["Electronics"]}}, AS_OF)
    assert r["ok"]
    e = r["data"]["episodes"][0]
    assert (e["first_day"], e["last_day"], e["assessments"], e["shortfall_percent"]) == ("2026-09-08", "2026-09-13", 24, 12.0)
    assert e["whole_period_shortfall_percent"] == pytest.approx(3.6, abs=0.01)


def test_get_alert_returns_any_status_in_wat_with_the_episode(world):
    r = tools.call("get_alert", {"alert_id": " alt-20260910-00001 "}, AS_OF)
    assert r["ok"], r
    d = r["data"]
    assert d["status"] == "resolved" and d["rule"] == "R-FEE-01" and d["detected_at_wat"].endswith("WAT") and "12:00" in d["detected_at_wat"]
    assert d["episode"]["first_day"] == "2026-09-08" and d["episode"]["shortfall_percent"] == 12.0 and d["metric"] == 0.102
    assert d["review_history"][0]["decision"] == "acknowledged"
    assert "Alert board" in d["where_to_find_it"]


def test_get_alert_unknown_id_and_alerts_not_yet_detected(world):
    assert not tools.call("get_alert", {"alert_id": "ALT-NOPE"}, AS_OF)["ok"]
    assert queries.alert_by_id("ALT-20260910-00001", "2026-09-09T00:00:00Z") is None                 # as of before the alert was raised
    assert queries.alert_by_id("ALT-20260910-00001", AS_OF)["status"] == "resolved"


def test_assessment_refs_map_alert_evidence_to_consignments(world):
    assert queries.assessment_refs(["FA-Electronics-CN-08-0", "nope"], AS_OF) == {"FA-Electronics-CN-08-0": "NSW-Electronics-CN-08-0"}


# ------------------------------------------------------------------ the figure quoted in the question
Q = "Electronics from China stood out: duty assessed 12% below expectation. Tell me about it."


def test_question_figures_are_percentages_and_naira_amounts_only():
    assert [c.text.strip() for c in verifier.question_figures("assessed 12.1% below expectation, about ₦24.2m")] == ["₦24.2m", "12.1%"]
    assert verifier.question_figures("Top 5 origins in Q3 2026 over the last 7 days, on 2 October 2026?") == []


def test_whole_period_numbers_do_not_reproduce_the_figure_but_the_episode_does(world):
    period = {"start": START, "end": END}
    whole = [tools.call("aggregate", {"metric": m, "period": period, "filters": {"commodities": ["Electronics"]}}, AS_OF) for m in ("assessed", "expected")]
    assert [c.text for c in verifier.unreconciled_question_figures(Q, whole)] == ["12%"]
    ep = [tools.call("shortfall_episodes", {"period": period}, AS_OF)]
    assert verifier.unreconciled_question_figures(Q, ep) == []


def call(name, **args):
    return lambda m: ChatResult("", [{"id": f"c-{name}", "name": name, "arguments": json.dumps(args)}])


def say(text, seen=None):
    def f(m):
        if seen is not None:
            seen.extend(str(x.get("content")) for x in m if x["role"] == "user")          # every user-role message the model was shown
        return ChatResult(text, [])
    return f


def test_agent_goes_back_and_finds_the_episode_when_the_first_answer_ignores_the_quoted_figure(world):
    period = {"start": START, "end": END}
    prompts: list[str] = []
    llm = StubLLM(chat_script=[call("aggregate", metric="assessed", period=period), say("The shortfall is small over the period.\nBasis: whole period."),
                               call("shortfall_episodes", period=period), say("Over 08–13 Sep the slice was assessed 12.0% below expectation.\nBasis: episode.", prompts)])
    res = Agent(llm, as_of=AS_OF).ask(Q)
    reprompt = next(p for p in prompts if "quotes 12%" in p)
    assert "get_alert" in reprompt and "shortfall_episodes" in reprompt and "not answered what was asked" in reprompt
    assert res.status == "verified" and res.unreconciled == [] and "12.0%" in res.text and "Note:" not in res.text
    assert [t["name"] for t in res.tool_trace] == ["aggregate", "shortfall_episodes"]


def test_agent_never_presents_an_answer_that_ignored_the_users_figure_as_verified(world):
    period = {"start": START, "end": END}
    llm = StubLLM(chat_script=[call("aggregate", metric="assessed", period=period), say("The shortfall is small over the period.\nBasis: whole period."),
                               say("Still just a small shortfall.\nBasis: whole period.")])
    res = Agent(llm, as_of=AS_OF).ask(Q)
    assert res.status == "partial" and res.unreconciled == ["12%"]
    assert "your question quotes 12%" in res.text and "could not reproduce" in res.text


def test_questions_without_a_quoted_figure_are_not_slowed_down_by_the_guard(world):
    llm = StubLLM(chat_script=[call("aggregate", metric="assessed", period={"start": START, "end": END}), say("Assessed was ₦0.00.\nBasis: whole period.")])
    res = Agent(llm, as_of=AS_OF).ask("How much was assessed in September?")
    assert res.rounds == 2 and res.unreconciled == [] and "Note:" not in res.text


# ------------------------------------------------------------------ real data (slow)
@pytest.mark.slow
def test_episode_tool_matches_the_independent_oracle_on_the_seeded_database(monkeypatch):
    if not REAL.exists():
        pytest.skip("run `make seed` first")
    from tests import oracle
    monkeypatch.setenv("NSW_DB_PATH", str(REAL))
    db.close_reader()
    c = oracle.conn()
    want = oracle.o_episode_electronics(c)
    e = tools.call("shortfall_episodes", {"period": "since July"}, oracle.AS_OF)["data"]["episodes"]
    assert len(e) == 1 and (e[0]["entity"], e[0]["commodity"], e[0]["origin_country"]) == ("NCS", "Electronics", "CN")
    assert e[0]["shortfall_percent"] == pytest.approx(want["episode_shortfall_percent"][0], abs=0.01)
    assert e[0]["shortfall"]["naira"] == pytest.approx(want["episode_shortfall_naira"][0], abs=1)
    assert e[0]["whole_period_shortfall_percent"] < 1.0


@pytest.mark.slow
def test_supervision_page_finds_a_resolved_alert_by_id_and_by_link(monkeypatch):
    if not REAL.exists():
        pytest.skip("run `make seed` first")
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv("NSW_DB_PATH", str(REAL))
    db.close_reader()
    page = str(ROOT / "app" / "screens" / "06_supervision.py")
    base = AppTest.from_file(page, default_timeout=180).run()
    assert not base.exception and not any("ALT-20260910-00001 · R-FEE-01" in m.value for m in base.markdown)       # resolved alerts are hidden on the default board
    typed = AppTest.from_file(page, default_timeout=180).run()
    typed.text_input(key="sup_find").set_value("alt-20260910-00001").run()
    linked = AppTest.from_file(page, default_timeout=180)
    linked.query_params["alert"] = "ALT-20260910-00001"
    linked.run()
    for at in (typed, linked):
        assert not at.exception, [e.value[:200] for e in at.exception]
        text = " ".join(m.value for m in at.markdown)
        assert "ALT-20260910-00001 · R-FEE-01" in text and "12.1% below expectation" in text and "08 Sep → 13 Sep" in text
    typed.text_input(key="sup_find").set_value("ALT-NOPE").run()
    assert any("No alert with id ALT-NOPE" in w.value for w in typed.warning)
