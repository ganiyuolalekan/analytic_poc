"""Independent oracle for the golden questions: computes expected values with raw SQL / pandas over a read-only copy of the database.
It deliberately does NOT import nsw_sim.analytics (no shared code with the app). Money values are naira; percentages are 0-100."""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

AS_OF = "2026-10-05T12:00:00Z"
ROOT = Path(__file__).resolve().parent.parent
CONSOLIDATED = ("NCS", "NRS", "NPA", "NIMASA", "SON", "NAFDAC", "NAQS", "NESREA", "FAAN", "NSW")


def conn() -> sqlite3.Connection:
    return sqlite3.connect(f"file:{os.environ.get('NSW_DB_PATH', ROOT / 'data' / 'nsw.db')}?mode=ro", uri=True)


def wat(y: int, m: int, d: int) -> str:
    return (datetime(y, m, d) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")


def month(y: int, m: int) -> tuple[str, str]:
    return wat(y, m, 1), wat(y + (m == 12), (m % 12) + 1, 1)


def q1(c, sql, *a):
    r = c.execute(sql, a).fetchone()
    return r[0] if r and r[0] is not None else 0.0


def cut(e: str) -> str:
    return min(e, AS_OF)


METRIC_SQL = {"assessed": ("fee_assessments", "occurred_at", "amount_ngn_minor"), "paid": ("payment_allocations", "paid_at", "amount_ngn_minor"),
              "settled": ("payment_allocations", "settled_at", "settled_ngn_minor"), "settled_gross": ("payment_allocations", "settled_at", "amount_ngn_minor"),
              "cost": ("payment_allocations", "settled_at", "collection_cost_ngn_minor"), "expected": ("fee_assessments", "occurred_at", "expected_amount_ngn_minor")}


def metric(c, name: str, s: str, e: str, entity: str | None = None, **dims) -> float:
    tbl, tcol, vcol = METRIC_SQL[name]
    sql = f"SELECT SUM({vcol}) FROM {tbl} WHERE {tcol}>=? AND {tcol}<? AND {tcol}<=?"
    args = [s, e, AS_OF]
    if entity:
        sql += " AND entity_id=?"
        args.append(entity)
    for k, v in dims.items():
        sql += f" AND {k}=?"
        args.append(v)
    return (q1(c, sql, *args)) / 100.0


def by(c, name: str, s: str, e: str, col: str, entity: str | None = None, **dims) -> dict:
    tbl, tcol, vcol = METRIC_SQL[name]
    sql = f"SELECT {col}, SUM({vcol}) FROM {tbl} WHERE {tcol}>=? AND {tcol}<? AND {tcol}<=?"
    args = [s, e, AS_OF]
    if entity:
        sql += " AND entity_id=?"
        args.append(entity)
    for k, v in dims.items():
        sql += f" AND {k}=?"
        args.append(v)
    return {r[0]: r[1] / 100.0 for r in c.execute(sql + f" GROUP BY {col}", args)}


def dwell_days(c, s: str, e: str, **dims) -> pd.Series:
    sql = "SELECT dwell_h/24.0 FROM consignments WHERE gate_out_at>=? AND gate_out_at<? AND gate_out_at<=?"
    args = [s, e, AS_OF]
    for k, v in dims.items():
        sql += f" AND {k}=?"
        args.append(v)
    return pd.Series([r[0] for r in c.execute(sql, args)], dtype=float)


def ledger(c, ents, s: str | None, e: str, classes: tuple[str, ...], sign: str) -> float:
    ph = ",".join("?" * len(ents))
    cl = ",".join("?" * len(classes))
    sql = (f"SELECT SUM(l.{'credit_minor-l.debit_minor' if sign == 'cr' else 'debit_minor-l.credit_minor'}) FROM journal_lines l JOIN chart_of_accounts a ON a.entity_id=l.entity_id AND a.account_code=l.account_code "
           f"WHERE l.entity_id IN ({ph}) AND a.class IN ({cl}) AND l.occurred_at<? AND l.occurred_at<=?" + (" AND l.occurred_at>=?" if s else ""))
    args = [*ents, *classes, e, AS_OF] + ([s] if s else [])
    return q1(c, sql, *args) / 100.0


# ----------------------------------------------------------------------------------------------- oracles (key -> fn(c) -> {label: value | [any-of values]})
S, E_SEP = month(2026, 9)
JUL, AUG = month(2026, 7), month(2026, 8)
OCT1, T_NOW = wat(2026, 10, 1), AS_OF


def o_totals_sep(c):
    return {"assessed": metric(c, "assessed", S, E_SEP), "paid": metric(c, "paid", S, E_SEP), "settled": metric(c, "settled", S, E_SEP)}


def o_ncs_mtd(c):
    return {"ncs_paid_mtd": metric(c, "paid", OCT1, cut(T_NOW), "NCS")}


def o_nafdac_week(c):
    return {"nafdac_assessed": metric(c, "assessed", wat(2026, 8, 24), wat(2026, 8, 31), "NAFDAC")}


def o_yesterday(c):
    y, d2 = metric(c, "paid", wat(2026, 10, 4), wat(2026, 10, 5)), metric(c, "paid", wat(2026, 10, 3), wat(2026, 10, 4))
    return {"yesterday": y, "day_before": d2, "difference": [abs(y - d2), abs(y - d2) / d2 * 100]}


def o_since_july(c):
    return {"total_paid_since_july": metric(c, "paid", JUL[0], cut(T_NOW))}


def o_rank_q3(c):
    d = by(c, "paid", JUL[0], wat(2026, 10, 1), "entity_id")
    top = sorted(d.items(), key=lambda kv: -kv[1])
    return {"first": top[0][1], "second": top[1][1], "third": top[2][1]}


def o_growth(c):
    j, a = by(c, "paid", *JUL, "entity_id"), by(c, "paid", *AUG, "entity_id")
    g = {k: (a[k] - j[k]) / j[k] * 100 for k in a if k in j and j[k] > 0 and k in CONSOLIDATED}
    k = max(g, key=g.get)
    return {"top_growth_pct": g[k]}


def o_ncs_duty_origin(c):
    d = by(c, "assessed", S, E_SEP, "origin_country", "NCS", fee_code="NCS-DUTY")
    top = max(d, key=d.get)
    return {"top_origin_duty": d[top]}


def o_top5_origin(c):
    d = by(c, "assessed", JUL[0], wat(2026, 10, 1), "origin_country")
    top = sorted(d.values(), reverse=True)
    return {"first": top[0], "second": top[1], "fifth": top[4]}


def o_son_nafdac(c):
    s_, n_ = metric(c, "assessed", *AUG, "SON"), metric(c, "assessed", *AUG, "NAFDAC")
    return {"SON": s_, "NAFDAC": n_, "difference": abs(s_ - n_)}


def o_npa_cost(c):
    return {"cost_per_100": 100 * metric(c, "cost", S, E_SEP, "NPA") / metric(c, "settled_gross", S, E_SEP, "NPA")}


def o_highest_cost_ratio(c):
    cost, gross = by(c, "cost", JUL[0], wat(2026, 10, 1), "entity_id"), by(c, "settled_gross", JUL[0], wat(2026, 10, 1), "entity_id")
    r = {k: 100 * cost[k] / gross[k] for k in cost if gross.get(k) and k in CONSOLIDATED}
    return {"highest_cost_per_100": max(r.values())}


def o_nimasa_eff(c):
    a, s_ = metric(c, "assessed", *AUG, "NIMASA"), metric(c, "settled_gross", *AUG, "NIMASA")
    return {"efficiency_pct": 100 * s_ / a}


def o_npa_surplus(c):
    return {"npa_surplus_aug": ledger(c, ["NPA"], AUG[0], AUG[1], ("Revenue",), "cr") - ledger(c, ["NPA"], AUG[0], AUG[1], ("Expense",), "dr")}


def o_ncs_expenses(c):
    rows = c.execute("SELECT a.name, SUM(l.debit_minor-l.credit_minor) FROM journal_lines l JOIN chart_of_accounts a ON a.entity_id=l.entity_id AND a.account_code=l.account_code "
                     "WHERE l.entity_id='NCS' AND a.class='Expense' AND l.occurred_at>=? AND l.occurred_at<? GROUP BY a.name", (S, E_SEP)).fetchall()
    tot = sum(r[1] for r in rows) / 100.0
    ops = c.execute("SELECT category, SUM(amount_ngn_minor) FROM expenses WHERE entity_id='NCS' AND occurred_at>=? AND occurred_at<? GROUP BY 1", (S, E_SEP)).fetchall()
    return {"total_expenses": [tot, sum(r[1] for r in ops) / 100.0], "largest_category": [max(r[1] for r in rows) / 100.0, max(r[1] for r in ops) / 100.0]}


def o_consolidated_bs(c):
    ents = list(CONSOLIDATED)
    end = wat(2026, 10, 1)
    return {"total_assets": ledger(c, ents, None, end, ("Asset",), "dr")}


def o_faan_grants(c):
    rows = c.execute("SELECT source_country, SUM(amount_ngn_minor) FROM funding_receipts WHERE entity_id='FAAN' AND source_type IN ('grant','concessional_loan') AND occurred_at<=? GROUP BY 1", (AS_OF,)).fetchall()
    out = {"total": sum(r[1] for r in rows) / 100.0}
    out.update({r[0]: r[1] / 100.0 for r in rows})
    return out


def o_son_payable(c):
    end = wat(2026, 10, 1)
    sql = "SELECT SUM(credit_minor-debit_minor) FROM journal_lines WHERE entity_id='SON' AND account_code='2300' AND occurred_at<? AND occurred_at<=?"
    return {"son_remittance_payable": q1(c, sql, end, AS_OF) / 100.0}


def o_in_transit(c):
    return {"in_transit": q1(c, "SELECT SUM(amount_ngn_minor) FROM payment_allocations WHERE paid_at<=? AND (settled_at IS NULL OR settled_at>?)", AS_OF, AS_OF) / 100.0}


def o_shortfall_pair(c):
    df = pd.read_sql_query("SELECT commodity_group g, origin_country o, COUNT(*) n, SUM(expected_amount_ngn_minor) ex, SUM(amount_ngn_minor) a FROM fee_assessments WHERE occurred_at>=? AND occurred_at<? AND occurred_at<=? "
                           "GROUP BY 1,2 HAVING n>=15", c, params=(S, E_SEP, AS_OF))
    df["pct"] = (df["ex"] - df["a"]) / df["ex"] * 100
    top = df.sort_values("pct", ascending=False).iloc[0]
    ncs = pd.read_sql_query("SELECT SUM(expected_amount_ngn_minor) ex, SUM(amount_ngn_minor) a FROM fee_assessments WHERE fee_code='NCS-DUTY' AND commodity_group=? AND origin_country=? AND occurred_at>=? AND occurred_at<? AND occurred_at<=?",
                            c, params=(top["g"], top["o"], S, E_SEP, AS_OF)).iloc[0]
    return {"shortfall_naira": [(top["ex"] - top["a"]) / 100.0, (ncs["ex"] - ncs["a"]) / 100.0]}


def o_dups_oct2(c):
    r = c.execute("SELECT COUNT(*), SUM(amount_ngn_minor) FROM payments WHERE is_duplicate=1 AND occurred_at>=? AND occurred_at<?", (wat(2026, 10, 2), wat(2026, 10, 3))).fetchone()
    return {"count": float(r[0]), "value": (r[1] or 0) / 100.0}


def o_it_by_bank(c):
    rows = c.execute("SELECT p.bank, SUM(a.amount_ngn_minor) FROM payment_allocations a JOIN payments p ON p.payment_id=a.payment_id WHERE a.paid_at<=? AND (a.settled_at IS NULL OR a.settled_at>?) GROUP BY 1", (AS_OF, AS_OF)).fetchall()
    d = {r[0]: r[1] / 100.0 for r in rows}
    return {"largest_bank": max(d.values()), "total": sum(d.values())}


def o_dwell_jul_sep(c):
    return {"july_median": float(dwell_days(c, *JUL).median()), "september_median": float(dwell_days(c, S, E_SEP).median())}


def o_top_delay_stage(c):
    df = pd.read_sql_query("SELECT s.nsw_ref, s.stage, MAX(s.wait_h+s.dur_h) h FROM stage_events s JOIN consignments c ON c.nsw_ref=s.nsw_ref WHERE s.status IN ('done','manual') AND s.data_complete=1 AND s.stage!='S01' "
                           "AND c.gate_out_at>=? AND c.gate_out_at<? AND c.gate_out_at<=? GROUP BY 1,2", c, params=(S, E_SEP, AS_OF))
    n = df["nsw_ref"].nunique()
    m = df.groupby("stage")["h"].sum() / n
    return {"top_stage_mean_hours": float(m.max())}


def o_digital_share(c):
    r = c.execute("SELECT SUM(digital_h), SUM(physical_h) FROM consignments WHERE gate_out_at>=? AND gate_out_at<? AND gate_out_at<=?", (S, E_SEP, AS_OF)).fetchone()
    return {"digital_pct": 100 * r[0] / (r[0] + r[1]), "physical_pct": 100 * r[1] / (r[0] + r[1])}


def o_exam_p90(c):
    s, e = wat(2026, 8, 12), wat(2026, 8, 16)
    base = "SELECT s.wait_h FROM stage_events s JOIN consignments c ON c.nsw_ref=s.nsw_ref WHERE s.stage='S06' AND c.port='NGAPP' AND s.occurred_at>=? AND s.occurred_at<? AND "
    done = pd.read_sql_query(base + "s.status IN ('done','manual') AND s.data_complete=1", c, params=(s, e))["wait_h"]
    queued = pd.read_sql_query(base + "s.status='queued'", c, params=(s, e))["wait_h"]
    return {"p90_wait_hours": [float(done.quantile(0.9)), float(queued.quantile(0.9))]}


def o_trend_weekly(c):
    return {"latest_complete_week_median": float(_weekly(c).iloc[-2])}


def _weekly(c) -> pd.Series:
    df = pd.read_sql_query("SELECT gate_out_at, dwell_h/24.0 d FROM consignments WHERE gate_out_at>=? AND gate_out_at<=?", c, params=(JUL[0], AS_OF))
    t = pd.to_datetime(df["gate_out_at"], utc=True).dt.tz_convert("Africa/Lagos").dt.tz_localize(None)
    df["w"] = t.dt.to_period("W-SUN").dt.start_time
    g = df.groupby("w")["d"].agg(["median", "count"])
    return g[g["count"] >= 30]["median"]


def o_high_alerts(c):
    return {"open_high_alerts": float(q1(c, "SELECT COUNT(*) FROM alerts WHERE severity='high' AND status IN ('open','acknowledged','under_review') AND detected_at<=?", AS_OF))}


def o_latest_remit(c):
    rows = c.execute("SELECT entity_id, days_late FROM remittances WHERE paid_at IS NOT NULL AND paid_at<=? ORDER BY days_late DESC LIMIT 1", (AS_OF,)).fetchone()
    return {"max_days_late": float(rows[1])}


def o_bankc_lag(c):
    return {"avg_lag_hours": q1(c, "SELECT AVG(lag_hours) FROM settlement_batches WHERE bank='C' AND closed_at>=? AND closed_at<? AND completed_at<=?", wat(2026, 9, 14), wat(2026, 9, 21), AS_OF)}


def o_faan_completeness(c):
    t0 = (datetime.strptime(AS_OF, "%Y-%m-%dT%H:%M:%SZ") - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"completeness_pct": 100 * q1(c, "SELECT AVG(data_complete) FROM stage_events WHERE owner_entity='FAAN' AND status IN ('done','manual') AND occurred_at>? AND occurred_at<=?", t0, AS_OF)}


def o_live15(c):
    t0 = (datetime.strptime(AS_OF, "%Y-%m-%dT%H:%M:%SZ") - timedelta(minutes=15)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"events": float(q1(c, "SELECT COUNT(*) FROM live_events WHERE occurred_at>=? AND occurred_at<=?", t0, AS_OF))}


def o_today_vs_yesterday(c):
    h = 12 + 1
    today = metric(c, "paid", wat(2026, 10, 5), AS_OF)
    yday = metric(c, "paid", wat(2026, 10, 4), (datetime.strptime(AS_OF, "%Y-%m-%dT%H:%M:%SZ") - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"))
    return {"today": today, "yesterday_same_time": yday}


def o_driver(c):
    a, s_ = by(c, "paid", *AUG, "entity_id"), by(c, "paid", S, E_SEP, "entity_id")
    delta = {k: s_.get(k, 0) - a.get(k, 0) for k in CONSOLIDATED}
    top = max(delta, key=lambda k: abs(delta[k]))
    ca, cs = by(c, "paid", *AUG, "origin_country", top), by(c, "paid", S, E_SEP, "origin_country", top)
    cd = {k: cs.get(k, 0) - ca.get(k, 0) for k in set(ca) | set(cs)}
    best = max(cd, key=lambda k: abs(cd[k]))
    return {"entity_change": abs(delta[top]), "country_change": abs(cd[best])}


def o_npa_change(c):
    a, s_ = metric(c, "assessed", *AUG, "NPA"), metric(c, "assessed", S, E_SEP, "NPA")
    return {"august": a, "september": s_, "change_pct": (s_ - a) / a * 100}


def o_howarewe(c):
    return {"assessed_mtd": metric(c, "assessed", OCT1, cut(T_NOW)), "paid_mtd": metric(c, "paid", OCT1, cut(T_NOW))}


def o_none(c):
    return {}


def o_consignments_sep(c):
    return {"consignments": float(q1(c, "SELECT COUNT(*) FROM consignments WHERE manifested_at>=? AND manifested_at<? AND manifested_at<=?", S, E_SEP, AS_OF))}


def o_modes_sep(c):
    rows = dict(c.execute("SELECT mode, COUNT(*) FROM consignments WHERE manifested_at>=? AND manifested_at<? GROUP BY 1", (S, E_SEP)).fetchall())
    return {"sea": float(rows["sea"]), "air": float(rows["air"])}


def o_npa_assessed_aug(c):
    return {"npa_assessed_aug": metric(c, "assessed", *AUG, "NPA")}


def o_settled_july(c):
    return {"settled_july": metric(c, "settled", *JUL)}


def o_nrs_sep(c):
    return {"nrs_assessed_sep": metric(c, "assessed", S, E_SEP, "NRS")}


def o_expenses_aug(c):
    ops = q1(c, "SELECT SUM(amount_ngn_minor) FROM expenses WHERE occurred_at>=? AND occurred_at<? AND occurred_at<=? AND entity_id IN (%s)" % ",".join("?" * len(CONSOLIDATED)), AUG[0], AUG[1], AS_OF, *CONSOLIDATED) / 100.0
    return {"operating_expenses_aug": [ops, ledger(c, list(CONSOLIDATED), AUG[0], AUG[1], ("Expense",), "dr")]}


def o_ncs_paid_jul(c):
    return {"ncs_paid_july": metric(c, "paid", *JUL, "NCS")}


def o_nrs_top_origin(c):
    d = by(c, "assessed", S, E_SEP, "origin_country", "NRS")
    return {"top_origin_nrs": max(d.values())}


def o_alerts_sep(c):
    return {"alerts_in_sep": float(q1(c, "SELECT COUNT(*) FROM alerts WHERE detected_at>=? AND detected_at<?", S, E_SEP))}


def o_bankA_batches(c):
    return {"batches": float(q1(c, "SELECT COUNT(*) FROM settlement_batches WHERE bank='A' AND closed_at>=? AND closed_at<?", S, E_SEP))}


def o_fx_sep5(c):
    return {"usd_ngn": q1(c, "SELECT rate_to_ngn FROM fx_rates WHERE ccy='USD' AND ts_utc=?", wat(2026, 9, 5))}


def o_dwell_ports(c):
    return {"apapa_p50": float(dwell_days(c, S, E_SEP, port="NGAPP").median()), "tincan_p50": float(dwell_days(c, S, E_SEP, port="NGTIN").median())}


def o_red_lane(c):
    return {"red_lane": float(q1(c, "SELECT COUNT(*) FROM consignments WHERE risk_lane='red' AND manifested_at>=? AND manifested_at<?", S, E_SEP))}


def o_npa_paid_sep(c):
    return {"npa_paid_sep": metric(c, "paid", S, E_SEP, "NPA")}


def o_remitted_sep(c):
    return {"remitted_sep": q1(c, "SELECT SUM(amount_ngn_minor) FROM remittances WHERE paid_at>=? AND paid_at<? AND paid_at<=?", S, E_SEP, AS_OF) / 100.0}


def o_china_aug(c):
    return {"china_aug": float(q1(c, "SELECT COUNT(*) FROM consignments WHERE origin_country='CN' AND manifested_at>=? AND manifested_at<?", *AUG))}


def o_electronics_sep(c):
    return {"electronics_assessed": metric(c, "assessed", S, E_SEP, commodity_group="Electronics")}


def o_bankc_in_transit(c):
    return {"bank_c_in_transit": q1(c, "SELECT SUM(a.amount_ngn_minor) FROM payment_allocations a JOIN payments p ON p.payment_id=a.payment_id WHERE p.bank='C' AND a.paid_at<=? AND (a.settled_at IS NULL OR a.settled_at>?)", AS_OF, AS_OF) / 100.0}


def o_nesrea_assessed_q3(c):
    return {"nesrea_q3": metric(c, "assessed", JUL[0], wat(2026, 10, 1), "NESREA")}


def o_ncs_refunds(c):
    return {"ncs_refunds_since_july": q1(c, "SELECT SUM(amount_ngn_minor) FROM refunds WHERE entity_id='NCS' AND fee_code!='UNAPPLIED' AND occurred_at>=? AND occurred_at<=?", JUL[0], AS_OF) / 100.0}


def o_son_remit_overdue(c):
    return {"son_days_late": float(q1(c, "SELECT days_late FROM remittances WHERE entity_id='SON' AND paid_at IS NOT NULL AND paid_at<=? ORDER BY days_late DESC LIMIT 1", AS_OF))}


def o_scanner_alert(c):
    return {"alerts_phys": float(q1(c, "SELECT COUNT(*) FROM alerts WHERE rule_code='R-PHYS-01' AND detected_at<=?", AS_OF))}


def o_unmatched_memo(c):
    return {"memo_amount": 18500.0}


ORACLES = {k[2:]: v for k, v in dict(globals()).items() if k.startswith("o_") and callable(v)}
ORACLES["nesrea_q3"] = ORACLES["nesrea_assessed_q3"]


def sample_trace_ref(c) -> str:
    return c.execute("SELECT nsw_ref FROM consignments WHERE gate_out_at<? AND status='completed' ORDER BY nsw_ref LIMIT 1 OFFSET 40", (wat(2026, 8, 15),)).fetchone()[0]


def o_trace(c):
    ref = sample_trace_ref(c)
    rows = c.execute("SELECT entity_id, SUM(amount_ngn_minor) FROM fee_assessments WHERE nsw_ref=? GROUP BY 1", (ref,)).fetchall()
    return {"total_assessed": sum(r[1] for r in rows) / 100.0}


def o_largest_ncs_payment(c):
    r = c.execute("SELECT nsw_ref, SUM(amount_ngn_minor) s FROM payment_allocations WHERE entity_id='NCS' AND paid_at>=? AND paid_at<? GROUP BY payment_id ORDER BY s DESC LIMIT 1", (wat(2026, 10, 4), wat(2026, 10, 5))).fetchone()
    tot = q1(c, "SELECT SUM(amount_ngn_minor) FROM payment_allocations WHERE nsw_ref=?", r[0])
    alloc = c.execute("SELECT MAX(amount_ngn_minor) FROM payment_allocations WHERE entity_id='NCS' AND paid_at>=? AND paid_at<?", (wat(2026, 10, 4), wat(2026, 10, 5))).fetchone()[0]
    return {"largest_ncs_payment": [r[1] / 100.0, alloc / 100.0], "total_paid_on_consignment": tot / 100.0}




def o_week_dwell(c):
    w = _weekly(c)
    return {"weekly_median_dwell": float(w.loc[pd.Timestamp("2026-09-14")])}


ORACLES["trace"], ORACLES["largest_ncs_payment"], ORACLES["week_dwell"] = o_trace, o_largest_ncs_payment, o_week_dwell
