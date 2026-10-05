"""Core queries. UI and assistant code MUST NOT query base tables directly: everything reads through here, which in turn
reads the ``v_*`` views bound to ``as_of`` (facts with ``occurred_at <= as_of`` only)."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, timedelta

import numpy as np
import pandas as pd

from nsw_sim import clock, db

MINOR = 100.0


@dataclass(frozen=True)
class Filters:
    start: str | None = None            # UTC ISO, inclusive
    end: str | None = None              # UTC ISO, exclusive
    entities: tuple[str, ...] = ()
    origins: tuple[str, ...] = ()
    modes: tuple[str, ...] = ()
    ports: tuple[str, ...] = ()
    commodities: tuple[str, ...] = ()
    processes: tuple[str, ...] = ()
    fee_codes: tuple[str, ...] = ()
    currency_view: str = "NGN"
    banks: tuple[str, ...] = ()
    statuses: tuple[str, ...] = ()
    severities: tuple[str, ...] = ()

    def with_(self, **kw) -> "Filters":
        return replace(self, **kw)

    def describe(self) -> str:
        parts = []
        if self.start or self.end:
            parts.append(f"{clock.fmt_wat(self.start, '%d %b %Y') if self.start else '…'} → "
                         f"{clock.fmt_wat(self.end, '%d %b %Y %H:%M') if self.end else 'now'}")
        for label, v in (("entities", self.entities), ("origin", self.origins), ("mode", self.modes), ("port", self.ports),
                         ("commodity", self.commodities), ("process", self.processes), ("fee", self.fee_codes)):
            if v:
                parts.append(f"{label}: {', '.join(v)}")
        parts.append(f"currency view: {self.currency_view}")
        return " · ".join(parts)

    def as_dict(self) -> dict:
        return {k: (list(v) if isinstance(v, tuple) else v) for k, v in self.__dict__.items()}


def now_iso() -> str:
    return clock.iso(clock.utcnow())


def _in(col: str, vals: tuple, params: list) -> str:
    if not vals:
        return ""
    params.extend(vals)
    return f" AND {col} IN ({','.join('?' * len(vals))})"


# metric -> (view, time column, value column, available dims)
_ALL_DIMS = {"entity": "entity_id", "process": "process_code", "fee_code": "fee_code", "origin_country": "origin_country",
             "port": "port", "mode": "mode", "commodity": "commodity_group"}
METRICS: dict[str, tuple[str, str, str, dict]] = {
    "assessed": ("v_assessments", "occurred_at", "amount_ngn_minor", _ALL_DIMS),
    "expected": ("v_assessments", "occurred_at", "expected_amount_ngn_minor", _ALL_DIMS),
    "paid": ("v_collections", "paid_at", "amount_ngn_minor", _ALL_DIMS),
    "settled": ("v_settled", "settled_at", "settled_ngn_minor", _ALL_DIMS),
    "settled_gross": ("v_settled", "settled_at", "amount_ngn_minor", _ALL_DIMS),
    "collection_cost": ("v_settled", "settled_at", "collection_cost_ngn_minor", _ALL_DIMS),
    "in_transit": ("v_collections", "paid_at", "amount_ngn_minor", _ALL_DIMS),
    "outstanding": ("v_assessed_vs_paid", "occurred_at", "(assessed_ngn_minor-paid_ngn_minor)", _ALL_DIMS),
    "consignments": ("v_consignments", "manifested_at", "1", {"origin_country": "origin_country", "port": "port", "mode": "mode",
                                                              "commodity": "commodity_group"}),
    "remitted": ("v_remittances", "paid_at", "amount_ngn_minor", {"entity": "entity_id"}),
    "expenses": ("v_expenses", "occurred_at", "amount_ngn_minor", {"entity": "entity_id", "category": "category"}),
    "refunds": ("v_refunds", "occurred_at", "amount_ngn_minor", {"entity": "entity_id"}),
}
_FILTER_COLS = {"entities": "entity_id", "origins": "origin_country", "modes": "mode", "ports": "port", "commodities": "commodity_group",
                "processes": "process_code", "fee_codes": "fee_code"}
_TIME_GROUPS = {"day": "date({t},'+1 hour')", "week": "date({t},'+1 hour','weekday 0','-6 days')", "month": "substr(date({t},'+1 hour'),1,7)",
                "hour": "substr({t},1,13)"}


def aggregate(metric: str, group_by: list[str] | tuple[str, ...] = (), f: Filters | None = None, as_of: str | None = None) -> pd.DataFrame:
    """The core metric engine. Columns: ``group_by...`` plus ``value`` (kobo; or count) and ``rows``. NGN minor units unless
    ``f.currency_view == 'USD'`` (then USD cents at the transaction-day rate)."""
    f = f or Filters()
    view, tcol, vcol, dims = METRICS[metric]
    conn = db.reader(as_of)
    params: list = []
    where = " WHERE 1=1"
    if f.start:
        where += f" AND {tcol} >= ?"
        params.append(f.start)
    if f.end:
        where += f" AND {tcol} < ?"
        params.append(f.end)
    if metric == "in_transit":
        where += " AND settled_at IS NULL"
    if metric == "refunds":
        where += " AND fee_code != 'UNAPPLIED'"          # refunds of unapplied receipts are balance-sheet only
    for attr, col in _FILTER_COLS.items():
        vals = getattr(f, attr)
        if vals and col in set(dims.values()):
            where += _in(col, vals, params)
    usd = f.currency_view == "USD" and metric not in ("consignments",)
    gcols, sel = [], []
    for g in group_by:
        if g in _TIME_GROUPS:
            expr = _TIME_GROUPS[g].format(t=tcol)
        elif g in dims:
            expr = dims[g]
        else:
            raise ValueError(f"metric {metric!r} cannot be grouped by {g!r}")
        gcols.append(g)
        sel.append(f"{expr} AS {g}")
    if usd and "day" not in group_by:
        sel.append(f"date({tcol},'+1 hour') AS _d")
        gcols.append("_d")
    cols = ", ".join(sel + [f"SUM({vcol}) AS value", "COUNT(*) AS rows"])
    grp = f" GROUP BY {', '.join(str(i + 1) for i in range(len(sel)))}" if sel else ""
    df = pd.read_sql_query(f"SELECT {cols} FROM {view}{where}{grp}", conn, params=params)
    df["value"] = df["value"].fillna(0)
    if usd and not df.empty:
        rates = usd_rates(as_of)
        dcol = "day" if "day" in df.columns else "_d"
        df["value"] = [v / _rate_on(rates, d) for v, d in zip(df["value"], df[dcol])]
        if "_d" in df.columns:
            keep = [c for c in df.columns if c not in ("_d", "value", "rows")]
            df = df.groupby(keep, as_index=False).agg(value=("value", "sum"), rows=("rows", "sum")) if keep else \
                pd.DataFrame({"value": [df["value"].sum()], "rows": [df["rows"].sum()]})
    return df


def total(metric: str, f: Filters | None = None, as_of: str | None = None) -> float:
    df = aggregate(metric, (), f, as_of)
    return float(df["value"].iloc[0]) if not df.empty else 0.0


def usd_rates(as_of: str | None = None) -> pd.Series:
    df = pd.read_sql_query("SELECT date(ts_utc,'+1 hour') AS d, rate_to_ngn FROM v_fx_rates WHERE ccy='USD' ORDER BY ts_utc", db.reader(as_of))
    return df.drop_duplicates("d").set_index("d")["rate_to_ngn"]


def _rate_on(rates: pd.Series, day: str) -> float:
    if rates.empty:
        return 1500.0
    try:
        return float(rates.loc[day])
    except KeyError:
        return float(rates.iloc[-1]) if day > rates.index[-1] else float(rates.iloc[0])


def previous_period(f: Filters) -> Filters:
    """Equal-length period immediately before ``f`` (for 'compare to previous period')."""
    s, e = clock.to_dt(f.start), clock.to_dt(f.end or now_iso())
    span = e - s
    return f.with_(start=clock.iso(s - span), end=clock.iso(s))


# ------------------------------------------------------------------------------------------- reference lookups
def entities_df(as_of: str | None = None) -> pd.DataFrame:
    return pd.read_sql_query("SELECT * FROM v_entities ORDER BY code", db.reader(as_of))


def countries_df() -> pd.DataFrame:
    return pd.read_sql_query("SELECT * FROM v_countries ORDER BY iso2", db.reader())


def distinct_values(col: str, view: str = "v_assessments", as_of: str | None = None) -> list[str]:
    allowed = {("process_code", "v_assessments"), ("fee_code", "v_assessments"), ("port", "v_consignments"), ("origin_country", "v_consignments"),
               ("commodity_group", "v_consignments"), ("mode", "v_consignments"), ("bank", "v_settlement_batches")}
    if (col, view) not in allowed:
        raise ValueError("not an allowed lookup")
    return [r[0] for r in db.reader(as_of).execute(f"SELECT DISTINCT {col} FROM {view} WHERE {col} IS NOT NULL ORDER BY 1")]


def watermark(as_of: str | None = None) -> str | None:
    return db.kv_get(db.reader(as_of), "watermark_utc")


def data_window() -> tuple[str | None, str | None]:
    r = db.reader().execute("SELECT MIN(occurred_at), MAX(occurred_at) FROM v_assessments").fetchone()
    return r[0], r[1]


def entity_mix(f: Filters, as_of: str | None = None) -> pd.DataFrame:
    """Assessed / paid / settled / remitted by entity for the period (one row per entity)."""
    out = None
    for m in ("assessed", "paid", "settled", "collection_cost", "in_transit", "outstanding", "remitted", "expenses"):
        ff = f if m not in ("remitted", "expenses") else f
        df = aggregate(m, ["entity"], ff, as_of).rename(columns={"value": m})[["entity", m]]
        out = df if out is None else out.merge(df, on="entity", how="outer")
    return out.fillna(0).sort_values("assessed", ascending=False).reset_index(drop=True)


def daily_series(metric: str, f: Filters, as_of: str | None = None, by: str | None = None, grain: str = "day") -> pd.DataFrame:
    g = [grain] + ([by] if by else [])
    return aggregate(metric, g, f, as_of)


def minute_series(minutes: int = 60, as_of: str | None = None, metric: str = "paid") -> pd.DataFrame:
    """Last N minutes by entity from the 1-minute rollups (fast path for the live fragment)."""
    end = clock.to_dt(as_of or now_iso())
    start = end - timedelta(minutes=minutes)
    return pd.read_sql_query("SELECT entity_id, minute, value FROM rollup_minute WHERE metric=? AND minute>? AND minute<=? ORDER BY minute",
                             db.reader(as_of), params=(metric, clock.iso(start)[:16] + "Z", clock.iso(end)[:16] + "Z"))


def todays_running_total(as_of: str | None = None, metric: str = "paid") -> dict:
    """Today's running total vs yesterday at the same time of day (WAT), from minute rollups."""
    end = clock.to_dt(as_of or now_iso())
    day0 = clock.wat_midnight_utc(clock.wat(end).date())
    y0, y_end = day0 - timedelta(days=1), end - timedelta(days=1)
    q = "SELECT SUM(value) FROM rollup_minute WHERE metric=? AND minute>=? AND minute<=?"
    c = db.reader(as_of)
    today = c.execute(q, (metric, clock.iso(day0)[:16] + "Z", clock.iso(end)[:16] + "Z")).fetchone()[0] or 0
    yday = c.execute(q, (metric, clock.iso(y0)[:16] + "Z", clock.iso(y_end)[:16] + "Z")).fetchone()[0] or 0
    return {"today": today, "yesterday_same_time": yday}


def live_events(limit: int = 50, after: str | None = None, entities: tuple = (), severities: tuple = (), as_of: str | None = None) -> pd.DataFrame:
    params: list = []
    where = " WHERE 1=1"
    if after:
        where += " AND occurred_at > ?"
        params.append(after)
    where += _in("entity_id", entities, params) + _in("severity", severities, params)
    return pd.read_sql_query(f"SELECT * FROM v_live_events{where} ORDER BY occurred_at DESC, event_id DESC LIMIT {int(limit)}", db.reader(as_of), params=params)


def events_per_minute(as_of: str | None = None, minutes: int = 30) -> pd.DataFrame:
    end = clock.to_dt(as_of or now_iso())
    return pd.read_sql_query("SELECT substr(occurred_at,1,16) AS minute, COUNT(*) AS n FROM v_live_events WHERE occurred_at>? GROUP BY 1 ORDER BY 1",
                             db.reader(as_of), params=(clock.iso(end - timedelta(minutes=minutes)),))


def country_collections(f: Filters, metric: str = "assessed", as_of: str | None = None) -> pd.DataFrame:
    return aggregate(metric, ["origin_country"], f, as_of).sort_values("value", ascending=False)


def situation_board(as_of: str | None = None) -> dict:
    """Active incidents, permit queue indicators, settlement lag per bank (engine facts)."""
    c = db.reader(as_of)
    inc = pd.read_sql_query("SELECT * FROM v_incidents WHERE end_at > ? ORDER BY start_at DESC", c, params=(clock.iso(as_of or now_iso()),))
    lag = pd.read_sql_query("SELECT bank, ROUND(AVG(lag_hours),1) AS avg_lag_h, COUNT(*) AS batches FROM v_settlement_batches WHERE completed_at IS NOT NULL "
                            "AND closed_at > ? GROUP BY bank ORDER BY bank", c, params=(clock.iso(clock.to_dt(as_of or now_iso()) - timedelta(days=3)),))
    q = pd.read_sql_query("SELECT owner_entity AS entity, ROUND(AVG(wait_h+dur_h),1) AS avg_planned_h, COUNT(*) AS n FROM v_stage_durations WHERE stage='S02' "
                          "AND status='queued' AND occurred_at > ? GROUP BY 1", c, params=(clock.iso(clock.to_dt(as_of or now_iso()) - timedelta(hours=24)),))
    ex = pd.read_sql_query("SELECT substr(c.port,1,5) AS port, ROUND(AVG(s.wait_h),1) AS avg_planned_wait_h, COUNT(*) AS n FROM v_stage_durations s "
                           "JOIN v_consignments c ON c.nsw_ref=s.nsw_ref WHERE s.stage='S06' AND s.status='queued' AND s.occurred_at > ? GROUP BY 1",
                           c, params=(clock.iso(clock.to_dt(as_of or now_iso()) - timedelta(hours=12)),))
    return {"incidents": inc, "settlement_lag": lag, "permit_queues": q, "exam_waits": ex}
