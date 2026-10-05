"""Clearance performance: dwell/clearance percentiles, stage waterfall, digital vs physical, bottlenecks, target tracker."""
from __future__ import annotations

import numpy as np
import pandas as pd

from nsw_sim import clock, db
from nsw_sim.analytics.queries import Filters, now_iso

H_PER_D = 24.0
STAGE_NAMES = {"S01": "Manifest accepted", "S02": "Permits approved", "S03": "Declaration filed", "S04": "Valuation & assessment",
               "S05": "Payment confirmed", "S06": "Examination", "S07": "Customs release", "S08": "Terminal charges & gate pass",
               "S09": "Truck call-up & evacuation"}
STAGE_OWNER_LABEL = {"S02": "Permitting agencies", "S03": "Agent / NSW", "S04": "NCS", "S05": "Banks / CBN rail", "S06": "NCS (physical)",
                     "S07": "NCS", "S08": "Terminal operator", "S09": "Haulage / terminal"}
CONTROLLABLE = {"S02": True, "S03": True, "S04": True, "S05": True, "S07": True, "S06": False, "S08": False, "S09": False}
STAGE_TYPE = {"S01": "D", "S02": "D", "S03": "D", "S04": "D", "S05": "D", "S06": "P", "S07": "D", "S08": "P", "S09": "P"}


def _cons_where(f: Filters, tcol: str, params: list, alias: str = "") -> str:
    a = f"{alias}." if alias else ""
    w = " WHERE 1=1"
    if f.start:
        w += f" AND {a}{tcol} >= ?"
        params.append(f.start)
    if f.end:
        w += f" AND {a}{tcol} < ?"
        params.append(f.end)
    for vals, col in ((f.ports, "port"), (f.origins, "origin_country"), (f.modes, "mode"), (f.commodities, "commodity_group")):
        if vals:
            w += f" AND {a}{col} IN ({','.join('?' * len(vals))})"
            params.extend(vals)
    return w


def completed(f: Filters, as_of: str | None = None) -> pd.DataFrame:
    """Consignments that gated out in the period (dwell is only known once they have left)."""
    params: list = []
    w = _cons_where(f, "gate_out_at", params) + " AND gate_out_at IS NOT NULL"
    return pd.read_sql_query(f"SELECT nsw_ref, mode, port, origin_country, commodity_group, bank, risk_lane, arrived_at, declared_at, released_at, "
                             f"gate_out_at, dwell_h, clearance_h, exit_h, digital_h, physical_h, handoff_h, teu, weight_kg, cif_value_ngn_minor "
                             f"FROM v_consignments{w}", db.reader(as_of), params=params)


def _pcts(s: pd.Series) -> dict:
    s = s.dropna()
    if s.empty:
        return {"n": 0, "p50": None, "p75": None, "p90": None, "p95": None, "mean": None}
    q = s.quantile([0.5, 0.75, 0.9, 0.95])
    return {"n": int(len(s)), "p50": q[0.5], "p75": q[0.75], "p90": q[0.9], "p95": q[0.95], "mean": float(s.mean())}


def dwell_table(f: Filters, by: str | None = None, as_of: str | None = None, target_days: float = 7.0) -> pd.DataFrame:
    """Percentiles (days) of dwell, clearance and release-to-exit; optionally by port/mode/commodity_group/origin_country/risk_lane."""
    df = completed(f, as_of)
    rows = []
    groups = [("ALL", df)] if not by else list(df.groupby(by))
    for key, g in groups:
        r = {"group": key}
        for name, col in (("dwell", "dwell_h"), ("clearance", "clearance_h"), ("exit", "exit_h")):
            for k, v in _pcts(g[col] / H_PER_D).items():
                r[f"{name}_{k}"] = v
        r["share_over_target"] = float((g["dwell_h"] / H_PER_D > target_days).mean()) if len(g) else None
        rows.append(r)
    return pd.DataFrame(rows)


def stage_summary(f: Filters, as_of: str | None = None) -> pd.DataFrame:
    """Per stage (done rows with complete timestamps): medians of wait, duration, total; SLA breach rate; digital/physical type."""
    params: list = []
    w = _cons_where(f, "occurred_at", params, "s") + " AND s.status IN ('done','manual') AND s.data_complete=1 AND s.wait_h IS NOT NULL"
    join = "JOIN v_consignments c ON c.nsw_ref = s.nsw_ref" if (f.ports or f.origins or f.modes or f.commodities) else ""
    if join:   # consignment dims live on c
        w = w.replace("s.port", "c.port").replace("s.origin_country", "c.origin_country").replace("s.mode", "c.mode").replace("s.commodity_group", "c.commodity_group")
    df = pd.read_sql_query(f"SELECT s.stage, s.system_type, s.owner_entity, s.wait_h, s.dur_h, s.sla_breach FROM v_stage_durations s {join}{w}",
                           db.reader(as_of), params=params)
    out = []
    for st, g in df.groupby("stage"):
        tot = g["wait_h"] + g["dur_h"]
        out.append({"stage": st, "name": STAGE_NAMES.get(st, st), "type": g["system_type"].iloc[0], "n": len(g), "median_wait_h": g["wait_h"].median(),
                    "median_dur_h": g["dur_h"].median(), "median_total_h": tot.median(), "mean_total_h": tot.mean(), "p90_total_h": tot.quantile(0.9),
                    "sla_breach_rate": g["sla_breach"].dropna().mean() if g["sla_breach"].notna().any() else None,
                    "owner": g["owner_entity"].mode().iloc[0] if len(g) else None, "controllable_by_nsw": CONTROLLABLE.get(st)})
    return pd.DataFrame(out).sort_values("stage") if out else pd.DataFrame()


def digital_physical(f: Filters, grain: str = "week", as_of: str | None = None) -> pd.DataFrame:
    df = completed(f, as_of)
    if df.empty:
        return pd.DataFrame(columns=["period", "digital_share", "physical_share", "digital_h", "physical_h", "n"])
    g = pd.to_datetime(df["gate_out_at"], utc=True).dt.tz_convert("Africa/Lagos").dt.tz_localize(None)
    key = g.dt.to_period("W-SUN").dt.start_time.dt.strftime("%Y-%m-%d") if grain == "week" else g.dt.strftime("%Y-%m-%d")
    t = df.assign(period=key).groupby("period").agg(digital_h=("digital_h", "sum"), physical_h=("physical_h", "sum"), n=("nsw_ref", "count")).reset_index()
    t["digital_share"] = t["digital_h"] / (t["digital_h"] + t["physical_h"])
    t["physical_share"] = 1 - t["digital_share"]
    return t


def overall_digital_share(f: Filters, as_of: str | None = None) -> float | None:
    df = completed(f, as_of)
    if df.empty:
        return None
    return float(df["digital_h"].sum() / (df["digital_h"].sum() + df["physical_h"].sum()))


def weekly_dwell(f: Filters, as_of: str | None = None, min_n: int = 30) -> pd.DataFrame:
    """Median dwell (days) by gate-out week (WAT, Monday start) with counts and p90."""
    df = completed(f, as_of)
    if df.empty:
        return pd.DataFrame(columns=["week", "median_days", "p90_days", "n"])
    g = pd.to_datetime(df["gate_out_at"], utc=True).dt.tz_convert("Africa/Lagos").dt.tz_localize(None)
    df = df.assign(week=g.dt.to_period("W-SUN").dt.start_time.dt.strftime("%Y-%m-%d"), d=df["dwell_h"] / H_PER_D)
    t = df.groupby("week").agg(median_days=("d", "median"), p90_days=("d", lambda s: s.quantile(0.9)), mean_days=("d", "mean"), n=("d", "count")).reset_index()
    return t[t["n"] >= min_n].reset_index(drop=True)


def handoff_waits(f: Filters, as_of: str | None = None) -> pd.DataFrame:
    s = stage_summary(f, as_of)
    if s.empty:
        return s
    return s[["stage", "name", "owner", "median_wait_h", "n"]].rename(columns={"median_wait_h": "median_handoff_wait_h"})


def bottlenecks(f: Filters, as_of: str | None = None) -> pd.DataFrame:
    """Mean hours per completed consignment by stage (S02 = slowest permitting agency), ranked by contribution to delay."""
    df = completed(f.with_(start=f.start, end=f.end), as_of)
    n = len(df)
    if not n:
        return pd.DataFrame()
    refs = df["nsw_ref"].tolist()
    conn = db.reader(as_of)
    parts = []
    for i in range(0, len(refs), 800):
        chunk = refs[i:i + 800]
        parts.append(pd.read_sql_query(
            f"SELECT nsw_ref, stage, MAX(wait_h+dur_h) AS h FROM v_stage_durations WHERE status IN ('done','manual') AND data_complete=1 "
            f"AND stage!='S01' AND nsw_ref IN ({','.join('?' * len(chunk))}) GROUP BY nsw_ref, stage", conn, params=chunk))
    s = pd.concat(parts)
    t = s.groupby("stage")["h"].sum().div(n).rename("mean_h_per_consignment").reset_index()
    t["share_of_total"] = t["mean_h_per_consignment"] / t["mean_h_per_consignment"].sum()
    t["name"] = t["stage"].map(STAGE_NAMES)
    t["owner"] = t["stage"].map(STAGE_OWNER_LABEL)
    t["type"] = t["stage"].map(STAGE_TYPE)
    t["controllable_by_nsw"] = t["stage"].map(CONTROLLABLE)
    return t.sort_values("mean_h_per_consignment", ascending=False).reset_index(drop=True)


def stage_percentile(stage: str, f: Filters, q: float = 0.9, column: str = "wait_h", as_of: str | None = None, queued: bool = False) -> dict:
    """Percentile of a stage metric (e.g. p90 exam wait at a port in a window). ``queued`` uses queue-entry planned values."""
    params: list = []
    w = _cons_where(f, "occurred_at", params, "s")
    for old in ("s.port", "s.origin_country", "s.mode", "s.commodity_group"):
        w = w.replace(old, old.replace("s.", "c."))
    status = "s.status='queued'" if queued else "s.status IN ('done','manual') AND s.data_complete=1"
    df = pd.read_sql_query(f"SELECT s.{column} AS v FROM v_stage_durations s JOIN v_consignments c ON c.nsw_ref=s.nsw_ref{w} AND s.stage=? AND {status}",
                           db.reader(as_of), params=params + [stage])
    v = df["v"].dropna()
    return {"n": int(len(v)), "value": float(v.quantile(q)) if len(v) else None, "median": float(v.median()) if len(v) else None}


def sla_breach_by_entity(f: Filters, as_of: str | None = None) -> pd.DataFrame:
    params: list = []
    w = _cons_where(f, "occurred_at", params) + " AND status IN ('done','manual') AND sla_breach IS NOT NULL"
    return pd.read_sql_query(f"SELECT owner_entity AS entity, stage, COUNT(*) AS n, AVG(sla_breach) AS breach_rate FROM v_stage_durations{w} "
                             f"GROUP BY 1,2 ORDER BY 1,2", db.reader(as_of), params=params)


def overall_sla_breach(f: Filters, as_of: str | None = None) -> float | None:
    df = sla_breach_by_entity(f, as_of)
    return float((df["breach_rate"] * df["n"]).sum() / df["n"].sum()) if not df.empty else None


def cost_of_delay(f: Filters, as_of: str | None = None, demurrage_per_teu_day: float = 28_000.0, storage_per_tonne_day: float = 4_500.0,
                  carrying_pct_pa: float = 0.18, reference_days: float = 7.0) -> dict:
    """Illustrative cost of excess dwell vs ``reference_days`` (assumptions editable in the UI; clearly labelled illustrative)."""
    df = completed(f, as_of)
    if df.empty:
        return {"n": 0, "excess_days_total": 0.0, "demurrage_ngn": 0.0, "storage_ngn": 0.0, "carrying_ngn": 0.0, "total_ngn": 0.0, "assumptions": {}}
    ex = (df["dwell_h"] / H_PER_D - reference_days).clip(lower=0)
    dem = float((ex * df["teu"].clip(lower=0)).sum() * demurrage_per_teu_day)
    sto = float((ex * df["weight_kg"] / 1000).sum() * storage_per_tonne_day)
    car = float((ex * df["cif_value_ngn_minor"] / MINOR_ * carrying_pct_pa / 365).sum())
    return {"n": int(len(df)), "excess_days_total": float(ex.sum()), "mean_excess_days": float(ex.mean()), "demurrage_ngn": dem, "storage_ngn": sto,
            "carrying_ngn": car, "total_ngn": dem + sto + car,
            "assumptions": {"demurrage_per_teu_day": demurrage_per_teu_day, "storage_per_tonne_day": storage_per_tonne_day,
                            "carrying_pct_pa": carrying_pct_pa, "reference_days": reference_days}}


MINOR_ = 100.0
