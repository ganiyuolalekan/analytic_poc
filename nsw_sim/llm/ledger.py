"""Cost ledger: aggregates ``llm_calls`` by role / model / day (Section 4.5).

Shows tokens always; shows an estimated cost only if ``llm.llm_prices_per_mtok`` is set in settings.yaml
(we do not guess prices): ``{model_id: {in: <USD per 1M input tokens>, out: <USD per 1M output tokens>}}``.
"""
from __future__ import annotations

import sqlite3

import pandas as pd

from nsw_sim import db
from nsw_sim.config import settings


def _price(model: str) -> dict | None:
    prices = settings()["llm"].get("llm_prices_per_mtok") or {}
    return prices.get(model)


def ledger(conn: sqlite3.Connection | None = None, since: str | None = None) -> pd.DataFrame:
    """Rows: day, role, model, calls, cached_calls, tokens_in, tokens_out, errors, fallbacks, est_cost_usd."""
    c = conn or db.connect()
    q = ("SELECT substr(ts,1,10) AS day, role, model, COUNT(*) AS calls, SUM(cached) AS cached_calls, "
         "SUM(tokens_in) AS tokens_in, SUM(tokens_out) AS tokens_out, "
         "SUM(outcome='error') AS errors, SUM(outcome IN ('fallback','repaired')) AS validation_issues, "
         "AVG(CASE WHEN cached=0 THEN latency_ms END) AS avg_latency_ms FROM llm_calls "
         + ("WHERE ts>=? " if since else "") + "GROUP BY day, role, model ORDER BY day, role")
    df = pd.read_sql_query(q, c, params=(since,) if since else ())
    if conn is None:
        c.close()
    if df.empty:
        return df.assign(est_cost_usd=pd.Series(dtype=float))

    def cost(r) -> float | None:
        p = _price(r["model"])
        return None if not p else (r["tokens_in"] * p["in"] + r["tokens_out"] * p["out"]) / 1e6
    df["est_cost_usd"] = df.apply(cost, axis=1)
    return df


def summary(conn: sqlite3.Connection | None = None, since: str | None = None) -> dict:
    df = ledger(conn, since)
    if df.empty:
        return {"calls": 0, "live_calls": 0, "cached_calls": 0, "cache_hit_rate": None, "tokens_in": 0, "tokens_out": 0,
                "tokens_total": 0, "est_cost_usd": None, "priced": False, "by_role": {}, "by_model": {}}
    calls, cached = int(df["calls"].sum()), int(df["cached_calls"].sum())
    priced = df["est_cost_usd"].notna().any()
    by_role = df.groupby("role")[["calls", "tokens_in", "tokens_out"]].sum().astype(int).to_dict("index")
    by_model = df.groupby("model")[["calls", "tokens_in", "tokens_out"]].sum().astype(int).to_dict("index")
    return {"calls": calls, "live_calls": calls - cached, "cached_calls": cached,
            "cache_hit_rate": cached / calls if calls else None,
            "tokens_in": int(df["tokens_in"].sum()), "tokens_out": int(df["tokens_out"].sum()),
            "tokens_total": int(df["tokens_in"].sum() + df["tokens_out"].sum()),
            "est_cost_usd": float(df["est_cost_usd"].sum()) if priced else None, "priced": bool(priced),
            "errors": int(df["errors"].sum()), "validation_issues": int(df["validation_issues"].sum()),
            "by_role": by_role, "by_model": by_model}


def format_summary(s: dict) -> str:
    if not s["calls"]:
        return "No model calls logged yet."
    lines = [f"Model calls: {s['calls']} ({s['live_calls']} live, {s['cached_calls']} cached; "
             f"cache hit rate {s['cache_hit_rate']:.0%})",
             f"Tokens: {s['tokens_in']:,} in + {s['tokens_out']:,} out = {s['tokens_total']:,}  "
             f"(errors {s.get('errors', 0)}, repaired/fallback {s.get('validation_issues', 0)})"]
    lines.append(f"Estimated cost: ${s['est_cost_usd']:.2f}" if s["priced"] else
                 "Estimated cost: n/a (tokens only: set llm.llm_prices_per_mtok in config/settings.yaml to price them)")
    for role, v in sorted(s["by_role"].items()):
        lines.append(f"  {role:10s} calls={v['calls']:5d}  in={v['tokens_in']:>9,}  out={v['tokens_out']:>9,}")
    return "\n".join(lines)
