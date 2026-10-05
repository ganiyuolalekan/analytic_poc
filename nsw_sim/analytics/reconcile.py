"""Four-way reconciliation (Assessed -> Paid -> Settled -> Remitted), exception classification and leakage indicators."""
from __future__ import annotations

from datetime import timedelta

import pandas as pd

from nsw_sim import clock, db
from nsw_sim.analytics.queries import Filters, now_iso

CLASSES = {
    "unpaid": "Assessed more than 72 hours ago and not paid",
    "part_paid": "Paid less than assessed (NGN fees)",
    "overpaid": "Paid more than assessed (NGN fees)",
    "settled_late": "Paid but not settled after 48 hours (T+2)",
    "duplicate": "Duplicate payment not yet refunded",
    "orphan_payment": "Payment with no matching assessment, not yet refunded",
    "mismatch": "Assessed differs from the fee-rule expectation by more than 1%",
}
_COLS = {"entities": "entity_id", "origins": "origin_country", "modes": "mode", "ports": "port", "commodities": "commodity_group",
         "processes": "process_code", "fee_codes": "fee_code"}


def _where(f: Filters, tcol: str = "occurred_at", alias: str = "") -> tuple[str, list]:
    a = f"{alias}." if alias else ""
    w, p = " WHERE 1=1", []
    if f.start:
        w += f" AND {a}{tcol} >= ?"
        p.append(f.start)
    if f.end:
        w += f" AND {a}{tcol} < ?"
        p.append(f.end)
    for attr, col in _COLS.items():
        vals = getattr(f, attr)
        if vals:
            w += f" AND {a}{col} IN ({','.join('?' * len(vals))})"
            p.extend(vals)
    return w, p


def four_way(f: Filters, as_of: str | None = None) -> dict:
    """Funnel for the cohort of assessments raised in the period (as of ``as_of``)."""
    conn, avp = db.avp(as_of)
    w, p = _where(f)
    r = conn.execute(f"SELECT COUNT(*), SUM(assessed_ngn_minor), SUM(paid_ngn_minor>0), SUM(CASE WHEN paid_ngn_minor>0 THEN paid_ngn_minor END), "
                     f"SUM(settled_ngn_minor>0), SUM(settled_ngn_minor+collection_cost_ngn_minor), SUM(collection_cost_ngn_minor), SUM(settled_ngn_minor) "
                     f"FROM {avp}{w}", p).fetchone()
    n, assessed, n_paid, paid, n_set, settled_gross, cost, net = [(x or 0) for x in r]
    # remitted share of the settled cohort: net settled by (entity, settlement period) x remit share of paid remittances
    sw, sp = _where(f, "occurred_at", "f")
    rows = conn.execute(f"SELECT a.entity_id, substr(a.settled_day,1,7), substr(a.settled_day,1,4)||'-Q'||((CAST(substr(a.settled_day,6,2) AS INT)-1)/3+1), SUM(a.settled_ngn_minor) "
                        f"FROM v_settled a JOIN v_assessments f ON f.assessment_id=a.assessment_id{sw} GROUP BY 1,2,3", sp).fetchall()
    rem = pd.read_sql_query("SELECT entity_id, period, share FROM v_remittances WHERE paid_at IS NOT NULL", conn)
    share = {(r_.entity_id, r_.period): r_.share for r_ in rem.itertuples()}
    remitted = 0.0
    for ent, mo, q, v in rows:
        s = share.get((ent, mo), share.get((ent, q)))
        if s is not None:
            remitted += v * s
    from nsw_sim.config import yaml_config  # noqa: F401  (kept for symmetry with other modules)
    awaiting = max(0.0, net - remitted)
    return {"assessed": {"count": n, "amount": assessed}, "paid": {"count": n_paid, "amount": paid},
            "settled": {"count": n_set, "amount": settled_gross, "net": net, "collection_cost": cost},
            "remitted": {"amount": int(remitted)}, "settled_not_remitted": {"amount": int(awaiting)},
            "in_transit": {"amount": int(paid - settled_gross)}, "unpaid": {"amount": int(assessed - paid)}}


def exception_summary(f: Filters, as_of: str | None = None) -> pd.DataFrame:
    ex = exceptions(f, as_of, limit_per_class=100000)
    if ex.empty:
        return pd.DataFrame(columns=["class", "count", "amount_minor"])
    g = ex.groupby("cls").agg(count=("amount_minor", "size"), amount_minor=("amount_minor", "sum")).reset_index().rename(columns={"cls": "class"})
    g["definition"] = g["class"].map(CLASSES)
    return g.sort_values("amount_minor", ascending=False)


def exceptions(f: Filters, as_of: str | None = None, limit_per_class: int = 300) -> pd.DataFrame:
    """Exception rows with classification, age, amount, entity, origin. ``in_transit`` within T+2 is timing, not an exception."""
    conn, avp = db.avp(as_of)
    now = clock.to_dt(as_of or now_iso())
    t72, t48 = clock.iso(now - timedelta(hours=72)), clock.iso(now - timedelta(hours=48))
    w, p = _where(f)
    now_s = clock.iso(now)
    base = ("SELECT '{cls}' AS cls, entity_id AS entity, nsw_ref, assessment_id AS subject, {amt} AS amount_minor, "
            "ROUND((julianday(?)-julianday({age}))*1.0,1) AS age_days, origin_country, commodity_group, fee_code, occurred_at AS at FROM {avp}{w} AND {cond} "
            "ORDER BY {amt_order} DESC LIMIT " + str(int(limit_per_class)))
    specs = [
        ("unpaid", "assessed_ngn_minor", "occurred_at", f"paid_ngn_minor=0 AND occurred_at<'{t72}'", "assessed_ngn_minor"),
        ("settled_late", "paid_ngn_minor", "last_paid_at", f"paid_ngn_minor>0 AND settled_ngn_minor=0 AND last_paid_at<'{t48}'", "paid_ngn_minor"),
        ("mismatch", "ABS(assessed_ngn_minor-expected_amount_ngn_minor)", "occurred_at",
         "expected_amount_ngn_minor>0 AND ABS(assessed_ngn_minor-expected_amount_ngn_minor)>0.01*expected_amount_ngn_minor", "ABS(assessed_ngn_minor-expected_amount_ngn_minor)"),
        ("part_paid", "assessed_ngn_minor-paid_ngn_minor", "occurred_at", "currency='NGN' AND paid_ngn_minor>0 AND paid_ngn_minor<assessed_ngn_minor*0.995", "assessed_ngn_minor-paid_ngn_minor"),
        ("overpaid", "paid_ngn_minor-assessed_ngn_minor", "occurred_at", "currency='NGN' AND paid_ngn_minor>assessed_ngn_minor*1.005", "paid_ngn_minor-assessed_ngn_minor"),
    ]
    parts = []
    for cls, amt, age, cond, order in specs:
        q = base.format(cls=cls, amt=amt, age=age, w=w, cond=cond, amt_order=order, avp=avp)
        parts.append(pd.read_sql_query(q, conn, params=[now_s, *p]))
    # payments that are not tied to an assessment
    pw, pp = "", []
    if f.start:
        pw += " AND pay.occurred_at >= ?"
        pp.append(f.start)
    if f.end:
        pw += " AND pay.occurred_at < ?"
        pp.append(f.end)
    for cls, cond in (("duplicate", "pay.is_duplicate=1"), ("orphan_payment", "pay.nsw_ref LIKE 'UNMATCHED%'")):
        parts.append(pd.read_sql_query(
            f"SELECT '{cls}' AS cls, 'NSW' AS entity, pay.nsw_ref AS nsw_ref, pay.payment_id AS subject, pay.amount_ngn_minor AS amount_minor, "
            f"ROUND(julianday(?)-julianday(pay.occurred_at),1) AS age_days, pay.origin_country, pay.commodity_group, NULL AS fee_code, pay.occurred_at AS at "
            f"FROM v_payments pay WHERE {cond} AND NOT EXISTS (SELECT 1 FROM v_refunds r WHERE r.refund_id='RFU-'||pay.payment_id){pw} "
            f"ORDER BY pay.amount_ngn_minor DESC LIMIT {int(limit_per_class)}", conn, params=[now_s, *pp]))
    out = pd.concat([x for x in parts if not x.empty], ignore_index=True) if any(not x.empty for x in parts) else pd.DataFrame(
        columns=["cls", "entity", "nsw_ref", "subject", "amount_minor", "age_days", "origin_country", "commodity_group", "fee_code", "at"])
    return out.sort_values(["amount_minor"], ascending=False).reset_index(drop=True)


def leakage_heatmap(f: Filters, as_of: str | None = None, min_n: int = 15, fee_code: str | None = None) -> pd.DataFrame:
    """Shortfall of assessed vs expected by commodity x origin. Positive = under-assessed."""
    w, p = _where(f)
    if fee_code:
        w += " AND fee_code=?"
        p.append(fee_code)
    df = pd.read_sql_query(f"SELECT commodity_group, origin_country, COUNT(*) n, SUM(expected_amount_ngn_minor) expected, SUM(amount_ngn_minor) assessed "
                           f"FROM v_assessments{w} GROUP BY 1,2 HAVING n>=?", db.reader(as_of), params=[*p, min_n])
    df["shortfall_pct"] = (df["expected"] - df["assessed"]) / df["expected"].where(df["expected"] > 0)
    df["shortfall_minor"] = df["expected"] - df["assessed"]
    return df.sort_values("shortfall_pct", ascending=False).reset_index(drop=True)


def unpaid_ageing(f: Filters, as_of: str | None = None) -> pd.DataFrame:
    now = as_of or now_iso()
    w, p = _where(f)
    conn, avp = db.avp(as_of)
    df = pd.read_sql_query(f"SELECT assessed_ngn_minor-paid_ngn_minor AS open_minor, (julianday(?)-julianday(occurred_at)) AS age FROM {avp}{w} "
                           f"AND assessed_ngn_minor>paid_ngn_minor AND paid_ngn_minor=0", conn, params=[now, *p])
    bins = [(0, 3, "0-3 days"), (3, 7, "3-7 days"), (7, 14, "7-14 days"), (14, 1e9, "over 14 days")]
    rows = [{"bucket": lab, "amount_minor": int(df[(df["age"] >= lo) & (df["age"] < hi)]["open_minor"].sum()), "count": int(((df["age"] >= lo) & (df["age"] < hi)).sum())}
            for lo, hi, lab in bins]
    return pd.DataFrame(rows)


def in_transit_by_bank(as_of: str | None = None) -> pd.DataFrame:
    return pd.read_sql_query("SELECT p.bank, SUM(a.amount_ngn_minor) AS in_transit_minor, COUNT(DISTINCT a.payment_id) AS payments FROM v_collections a "
                             "JOIN v_payments p ON p.payment_id=a.payment_id WHERE a.settled_at IS NULL GROUP BY 1 ORDER BY 2 DESC", db.reader(as_of))


def duplicate_payments(f: Filters, as_of: str | None = None) -> pd.DataFrame:
    pw, pp = "", []
    if f.start:
        pw += " AND occurred_at >= ?"
        pp.append(f.start)
    if f.end:
        pw += " AND occurred_at < ?"
        pp.append(f.end)
    return pd.read_sql_query(f"SELECT payment_id, payment_ref, nsw_ref, payer_id, bank, amount_ngn_minor, occurred_at FROM v_payments WHERE is_duplicate=1{pw} ORDER BY occurred_at DESC",
                             db.reader(as_of), params=pp)


def variance_bridge(f: Filters, as_of: str | None = None) -> list[dict]:
    """Waterfall from assessed to remitted: each step is a named gap category (all amounts computed by SQL)."""
    fw = four_way(f, as_of)
    A, P = fw["assessed"]["amount"], fw["paid"]["amount"]
    gross, cost, net = fw["settled"]["amount"], fw["settled"]["collection_cost"], fw["settled"]["net"]
    R = fw["remitted"]["amount"]
    retained = 0
    # retained under the remittance rule = net settled that the rule does not require to be remitted
    w, p = _where(f)
    conn, avp = db.avp(as_of)
    rows = conn.execute(f"SELECT entity_id, SUM(settled_ngn_minor) FROM {avp}{w} GROUP BY 1", p).fetchall()
    sh = pd.read_sql_query("SELECT entity_id, AVG(share) s FROM v_remittances GROUP BY 1", conn).set_index("entity_id")["s"].to_dict()
    for ent, v in rows:
        retained += (v or 0) * (1 - sh.get(ent, 1.0 if ent not in ("NSW",) else 0.0))
    steps = [("Assessed", A, "total"), ("Unpaid / not yet paid (net of FX variance)", -(A - P), "delta"), ("Paid", P, "total"),
             ("In transit (paid, not settled)", -(P - gross), "delta"), ("Collection costs withheld", -cost, "delta"), ("Settled to agencies (net)", net, "total"),
             ("Retained under the remittance rule", -int(retained), "delta"), ("Awaiting remittance (timing)", -max(0, int(net - retained - R)), "delta"),
             ("Remitted to Treasury", R, "total")]
    return [{"label": l, "amount": int(a), "kind": k} for l, a, k in steps]
