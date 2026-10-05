"""Data quality and the Data Confidence Score (per agency): completeness 40% / timeliness 25% / consistency 20% /
remittance punctuality 15% (weights configurable). Also onboarding progress."""
from __future__ import annotations

from datetime import timedelta

import pandas as pd

from nsw_sim import clock, db
from nsw_sim.analytics.queries import now_iso

WEIGHTS = {"completeness": 0.40, "timeliness": 0.25, "consistency": 0.20, "punctuality": 0.15}
RAG = [(90, "green"), (75, "amber"), (0, "red")]


def rag(score: float | None) -> str:
    if score is None:
        return "grey"
    return next(c for lo, c in RAG if score >= lo)


def _window(as_of: str | None, days: float) -> tuple[str, str]:
    end = clock.to_dt(as_of or now_iso())
    return clock.iso(end - timedelta(days=days)), clock.iso(end)


def scorecard(as_of: str | None = None, window_days: float = 7, entities: tuple[str, ...] = ()) -> pd.DataFrame:
    """One row per fee-earning entity with the five scorecard components, the composite score and RAG."""
    c = db.reader(as_of)
    s, e = _window(as_of, window_days)
    st = pd.read_sql_query("SELECT owner_entity AS entity, COUNT(*) n, AVG(data_complete) completeness, "
                           "AVG(CASE WHEN status='done' THEN 1.0 ELSE 0.0 END) digital_share FROM v_stage_durations "
                           "WHERE status IN ('done','manual') AND occurred_at>? AND occurred_at<=? GROUP BY 1", c, params=(s, e)).set_index("entity")
    tm = pd.read_sql_query("SELECT owner_entity AS entity, 1.0-AVG(sla_breach) timeliness, COUNT(*) n_sla FROM v_stage_durations WHERE status IN ('done','manual') "
                           "AND sla_breach IS NOT NULL AND data_complete=1 AND occurred_at>? AND occurred_at<=? GROUP BY 1", c, params=(s, e)).set_index("entity")
    d14, d3 = clock.iso(clock.to_dt(e) - timedelta(days=14)), clock.iso(clock.to_dt(e) - timedelta(days=3))
    cs = pd.read_sql_query("SELECT entity_id AS entity, COUNT(*) n, AVG(CASE WHEN paid_ngn_minor>0 AND settled_ngn_minor>0 THEN 1.0 ELSE 0.0 END) paid_settled, "
                           "AVG(CASE WHEN ABS(assessed_ngn_minor-expected_amount_ngn_minor)<=0.01*expected_amount_ngn_minor THEN 1.0 ELSE 0.0 END) accuracy "
                           "FROM v_assessed_vs_paid WHERE occurred_at>? AND occurred_at<=? GROUP BY 1", c, params=(d14, d3)).set_index("entity")
    rm = pd.read_sql_query("SELECT entity_id AS entity, COUNT(*) n_rem, AVG(CASE WHEN days_late<=1 THEN 1.0 ELSE 0.0 END) punctuality FROM v_remittances "
                           "WHERE paid_at IS NOT NULL AND occurred_at>? GROUP BY 1", c, params=(clock.iso(clock.to_dt(e) - timedelta(days=120)),)).set_index("entity")
    stl = pd.read_sql_query("SELECT entity_id AS entity, AVG((julianday(settled_at)-julianday(paid_at))*24) settle_lag_h, SUM(collection_cost_ngn_minor)*1.0/SUM(amount_ngn_minor) cost_ratio "
                            "FROM v_settled WHERE settled_at>? AND settled_at<=? GROUP BY 1", c, params=(s, e)).set_index("entity")
    ents = [r[0] for r in c.execute("SELECT code FROM v_entities WHERE type IN ('collector','operator','regulator','platform') ORDER BY code")]
    rows = []
    for ent in ents:
        if entities and ent not in entities:
            continue
        comp = float(st.at[ent, "completeness"]) if ent in st.index else None
        timel = float(tm.at[ent, "timeliness"]) if ent in tm.index else 1.0
        cons = None
        if ent in cs.index:
            cons = 0.5 * float(cs.at[ent, "paid_settled"]) + 0.5 * float(cs.at[ent, "accuracy"])
        pun = float(rm.at[ent, "punctuality"]) if ent in rm.index else 1.0
        parts = {"completeness": comp, "timeliness": timel, "consistency": cons if cons is not None else 1.0, "punctuality": pun}
        score = None if comp is None else 100 * sum(WEIGHTS[k] * (parts[k] if parts[k] is not None else 1.0) for k in WEIGHTS)
        rows.append({"entity": ent, **{k: parts[k] for k in WEIGHTS}, "score": score, "rag": rag(score),
                     "digital_share": float(st.at[ent, "digital_share"]) if ent in st.index else None,
                     "events": int(st.at[ent, "n"]) if ent in st.index else 0,
                     "settlement_lag_h": float(stl.at[ent, "settle_lag_h"]) if ent in stl.index else None,
                     "collection_cost_ratio": float(stl.at[ent, "cost_ratio"]) if ent in stl.index else None})
    return pd.DataFrame(rows)


def score_for(entity: str, as_of: str | None = None, window_days: float = 7) -> dict | None:
    df = scorecard(as_of, window_days, (entity,))
    return None if df.empty else df.iloc[0].to_dict()


def overall_score(as_of: str | None = None, window_days: float = 7) -> float | None:
    df = scorecard(as_of, window_days)
    if df.empty:
        return None
    w = df["events"].clip(lower=1)
    return float((df["score"].fillna(0) * w).sum() / w.sum())


def completeness_trend(as_of: str | None = None, days: int = 60, entities: tuple[str, ...] = ()) -> pd.DataFrame:
    """Daily completeness and digital share per entity from stage events (the B9 gap is visible here)."""
    c = db.reader(as_of)
    s = clock.iso(clock.to_dt(as_of or now_iso()) - timedelta(days=days))
    df = pd.read_sql_query("SELECT owner_entity AS entity, date(occurred_at,'+1 hour') AS day, COUNT(*) n, AVG(data_complete) completeness, "
                           "AVG(CASE WHEN status='done' THEN 1.0 ELSE 0.0 END) digital_share FROM v_stage_durations "
                           "WHERE status IN ('done','manual') AND occurred_at>? GROUP BY 1,2 ORDER BY 2", c, params=(s,))
    if entities:
        df = df[df["entity"].isin(entities)]
    return df


def field_completeness(entity: str, as_of: str | None = None, days: int = 14) -> pd.DataFrame:
    s = (clock.to_dt(as_of or now_iso()) - timedelta(days=days)).strftime("%Y-%m-%d")
    return pd.read_sql_query("SELECT dim_value AS field, SUM(CASE WHEN metric='dq_present' THEN value END) AS present, SUM(CASE WHEN metric='dq_total' THEN value END) AS total "
                             "FROM rollup_day WHERE entity_id=? AND metric IN ('dq_present','dq_total') AND day>=? GROUP BY 1", db.reader(as_of), params=(entity, s))


def late_data(as_of: str | None = None, days: int = 14) -> pd.DataFrame:
    """Stage records captured manually (not digitally) per entity per day: the 'late-arriving / onboarding' signal."""
    s = clock.iso(clock.to_dt(as_of or now_iso()) - timedelta(days=days))
    return pd.read_sql_query("SELECT owner_entity AS entity, date(occurred_at,'+1 hour') AS day, SUM(status='manual') AS manual, COUNT(*) AS n FROM v_stage_durations "
                             "WHERE status IN ('done','manual') AND occurred_at>? GROUP BY 1,2 ORDER BY 2", db.reader(as_of), params=(s,))
