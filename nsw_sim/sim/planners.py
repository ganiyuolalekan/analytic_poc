"""LLM roles that produce *plans* (never rows): profile builder, weekly flow planner, weekly entity ops planner,
plus the live director check. Each goes through validate->clamp->repair->fallback and is cached in the database."""
from __future__ import annotations

import json
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from typing import Callable

from nsw_sim import clock
from nsw_sim.config import get_logger, settings, yaml_config
from nsw_sim.llm import prompts as P
from nsw_sim.llm.schemas import (CountrySens, DirectorOutput, Directives, EntityProfile, ExpenseCategory, ExpenseStructure,
                                 FeedItem, FlowPlan, OpsPlan, PartnerFunding)
from nsw_sim.llm.validate import Clamp, RoleResult, normalise_shares, run_json_role
from nsw_sim.sim import fallback as fb
from nsw_sim.sim.reference import entity_cards, entity_codes, ref

log = get_logger("nsw.planners")
EVENT_TYPES = set(yaml_config("nsw_formats")["event_types"])


# =============================================================================== profile
def _clamp_rule_numbers(rule, base, c: Clamp) -> None:
    if base.rate > 0:
        rule.rate = c.val(rule.rate, base.rate * 0.6, base.rate * 1.5)
    else:
        rule.rate = base.rate
    if base.rate_by_group:
        rule.rate_by_group = {g: c.val((rule.rate_by_group or {}).get(g, v), v * 0.6, v * 1.5) for g, v in base.rate_by_group.items()}
    else:
        rule.rate_by_group = None
    rule.amount = c.val(rule.amount, base.amount * 0.6, base.amount * 1.5) if base.amount else base.amount
    rule.p = min(1.0, c.val(rule.p, base.p * 0.5, base.p * 1.5)) if base.p < 1 else 1.0


def check_profile(code: str) -> Callable[[EntityProfile], tuple[EntityProfile, list[str], int]]:
    base = fb.fallback_profile(code)

    def check(p: EntityProfile) -> tuple[EntityProfile, list[str], int]:
        c, errors = Clamp(), []
        if p.entity != code:
            errors.append(f"entity must be {code}")
        if base.fee_rules and not p.fee_rules:
            errors.append("fee_rules must not be empty")
        out = base.model_copy(deep=True)
        # fee rules: structure (basis/currency/conditions/stage) always from the baseline, numbers/names from the model
        by_code = {r.fee_code: r for r in p.fee_rules}
        for i, brule in enumerate(out.fee_rules):
            m = by_code.get(brule.fee_code)
            if m is not None:
                name = re.sub(r"\s+", " ", m.name).strip()[:80]
                if name:
                    brule.name = name if "simulated" in name.lower() else f"{name} (simulated)"
                _clamp_rule_numbers_into(brule, m, c)
            brule.revenue_account = str(4100 + 10 * (i + 1))
        # country sensitivity
        for iso2 in ref()["countries"]:
            m = p.country_sensitivity.get(iso2)
            if m is None:
                continue
            b = out.country_sensitivity[iso2]
            out.country_sensitivity[iso2] = CountrySens(
                fee_multiplier=c.val(m.fee_multiplier, 0.85, 1.20), inspection_rate_modifier=c.val(m.inspection_rate_modifier, 0.6, 1.8),
                doc_issue_rate=c.val(m.doc_issue_rate, 0.005, 0.18), dwell_modifier=c.val(m.dwell_modifier, 0.8, 1.3))
            del b
        # partner funding (facility names generic; amounts bounded)
        if base.partner_funding and p.partner_funding:
            pf = []
            for m in p.partner_funding[:2]:
                b0 = base.partner_funding[0]
                if m.partner_country not in settings_partner_countries():
                    c.n += 1
                    m.partner_country = b0.partner_country
                nm = m.facility_name if "(simulated)" in m.facility_name.lower() else f"{m.facility_name} (Simulated)"
                pf.append(PartnerFunding(facility_name=nm[:80], partner_country=m.partner_country, instrument=m.instrument,
                                         amount_ngn_per_quarter=c.val(m.amount_ngn_per_quarter, b0.amount_ngn_per_quarter * 0.5,
                                                                      b0.amount_ngn_per_quarter * 1.6), purpose=m.purpose[:100]))
            out.partner_funding = pf or out.partner_funding
        # expenses
        if base.expense_structure and p.expense_structure and base.expense_structure.opex_ratio > 0:
            bs, ms = base.expense_structure, p.expense_structure
            names = {x.name for x in bs.categories}
            cats = [x for x in ms.categories if x.name in names]
            shares_ok = len(cats) == len(names) and 0.9 <= sum(x.share for x in cats) <= 1.1
            if shares_ok:
                tot = sum(x.share for x in cats)
                new_cats = [ExpenseCategory(name=x.name, share=x.share / tot, monthly_growth=c.val(x.monthly_growth, 0.0, 0.01)) for x in cats]
            else:
                c.n += 1
                new_cats = bs.categories
            out.expense_structure = ExpenseStructure(
                opex_ratio=c.val(ms.opex_ratio, bs.opex_ratio * 0.8, bs.opex_ratio * 1.2), categories=new_cats,
                appropriation_coverage=c.val(ms.appropriation_coverage, max(0.0, bs.appropriation_coverage - 0.1),
                                             min(1.0, bs.appropriation_coverage + 0.1)))
        out.collection_cost_rate = c.val(p.collection_cost_rate, base.collection_cost_rate * 0.6, base.collection_cost_rate * 1.4) \
            if base.collection_cost_rate else 0.0
        if base.remittance.basis != "none":
            out.remittance.share = c.val(p.remittance.share, max(0.0, base.remittance.share - 0.05), min(1.0, base.remittance.share + 0.05))
            out.remittance.due_day = int(c.val(p.remittance.due_day, 8, 12))
            out.remittance.typical_delay_days = [int(c.val(x, -3, 4)) for x in p.remittance.typical_delay_days[:8]] or base.remittance.typical_delay_days
        for k, bv in base.sla_hours.items():
            if k in p.sla_hours:
                out.sla_hours[k] = c.val(p.sla_hours[k], bv * 0.8, bv * 1.3)
        out.processing_speed_factor = c.val(p.processing_speed_factor, 0.8, 1.25)
        bo = base.onboarding
        s0 = c.val(p.onboarding.digital_share_start, max(0.5, bo.digital_share_start - 0.05), min(0.995, bo.digital_share_start + 0.05))
        s1 = c.val(p.onboarding.digital_share_end, max(0.5, bo.digital_share_end - 0.05), min(0.995, bo.digital_share_end + 0.05))
        out.onboarding.digital_share_start, out.onboarding.digital_share_end = s0, max(s0, s1)
        out.onboarding.completeness = c.val(p.onboarding.completeness, 0.95, 0.995)
        out.seasonality = {str(m): c.val(p.seasonality.get(str(m), base.seasonality[str(m)]), 0.85, 1.15) for m in range(1, 13)}
        quirks = [re.sub(r"\s+", " ", q).strip()[:140] for q in p.quirks if isinstance(q, str) and q.strip()]
        out.quirks = quirks[:5] or base.quirks
        return out, errors, c.n

    return check


def _clamp_rule_numbers_into(rule, m, c: Clamp) -> None:
    """Apply the model's numbers onto the baseline rule within bands."""
    base = rule.model_copy()
    if base.rate > 0:
        rule.rate = c.val(m.rate or base.rate, base.rate * 0.6, base.rate * 1.5)
    if base.rate_by_group:
        rule.rate_by_group = {g: c.val((m.rate_by_group or {}).get(g, v), v * 0.6, v * 1.5) for g, v in base.rate_by_group.items()}
    if base.amount:
        rule.amount = c.val(m.amount or base.amount, base.amount * 0.6, base.amount * 1.5)
    if base.p < 1:
        rule.p = min(1.0, c.val(m.p, base.p * 0.5, base.p * 1.5))


def settings_partner_countries() -> set[str]:
    return set(ref()["partner_countries"])


def build_profile(llm, code: str, refresh: bool = False) -> RoleResult:
    card = entity_cards()[code]
    base = fb.fallback_profile(code)
    brief = {"fee_rules": [{k: v for k, v in r.model_dump().items() if k in (
        "fee_code", "name", "basis", "rate", "rate_by_group", "amount", "currency", "p")} for r in base.fee_rules],
        "partner_funding": [x.model_dump() for x in base.partner_funding],
        "expense_structure": base.expense_structure.model_dump() if base.expense_structure else None,
        "collection_cost_rate": base.collection_cost_rate, "remittance": base.remittance.model_dump(),
        "sla_hours": base.sla_hours, "country_baseline_sample": {k: base.country_sensitivity[k].model_dump() for k in ("CN", "NL", "GH")}}
    user = P.profile_user(code, card, brief, list(ref()["countries"]), list(ref()["commodity_groups"]),
                          settings()["monthly_targets_ngn_bn"].get(code))
    return run_json_role(llm, "profile", P.PROFILE_SYSTEM, user, EntityProfile, check_profile(code),
                         lambda: _finalise_fallback(code), refresh=refresh, schema_version=P.PROMPT_VERSION)


def _finalise_fallback(code: str) -> EntityProfile:
    p = fb.fallback_profile(code).model_copy(deep=True)
    for i, r in enumerate(p.fee_rules):
        r.revenue_account = str(4100 + 10 * (i + 1))
    return p


def ensure_profiles(conn: sqlite3.Connection, llm, *, reseed: bool = False, progress: Callable[[str], None] | None = None) -> dict[str, EntityProfile]:
    """Build (or load) the 12 entity profiles. Cached in ``entity_profiles``; regenerate only with ``reseed``."""
    out: dict[str, EntityProfile] = {}
    todo = []
    for code in entity_codes():
        row = conn.execute("SELECT profile_json, source FROM entity_profiles WHERE entity_id=?", (code,)).fetchone()
        if row and not reseed and not (row[1] == "fallback" and not llm.offline):
            out[code] = EntityProfile.model_validate_json(row[0])
        else:
            todo.append(code)
    if todo:
        with ThreadPoolExecutor(max_workers=getattr(llm, "concurrency", 6)) as ex:
            futs = {ex.submit(build_profile, llm, c, reseed): c for c in todo}
            for f in as_completed(futs):
                code, res = futs[f], f.result()
                prof = res.obj
                out[code] = prof
                conn.execute("INSERT OR REPLACE INTO entity_profiles(entity_id,profile_json,source,model,version,created_at) "
                             "VALUES(?,?,?,?,?,?)", (code, prof.model_dump_json(), res.source, res.model or "", 1, clock.iso(clock.utcnow())))
                conn.execute("UPDATE entities SET profile_json=?, profile_source=? WHERE entity_id=?", (prof.model_dump_json(), res.source, code))
                _log_run(conn, "profile", code, "-", "-", res)
                if progress:
                    progress(f"profile {code}: {res.source} ({res.outcome}, clamped={res.clamped})")
    return {c: out[c] for c in entity_codes()}      # canonical order: the engine's random draws depend on it


# =============================================================================== flow plan
def beats_for_week(week_start: date) -> list[dict]:
    """Scenario beats that touch this week, as week-relative day indexes (for the prompts)."""
    sim0 = clock.sim_start()
    out = []
    ws0 = clock.wat_midnight_utc(week_start)
    for bid, b in yaml_config("scenario")["beats"].items():
        d0 = (sim0 + timedelta(days=b["start_day"]) - ws0).days
        d1 = (sim0 + timedelta(days=b["end_day"]) - ws0).days
        if d1 > 0 and d0 < 7:
            out.append({"beat": bid, "name": b["name"], "from_day": max(d0, 0), "to_day": min(d1, 7)})
    return out


def check_flow(week_start: date) -> Callable[[FlowPlan], tuple[FlowPlan, list[str], int]]:
    tol = settings()["plausibility"]["share_sum_tolerance"]
    lo, hi = settings()["plausibility"]["count_multiplier_range"]
    cfg = ref()

    def check(p: FlowPlan) -> tuple[FlowPlan, list[str], int]:
        c, errors = Clamp(), []
        if len(p.daily_multiplier) != 7:
            errors.append("daily_multiplier must have 7 values (Mon..Sun)")
        if len(p.hourly_shape) != 24:
            errors.append("hourly_shape must have 24 values")
        if len(p.fx_path) != 7:
            errors.append("fx_path must have 7 values")
        sea, air = set(cfg["ports"]["sea"]), set(cfg["ports"]["air"])
        port = {k: v for k, v in p.port_share.items() if k in sea | air}
        sea_p = normalise_shares({k: v for k, v in port.items() if k in sea}, sea, tol * 2, "port_share(sea ports)", errors)
        air_p = normalise_shares({k: v for k, v in port.items() if k in air}, air, tol * 2, "port_share(air ports)", errors)
        mode = normalise_shares(p.mode_share, {"sea", "air"}, tol, "mode_share", errors)
        origin = normalise_shares(p.origin_share, set(cfg["countries"]), tol, "origin_share", errors)
        comm = normalise_shares(p.commodity_share, set(cfg["commodity_groups"]), tol, "commodity_share", errors)
        if errors:
            return p, errors, c.n
        fx = []
        prev = None
        for v in p.fx_path:
            v = c.val(v, 1450.0, 1560.0)
            if prev is not None:
                v = c.val(v, prev * 0.994, prev * 1.006)
            fx.append(round(v, 2))
            prev = v
        sea_share = c.val(mode.get("sea", 0.8), 0.70, 0.88)
        clean = FlowPlan(
            week_start=week_start.isoformat(), daily_multiplier=[round(c.val(x, lo, hi), 3) for x in p.daily_multiplier],
            port_share={**sea_p, **air_p}, mode_share={"sea": sea_share, "air": 1 - sea_share}, origin_share=origin,
            commodity_share=comm, hourly_shape=[round(c.val(x, 0.02, 3.0), 3) for x in p.hourly_shape], fx_path=fx,
            value_multiplier=c.val(p.value_multiplier, 0.85, 1.15), expected_incidents=p.expected_incidents[:5],
            narrative=re.sub(r"\s+", " ", p.narrative)[:300])
        return clean, [], c.n

    return check


def build_flow_plan(llm, week_start: date, prev_fx: float | None, refresh: bool = False) -> RoleResult:
    base = fb.fallback_flow_plan(week_start)
    brief = {"port_share": base.port_share, "origin_share": {k: round(v, 3) for k, v in base.origin_share.items()},
             "commodity_share": {k: round(v, 3) for k, v in base.commodity_share.items()}, "mode_share": base.mode_share}
    user = P.flow_user(week_start.isoformat(), beats_for_week(week_start), {}, prev_fx, brief)
    fx_start = fb.fallback_fx_path(week_start, prev_fx)
    return run_json_role(llm, "flow_plan", P.FLOW_SYSTEM.format(date=week_start.isoformat()), user, FlowPlan,
                         check_flow(week_start), lambda: base.model_copy(update={"fx_path": fx_start}),
                         refresh=refresh, schema_version=P.PROMPT_VERSION)


# =============================================================================== ops plan
def check_ops(code: str, week_start: date, prof: EntityProfile) -> Callable[[OpsPlan], tuple[OpsPlan, list[str], int]]:
    cats = {x.name for x in prof.expense_structure.categories} if prof.expense_structure else set()
    facilities = {x.facility_name for x in prof.partner_funding}
    fee_codes = {r.fee_code for r in prof.fee_rules}

    def check(p: OpsPlan) -> tuple[OpsPlan, list[str], int]:
        c, errors = Clamp(), []
        if len(p.expense_day_weights) != 7 or sum(max(0.0, x) for x in p.expense_day_weights) <= 0:
            errors.append("expense_day_weights must be 7 non-negative numbers with a positive sum")
        if errors:
            return p, errors, c.n
        out = p.model_copy(deep=True)
        out.entity, out.week_start = code, week_start.isoformat()
        out.expense_multipliers = {k: c.val(v, 0.6, 1.6) for k, v in p.expense_multipliers.items() if k in cats}
        out.expense_day_weights = [max(0.0, float(x)) for x in p.expense_day_weights]
        out.appropriation_releases = [x.model_copy(update={"day": int(c.val(x.day, 0, 6)), "amount_factor": c.val(x.amount_factor, 0.0, 2.0)})
                                      for x in p.appropriation_releases[:3]]
        rec = []
        for x in p.partner_receipts[:1]:
            fac = x.facility if x.facility in facilities else (next(iter(facilities)) if facilities else None)
            if fac is None:
                continue
            pc = x.partner_country if x.partner_country in settings_partner_countries() else \
                next(f.partner_country for f in prof.partner_funding if f.facility_name == fac)
            rec.append(x.model_copy(update={"day": int(c.val(x.day, 0, 6)), "facility": fac, "partner_country": pc,
                                            "amount_factor": c.val(x.amount_factor, 0.5, 1.3)}))
        out.partner_receipts = rec
        out.refunds = [x.model_copy(update={"day": int(c.val(x.day, 0, 6)), "amount_factor": c.val(x.amount_factor, 0.1, 1.0),
                                            "memo": x.memo[:100]}) for x in p.refunds[:2] if x.fee_code in fee_codes]
        out.remittance_delay_days = int(c.val(p.remittance_delay_days, 0, 5))
        out.dq_incidents = [x.model_copy(update={"day": int(c.val(x.day, 0, 6)), "count": int(c.val(x.count, 1, 3))}) for x in p.dq_incidents[:3]]
        out.note = re.sub(r"\s+", " ", p.note)[:200]
        return out, [], c.n

    return check


def build_ops_plan(llm, code: str, week_start: date, prof: EntityProfile, refresh: bool = False) -> RoleResult:
    brief = {"categories": [x.name for x in (prof.expense_structure.categories if prof.expense_structure else [])],
             "facility": [x.model_dump() for x in prof.partner_funding][:1], "fee_codes": [r.fee_code for r in prof.fee_rules],
             "appropriation_coverage": prof.expense_structure.appropriation_coverage if prof.expense_structure else 0,
             "remittance": prof.remittance.model_dump(include={"basis", "frequency", "due_day"})}
    user = P.ops_user(code, week_start.isoformat(), brief, beats_for_week(week_start))
    return run_json_role(llm, "ops_plan", P.OPS_SYSTEM.format(entity=code, date=week_start.isoformat()), user, OpsPlan,
                         check_ops(code, week_start, prof), lambda: fb.fallback_ops_plan(code, week_start),
                         refresh=refresh, schema_version=P.PROMPT_VERSION)


# =============================================================================== director (live)
def check_director(max_ports: dict[str, int]) -> Callable[[DirectorOutput], tuple[DirectorOutput, list[str], int]]:
    codes = set(entity_codes())

    def check(p: DirectorOutput) -> tuple[DirectorOutput, list[str], int]:
        c = Clamp()
        d = p.directives
        banks = set(ref()["banks"])
        directives = Directives(
            arrival_multiplier=c.val(d.arrival_multiplier, 0.5, 2.0),
            scanner_offline={k: int(c.val(v, 0, max(0, max_ports[k] - 1))) for k, v in d.scanner_offline.items() if k in max_ports},
            permit_queue_pressure={k: c.val(v, 0.8, 2.5) for k, v in d.permit_queue_pressure.items() if k in codes},
            settlement_lag_change_h={k: c.val(v, -12.0, 72.0) for k, v in d.settlement_lag_change_h.items() if k in banks},
            payment_failure_rate=c.val(d.payment_failure_rate, 0.0, 0.15))
        items = []
        for it in p.feed_items:
            text = f"{it.headline} {it.description}"
            if re.search(r"\d|₦|\bbn\b|million|billion", text, re.I) or not it.headline.strip():
                c.n += 1                              # feed text must not contain numbers (amounts come from the engine)
                continue
            items.append(FeedItem(headline=it.headline.strip()[:90], description=it.description.strip()[:200],
                                  severity=it.severity, entity=it.entity if it.entity in codes else "NSW",
                                  type=it.type if it.type in EVENT_TYPES else "ops.congestion.alert"))
        return DirectorOutput(directives=directives, feed_items=items[:10]), [], c.n

    return check


# =============================================================================== batching / persistence
def _log_run(conn, kind: str, entity: str, ws: str, we: str, res: RoleResult) -> None:
    conn.execute("INSERT OR REPLACE INTO generation_runs(run_id,kind,entity_id,window_start,window_end,status,source,llm_model,"
                 "tokens_in,tokens_out,latency_ms,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                 (f"{kind}:{entity}:{ws}", kind, entity, ws, we, "done", res.source, res.model, res.tokens_in, res.tokens_out,
                  res.latency_ms, clock.iso(clock.utcnow())))


def weeks_between(start: date, end: date) -> list[date]:
    ws = clock.week_start(start)
    out = []
    while ws <= end:
        out.append(ws)
        ws += timedelta(days=7)
    return out


def load_plan(conn, kind: str, entity: str, week: date):
    row = conn.execute("SELECT plan_json, source FROM weekly_plans WHERE kind=? AND entity_id=? AND week_start=?",
                       (kind, entity, week.isoformat())).fetchone()
    if not row:
        return None, None
    cls = FlowPlan if kind == "flow" else OpsPlan
    return cls.model_validate_json(row[0]), row[1]


def pending_plan_work(conn, weeks: list[date], llm_offline: bool, ops_llm_from: date) -> dict:
    """Counts of plan calls still needed (for ``make estimate-cost``)."""
    flow = ops = 0
    for w in weeks:
        _, src = load_plan(conn, "flow", "ALL", w)
        if src is None or (src == "fallback" and not llm_offline):
            flow += 1
        if w >= ops_llm_from:
            for code in entity_codes():
                _, src = load_plan(conn, "ops", code, w)
                if code not in ("CBN", "MOF-FA") and (src is None or (src == "fallback" and not llm_offline)):
                    ops += 1
    return {"flow_plan": flow, "ops_plan": ops}


def ensure_plans(conn: sqlite3.Connection, llm, profiles: dict[str, EntityProfile], weeks: list[date], *,
                 ops_llm_from: date | None = None, progress: Callable[[str], None] | None = None) -> dict:
    """Make sure a flow plan and ops plans exist for each week. Flow plans are chained (FX continuity) so they run in
    order; ops plans run in parallel. Ops plans before ``ops_llm_from`` (warm-up) use the deterministic fallback."""
    stats = {"flow_llm": 0, "flow_fallback": 0, "ops_llm": 0, "ops_fallback": 0}
    prev_fx: float | None = None
    for w in weeks:
        plan, src = load_plan(conn, "flow", "ALL", w)
        if plan is None or (src == "fallback" and not llm.offline):
            res = build_flow_plan(llm, w, prev_fx)
            plan, src = res.obj, res.source
            conn.execute("INSERT OR REPLACE INTO weekly_plans(kind,entity_id,week_start,plan_json,source,model,created_at) VALUES(?,?,?,?,?,?,?)",
                         ("flow", "ALL", w.isoformat(), plan.model_dump_json(), src, res.model or "", clock.iso(clock.utcnow())))
            _log_run(conn, "flow", "ALL", w.isoformat(), (w + timedelta(days=7)).isoformat(), res)
            if progress:
                progress(f"flow plan {w}: {src} ({res.outcome}, clamped={res.clamped})")
        stats["flow_llm" if src == "llm" else "flow_fallback"] += 1
        prev_fx = plan.fx_path[-1]
    jobs = []
    for w in weeks:
        for code in entity_codes():
            if code in ("CBN", "MOF-FA"):
                continue
            plan, src = load_plan(conn, "ops", code, w)
            if plan is not None and not (src == "fallback" and not llm.offline and ops_llm_from and w >= ops_llm_from):
                stats["ops_llm" if src == "llm" else "ops_fallback"] += 1
                continue
            jobs.append((code, w))
    if jobs:
        def work(job):
            code, w = job
            if ops_llm_from and w < ops_llm_from:
                return job, RoleResult(fb.fallback_ops_plan(code, w), "fallback", "fallback")
            return job, build_ops_plan(llm, code, w, profiles[code])
        with ThreadPoolExecutor(max_workers=getattr(llm, "concurrency", 6)) as ex:
            for f in as_completed([ex.submit(work, j) for j in jobs]):
                (code, w), res = f.result()
                conn.execute("INSERT OR REPLACE INTO weekly_plans(kind,entity_id,week_start,plan_json,source,model,created_at) VALUES(?,?,?,?,?,?,?)",
                             ("ops", code, w.isoformat(), res.obj.model_dump_json(), res.source, res.model or "", clock.iso(clock.utcnow())))
                _log_run(conn, "ops", code, w.isoformat(), (w + timedelta(days=7)).isoformat(), res)
                stats["ops_llm" if res.source == "llm" else "ops_fallback"] += 1
                if progress and (stats["ops_llm"] + stats["ops_fallback"]) % 40 == 0:
                    progress(f"ops plans: {stats['ops_llm']} llm / {stats['ops_fallback']} fallback")
    return stats


def estimate(conn, weeks: list[date], llm) -> dict:
    """Expected calls and tokens for pending plan work (``make estimate-cost``)."""
    n_prof = sum(1 for c in entity_codes() if not conn.execute("SELECT 1 FROM entity_profiles WHERE entity_id=?", (c,)).fetchone())
    ops_from = clock.week_start(clock.wat(clock.sim_start()).date())
    pend = pending_plan_work(conn, weeks, llm.offline, ops_from)
    tok = {"profile": 5500, "flow_plan": 3500, "ops_plan": 2200}
    calls = {"profile": n_prof, **pend}
    return {"calls": calls, "est_tokens": {k: v * tok[k] for k, v in calls.items()},
            "total_calls": sum(calls.values()), "total_tokens": sum(v * tok[k] for k, v in calls.items()),
            "note": "Cached calls cost nothing; the director adds ~80 calls/hour while the app runs (about 2.2k tokens each)."}
