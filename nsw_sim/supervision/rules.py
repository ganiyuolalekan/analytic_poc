"""Supervision rules R-SLA-01 ... R-ONB-01 (Section 10.1; thresholds in config/alert_rules.yaml).

Each rule reads through the as_of-bound views, returns *firings* with their supporting rows, and the engine raises/refreshes
alerts (de-duplicated per episode) and marks cleared episodes."""
from __future__ import annotations

from datetime import timedelta

import pandas as pd

from nsw_sim import clock, db
from nsw_sim.config import yaml_config
from nsw_sim.sim.ids import IdFactory
from nsw_sim.supervision import alerts

Firing = dict


def _cfg() -> dict:
    return yaml_config("alert_rules")


def _w(t: float, hours: float) -> tuple[str, str]:
    return clock.iso(t - hours * 3600), clock.iso(t)


def _firing(entity: str, subject: str, metric: float, threshold: float, window: tuple[str, str], summary: str, rows: list, drill: dict) -> Firing:
    return {"entity": entity, "subject": subject, "metric": float(metric), "threshold": float(threshold), "window": window,
            "details": {"summary": summary, "supporting_rows": rows[:25], "n_supporting": len(rows), "drill": drill}}


# --------------------------------------------------------------------------------------------- the rules
def r_sla(c, t: float, cfg: dict, live: bool) -> list[Firing]:
    s, e = _w(t, cfg["window_hours"])
    df = pd.read_sql_query("SELECT owner_entity AS entity, stage, nsw_ref, sla_breach FROM v_stage_durations WHERE occurred_at>? AND occurred_at<=? AND sla_breach IS NOT NULL AND "
                           "((stage='S02' AND status='queued') OR (stage!='S02' AND status IN ('done','manual') AND data_complete=1))", c, params=(s, e))
    out = []
    for (ent, st), g in df.groupby(["entity", "stage"]):
        if ent == "CBN" or len(g) < cfg["min_events"]:
            continue
        rate = float(g["sla_breach"].mean())
        if rate > cfg["threshold"]:
            refs = g[g["sla_breach"] == 1]["nsw_ref"].tolist()
            out.append(_firing(ent, f"{ent}:{st}", rate, cfg["threshold"], (s, e), f"{ent} stage {st}: {rate:.1%} of {len(g)} records breached the SLA in 24 hours (threshold {cfg['threshold']:.0%}).",
                               refs, {"view": "stage_events", "owner": ent, "stage": st, "start": s, "end": e}))
    return out


def r_phys(c, t: float, cfg: dict, live: bool) -> list[Firing]:
    hrs = 3.0 if live else cfg["window_hours"]
    min_n = 6 if live else cfg["min_events"]
    s, e = _w(t, hrs)
    base_s = clock.iso(t - cfg["baseline_days"] * 86400)
    df = pd.read_sql_query("SELECT c.port, s.wait_h, s.nsw_ref, s.occurred_at FROM v_stage_durations s JOIN v_consignments c ON c.nsw_ref=s.nsw_ref "
                           "WHERE s.stage='S06' AND s.status='queued' AND s.occurred_at>? AND s.occurred_at<=?", c, params=(base_s, e))
    out = []
    for port, g in df.groupby("port"):
        recent, base = g[g["occurred_at"] > s], g[g["occurred_at"] <= s]
        if len(recent) < min_n or len(base) < 30:
            continue
        ratio = float(recent["wait_h"].median() / max(base["wait_h"].median(), 1e-6))
        if ratio >= cfg["ratio"]:
            out.append(_firing("NCS", f"exam-wait:{port}", ratio, cfg["ratio"], (s, e),
                               f"Median examination wait at {port} is {ratio:.1f}x its {cfg['baseline_days']}-day baseline over the last {hrs:.0f} hours.",
                               recent["nsw_ref"].tolist(), {"view": "stage_events", "stage": "S06", "port": port, "start": s, "end": e}))
    return out


def r_fee(c, t: float, cfg: dict, live: bool) -> list[Firing]:
    s, e = _w(t, cfg["window_days"] * 24)
    df = pd.read_sql_query("SELECT entity_id, fee_code, commodity_group, origin_country, COUNT(*) n, SUM(expected_amount_ngn_minor) ex, SUM(amount_ngn_minor) ass "
                           "FROM v_assessments WHERE occurred_at>? AND occurred_at<=? AND expected_amount_ngn_minor>0 GROUP BY 1,2,3,4 HAVING n>=?", c,
                           params=(s, e, cfg["min_assessments"]))
    out = []
    for r in df.itertuples():
        short = (r.ex - r.ass) / r.ex
        if short > cfg["threshold"]:
            refs = [x[0] for x in c.execute("SELECT assessment_id FROM v_assessments WHERE entity_id=? AND fee_code=? AND commodity_group=? AND origin_country=? AND occurred_at>? AND occurred_at<=? "
                                            "AND amount_ngn_minor<expected_amount_ngn_minor LIMIT 25", (r.entity_id, r.fee_code, r.commodity_group, r.origin_country, s, e))]
            out.append(_firing(r.entity_id, f"{r.fee_code}:{r.commodity_group}:{r.origin_country}", short, cfg["threshold"], (s, e),
                               f"{r.fee_code} for {r.commodity_group} from {r.origin_country} is assessed {short:.1%} below the fee-rule expectation over {cfg['window_days']} days ({r.n} assessments).",
                               refs, {"view": "assessments", "entity": r.entity_id, "fee_code": r.fee_code, "commodity": r.commodity_group, "origin": r.origin_country, "start": s, "end": e}))
    return out


def r_rec(c, t: float, cfg: dict, live: bool) -> list[Firing]:
    cutoff, lo = clock.iso(t - cfg["max_hours"] * 3600), clock.iso(t - 10 * 86400)
    df = pd.read_sql_query("SELECT p.bank, a.payment_id FROM v_collections a JOIN v_payments p ON p.payment_id=a.payment_id WHERE a.settled_at IS NULL AND a.paid_at<? AND a.paid_at>?", c, params=(cutoff, lo))
    out = []
    for bank, g in df.groupby("bank"):
        n = g["payment_id"].nunique()
        if n >= cfg["min_count"]:
            out.append(_firing("CBN", f"paid-not-settled:Bank {bank}", n, cfg["min_count"], (lo, clock.iso(t)),
                               f"{n} payments via Bank {bank} were confirmed more than {cfg['max_hours']} hours ago and are not yet settled.", g["payment_id"].unique().tolist(),
                               {"view": "collections", "bank": bank, "end": cutoff}))
    return out


def r_set(c, t: float, cfg: dict, live: bool) -> list[Firing]:
    thr = cfg["threshold_hours"]
    s, e = _w(t, 48)
    rows = c.execute("SELECT bank, batch_id, closed_at, completed_at, lag_hours FROM v_settlement_batches WHERE closed_at>?", (clock.iso(t - 5 * 86400),)).fetchall()
    worst: dict[str, tuple[float, list]] = {}
    for bank, bid, closed, comp, lag in rows:
        eff = lag if comp else (t - clock.to_epoch(closed)) / 3600
        if comp and clock.to_epoch(comp) < t - 24 * 3600:
            continue
        if eff and eff > thr:
            w = worst.setdefault(bank, (0.0, []))
            worst[bank] = (max(w[0], eff), w[1] + [bid])
    return [_firing("CBN", f"settlement-lag:Bank {b}", v[0], thr, (s, e), f"Bank {b} settlement lag reached {v[0]:.0f} hours (threshold {thr} hours).", v[1],
                    {"view": "settlement_batches", "bank": b}) for b, v in worst.items()]


def r_rem(c, t: float, cfg: dict, live: bool) -> list[Firing]:
    out = []
    now = clock.iso(t)
    for rid, ent, period, due, paid, amt, status in c.execute("SELECT remittance_id, entity_id, period, due_date, paid_at, amount_ngn_minor, status FROM v_remittances"):
        ref_t = clock.to_epoch(paid) if paid else t
        late = (ref_t - clock.to_epoch(due)) / 86400
        if late > cfg["grace_days"] and (not paid or clock.to_epoch(paid) >= t - 3 * 86400 or True):
            if paid and clock.to_epoch(due) + cfg["grace_days"] * 86400 > t:
                continue
            out.append(_firing(ent, rid, late, cfg["grace_days"], (due, now), f"{ent} remittance {rid} for {period} is {late:.0f} days past its due date" + ("" if not paid else " (since paid)") + ".",
                               [rid], {"view": "remittances", "remittance_id": rid}))
    return out


def r_dup(c, t: float, cfg: dict, live: bool) -> list[Firing]:
    s, e = _w(t, cfg["window_minutes"] / 60)
    rows = c.execute("SELECT payment_id FROM v_payments WHERE is_duplicate=1 AND occurred_at>? AND occurred_at<=?", (s, e)).fetchall()
    if len(rows) >= cfg["min_count"]:
        return [_firing("NSW", "duplicate-payments", len(rows), cfg["min_count"], (s, e), f"{len(rows)} duplicate payments were confirmed in the last hour.", [r[0] for r in rows],
                        {"view": "payments", "duplicates": True, "start": s, "end": e})]
    return []


def r_fx(c, t: float, cfg: dict, live: bool) -> list[Firing]:
    df = pd.read_sql_query("SELECT date(ts_utc,'+1 hour') d, rate_to_ngn r FROM v_fx_rates WHERE ccy='USD' AND ts_utc>? ORDER BY ts_utc", c, params=(clock.iso(t - 4 * 86400),))
    out = []
    for i in range(1, len(df)):
        mv = df["r"].iloc[i] / df["r"].iloc[i - 1] - 1
        if abs(mv) > cfg["threshold"]:
            out.append(_firing("CBN", f"fx-move:{df['d'].iloc[i]}", abs(mv), cfg["threshold"], (df["d"].iloc[i - 1], df["d"].iloc[i]),
                               f"USD/NGN moved {mv:+.1%} on {df['d'].iloc[i]}.", [df["d"].iloc[i]], {"view": "fx_rates", "day": df["d"].iloc[i]}))
    return out


def r_dq(c, t: float, cfg: dict, live: bool) -> list[Firing]:
    s, e = _w(t, cfg["window_hours"])
    df = pd.read_sql_query("SELECT owner_entity AS entity, COUNT(*) n, AVG(data_complete) comp FROM v_stage_durations WHERE status IN ('done','manual') AND occurred_at>? AND occurred_at<=? GROUP BY 1",
                           c, params=(s, e))
    out = []
    for r in df.itertuples():
        if r.n >= cfg["min_events"] and r.comp < cfg["threshold"]:
            refs = [x[0] for x in c.execute("SELECT nsw_ref FROM v_stage_durations WHERE owner_entity=? AND data_complete=0 AND occurred_at>? AND occurred_at<=? LIMIT 25", (r.entity, s, e))]
            out.append(_firing(r.entity, f"completeness:{r.entity}", r.comp, cfg["threshold"], (s, e), f"{r.entity} data completeness is {r.comp:.1%} over 24 hours (threshold {cfg['threshold']:.0%}).",
                               refs, {"view": "stage_events", "owner": r.entity, "incomplete": True, "start": s, "end": e}))
    return out


def r_cash(c, t: float, cfg: dict, live: bool) -> list[Firing]:
    s7, s30 = clock.iso(t - 7 * 86400), clock.iso(t - cfg["baseline_days"] * 86400)
    df = pd.read_sql_query("SELECT entity_id, CASE WHEN settled_at>? THEN 'recent' ELSE 'base' END per, SUM(collection_cost_ngn_minor)*1.0/SUM(amount_ngn_minor) ratio FROM v_settled WHERE settled_at>? GROUP BY 1,2",
                           c, params=(s7, s30))
    out = []
    for ent, g in df.groupby("entity_id"):
        d = dict(zip(g["per"], g["ratio"]))
        if "recent" in d and "base" in d and d["base"] > 0 and d["recent"] / d["base"] > cfg["ratio"]:
            out.append(_firing(ent, f"cost-ratio:{ent}", d["recent"] / d["base"], cfg["ratio"], (s7, clock.iso(t)), f"{ent} collection cost ratio is {d['recent'] / d['base']:.1f}x its {cfg['baseline_days']}-day mean.", [ent], {"view": "settled", "entity": ent}))
    return out


def r_tgt(c, t: float, cfg: dict, live: bool) -> list[Firing]:
    from nsw_sim.analytics import clearance, forecast
    from nsw_sim.analytics.queries import Filters
    f = Filters(start=clock.iso(clock.sim_start()), end=clock.iso(t))
    wk = clearance.weekly_dwell(f, clock.iso(t))
    proj = forecast.project_to_target(wk.iloc[:-1] if len(wk) > 1 else wk)
    if proj.get("projected_date") is None or not proj.get("reaches_target", False):
        pdte = proj.get("projected_date")
        return [_firing("NSW", "dwell-target", float(proj.get("current_fit_days") or 0), float(proj["target_days"]), (clock.iso(t - 56 * 86400), clock.iso(t)),
                        f"On the current trend median dwell reaches {proj['target_days']} days " + (f"around {pdte}, after the {proj['target_date']} target date." if pdte else "not at all within the projection horizon."),
                        [pdte or "none"], {"view": "target_tracker"})]
    return []


def r_onb(c, t: float, cfg: dict, live: bool) -> list[Firing]:
    s, e = _w(t, cfg["window_hours"])
    df = pd.read_sql_query("SELECT owner_entity AS entity, COUNT(*) n, AVG(CASE WHEN status='done' THEN 1.0 ELSE 0.0 END) dig FROM v_stage_durations WHERE status IN ('done','manual') AND occurred_at>? AND occurred_at<=? GROUP BY 1",
                           c, params=(s, e))
    return [_firing(r.entity, f"onboarding:{r.entity}", r.dig, cfg["threshold"], (s, e), f"Only {r.dig:.1%} of {r.entity} stage records arrived digitally in 24 hours (threshold {cfg['threshold']:.0%}).", [],
                    {"view": "stage_events", "owner": r.entity, "manual": True, "start": s, "end": e})
            for r in df.itertuples() if r.n >= cfg["min_events"] and r.dig < cfg["threshold"] and r.entity not in ("CBN",)]


RULES = {"R-SLA-01": r_sla, "R-PHYS-01": r_phys, "R-FEE-01": r_fee, "R-REC-01": r_rec, "R-SET-01": r_set, "R-REM-01": r_rem, "R-DUP-01": r_dup,
         "R-FX-01": r_fx, "R-DQ-01": r_dq, "R-CASH-01": r_cash, "R-TGT-01": r_tgt, "R-ONB-01": r_onb}
DAILY_ONLY = {"R-CASH-01", "R-TGT-01"}


def evaluate_at(conn, ids: IdFactory, t: float, *, live: bool = False, rules: list[str] | None = None) -> int:
    """Evaluate the rules as of ``t``; returns the number of newly raised alerts."""
    cfgs = _cfg()["rules"]
    c = db.reader(clock.iso(t))
    new = 0
    is_midnight = clock.wat(t).hour == 0
    conn.execute("BEGIN")
    try:
        for code, fn in RULES.items():
            if rules and code not in rules:
                continue
            if code in DAILY_ONLY and not is_midnight and not live:
                continue
            cfg = cfgs[code]
            firings = fn(c, t, cfg, live)
            keys = set()
            for fr in firings:
                keys.add(f"{code}|{fr['entity']}|{fr['subject']}")
                if alerts.raise_alert(conn, ids, code, cfg["severity"], fr["entity"], fr["subject"], t, fr["window"], fr["metric"], fr["threshold"], fr["details"]):
                    new += 1
            alerts.mark_cleared(conn, code, keys, t)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    db.kv_set(conn, "rules_last_eval", clock.iso(t))
    return new


def evaluate_incremental(conn, engine, t: float) -> int:
    """Called by the service: evaluate any missed 6-hour checkpoints (catch-up) and a live evaluation at ``t``."""
    ids = engine.ids
    last = db.kv_get(conn, "rules_last_eval")
    last_t = clock.to_epoch(last) if last else clock.sim_start().timestamp()
    n = 0
    if t - last_t > 8 * 3600:
        for cp in checkpoints(last_t, t):
            n += evaluate_at(conn, ids, cp)
        return n
    if t - last_t >= 25:
        n += evaluate_at(conn, ids, t, live=True)
    return n


def checkpoints(start: float, end: float) -> list[float]:
    hrs = _cfg()["checkpoints_wat"]
    out = []
    d = clock.wat(start).date()
    while True:
        base = clock.wat_midnight_utc(d).timestamp()
        for h in hrs:
            cp = base + h * 3600
            if start < cp <= end:
                out.append(cp)
        d += timedelta(days=1)
        if clock.wat_midnight_utc(d).timestamp() > end + 86400:
            break
    return sorted(set(out))
