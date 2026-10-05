"""LLM roles are proven with deliberately bad outputs: clamp, repair, fallback; and offline determinism."""
import json
from datetime import date

from nsw_sim.llm.client import StubLLM
from nsw_sim.llm.schemas import DirectorOutput, EntityProfile, FlowPlan, OpsPlan
from nsw_sim.sim import fallback as fb
from nsw_sim.sim import planners as pl
from nsw_sim.sim.reference import entity_codes

WEEK = date(2026, 7, 6)


def _flow_json(**over):
    p = fb.fallback_flow_plan(WEEK).model_dump()
    p.update(over)
    return json.dumps(p)


def test_all_twelve_fallback_profiles_are_complete_and_valid():
    for code in entity_codes():
        p = fb.fallback_profile(code)
        assert isinstance(p, EntityProfile) and p.entity == code
        assert set(p.country_sensitivity) == set(pl.ref()["countries"])
        assert p.expense_structure is not None and abs(sum(c.share for c in p.expense_structure.categories) - 1) < 1e-6
        EntityProfile.model_validate_json(p.model_dump_json())


def test_profile_out_of_band_values_are_clamped_not_trusted():
    base = fb.fallback_profile("NCS").model_dump()
    bad = json.loads(json.dumps(base))
    bad["fee_rules"][0]["rate_by_group"] = {k: v * 50 for k, v in bad["fee_rules"][0]["rate_by_group"].items()}
    bad["collection_cost_rate"] = 0.9
    bad["country_sensitivity"]["CN"]["fee_multiplier"] = 7
    bad["remittance"]["share"] = 0.1
    bad["remittance"]["basis"] = "none"          # structural fields cannot be changed by the model
    stub = StubLLM(handlers={"profile": lambda s, u: json.dumps(bad)})
    res = pl.build_profile(stub, "NCS")
    assert res.source == "llm" and res.clamped >= 4
    p = res.obj
    assert p.collection_cost_rate <= base["collection_cost_rate"] * 1.4 + 1e-9
    assert p.country_sensitivity["CN"].fee_multiplier == 1.2
    assert p.remittance.basis == "net_settled" and p.remittance.share >= 0.90 - 1e-9
    duty = next(r for r in p.fee_rules if r.fee_code == "NCS-DUTY")
    assert duty.rate_by_group["Vehicles & parts"] <= 0.20 * 1.5 + 1e-9
    assert duty.revenue_account == "4110"


def test_garbage_output_uses_fallback_and_is_marked():
    stub = StubLLM(handlers={"profile": lambda s, u: "not json at all"})
    res = pl.build_profile(stub, "SON")
    assert res.source == "fallback" and res.outcome == "fallback"
    assert res.obj.entity == "SON" and res.obj.fee_rules[0].revenue_account == "4110"
    assert len(stub.calls) == 2                          # original + exactly one repair call


def test_repair_path_succeeds_on_second_call():
    calls = {"n": 0}

    def handler(system, user):
        calls["n"] += 1
        if calls["n"] == 1:
            return _flow_json(daily_multiplier=[1, 1, 1])        # wrong length -> hard error -> repair
        assert "failed validation" in user and "7 values" in user
        return _flow_json()
    res = pl.build_flow_plan(StubLLM(handlers={"flow_plan": handler}), WEEK, None)
    assert res.source == "llm" and res.outcome == "repaired" and calls["n"] == 2


def test_shares_must_sum_to_one_then_normalised():
    bad = fb.fallback_flow_plan(WEEK).origin_share
    bad = {k: v * 0.5 for k, v in bad.items()}               # sums to 0.5 -> repair, then fallback
    res = pl.build_flow_plan(StubLLM(handlers={"flow_plan": lambda s, u: _flow_json(origin_share=bad)}), WEEK, None)
    assert res.source == "fallback"
    near = {k: v * 1.005 for k, v in fb.fallback_flow_plan(WEEK).origin_share.items()}   # within ±0.01: normalised
    res = pl.build_flow_plan(StubLLM(handlers={"flow_plan": lambda s, u: _flow_json(origin_share=near)}), WEEK, None)
    assert res.source == "llm" and abs(sum(res.obj.origin_share.values()) - 1) < 1e-9


def test_flow_plan_counts_and_fx_clamped_to_bounds():
    res = pl.build_flow_plan(StubLLM(handlers={"flow_plan": lambda s, u: _flow_json(
        daily_multiplier=[9, 0.01, 1, 1, 1, 1, 1], fx_path=[1000, 3000, 1500, 1500, 1500, 1500, 1500])}), WEEK, None)
    p: FlowPlan = res.obj
    assert p.daily_multiplier[0] == 3.0 and p.daily_multiplier[1] == 0.3
    assert all(1450 <= x <= 1560 for x in p.fx_path)
    assert all(abs(b / a - 1) <= 0.0061 for a, b in zip(p.fx_path, p.fx_path[1:]))


def test_ops_plan_bounded_by_profile():
    prof = fb.fallback_profile("NAFDAC")
    raw = fb.fallback_ops_plan("NAFDAC", WEEK).model_dump()
    raw["expense_multipliers"] = {"Personnel": 40, "Made up category": 2}
    raw["refunds"] = [{"day": 2, "fee_code": "FAKE-CODE", "amount_factor": 5}, {"day": 9, "fee_code": "NAFDAC-PERMIT", "amount_factor": 5}]
    raw["partner_receipts"] = [{"day": 1, "facility": "Some Real Bank", "partner_country": "XX", "amount_factor": 99}]
    res = pl.build_ops_plan(StubLLM(handlers={"ops_plan": lambda s, u: json.dumps(raw)}), "NAFDAC", WEEK, prof)
    p: OpsPlan = res.obj
    assert p.expense_multipliers == {"Personnel": 1.6}
    assert [r.fee_code for r in p.refunds] == ["NAFDAC-PERMIT"] and p.refunds[0].amount_factor == 1.0 and p.refunds[0].day == 6
    assert p.partner_receipts[0].facility == prof.partner_funding[0].facility_name
    assert p.partner_receipts[0].partner_country in pl.settings_partner_countries()


def test_director_feed_items_with_numbers_are_dropped_and_directives_clamped():
    out = {"schema_version": 1, "directives": {"arrival_multiplier": 9, "scanner_offline": {"NGAPP": 99, "XXX": 1},
                                               "payment_failure_rate": 0.9, "settlement_lag_change_h": {"C": 500}},
           "feed_items": [{"headline": "Collections hit ₦5bn today", "description": "x", "type": "nsw.payment.confirmed"},
                          {"headline": "Queue easing at Apapa", "description": "Examination waits are shortening.", "severity": "low",
                           "entity": "NCS", "type": "bogus.type"}]}
    from nsw_sim.llm import prompts as P
    from nsw_sim.llm.validate import run_json_role
    res = run_json_role(StubLLM(handlers={"director": lambda s, u: json.dumps(out)}), "director", P.DIRECTOR_SYSTEM, "ctx",
                        DirectorOutput, pl.check_director({"NGAPP": 4}), lambda: fb.fallback_director("x"))
    d = res.obj
    assert d.directives.arrival_multiplier == 2.0 and d.directives.payment_failure_rate == 0.15
    assert d.directives.scanner_offline == {"NGAPP": 3} and d.directives.settlement_lag_change_h == {"C": 72.0}
    assert len(d.feed_items) == 1 and d.feed_items[0].type == "ops.congestion.alert"


def test_offline_ensure_plans_is_deterministic_and_idempotent(conn):
    from nsw_sim.sim.reference import seed_reference
    seed_reference(conn)
    stub = StubLLM(offline=True)
    stub.concurrency = 2
    profiles = pl.ensure_profiles(conn, stub)
    assert len(profiles) == 12 and conn.execute("SELECT COUNT(DISTINCT source) FROM entity_profiles").fetchone()[0] == 1
    weeks = pl.weeks_between(date(2026, 6, 1), date(2026, 6, 28))
    s1 = pl.ensure_plans(conn, stub, profiles, weeks)
    first = conn.execute("SELECT plan_json FROM weekly_plans WHERE kind='ops' AND entity_id='NCS' AND week_start='2026-06-08'").fetchone()[0]
    s2 = pl.ensure_plans(conn, stub, profiles, weeks)
    assert s1 == s2 and s1["flow_fallback"] == 4 and s1["ops_fallback"] == 4 * 10
    assert conn.execute("SELECT COUNT(*) FROM weekly_plans").fetchone()[0] == 4 + 40
    assert conn.execute("SELECT plan_json FROM weekly_plans WHERE kind='ops' AND entity_id='NCS' AND week_start='2026-06-08'").fetchone()[0] == first
    assert pl.estimate(conn, weeks, stub)["total_calls"] == 0
