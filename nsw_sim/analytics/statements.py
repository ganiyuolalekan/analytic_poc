"""Financial statements computed from the ledger by SQL (never stored): performance, position, cash flow, and the
government-specific revenue-collection-and-remittance statement. Every line carries a trace descriptor."""
from __future__ import annotations

import pandas as pd

from nsw_sim import clock, db
from nsw_sim.analytics.queries import Filters, now_iso
from nsw_sim.sim.reference import consolidated_entities

COLS = ["entity_id", "account_code", "account_name", "class", "statement_line", "debit_minor", "credit_minor"]


def resolve_entities(entity: str | list[str] | tuple[str, ...] | None) -> list[str]:
    if entity in (None, "ALL", "all", ["ALL"]):
        return consolidated_entities()
    return [entity] if isinstance(entity, str) else list(entity)


def ledger_sums(entities: list[str], start: str | None, end: str | None, as_of: str | None = None, dims: tuple[str, ...] = ("account_code",),
                use_rollup: bool = True) -> pd.DataFrame:
    """Debit/credit sums over [start, end) grouped by ``dims`` (account_code, entity_id, origin_country, process_code, day).
    Whole WAT days use the daily rollup; partial days use the journal lines. Both are exact (tested to be equal)."""
    from datetime import timedelta
    end = min(end or now_iso(), as_of or now_iso())
    conn = db.reader(as_of)
    e_dt = clock.to_dt(end)
    s_dt = clock.to_dt(start) if start else clock.to_dt("2000-01-01T00:00:00Z")
    ent_ph = ",".join("?" * len(entities))
    sel = {"account_code": "account_code", "entity_id": "entity_id", "origin_country": "COALESCE(NULLIF(origin_country,''),'-')",
           "process_code": "COALESCE(NULLIF(process_code,''),'-')", "day": "day"}
    lines_sel = {**sel, "origin_country": "COALESCE(origin_country,'-')", "process_code": "COALESCE(process_code,'-')"}
    parts: list[pd.DataFrame] = []

    def from_lines(a: str, b: str) -> None:
        cols = ", ".join(f"{lines_sel[d]} AS {d}" for d in dims)
        q = (f"SELECT {cols}, SUM(debit_minor) AS debit_minor, SUM(credit_minor) AS credit_minor, COUNT(*) AS n_lines FROM v_ledger_lines "
             f"WHERE entity_id IN ({ent_ph}) AND occurred_at>=? AND occurred_at<?" + (f" GROUP BY {', '.join(dims)}" if dims else ""))
        parts.append(pd.read_sql_query(q, conn, params=[*entities, a, b]))

    if not use_rollup:
        from_lines(clock.iso(s_dt), clock.iso(e_dt))
    else:
        first_full = clock.wat(s_dt).date()
        if clock.wat_midnight_utc(first_full) < s_dt:
            first_full += timedelta(days=1)
        last_full_excl = clock.wat(e_dt).date()          # days strictly before this are complete before `end`
        if first_full >= last_full_excl:
            from_lines(clock.iso(s_dt), clock.iso(e_dt))
        else:
            head_end = clock.iso(clock.wat_midnight_utc(first_full))
            tail_start = clock.iso(clock.wat_midnight_utc(last_full_excl))
            if clock.iso(s_dt) < head_end:
                from_lines(clock.iso(s_dt), head_end)
            if tail_start < clock.iso(e_dt):
                from_lines(tail_start, clock.iso(e_dt))
            cols = ", ".join(f"{sel[d]} AS {d}" for d in dims)
            q = (f"SELECT {cols}, SUM(debit_minor) AS debit_minor, SUM(credit_minor) AS credit_minor, SUM(n_lines) AS n_lines FROM rollup_ledger_day "
                 f"WHERE entity_id IN ({ent_ph}) AND day>=? AND day<?" + (f" GROUP BY {', '.join(dims)}" if dims else ""))
            parts.append(pd.read_sql_query(q, conn, params=[*entities, first_full.isoformat(), last_full_excl.isoformat()]))
    df = pd.concat([p for p in parts if not p.empty], ignore_index=True) if any(not p.empty for p in parts) else pd.DataFrame(columns=[*dims, "debit_minor", "credit_minor", "n_lines"])
    if dims and not df.empty:
        df = df.groupby(list(dims), as_index=False)[["debit_minor", "credit_minor", "n_lines"]].sum()
    elif not dims:
        df = pd.DataFrame([{"debit_minor": df["debit_minor"].sum(), "credit_minor": df["credit_minor"].sum(), "n_lines": df["n_lines"].sum()}])
    return df


def coa(entities: list[str], as_of: str | None = None) -> pd.DataFrame:
    ph = ",".join("?" * len(entities))
    return pd.read_sql_query(f"SELECT account_code, MIN(name) AS account_name, class, MIN(statement_line) AS statement_line FROM chart_of_accounts WHERE entity_id IN ({ph}) "
                          f"GROUP BY account_code, class", db.reader(as_of), params=entities)


def _line(key: str, label: str, section: str, amount: int, prev: int | None, entities: list[str], accounts: list[str], start: str | None, end: str,
          kind: str = "flow", sign: int = 1) -> dict:
    return {"key": key, "label": label, "section": section, "amount": int(amount), "prev": None if prev is None else int(prev),
            "trace": {"entities": entities, "accounts": accounts, "start": start, "end": end, "kind": kind, "sign": sign}}


def performance(entity, f: Filters, as_of: str | None = None, compare: Filters | None = None) -> dict:
    """Statement of Financial Performance: revenue by stream, expenses by category, surplus, distributions."""
    ents = resolve_entities(entity)
    cur = ledger_sums(ents, f.start, f.end, as_of, ("account_code",))
    prev = ledger_sums(ents, compare.start, compare.end, as_of, ("account_code",)) if compare else None
    names = coa(ents, as_of).set_index("account_code")

    def amt(df, acct, side):
        if df is None or df.empty:
            return 0
        r = df[df["account_code"] == acct]
        if r.empty:
            return 0
        d, c = int(r["debit_minor"].iloc[0]), int(r["credit_minor"].iloc[0])
        return (c - d) if side == "cr" else (d - c)

    lines, accounts = [], sorted(set(cur["account_code"]) | (set(prev["account_code"]) if prev is not None and not prev.empty else set()))
    end = f.end or now_iso()
    rev_accts = [a for a in accounts if a in names.index and names.at[a, "class"] == "Revenue"]
    exp_accts = [a for a in accounts if a in names.index and names.at[a, "class"] == "Expense"]
    for a in rev_accts:
        lines.append(_line(f"rev:{a}", names.at[a, "statement_line"], "Revenue", amt(cur, a, "cr"), amt(prev, a, "cr") if prev is not None else None, ents, [a], f.start, end))
    tot_rev = sum(x["amount"] for x in lines)
    tot_rev_p = sum(x["prev"] or 0 for x in lines) if prev is not None else None
    exp_lines = [_line(f"exp:{a}", names.at[a, "statement_line"], "Expenses", amt(cur, a, "dr"), amt(prev, a, "dr") if prev is not None else None, ents, [a], f.start, end,
                       sign=-1) for a in exp_accts]
    tot_exp = sum(x["amount"] for x in exp_lines)
    tot_exp_p = sum(x["prev"] or 0 for x in exp_lines) if prev is not None else None
    dist = amt(cur, "3200", "dr")
    dist_p = amt(prev, "3200", "dr") if prev is not None else None
    surplus, surplus_p = tot_rev - tot_exp, (None if prev is None else tot_rev_p - tot_exp_p)
    return {"title": "Statement of Financial Performance", "entities": ents, "period": [f.start, end],
            "revenue": lines, "total_revenue": tot_rev, "total_revenue_prev": tot_rev_p, "expenses": exp_lines, "total_expenses": tot_exp,
            "total_expenses_prev": tot_exp_p, "surplus": surplus, "surplus_prev": surplus_p,
            "distributions": _line("dist:3200", "Distributions to Treasury", "Distributions", dist, dist_p, ents, ["3200"], f.start, end, sign=-1),
            "surplus_after_distributions": surplus - dist}


def revenue_breakdown(entity, f: Filters, by: str = "origin_country", as_of: str | None = None) -> pd.DataFrame:
    """Revenue by origin country or process, from ledger lines (revenue accounts only)."""
    ents = resolve_entities(entity)
    names = coa(ents, as_of)
    rev = set(names[names["class"] == "Revenue"]["account_code"])
    df = ledger_sums(ents, f.start, f.end, as_of, ("account_code", by))
    df = df[df["account_code"].isin(rev)]
    df["revenue_minor"] = df["credit_minor"] - df["debit_minor"]
    return df.groupby(by, as_index=False)["revenue_minor"].sum().sort_values("revenue_minor", ascending=False)


def position(entity, as_at: str | None, as_of: str | None = None, compare_at: str | None = None) -> dict:
    """Statement of Financial Position as at ``as_at``. Assets = Liabilities + Equity (accumulated surplus + result to date − distributions)."""
    ents = resolve_entities(entity)
    as_at = as_at or now_iso()
    cur = ledger_sums(ents, None, as_at, as_of, ("account_code",))
    prev = ledger_sums(ents, None, compare_at, as_of, ("account_code",)) if compare_at else None
    names = coa(ents, as_of).set_index("account_code")

    def bal(df, a):
        if df is None or df.empty:
            return 0
        r = df[df["account_code"] == a]
        return 0 if r.empty else int(r["debit_minor"].iloc[0]) - int(r["credit_minor"].iloc[0])

    def section(cls: str, sign: int, label: str) -> tuple[list[dict], int, int | None]:
        accts = sorted(a for a in set(cur["account_code"]) | (set(prev["account_code"]) if prev is not None and not prev.empty else set())
                       if a in names.index and names.at[a, "class"] == cls and a not in ("3100", "3200"))
        ls = [_line(f"{cls}:{a}", names.at[a, "statement_line"], label, sign * bal(cur, a), None if prev is None else sign * bal(prev, a), ents, [a], None, as_at, kind="balance", sign=sign)
              for a in accts]
        return ls, sum(x["amount"] for x in ls), (None if prev is None else sum(x["prev"] for x in ls))
    assets, ta, ta_p = section("Asset", 1, "Assets")
    liabs, tl, tl_p = section("Liability", -1, "Liabilities")
    result = -sum(bal(cur, a) for a in cur["account_code"] if a in names.index and names.at[a, "class"] in ("Revenue", "Expense"))
    result_p = None if prev is None else -sum(bal(prev, a) for a in prev["account_code"] if a in names.index and names.at[a, "class"] in ("Revenue", "Expense"))
    eq = [_line("eq:3100", "Accumulated surplus (opening)", "Equity", -bal(cur, "3100"), None if prev is None else -bal(prev, "3100"), ents, ["3100"], None, as_at, "balance", -1),
          {"key": "eq:result", "label": "Surplus / (deficit) to date", "section": "Equity", "amount": int(result), "prev": result_p,
           "trace": {"entities": ents, "accounts": [a for a in cur["account_code"] if a in names.index and names.at[a, "class"] in ("Revenue", "Expense")],
                     "start": None, "end": as_at, "kind": "balance", "sign": -1}},
          _line("eq:3200", "Distributions to Treasury", "Equity", -bal(cur, "3200"), None if prev is None else -bal(prev, "3200"), ents, ["3200"], None, as_at, "balance", -1)]
    te = sum(x["amount"] for x in eq)
    te_p = None if prev is None else sum(x["prev"] for x in eq)
    return {"title": "Statement of Financial Position", "entities": ents, "as_at": as_at, "assets": assets, "total_assets": ta, "total_assets_prev": ta_p,
            "liabilities": liabs, "total_liabilities": tl, "total_liabilities_prev": tl_p, "equity": eq, "total_equity": te, "total_equity_prev": te_p,
            "balances": ta == tl + te, "difference": ta - (tl + te)}


CASH_GROUPS = {"settlement": "Receipts: settlements from NSW collection", "funding": "Receipts: appropriation and partner funding",
               "refund": "Payments: refunds and adjustments", "expense": "Payments: operating expenses", "remittance_paid": "Payments: remittances to Treasury",
               "remittance_received": "Receipts: remittances received", "opening": "Opening balances introduced", "unapplied_receipt": "Receipts: unapplied (to be refunded)",
               "unapplied_refund": "Payments: refunds of unapplied receipts", "payment": "Receipts: collections held (settlement rail)",
               "settlement_batch": "Payments: settlement to agencies"}


def cash_flow(entity, f: Filters, as_of: str | None = None) -> dict:
    """Direct-method cash flow from postings to the bank account (1100)."""
    ents = resolve_entities(entity)
    conn = db.reader(as_of)
    ph = ",".join("?" * len(ents))
    end = min(f.end or now_iso(), as_of or now_iso())
    start = f.start or "2000-01-01T00:00:00Z"
    df = pd.read_sql_query(f"SELECT e.ref_type, SUM(l.debit_minor)-SUM(l.credit_minor) AS net FROM v_ledger_lines l JOIN v_journal_entries e ON e.entry_id=l.entry_id "
                           f"WHERE l.account_code='1100' AND l.entity_id IN ({ph}) AND l.occurred_at>=? AND l.occurred_at<? GROUP BY 1", conn, params=[*ents, start, end])
    opening = pd.read_sql_query(f"SELECT SUM(debit_minor)-SUM(credit_minor) AS b FROM v_ledger_lines WHERE account_code='1100' AND entity_id IN ({ph}) AND occurred_at<?",
                                conn, params=[*ents, start])["b"].iloc[0] or 0
    lines = [{"key": f"cash:{r.ref_type}", "label": CASH_GROUPS.get(r.ref_type, r.ref_type), "amount": int(r.net),
              "trace": {"entities": ents, "accounts": ["1100"], "start": start, "end": end, "kind": "flow", "ref_type": r.ref_type, "sign": 1}}
             for r in df.itertuples() if r.net]
    net = sum(x["amount"] for x in lines)
    return {"title": "Cash Flow (direct method)", "entities": ents, "period": [f.start, end], "lines": lines, "net_change": net, "opening": int(opening),
            "closing": int(opening + net)}


def collection_remittance(entity, f: Filters, as_of: str | None = None) -> pd.DataFrame:
    """Assessed, paid, settled, outstanding by fee code; collection efficiency (settled/assessed) and cost of collection per ₦100."""
    from nsw_sim.analytics.queries import aggregate
    ents = resolve_entities(entity)
    ff = f.with_(entities=tuple(ents))
    out = None
    for m in ("assessed", "paid", "settled_gross", "settled", "collection_cost", "outstanding", "in_transit"):
        d = aggregate(m, ["entity", "fee_code"], ff, as_of).rename(columns={"value": m})[["entity", "fee_code", m]]
        out = d if out is None else out.merge(d, on=["entity", "fee_code"], how="outer")
    out = out.fillna(0)
    out["collection_efficiency"] = out["settled_gross"] / out["assessed"].where(out["assessed"] > 0)
    out["cost_per_100"] = 100 * out["collection_cost"] / out["settled_gross"].where(out["settled_gross"] > 0)
    return out.sort_values(["entity", "assessed"], ascending=[True, False]).reset_index(drop=True)


def trial_balance(entity, as_at: str | None, as_of: str | None = None) -> pd.DataFrame:
    ents = resolve_entities(entity)
    df = ledger_sums(ents, None, as_at or now_iso(), as_of, ("entity_id", "account_code"))
    names = pd.read_sql_query("SELECT entity_id, account_code, name, class FROM chart_of_accounts", db.reader(as_of))
    df = df.merge(names, on=["entity_id", "account_code"], how="left")
    df["balance_minor"] = df["debit_minor"] - df["credit_minor"]
    return df.sort_values(["entity_id", "account_code"]).reset_index(drop=True)
