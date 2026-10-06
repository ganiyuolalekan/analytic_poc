"""Assistant tools (Section 13.2): deterministic Python, JSON in/out, all take an optional ``as_of``.
Each returns ``{ok, data, row_count, sql_or_formula, as_of, notes}``; errors are structured, never stack traces.
Naira values are returned as plain naira (not kobo) together with a display string."""
from __future__ import annotations

import ast
import calendar
import json
import operator
import re
import time
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, getcontext

import pandas as pd

from nsw_sim import clock, db
from nsw_sim.analytics import clearance, queries, reconcile, statements
from nsw_sim.analytics import trace as trace_mod
from nsw_sim.analytics.queries import Filters
from nsw_sim.assistant import guardrails
from nsw_sim.money import fmt_ngn
from nsw_sim.sim.reference import consolidated_entities, entity_cards

getcontext().prec = 28
MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})
MONTHS["sept"] = 9


def _mid(d: date) -> str:
    return clock.iso(clock.wat_midnight_utc(d))


# =============================================================================== resolve_period
def resolve_period(text: str, as_of: str | None = None) -> dict:
    """'last month', 'Q3', 'this week', 'since July', 'between 12 and 15 Aug' -> exact UTC start/end (end exclusive) and a WAT label."""
    as_of = as_of or queries.watermark() or queries.now_iso()
    now = clock.to_dt(as_of)
    today = clock.wat(now).date()
    year = today.year
    t = " ".join((text or "").lower().replace(",", " ").split())

    def res(a: str, b: str, label: str, assumption: str | None = None) -> dict:
        b = min(b, as_of) if b > as_of else b
        return {"start": a, "end": b, "label": label, "assumption": assumption, "as_of": as_of}

    def month_range(y: int, m: int) -> tuple[str, str]:
        d0 = date(y, m, 1)
        d1 = date(y + (m == 12), (m % 12) + 1, 1)
        return _mid(d0), _mid(d1)
    yr = re.search(r"\b(20\d{2})\b", t)
    if yr:
        year = int(yr.group(1))
    m = re.search(r"last\s+(\d+)\s+(minute|min|hour|hr|day|week)s?", t)
    if m:
        n, u = int(m.group(1)), m.group(2)
        if u in ("day", "week"):
            days = n * (7 if u == "week" else 1)
            return res(_mid(today - timedelta(days=days - 1)), as_of, f"last {n} {u}s (to now)")
        secs = n * (60 if u.startswith("min") else 3600)
        return res(clock.iso(now - timedelta(seconds=secs)), as_of, f"last {n} {u}s")
    if re.search(r"\b(right now|now|live|currently)\b", t) and "since" not in t:
        return res(clock.iso(now - timedelta(minutes=60)), as_of, "last 60 minutes", "I took 'now' as the last 60 minutes.")
    if "day before yesterday" in t:
        return res(_mid(today - timedelta(days=2)), _mid(today - timedelta(days=1)), f"{today - timedelta(days=2)}")
    if "yesterday" in t:
        return res(_mid(today - timedelta(days=1)), _mid(today), f"{today - timedelta(days=1)} (yesterday)")
    if re.search(r"\btoday\b", t):
        return res(_mid(today), as_of, f"{today} (today, to now)")
    m = re.search(r"since\s+(?:1\s+|the\s+start\s+of\s+)?(" + "|".join(MONTHS) + r")\b(?:\s+(\d{1,2}))?", t) or re.search(r"since\s+(\d{1,2})\s+(" + "|".join(MONTHS) + r")\b", t)
    if m:
        if m.group(1).isdigit():
            d = date(year, MONTHS[m.group(2)], int(m.group(1)))
        else:
            d = date(year, MONTHS[m.group(1)], int(m.group(2)) if m.group(2) else 1)
        return res(_mid(d), as_of, f"since {d:%d %B %Y} (to now)")
    m = re.search(r"week of\s+(\d{1,2})(?:st|nd|rd|th)?\s+(" + "|".join(MONTHS) + r")|week of\s+(" + "|".join(MONTHS) + r")\s+(\d{1,2})", t)
    if m:
        d = date(year, MONTHS[m.group(2) or m.group(3)], int(m.group(1) or m.group(4)))
        mon = d - timedelta(days=d.weekday())
        return res(_mid(mon), _mid(mon + timedelta(days=7)), f"week of {mon:%d %b %Y} (Mon-Sun)", None if d == mon else f"I took the Monday-to-Sunday week containing {d:%d %b}.")
    m = re.search(r"(?:between\s+)?(\d{1,2})(?:st|nd|rd|th)?\s*(?:-|–|to|and)\s*(\d{1,2})(?:st|nd|rd|th)?\s+(" + "|".join(MONTHS) + r")\b", t)
    if m:
        a, b, mo = int(m.group(1)), int(m.group(2)), MONTHS[m.group(3)]
        return res(_mid(date(year, mo, a)), _mid(date(year, mo, b) + timedelta(days=1)), f"{a}-{b} {calendar.month_name[mo]} {year} (inclusive)")
    m = re.search(r"\bq([1-4])\b", t)
    if m:
        q = int(m.group(1))
        a, _ = month_range(year, 3 * (q - 1) + 1)
        _, b = month_range(year, 3 * q)
        return res(a, b, f"Q{q} {year}")
    if "quarter to date" in t or "qtd" in t or "this quarter" in t:
        q0 = 3 * ((today.month - 1) // 3) + 1
        return res(month_range(year, q0)[0], as_of, f"quarter to date ({year})")
    if "last quarter" in t:
        q0 = 3 * ((today.month - 1) // 3) + 1
        pm, py = (q0 - 3, year) if q0 > 3 else (10, year - 1)
        return res(month_range(py, pm)[0], month_range(py, pm + 2)[1], f"last quarter ({py} from {calendar.month_name[pm]})")
    if "last week" in t:
        mon = today - timedelta(days=today.weekday() + 7)
        return res(_mid(mon), _mid(mon + timedelta(days=7)), f"last week ({mon:%d %b} to {mon + timedelta(days=6):%d %b})")
    if "this week" in t or "week to date" in t:
        mon = today - timedelta(days=today.weekday())
        return res(_mid(mon), as_of, f"this week (from {mon:%d %b})")
    if "last month" in t:
        d1 = today.replace(day=1)
        d0 = (d1 - timedelta(days=1)).replace(day=1)
        return res(_mid(d0), _mid(d1), f"last month ({d0:%B %Y})")
    if "month to date" in t or "mtd" in t or "this month" in t:
        return res(_mid(today.replace(day=1)), as_of, f"month to date ({today:%B %Y})")
    if "year to date" in t or "ytd" in t:
        return res(_mid(date(year, 1, 1)), as_of, f"year to date ({year})")
    m = re.search(r"(?:on\s+)?(\d{1,2})(?:st|nd|rd|th)?\s+(" + "|".join(MONTHS) + r")\b|(" + "|".join(MONTHS) + r")\s+(\d{1,2})(?:st|nd|rd|th)?\b", t)
    if m:
        d = date(year, MONTHS[m.group(2) or m.group(3)], int(m.group(1) or m.group(4)))
        return res(_mid(d), _mid(d + timedelta(days=1)), f"{d:%d %B %Y}")
    m = re.search(r"\b(" + "|".join(MONTHS) + r")\b", t)
    if m:
        mo = MONTHS[m.group(1)]
        a, b = month_range(year, mo)
        return res(a, b, f"{calendar.month_name[mo]} {year}")
    return res(_mid(today.replace(day=1)), as_of, f"month to date ({today:%B %Y})", "No period was given, so I assumed the current month to date.")


# =============================================================================== helpers
def _ok(data, rows: int | None = None, formula: str = "", as_of: str | None = None, notes: str | list | None = None) -> dict:
    return {"ok": True, "data": data, "row_count": rows if rows is not None else (len(data) if isinstance(data, (list, dict)) else 1), "sql_or_formula": formula,
            "as_of": as_of, "notes": notes or ""}


def _err(msg: str, as_of: str | None = None) -> dict:
    return {"ok": False, "error": msg, "data": None, "row_count": 0, "sql_or_formula": "", "as_of": as_of, "notes": ""}


def _naira(minor) -> float:
    return round(float(minor) / 100.0, 2)


def _money(minor) -> dict:
    return {"naira": _naira(minor), "display": fmt_ngn(minor)}


def _filters(period: dict | str | None, flt: dict | None, as_of: str) -> tuple[Filters, dict]:
    p = resolve_period(period, as_of) if isinstance(period, str) or period is None else {
        "start": period.get("start"), "end": min(period.get("end") or as_of, as_of), "label": period.get("label") or f"{period.get('start')} → {period.get('end')}", "assumption": None}
    flt = flt or {}

    def tup(k: str, alt: str | None = None):
        v = flt.get(k) or (flt.get(alt) if alt else None) or ()
        return tuple([v] if isinstance(v, str) else v)
    f = Filters(start=p["start"], end=p["end"], entities=tup("entities", "entity"), origins=tup("origins", "origin_country"), modes=tup("modes", "mode"), ports=tup("ports", "port"),
                commodities=tup("commodities", "commodity"), processes=tup("processes", "process"), fee_codes=tup("fee_codes", "fee_code"), currency_view=flt.get("currency_view", "NGN"))
    return f, p


MONEY_METRICS = {"assessed", "paid", "settled", "settled_gross", "remitted", "outstanding", "in_transit", "expenses", "collection_cost", "refunds", "expected"}
DIMS = {"entity", "process", "fee_code", "origin_country", "port", "mode", "commodity", "day", "week", "month", "category"}
ENTITY_ALIASES = {"customs": "NCS", "ncs": "NCS", "firs": "NRS", "nrs": "NRS", "ports authority": "NPA", "npa": "NPA", "nimasa": "NIMASA", "son": "SON", "nafdac": "NAFDAC", "naqs": "NAQS",
                  "nesrea": "NESREA", "faan": "FAAN", "airports": "FAAN", "cbn": "CBN", "central bank": "CBN", "nsw": "NSW", "mof": "MOF-FA", "treasury": "MOF-FA"}


def _norm_entities(f: Filters) -> Filters:
    ents = []
    for e in f.entities:
        ents.append(ENTITY_ALIASES.get(str(e).lower(), str(e).upper()))
    return f.with_(entities=tuple(ents))


# =============================================================================== tools
def list_entities(as_of: str | None = None, **_) -> dict:
    df = queries.entities_df(as_of)
    return _ok([{"code": r.code, "name": r.name, "type": r.type} for r in df.itertuples()], formula="entities reference table", as_of=as_of)


def get_entity_profile(entity: str, as_of: str | None = None, **_) -> dict:
    code = ENTITY_ALIASES.get(str(entity).lower(), str(entity).upper())
    card = entity_cards().get(code)
    if not card:
        return _err(f"unknown entity {entity!r}; codes: {', '.join(entity_cards())}", as_of)
    prof = queries.entity_profile(code, as_of)
    fees = [{"fee_code": r["fee_code"], "name": r["name"]} for r in (prof or {}).get("fee_rules", [])]
    return _ok({"code": code, "name": card["name"], "type": card["type"], "mandate": card["mandate"], "processes": card["processes"], "fee_codes": fees,
                "remittance_rule": card["remittance"]}, formula="entity card + stored profile (fee names only)", as_of=as_of)


def _metric_value(metric: str, groups: list[str], f: Filters, as_of: str):
    """Returns DataFrame with group columns + value (+ unit info)."""
    if metric in queries.METRICS:
        df = queries.aggregate(metric, groups, f, as_of)
        return df, ("money" if metric in MONEY_METRICS else "count"), f"SUM over {queries.METRICS[metric][0]}.{queries.METRICS[metric][2]} for {queries.METRICS[metric][1]} in [start,end) grouped by {groups or 'total'}"
    if metric == "avg_fee":
        a = queries.aggregate("assessed", groups, f, as_of)
        a["value"] = a["value"] / a["rows"].where(a["rows"] > 0)
        return a, "money", "assessed amount divided by number of assessments"
    if metric == "surplus":
        ents = list(f.entities) or consolidated_entities()
        rows = []
        for e in ents:
            p = statements.performance(e, f, as_of)
            rows.append({"entity": e, "value": p["surplus"], "rows": 1})
        df = pd.DataFrame(rows)
        if not groups:
            df = pd.DataFrame([{"value": df["value"].sum(), "rows": len(df)}])
        return df, "money", "ledger revenue minus expenses (statement of financial performance)"
    if metric == "collection_efficiency":
        a, s = queries.aggregate("assessed", groups, f, as_of), queries.aggregate("settled_gross", groups, f, as_of)
        d = a.merge(s, on=groups, suffixes=("_a", "_s")) if groups else pd.DataFrame({"value_a": a["value"], "value_s": s["value"], "rows_a": a["rows"]})
        d["value"] = d["value_s"] / d["value_a"].where(d["value_a"] > 0)
        d["rows"] = d["rows_a"]
        return d[[*groups, "value", "rows"]], "ratio", "settled (gross) divided by assessed"
    if metric == "cost_per_100":
        a, s = queries.aggregate("collection_cost", groups, f, as_of), queries.aggregate("settled_gross", groups, f, as_of)
        d = a.merge(s, on=groups, suffixes=("_c", "_s")) if groups else pd.DataFrame({"value_c": a["value"], "value_s": s["value"], "rows_c": a["rows"]})
        d["value"] = 100 * d["value_c"] / d["value_s"].where(d["value_s"] > 0)
        d["rows"] = d["rows_c"]
        return d[[*groups, "value", "rows"]], "naira_per_100", "100 x collection cost divided by gross settled amount"
    if metric in ("dwell_p50", "dwell_p90", "clearance_p50") and groups in (["week"], ["month"]):
        done = clearance.completed(f, as_of)
        col = "clearance_h" if metric == "clearance_p50" else "dwell_h"
        if done.empty:
            return pd.DataFrame(columns=[groups[0], "value", "rows"]), "days", metric
        t = pd.to_datetime(done["gate_out_at"], utc=True).dt.tz_convert("Africa/Lagos").dt.tz_localize(None)
        key = t.dt.to_period("W-SUN").dt.start_time.dt.strftime("%Y-%m-%d") if groups[0] == "week" else t.dt.strftime("%Y-%m")
        d = (done[col] / 24.0).groupby(key)
        q = 0.9 if metric == "dwell_p90" else 0.5
        out = pd.DataFrame({groups[0]: d.quantile(q).index, "value": d.quantile(q).values, "rows": d.size().values})
        return out, "days", f"{metric} by gate-out {groups[0]} (Monday-start weeks, WAT): percentile of dwell days for consignments that left in that {groups[0]}"
    if metric in ("dwell_p50", "dwell_p90", "clearance_p50"):
        by = {"port": "port", "mode": "mode", "commodity": "commodity_group", "origin_country": "origin_country"}.get(groups[0]) if groups else None
        t = clearance.dwell_table(f, by, as_of)
        col = {"dwell_p50": "dwell_p50", "dwell_p90": "dwell_p90", "clearance_p50": "clearance_p50"}[metric]
        d = pd.DataFrame({**({groups[0]: t["group"]} if groups else {}), "value": t[col], "rows": t["dwell_n"]})
        return d, "days", f"{metric}: percentile (days) of dwell/clearance for consignments that gated out in the period"
    if metric == "sla_breach_rate":
        t = clearance.sla_breach_by_entity(f, as_of)
        if groups == ["entity"]:
            g = t.groupby("entity").apply(lambda x: (x["breach_rate"] * x["n"]).sum() / x["n"].sum(), include_groups=False).reset_index(name="value")
            g["rows"] = t.groupby("entity")["n"].sum().values
            return g, "ratio", "share of stage records with processing time above SLA"
        v = clearance.overall_sla_breach(f, as_of)
        return pd.DataFrame([{"value": v, "rows": int(t["n"].sum()) if not t.empty else 0}]), "ratio", "share of stage records with processing time above SLA"
    raise ValueError(f"unknown metric {metric!r}")


METRIC_LIST = sorted(set(queries.METRICS) | {"avg_fee", "surplus", "collection_efficiency", "cost_per_100", "dwell_p50", "dwell_p90", "clearance_p50", "sla_breach_rate"})


def _rows(df: pd.DataFrame, groups: list[str], unit: str, limit: int = 200) -> list[dict]:
    out = []
    for r in df.head(limit).to_dict("records"):
        row = {g: r[g] for g in groups}
        v = r["value"]
        if v is None or v != v:
            row["value"] = None
        elif unit == "money":
            row.update(_money(v))
            row["value"] = _naira(v)
        elif unit == "ratio":
            row.update({"value": round(float(v), 6), "percent": round(float(v) * 100, 2)})
        else:
            row["value"] = round(float(v), 4) if unit != "count" else int(round(float(v)))
        row["records"] = int(r.get("rows", 0)) if r.get("rows") == r.get("rows") else 0
        out.append(row)
    return out


def aggregate(metric: str, group_by: list[str] | None = None, filters: dict | None = None, period: dict | str | None = None, compare_period: dict | str | None = None,
              as_of: str | None = None, **_) -> dict:
    as_of = as_of or queries.watermark() or queries.now_iso()
    groups = list(group_by or [])
    if metric not in METRIC_LIST:
        return _err(f"unknown metric {metric!r}; choose one of {METRIC_LIST}", as_of)
    bad = [g for g in groups if g not in DIMS]
    if bad:
        return _err(f"unknown group_by {bad}; choose from {sorted(DIMS)}", as_of)
    try:
        f, p = _filters(period, filters, as_of)
        f = _norm_entities(f)
        df, unit, formula = _metric_value(metric, groups, f, as_of)
        data = {"metric": metric, "period": p["label"], "assumption": p.get("assumption"), "group_by": groups, "rows": _rows(df, groups, unit)}
        if compare_period is not None:
            f2, p2 = _filters(compare_period, filters, as_of)
            df2, _, _ = _metric_value(metric, groups, _norm_entities(f2), as_of)
            data["compare_period"] = p2["label"]
            data["compare_rows"] = _rows(df2, groups, unit)
        return _ok(data, len(df), formula, as_of, f"filters: {f.describe()}")
    except Exception as e:  # noqa: BLE001
        return _err(f"{type(e).__name__}: {e}", as_of)


def top_n(metric: str, dimension: str, n: int = 5, period: dict | str | None = None, filters: dict | None = None, as_of: str | None = None,
          compare_period: dict | str | None = None, rank_by: str = "value", **_) -> dict:
    """Rank a dimension by a metric. With ``compare_period`` each row also carries the previous value, the change and change_pct (computed in code);
    ``rank_by`` = value | change | change_pct (use change_pct for 'grew the most')."""
    r = aggregate(metric, [dimension], filters, period, compare_period, as_of)
    if not r["ok"]:
        return r
    rows = [x for x in r["data"]["rows"] if x.get("value") is not None]
    if compare_period is not None:
        prev = {x[dimension]: x for x in r["data"].get("compare_rows", [])}
        for x in rows:
            p = prev.get(x[dimension], {}).get("value")
            x["previous_value"] = p
            if p is not None:
                x["change"] = round(x["value"] - p, 2)
                x["change_pct"] = round((x["value"] - p) / abs(p) * 100, 4) if p else None
        r["data"].pop("compare_rows", None)
    total = sum(x["value"] for x in rows if "naira" in x)          # money rows only: a share of the total is code's job, not the model's
    if total:
        for x in rows:
            if "naira" in x:
                x["share_pct"] = round(x["value"] / total * 100, 2)
    key = rank_by if rank_by in ("value", "change", "change_pct") else "value"
    rows = [x for x in rows if x.get(key) is not None]
    rows.sort(key=lambda x: x[key], reverse=True)
    r["data"]["rows"] = [{"rank": i + 1, **x} for i, x in enumerate(rows[: max(1, min(int(n), 50))])]
    r["data"]["total_groups"] = len(rows)
    r["row_count"] = len(r["data"]["rows"])
    r["sql_or_formula"] += f"; top {n} by value"
    return r


def trend(metric: str, grain: str = "day", period: dict | str | None = None, filters: dict | None = None, as_of: str | None = None, **_) -> dict:
    if grain not in ("hour", "day", "week", "month"):
        return _err("grain must be hour, day, week or month", as_of)
    r = aggregate(metric, [grain], filters, period, None, as_of)
    if not r["ok"]:
        return r
    rows = sorted(r["data"]["rows"], key=lambda x: x[grain])
    vals = [x["value"] for x in rows if x.get("value") is not None]
    stats = {}
    if len(vals) >= 2 and vals[0]:
        stats = {"first": vals[0], "last": vals[-1], "change_pct": round((vals[-1] - vals[0]) / abs(vals[0]) * 100, 2), "points": len(vals),
                 "avg_growth_per_period_pct": round(((vals[-1] / vals[0]) ** (1 / (len(vals) - 1)) - 1) * 100, 3) if vals[0] > 0 and vals[-1] > 0 else None}
    r["data"].update({"series": rows[-120:], "stats": stats})
    r["data"].pop("rows", None)
    r["row_count"] = len(rows)
    r["sql_or_formula"] += f"; by {grain} with simple growth statistics"
    return r


def compare(metric: str, items: list[str], period: dict | str | None = None, by: str = "entity", filters: dict | None = None, as_of: str | None = None, **_) -> dict:
    """Side by side with differences computed in code. ``by`` = entity | origin_country | port | mode | commodity, or 'period' (items are period phrases)."""
    as_of = as_of or queries.watermark() or queries.now_iso()
    out = []
    for it in items:
        if by == "period":
            r = aggregate(metric, [], filters, it, None, as_of)
            label = it
        else:
            fl = dict(filters or {})
            key = {"entity": "entities", "origin_country": "origins", "port": "ports", "mode": "modes", "commodity": "commodities"}[by]
            fl[key] = [it]
            r = aggregate(metric, [], fl, period, None, as_of)
            label = it
        if not r["ok"]:
            return r
        row = r["data"]["rows"][0] if r["data"]["rows"] else {"value": None}
        out.append({"item": label, "period": r["data"]["period"], **{k: v for k, v in row.items() if k in ("value", "naira", "display", "percent")}})
    diffs = []
    if len(out) >= 2 and all(o.get("value") is not None for o in out):
        a, b = out[0]["value"], out[1]["value"]
        diffs = [{"between": f"{out[0]['item']} vs {out[1]['item']}", "difference": round(a - b, 6), "ratio": round(a / b, 6) if b else None,
                  "change_pct_from_second_to_first": round((a - b) / abs(b) * 100, 4) if b else None}]
    return _ok({"metric": metric, "by": by, "items": out, "differences": diffs}, len(out), "per-item aggregate; differences computed in code", as_of)


def get_statement(entity: str, statement: str, period: dict | str | None = None, compare_period: dict | str | None = None, as_of: str | None = None, **_) -> dict:
    as_of = as_of or queries.watermark() or queries.now_iso()
    ent = "ALL" if str(entity).lower() in ("all", "consolidated", "all nsw entities") else ENTITY_ALIASES.get(str(entity).lower(), str(entity).upper())
    f, p = _filters(period, None, as_of)
    prev = _filters(compare_period, None, as_of)[0] if compare_period else None
    st = statement.lower()
    try:
        if st.startswith("perf") or st in ("income", "surplus", "p&l"):
            s = statements.performance(ent, f, as_of, prev)
            def L(x):
                return {"line": x["label"], **_money(x["amount"]), **({"previous": _money(x["prev"])} if x.get("prev") is not None else {})}
            data = {"statement": "Statement of Financial Performance", "entity": ent, "period": p["label"], "revenue": [L(x) for x in s["revenue"]], "total_revenue": _money(s["total_revenue"]),
                    "expenses": [L(x) for x in s["expenses"]], "total_expenses": _money(s["total_expenses"]), "surplus": _money(s["surplus"]), "distributions_to_treasury": _money(s["distributions"]["amount"]),
                    "largest_expense": max(({"category": x["label"], **_money(x["amount"])} for x in s["expenses"]), key=lambda z: z["naira"], default=None)}
        elif st.startswith("pos") or "balance" in st:
            s = statements.position(ent, p["end"], as_of, prev.end if prev else None)
            def L(x):
                return {"line": x["label"], **_money(x["amount"])}
            data = {"statement": "Statement of Financial Position", "entity": ent, "as_at": p["end"], "assets": [L(x) for x in s["assets"]], "total_assets": _money(s["total_assets"]),
                    "liabilities": [L(x) for x in s["liabilities"]], "total_liabilities": _money(s["total_liabilities"]), "equity": [L(x) for x in s["equity"]], "total_equity": _money(s["total_equity"]),
                    "balances": s["balances"], "difference": _money(s["difference"])}
        elif "cash" in st:
            s = statements.cash_flow(ent, f, as_of)
            data = {"statement": "Cash Flow (direct)", "entity": ent, "period": p["label"], "opening": _money(s["opening"]), "lines": [{"line": x["label"], **_money(x["amount"])} for x in s["lines"]],
                    "net_change": _money(s["net_change"]), "closing": _money(s["closing"])}
        else:
            cr = statements.collection_remittance(ent, f, as_of)
            data = {"statement": "Revenue collection and remittance", "entity": ent, "period": p["label"], "rows": [{"fee_code": r.fee_code, "assessed": _money(r.assessed), "paid": _money(r.paid), "settled_gross": _money(r.settled_gross),
                                                                                                                      "outstanding": _money(r.outstanding), "collection_efficiency": None if r.collection_efficiency != r.collection_efficiency else round(r.collection_efficiency, 4)} for r in cr.itertuples()]}
        return _ok(data, formula=f"statement computed from the ledger by SQL ({st})", as_of=as_of, notes=p.get("assumption"))
    except Exception as e:  # noqa: BLE001
        return _err(f"{type(e).__name__}: {e}", as_of)


def get_reconciliation(period: dict | str | None = None, filters: dict | None = None, as_of: str | None = None, **_) -> dict:
    as_of = as_of or queries.watermark() or queries.now_iso()
    f, p = _filters(period, filters, as_of)
    f = _norm_entities(f)
    fw = reconcile.four_way(f, as_of)
    summ = reconcile.exception_summary(f, as_of)
    hm = reconcile.leakage_heatmap(f, as_of)
    itb = reconcile.in_transit_by_bank(as_of)
    data = {"period": p["label"], "funnel": {k: {kk: (_money(vv) if kk in ("amount", "net", "collection_cost") else vv) for kk, vv in v.items()} for k, v in fw.items()},
            "exceptions": [{"class": r._1, "count": int(r.count), **_money(r.amount_minor)} for r in summ.itertuples()] if not summ.empty else [],
            "in_transit_by_bank": [{"bank": r.bank, **_money(r.in_transit_minor), "payments": int(r.payments)} for r in itb.itertuples()],
            "largest_shortfall": ({"commodity_group": hm.iloc[0]["commodity_group"], "origin_country": hm.iloc[0]["origin_country"], "shortfall_percent": round(float(hm.iloc[0]["shortfall_pct"]) * 100, 2),
                                   "assessments": int(hm.iloc[0]["n"]), **_money(hm.iloc[0]["shortfall_minor"])} if not hm.empty else None)}
    data["total_in_transit_now"] = _money(float(itb["in_transit_minor"].sum()) if not itb.empty else 0)
    return _ok(data, formula="four-way match over the cohort of assessments in the period", as_of=as_of, notes=p.get("assumption"))


def get_clearance_stats(period: dict | str | None = None, filters: dict | None = None, stage: str | None = None, percentile: float = 0.5, column: str = "wait_h", queued: bool = False,
                        as_of: str | None = None, **_) -> dict:
    as_of = as_of or queries.watermark() or queries.now_iso()
    f, p = _filters(period, filters, as_of)
    f = _norm_entities(f)
    if stage:
        r = clearance.stage_percentile(stage.upper(), f, float(percentile), column, as_of, queued)
        return _ok({"stage": stage.upper(), "column": column, "percentile": percentile, "queued_entry_values": queued, "period": p["label"], **r}, formula=f"p{int(percentile * 100)} of stage_events.{column} for {stage}",
                   as_of=as_of, notes=p.get("assumption"))
    dw = clearance.dwell_table(f, None, as_of)
    ss = clearance.stage_summary(f, as_of)
    bn = clearance.bottlenecks(f, as_of)
    data = {"period": p["label"], "dwell_days": {k: (round(float(v), 3) if v == v and v is not None else None) for k, v in dw.iloc[0].items() if k.startswith(("dwell", "clearance", "exit"))} if not dw.empty else {},
            "digital_share_of_time": clearance.overall_digital_share(f, as_of), "stages": [{"stage": r.stage, "name": r.name, "type": r.type, "median_wait_h": round(r.median_wait_h, 2), "median_duration_h": round(r.median_dur_h, 2),
                                                                                         "median_total_h": round(r.median_total_h, 2), "sla_breach_rate": None if r.sla_breach_rate != r.sla_breach_rate else round(r.sla_breach_rate, 4)} for r in ss.itertuples()],
            "largest_delay_stage": ({"stage": bn.iloc[0]["stage"], "name": bn.iloc[0]["name"], "mean_hours_per_consignment": round(float(bn.iloc[0]["mean_h_per_consignment"]), 2), "share_of_total": round(float(bn.iloc[0]["share_of_total"]), 4)} if not bn.empty else None)}
    return _ok(data, formula="dwell percentiles over consignments that gated out in the period; stage medians over completed stage records", as_of=as_of, notes=p.get("assumption"))


def trace(reference: str, as_of: str | None = None, **_) -> dict:
    as_of = as_of or queries.watermark() or queries.now_iso()
    hits = trace_mod.search(reference, as_of)
    if not hits or hits[0]["kind"] != "consignment":
        return _err(f"no consignment found for {reference!r}", as_of)
    t = trace_mod.consignment_trace(hits[0]["ref"], as_of)
    h = t["consignment"]
    fees = [{"entity": r.entity_id, "fee_code": r.fee_code, "assessed": _money(r.assessed_ngn_minor), "paid": _money(r.paid_ngn_minor), "settled": _money(r.settled_ngn_minor)} for r in t["fees"].itertuples()]
    stages = [{"stage": r.stage, "owner": r.owner_entity, "completed": r.occurred_at, "wait_h": None if r.wait_h != r.wait_h else round(r.wait_h, 1), "duration_h": None if r.dur_h != r.dur_h else round(r.dur_h, 1)} for r in t["stages"][t["stages"]["status"] != "queued"].itertuples()]
    data = {"nsw_ref": hits[0]["ref"], "origin_country": h["origin_country"], "port": h["port"], "commodity_group": h["commodity_group"], "mode": h["mode"], "risk_lane": h["risk_lane"],
            "cif_value": _money(h["cif_value_ngn_minor"]), "fees_by_agency": fees, "total_assessed": _money(sum(r["assessed"]["naira"] for r in fees) * 100), "stages": stages,
            "payments": [{"payment_ref": r.payment_ref, "status": r.status, "bank": r.bank, **_money(r.amount_ngn_minor)} for r in t["payments"].itertuples()],
            "alerts": [r.alert_id for r in t["alerts"].itertuples()] if not t["alerts"].empty else []}
    return _ok(data, len(fees), "consignment trace: stage events, fee assessments, payments, settlements", as_of)


RULE_ALIASES = {"duplicatepayment": "R-DUP-01", "duplicatepayments": "R-DUP-01", "duplicate": "R-DUP-01", "dup": "R-DUP-01", "sla": "R-SLA-01", "slabreach": "R-SLA-01", "physical": "R-PHYS-01",
                "physicalbottleneck": "R-PHYS-01", "exam": "R-PHYS-01", "examwait": "R-PHYS-01", "scanner": "R-PHYS-01", "fee": "R-FEE-01", "feeshortfall": "R-FEE-01", "underassessment": "R-FEE-01",
                "settlementlag": "R-SET-01", "settlement": "R-SET-01", "paidnotsettled": "R-REC-01", "remittance": "R-REM-01", "lateremittance": "R-REM-01", "fx": "R-FX-01", "fxmove": "R-FX-01",
                "dataquality": "R-DQ-01", "completeness": "R-DQ-01", "onboarding": "R-ONB-01", "collectioncost": "R-CASH-01", "target": "R-TGT-01"}


def list_alerts(status: str | None = None, severity: str | None = None, entity: str | None = None, period: dict | str | None = None, rule: str | None = None, as_of: str | None = None, **_) -> dict:
    as_of = as_of or queries.watermark() or queries.now_iso()
    f, p = _filters(period or {"start": None, "end": as_of, "label": "all time"}, None, as_of)
    if rule:
        rule = RULE_ALIASES.get(re.sub(r"[^a-z]", "", rule.lower()), rule.upper())
    st = (status,) if isinstance(status, str) and status != "open_all" else (("open", "acknowledged", "under_review") if status == "open_all" else ())
    df = queries.alerts(f, as_of, st, (severity,) if severity else (), (ENTITY_ALIASES.get(entity.lower(), entity.upper()),) if entity else (), (rule,) if rule else ())
    rows = [{"alert_id": r.alert_id, "rule": r.rule_code, "severity": r.severity, "entity": r.entity, "subject": r.subject, "detected_at": r.detected_at, "status": r.status, "metric": round(float(r.metric_value), 4),
             "threshold": r.threshold, "summary": json.loads(r.details_json or "{}").get("summary", "")} for r in df.head(40).itertuples()]
    return _ok({"period": p["label"], "count": int(len(df)), "alerts": rows}, len(df), "alerts table filtered by status/severity/entity/period", as_of)


def _episode_row(r, f_period: Filters, as_of: str) -> dict:
    """One under-assessment episode as a tool row, with the same slice's shortfall over the whole period for comparison (dilution)."""
    whole = reconcile.slice_daily(r.entity_id, r.fee_code, r.commodity_group, r.origin_country, f_period.start or "0000", f_period.end or as_of, as_of)
    ex, ass = float(whole["expected"].sum()), float(whole["assessed"].sum())
    return {"entity": r.entity_id, "fee_code": r.fee_code, "commodity": r.commodity_group, "origin_country": r.origin_country, "first_day": r.start_day, "last_day": r.end_day,
            "days": int(r.days), "assessments": int(r.n), "expected": _money(r.expected_minor), "assessed": _money(r.assessed_minor), "shortfall": _money(r.shortfall_minor),
            "shortfall_percent": round(float(r.shortfall_pct) * 100, 2),
            "whole_period_shortfall_percent": round((ex - ass) / ex * 100, 2) if ex > 0 else None, "whole_period_shortfall": _money(ex - ass) if ex > 0 else None}


def shortfall_episodes(period: dict | str | None = None, filters: dict | None = None, min_pct: float = 5.0, as_of: str | None = None, **_) -> dict:
    as_of = as_of or queries.watermark() or queries.now_iso()
    f, p = _filters(period, filters, as_of)
    f = _norm_entities(f)
    df = reconcile.shortfall_episodes(f, as_of, None, float(min_pct) / 100.0)
    rows = [_episode_row(r, f, as_of) for r in df.itertuples()]
    note = ("Each episode is a run of consecutive WAT days on which the slice was assessed more than the threshold below the fee-rule expectation; shortfall_percent and shortfall "
            "are computed over the episode itself, whole_period_* over the same slice for the whole period (the episode is diluted there).")
    return _ok({"period": p["label"], "threshold_percent": float(min_pct), "episodes": rows}, len(rows), "daily assessed vs expected per entity/fee/commodity/origin; runs of days over threshold", as_of, note)


def get_alert(alert_id: str, as_of: str | None = None, **_) -> dict:
    as_of = as_of or queries.watermark() or queries.now_iso()
    a = queries.alert_by_id(alert_id, as_of)
    if a is None:
        return _err(f"No alert with id {alert_id!r} has been raised as of the data time. Use list_alerts to browse alerts.", as_of)
    det = json.loads(a["details_json"] or "{}")
    data = {"alert_id": a["alert_id"], "rule": a["rule_code"], "severity": a["severity"], "entity": a["entity"], "subject": a["subject"], "status": a["status"], "assigned_to": a["assigned_to"],
            "detected_at_wat": clock.fmt_wat(a["detected_at"]), "window_start_wat": clock.fmt_wat(a["window_start"]), "window_end_wat": clock.fmt_wat(a["window_end"]),
            "condition_cleared_at_wat": clock.fmt_wat(a["cleared_at"]) if isinstance(a["cleared_at"], str) else None,
            "metric": round(float(a["metric_value"]), 4), "threshold": a["threshold"], "summary": det.get("summary", ""), "supporting_records": det.get("n_supporting"), "drill": det.get("drill"),
            "where_to_find_it": "Supervision page > Alert board: search the alert id (resolved alerts are included)"}
    try:
        data["review_history"] = [{"at_wat": clock.fmt_wat(h["at"]), "role": h["role"], "decision": h["decision"]} for h in queries.alert_reviews(a["alert_id"], as_of)[-8:]]
    except Exception:  # noqa: BLE001 - review history is a courtesy; the alert itself is the answer
        pass
    drill = det.get("drill") or {}
    if a["rule_code"] == "R-FEE-01" and drill.get("view") == "assessments":
        t0 = clock.to_dt(a["detected_at"])
        lo, hi = clock.iso(t0 - timedelta(days=14)), min(as_of, clock.iso(t0 + timedelta(days=14)))
        f = Filters(start=lo, end=hi, entities=(drill["entity"],), fee_codes=(drill["fee_code"],), commodities=(drill["commodity"],), origins=(drill["origin"],))
        eps = reconcile.shortfall_episodes(f, as_of, None, 0.05)
        if not eps.empty:
            day = clock.wat_day(a["detected_at"])
            inside = eps[(eps["start_day"] <= day) & (eps["end_day"] >= day)]
            r = (inside if not inside.empty else eps).iloc[0]
            data["episode"] = _episode_row(r, Filters(start=clock.iso(clock.sim_start()), end=as_of), as_of)
            data["metric_note"] = ("'metric' is the shortfall (as a fraction) over the rule's trailing window when the alert first fired; 'episode' is the whole run of days on which "
                                   "the shortfall persisted, so its percentage can differ.")
    return _ok(data, 1, "alerts table by id; review history; episode via daily assessed vs expected for the alert's slice", as_of)


def get_live_snapshot(minutes: int = 15, as_of: str | None = None, **_) -> dict:
    as_of = as_of or queries.watermark() or queries.now_iso()
    end = clock.to_dt(as_of)
    f = Filters(start=clock.iso(end - timedelta(minutes=int(minutes))), end=as_of)
    ev = queries.live_events(100, as_of=as_of)
    ev = ev[ev["occurred_at"] >= f.start]
    paid = queries.aggregate("paid", ["entity"], f, as_of)
    inc = queries.situation_board(as_of)["incidents"]
    data = {"window_minutes": int(minutes), "events": int(len(ev)), "event_types": ev["type"].value_counts().head(8).to_dict(), "paid_total": _money(paid["value"].sum() if not paid.empty else 0),
            "paid_by_entity": [{"entity": r.entity, **_money(r.value)} for r in paid.itertuples()], "active_incidents": [r.label or r.kind for r in inc.itertuples()],
            "recent_headlines": [{"at": r.occurred_at, "headline": r.headline} for r in ev.head(8).itertuples()]}
    return _ok(data, len(ev), f"live events and payments in the last {minutes} minutes", as_of)


_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv, ast.Pow: operator.pow, ast.USub: operator.neg, ast.UAdd: operator.pos, ast.Mod: operator.mod}
_FUNCS = {"abs": abs, "round": round, "min": min, "max": max}


def compute(expression: str, variables: dict | None = None, as_of: str | None = None, **_) -> dict:
    """Safe decimal calculator (AST whitelist). The only place the model may request arithmetic."""
    variables = {k: Decimal(str(v)) for k, v in (variables or {}).items() if isinstance(v, (int, float, str)) and str(v).replace(".", "", 1).replace("-", "", 1).replace("e", "").replace("E", "").replace("+", "").isdigit()}

    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return Decimal(str(n.value))
        if isinstance(n, ast.Name):
            if n.id in variables:
                return variables[n.id]
            raise ValueError(f"unknown variable {n.id!r}")
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
            a, b = ev(n.left), ev(n.right)
            if isinstance(n.op, ast.Pow) and (abs(b) > 10 or b != b.to_integral_value()):
                raise ValueError("exponent must be a small integer")
            return _OPS[type(n.op)](a, b)
        if isinstance(n, ast.UnaryOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.operand))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in _FUNCS:
            args = [ev(a) for a in n.args]
            return Decimal(str(_FUNCS[n.func.id](*args)))
        raise ValueError("only arithmetic on numbers and named variables is allowed")
    try:
        if len(expression) > 300:
            raise ValueError("expression too long")
        v = ev(ast.parse(expression, mode="eval"))
        return _ok({"expression": expression, "result": float(v), "result_exact": format(v.normalize(), "f")}, 1, f"decimal evaluation of {expression}", as_of)
    except (ValueError, InvalidOperation, ZeroDivisionError, SyntaxError, TypeError) as e:
        return _err(f"cannot compute: {e}", as_of)


def query_readonly(sql: str, as_of: str | None = None, **_) -> dict:
    as_of = as_of or queries.watermark() or queries.now_iso()
    try:
        s = guardrails.check_sql(sql)
    except guardrails.SQLRejected as e:
        return _err(f"query rejected: {e}", as_of)
    conn = db.reader(as_of)
    t0 = time.time()
    conn.set_progress_handler(lambda: 1 if time.time() - t0 > 5 else 0, 20000)
    try:
        cur = conn.execute(f"SELECT * FROM ({s}) LIMIT {guardrails.ROW_CAP}")
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    except Exception as e:  # noqa: BLE001
        return _err(f"query failed: {type(e).__name__}: {e}", as_of)
    finally:
        conn.set_progress_handler(None, 0)
    return _ok({"columns": cols, "rows": rows}, len(rows), s, as_of, f"row cap {guardrails.ROW_CAP}; amounts in *_minor columns are kobo (divide by 100 for naira)")


# =============================================================================== registry + OpenAI tool specs
TOOLS = {"resolve_period": lambda **k: _ok(resolve_period(k.get("text", ""), k.get("as_of")), 1, "deterministic date parser (WAT)", k.get("as_of")), "list_entities": list_entities,
         "get_entity_profile": get_entity_profile, "aggregate": aggregate, "get_statement": get_statement, "top_n": top_n, "trend": trend, "compare": compare,
         "get_reconciliation": get_reconciliation, "shortfall_episodes": shortfall_episodes, "get_clearance_stats": get_clearance_stats, "trace": trace, "list_alerts": list_alerts,
         "get_alert": get_alert, "get_live_snapshot": get_live_snapshot,
         "compute": compute, "query_readonly": query_readonly}

_FLT = {"type": "object", "description": "Optional filters: entities[], origins[] (ISO2), modes[] (sea|air), ports[], commodities[], processes[], fee_codes[]", "additionalProperties": True}
_PER = {"description": "A period phrase (e.g. 'September 2026', 'since July', 'last month', 'between 12 and 15 Aug') or {start,end} in UTC ISO", "anyOf": [{"type": "string"}, {"type": "object"}]}


def specs() -> list[dict]:
    def fn(name, desc, props, req=()):
        return {"type": "function", "function": {"name": name, "description": desc, "parameters": {"type": "object", "properties": props, "required": list(req)}}}
    return [
        fn("resolve_period", "Convert a natural-language period into exact start/end (WAT days, end exclusive).", {"text": {"type": "string"}}, ["text"]),
        fn("list_entities", "List the agencies/entities with codes and names.", {}),
        fn("get_entity_profile", "Mandate, processes, fee codes and remittance rule of one entity.", {"entity": {"type": "string"}}, ["entity"]),
        fn("aggregate", f"Core metric engine. metric one of {METRIC_LIST}. group_by from {sorted(DIMS)}. Returns naira values with display strings.",
           {"metric": {"type": "string"}, "group_by": {"type": "array", "items": {"type": "string"}}, "filters": _FLT, "period": _PER, "compare_period": _PER}, ["metric"]),
        fn("get_statement", "Financial statement from the ledger: performance | position | cash_flow | collection for an entity code or 'all' (consolidated).",
           {"entity": {"type": "string"}, "statement": {"type": "string"}, "period": _PER, "compare_period": _PER}, ["entity", "statement"]),
        fn("top_n", "Rank a dimension by a metric. For growth between two periods pass compare_period (the earlier period) and rank_by='change_pct' (or 'change'); change and change_pct are computed in code.",
           {"metric": {"type": "string"}, "dimension": {"type": "string"}, "n": {"type": "integer"}, "period": _PER, "filters": _FLT, "compare_period": _PER, "rank_by": {"type": "string"}}, ["metric", "dimension"]),
        fn("trend", "Time series of a metric with growth statistics (grain hour|day|week|month).", {"metric": {"type": "string"}, "grain": {"type": "string"}, "period": _PER, "filters": _FLT}, ["metric"]),
        fn("compare", "Side-by-side comparison with differences computed in code. by=entity|origin_country|port|mode|commodity or 'period' (items are period phrases).",
           {"metric": {"type": "string"}, "items": {"type": "array", "items": {"type": "string"}}, "period": _PER, "by": {"type": "string"}, "filters": _FLT}, ["metric", "items"]),
        fn("get_reconciliation", "Four-way match funnel, exception summary, in-transit by bank and the largest assessment shortfall.", {"period": _PER, "filters": _FLT}),
        fn("shortfall_episodes", "WHEN was something under-assessed: finds episodes (runs of consecutive days) on which an entity/fee/commodity/origin slice was assessed more than min_pct percent below the fee-rule expectation, "
           "with the shortfall computed over the episode itself and, for comparison, over the whole period. Use for ANY question about a shortfall, under-assessment or leakage that 'stood out' or that quotes a "
           "percentage below expectation: the episode's own window is the right basis, not the whole period. Pass the broadest period that could contain it (e.g. 'since July').",
           {"period": _PER, "filters": _FLT, "min_pct": {"type": "number", "description": "daily shortfall threshold in percent (default 5)"}}),
        fn("get_clearance_stats", "Dwell/clearance percentiles, stage medians, digital share of time, and largest_delay_stage (the stage contributing most to delay, mean hours per consignment): use this for any clearance, dwell, delay or digital-vs-physical question. With stage (S02..S09) returns a percentile of wait_h or dur_h (queued=true uses queue-entry planned values).",
           {"period": _PER, "filters": _FLT, "stage": {"type": "string"}, "percentile": {"type": "number"}, "column": {"type": "string"}, "queued": {"type": "boolean"}}),
        fn("trace", "Trace one consignment by NSW reference, declaration, payment reference or container: agencies, fees, payments, stages.", {"reference": {"type": "string"}}, ["reference"]),
        fn("list_alerts", "List alerts raised in the period. OMIT status to include every status (use status only when the user asks for open/unresolved/resolved ones; open_all = open+acknowledged+under_review). rule is a code (R-SLA-01 SLA, R-PHYS-01 exam/scanner wait, R-FEE-01 fee shortfall, R-REC-01 paid-not-settled, R-SET-01 settlement lag, R-REM-01 late remittance, R-DUP-01 duplicate payments, R-FX-01 FX move, R-DQ-01 data completeness, R-CASH-01 cost spike, R-TGT-01 dwell target, R-ONB-01 onboarding) or a short name.", {"status": {"type": "string"}, "severity": {"type": "string"}, "entity": {"type": "string"}, "period": _PER, "rule": {"type": "string"}}),
        fn("get_alert", "Everything about ONE alert by its id (for example ALT-20260910-00001), of any status: rule, status, when it was detected, the rule's window, metric vs threshold, drill filters, review history "
           "and, for fee-shortfall alerts, the episode over which the shortfall ran. Use whenever the user names an alert id.", {"alert_id": {"type": "string"}}, ["alert_id"]),
        fn("get_live_snapshot", "Collections, events and incidents in the last N minutes.", {"minutes": {"type": "integer"}}),
        fn("compute", "Safe decimal calculator for ANY arithmetic (differences, ratios, percentages). ONE expression per call using only numbers, the variable names you pass, + - * / ** % and parentheses (and abs/round/min/max); no dicts, lists or strings. Example: expression '(a-b)/b*100', variables {'a': 120, 'b': 100}.", {"expression": {"type": "string"}, "variables": {"type": "object"}}, ["expression"]),
        fn("query_readonly", f"Read-only SELECT over these views only: {', '.join(sorted(guardrails.ALLOWED_VIEWS))}. Use when no specific tool fits. Row cap 500; amounts in *_minor are kobo.", {"sql": {"type": "string"}}, ["sql"]),
    ]


def call(name: str, args: dict, as_of: str | None) -> dict:
    fn = TOOLS.get(name)
    if fn is None:
        return _err(f"unknown tool {name!r}", as_of)
    t0 = time.time()
    try:
        args = {k: v for k, v in (args or {}).items() if k != "as_of"}
        out = fn(**args, as_of=as_of) if name != "resolve_period" else fn(text=args.get("text", ""), as_of=as_of)
    except TypeError as e:
        out = _err(f"bad arguments: {e}", as_of)
    except Exception as e:  # noqa: BLE001
        out = _err(f"{type(e).__name__}: {e}", as_of)
    out["duration_ms"] = int((time.time() - t0) * 1000)
    return out


_SCHEMA_CACHE: dict = {}


def schema_catalog() -> str:
    """Compact catalogue of the read-only views (columns) plus enumerations, injected into the assistant's system prompt so SQL uses real names."""
    if "txt" in _SCHEMA_CACHE:
        return _SCHEMA_CACHE["txt"]
    conn = db.reader(queries.watermark() or queries.now_iso())
    lines = []
    for v in sorted(guardrails.ALLOWED_VIEWS):
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({v})")]
        if v in ("v_journal_entries",):
            continue
        lines.append(f"{v}({', '.join(cols)})")
    enums = ("Enumerations: entity codes " + ", ".join(entity_cards()) + "; stage codes S01..S09 (S02 permits, S03 declaration, S04 assessment, S05 payment, S06 exam, S07 release, S08 terminal, S09 evacuation); stage status done|manual|queued; "
             "fee_assessments/collections have entity_id, fee_code (e.g. NCS-DUTY, NPA-PORTDUES), process_code, origin_country (ISO2), commodity_group, mode (sea|air), port (NGAPP, NGTIN, NGLEK, NGONN, NGPHC, LOS, ABV, KAN, PHC); "
             "funding source_type appropriation|grant|concessional_loan; payments status confirmed|failed|duplicate and is_duplicate 0/1 (nsw_ref starting UNMATCHED = unmatched payment); alerts severity high|medium|low|info, "
             "status open|acknowledged|under_review|resolved|dismissed; alert rule codes R-SLA-01, R-PHYS-01, R-FEE-01, R-REC-01, R-SET-01, R-REM-01, R-DUP-01, R-FX-01, R-DQ-01, R-CASH-01, R-TGT-01, R-ONB-01; banks are the single letters A, B, C, D, E, F (bank column values are 'A'..'F', never 'Bank A'); "
             "all *_minor amounts are kobo (divide by 100); timestamps are UTC ISO text (WAT = UTC+1; day columns are WAT dates).")
    _SCHEMA_CACHE["txt"] = "Views available to query_readonly:\n" + "\n".join(lines) + "\n" + enums
    return _SCHEMA_CACHE["txt"]
