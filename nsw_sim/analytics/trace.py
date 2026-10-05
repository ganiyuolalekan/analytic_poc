"""Trace workbench: search, consignment timeline + money trace + ledger trace, and statement-line drill-through
L0 line -> L1 accounts -> L2 journal entries (paged) -> L3 source documents -> L4 consignment, with tie-out checks."""
from __future__ import annotations

import json
import re

import pandas as pd

from nsw_sim import db
from nsw_sim.analytics.statements import ledger_sums, resolve_entities
from nsw_sim.analytics.queries import now_iso

_PATTERNS = [
    ("consignment", re.compile(r"^NSW-\d{6}-[A-Z]{3,5}-\d{7}$", re.I)), ("declaration", re.compile(r"^C\d{6}/\d{2}$", re.I)),
    ("payment_ref", re.compile(r"^\d{12}$")), ("container", re.compile(r"^[A-Z]{4}\d{7}$", re.I)),
    ("journal", re.compile(r"^JE-", re.I)), ("alert", re.compile(r"^ALT-", re.I)), ("remittance", re.compile(r"^REM-", re.I)),
    ("permit", re.compile(r"/")), ("bl", re.compile(r"^[A-Z]{3,4}-?\d{8,10}$", re.I)), ("form_m", re.compile(r"^MF\d+$", re.I)), ("paar", re.compile(r"^PAAR\d+$", re.I)),
]


def search(q: str, as_of: str | None = None) -> list[dict]:
    """Resolve any reference (NSW ref, declaration, payment ref, container, permit no, journal entry, alert id, remittance) to targets."""
    q = (q or "").strip()
    if not q:
        return []
    c = db.reader(as_of)
    out: list[dict] = []

    def cons(where: str, arg, kind: str) -> None:
        for r in c.execute(f"SELECT nsw_ref, origin_country, commodity_group, port FROM v_consignments WHERE {where}", (arg,)).fetchall():
            out.append({"kind": "consignment", "ref": r[0], "label": f"{r[0]} · {r[3]} · {r[1]} · {r[2]} (matched {kind})"})
    for kind, pat in _PATTERNS:
        if not pat.search(q):
            continue
        if kind == "consignment":
            cons("nsw_ref=?", q.upper(), "NSW reference")
        elif kind == "declaration":
            cons("declaration_no=?", q.upper(), "declaration")
        elif kind == "container":
            cons("container_no=?", q.upper(), "container")
        elif kind == "bl":
            cons("bl_no=?", q.upper(), "bill of lading")
        elif kind == "form_m":
            cons("form_m=?", q.upper(), "Form M")
        elif kind == "paar":
            cons("paar=?", q.upper(), "PAAR")
        elif kind == "payment_ref":
            for r in c.execute("SELECT nsw_ref, payment_id FROM v_payments WHERE payment_ref=?", (q,)).fetchall():
                out.append({"kind": "consignment", "ref": r[0], "label": f"{r[0]} (matched payment reference, {r[1]})"})
        elif kind == "permit":
            for r in c.execute("SELECT nsw_ref, owner_entity FROM v_stage_durations WHERE doc_no=?", (q,)).fetchall():
                out.append({"kind": "consignment", "ref": r[0], "label": f"{r[0]} (matched {r[1]} document {q})"})
        elif kind == "journal":
            r = c.execute("SELECT entry_id, entity_id, nsw_ref, ref_type, ref_id, occurred_at FROM v_journal_entries WHERE entry_id=?", (q.upper(),)).fetchone()
            if r:
                out.append({"kind": "journal", "ref": r[0], "nsw_ref": r[2], "label": f"Journal entry {r[0]} · {r[1]} · {r[3]}"})
        elif kind == "alert":
            r = c.execute("SELECT alert_id, rule_code, entity_id, subject FROM v_alerts WHERE alert_id=?", (q.upper(),)).fetchone()
            if r:
                out.append({"kind": "alert", "ref": r[0], "label": f"Alert {r[0]} · {r[1]} · {r[2]}"})
        elif kind == "remittance":
            r = c.execute("SELECT remittance_id, entity_id, period FROM v_remittances WHERE remittance_id=?", (q.upper(),)).fetchone()
            if r:
                out.append({"kind": "remittance", "ref": r[0], "label": f"Remittance {r[0]} · {r[1]} · {r[2]}"})
    seen, uniq = set(), []
    for o in out:
        if (o["kind"], o["ref"]) not in seen:
            seen.add((o["kind"], o["ref"]))
            uniq.append(o)
    return uniq


def consignment_trace(ref: str, as_of: str | None = None) -> dict | None:
    c = db.reader(as_of)
    head = pd.read_sql_query("SELECT * FROM v_consignments WHERE nsw_ref=?", c, params=(ref,))
    if head.empty:
        return None
    h = head.iloc[0].to_dict()
    names = {r[0]: r[1] for r in c.execute("SELECT party_id, name FROM v_parties WHERE party_id IN (?,?,?)", (h["importer_id"], h["agent_id"], "SHP-" + str(h["carrier"])[4:] if str(h["carrier"]).startswith("SHP") else h["carrier"]))}
    h["importer_name"], h["agent_name"] = names.get(h["importer_id"]), names.get(h["agent_id"])
    stages = pd.read_sql_query("SELECT stage, system_type, owner_entity, ready_at, started_at, occurred_at, wait_h, dur_h, sla_hours, sla_breach, status, data_complete, doc_no "
                               "FROM v_stage_durations WHERE nsw_ref=? ORDER BY occurred_at, stage", c, params=(ref,))
    fees = pd.read_sql_query("SELECT assessment_id, entity_id, process_code, fee_code, currency, assessed_ngn_minor, expected_amount_ngn_minor, paid_ngn_minor, settled_ngn_minor, "
                             "collection_cost_ngn_minor, occurred_at FROM v_assessed_vs_paid WHERE nsw_ref=? ORDER BY occurred_at, entity_id", c, params=(ref,))
    pays = pd.read_sql_query("SELECT payment_id, payment_ref, bank, channel, amount_ngn_minor, status, is_duplicate, initiated_at, occurred_at FROM v_payments WHERE nsw_ref=? ORDER BY occurred_at", c, params=(ref,))
    pids = pays["payment_id"].tolist()
    stl = pd.DataFrame()
    if pids:
        stl = pd.read_sql_query(f"SELECT s.settlement_id, s.batch_id, s.payment_id, s.entity_id, s.amount_ngn_minor, s.collection_cost_ngn_minor, s.occurred_at FROM v_settlements s "
                                f"WHERE s.payment_id IN ({','.join('?' * len(pids))}) ORDER BY occurred_at", c, params=pids)
    sids = stl["settlement_id"].tolist() if not stl.empty else []
    q = "SELECT entry_id, entity_id, occurred_at, ref_type, ref_id, memo FROM v_journal_entries WHERE nsw_ref=?"
    params: list = [ref]
    if sids:
        q += f" OR (ref_type='settlement' AND ref_id IN ({','.join('?' * len(sids))}))"
        params += sids
    entries = pd.read_sql_query(q + " ORDER BY occurred_at, entity_id", c, params=params)
    if not entries.empty:
        ids_ = entries["entry_id"].tolist()
        lines = pd.read_sql_query(f"SELECT entry_id, account_code, account_name, debit_minor, credit_minor FROM v_ledger_lines WHERE entry_id IN ({','.join('?' * len(ids_))})", c, params=ids_)
    else:
        lines = pd.DataFrame(columns=["entry_id", "account_code", "account_name", "debit_minor", "credit_minor"])
    alerts = pd.read_sql_query("SELECT alert_id, rule_code, severity, entity_id, detected_at, status FROM v_alerts WHERE details_json LIKE ? OR subject=? ORDER BY detected_at",
                               c, params=(f'%{ref}%', ref)) if _has_details(c) else pd.DataFrame()
    return {"consignment": h, "stages": stages, "fees": fees, "payments": pays, "settlements": stl, "journal_entries": entries, "journal_lines": lines,
            "alerts": alerts, "sankey": money_sankey(h, fees, pays, stl, as_of), "recon": recon_state(fees)}


def _has_details(c) -> bool:
    return True


def recon_state(fees: pd.DataFrame) -> pd.DataFrame:
    """Per-entity reconciliation state of one consignment: assessed / paid / settled and status per leg."""
    if fees.empty:
        return pd.DataFrame()
    g = fees.groupby("entity_id").agg(assessed=("assessed_ngn_minor", "sum"), paid=("paid_ngn_minor", "sum"), settled=("settled_ngn_minor", "sum"),
                                      cost=("collection_cost_ngn_minor", "sum")).reset_index()
    g["state"] = ["unpaid" if r.paid == 0 else ("in transit" if r.settled == 0 else "settled") for r in g.itertuples()]
    return g


def money_sankey(h: dict, fees: pd.DataFrame, pays: pd.DataFrame, stl: pd.DataFrame, as_of: str | None) -> dict:
    """Nodes/links: payer -> NSW payment -> settlement bank -> each entity -> (collection cost | Treasury remittance share)."""
    if fees.empty:
        return {"labels": [], "source": [], "target": [], "value": [], "status": []}
    c = db.reader(as_of)
    share = {r[0]: r[1] for r in c.execute("SELECT entity_id, AVG(share) FROM v_remittances GROUP BY 1")}
    labels = ["Payer", "NSW payment", f"Bank {h['bank']}"]
    idx = {k: i for i, k in enumerate(labels)}
    src, tgt, val, status = [], [], [], []

    def node(name: str) -> int:
        if name not in idx:
            idx[name] = len(labels)
            labels.append(name)
        return idx[name]
    paid_total = int(fees["paid_ngn_minor"].sum())
    if paid_total:
        src += [0, 1]; tgt += [1, 2]; val += [paid_total, paid_total]; status += ["paid", "paid"]
    for r in fees.groupby("entity_id").agg(paid=("paid_ngn_minor", "sum"), settled=("settled_ngn_minor", "sum"), cost=("collection_cost_ngn_minor", "sum")).reset_index().itertuples():
        if r.paid == 0:
            continue
        e = node(r.entity_id)
        src.append(2); tgt.append(e); val.append(int(r.paid)); status.append("settled" if r.settled else "in transit")
        if r.settled:
            cost_n = node(f"{r.entity_id} · collection cost")
            src.append(e); tgt.append(cost_n); val.append(int(r.cost)); status.append("settled")
            s = share.get(r.entity_id)
            if s:
                tr = node("Treasury (Federation Account)")
                src.append(e); tgt.append(tr); val.append(int(r.settled * s)); status.append("per remittance rule")
    return {"labels": labels, "source": src, "target": tgt, "value": val, "status": status}


# =============================================================================== statement-line trace
def _fee_account_map(entity: str, as_of: str | None) -> dict[str, str]:
    """revenue account -> fee_code for an entity (from its stored profile)."""
    row = db.reader(as_of).execute("SELECT profile_json FROM entity_profiles WHERE entity_id=?", (entity,)).fetchone()
    if not row:
        return {}
    return {r["revenue_account"]: r["fee_code"] for r in json.loads(row[0]).get("fee_rules", [])}


def l1_accounts(entities: list[str], accounts: list[str], start: str | None, end: str, kind: str = "flow", as_of: str | None = None) -> pd.DataFrame:
    """L1: accounts under a statement line with ledger totals (net debit - credit) and line counts."""
    df = ledger_sums(entities, None if kind == "balance" else start, end, as_of, ("entity_id", "account_code"))
    df = df[df["account_code"].isin(accounts)].copy()
    df["net_debit_minor"] = df["debit_minor"] - df["credit_minor"]
    return df.reset_index(drop=True)


def l2_entries(entities: list[str], account: str, start: str | None, end: str, kind: str = "flow", page: int = 0, size: int = 50,
               as_of: str | None = None, ref_type: str | None = None) -> tuple[pd.DataFrame, int, int]:
    """L2: journal entries touching ``account`` (paged). Returns (page, total_rows, total_net_debit over ALL rows)."""
    c = db.reader(as_of)
    ph = ",".join("?" * len(entities))
    s = start if (start and kind != "balance") else "0000"
    e = min(end, as_of or now_iso())
    w = f"l.entity_id IN ({ph}) AND l.account_code=? AND l.occurred_at>=? AND l.occurred_at<?"
    p: list = [*entities, account, s, e]
    if ref_type:
        w += " AND je.ref_type=?"
        p.append(ref_type)
    tot = c.execute(f"SELECT COUNT(*), COALESCE(SUM(l.debit_minor-l.credit_minor),0) FROM v_ledger_lines l JOIN v_journal_entries je ON je.entry_id=l.entry_id WHERE {w}", p).fetchone()
    df = pd.read_sql_query(f"SELECT l.entry_id, l.entity_id, l.occurred_at, je.ref_type, je.ref_id, je.nsw_ref, je.memo, l.debit_minor, l.credit_minor, l.debit_minor-l.credit_minor AS net_debit_minor "
                           f"FROM v_ledger_lines l JOIN v_journal_entries je ON je.entry_id=l.entry_id WHERE {w} ORDER BY l.occurred_at, l.entry_id LIMIT {int(size)} OFFSET {int(page * size)}", c, params=p)
    return df, int(tot[0]), int(tot[1])


def l3_sources(entry_id: str, as_of: str | None = None) -> dict:
    """L3: the source documents behind one journal entry plus per-account amounts re-derived from them (for the tie-out check)."""
    c = db.reader(as_of)
    e = c.execute("SELECT entry_id, entity_id, occurred_at, ref_type, ref_id, nsw_ref FROM v_journal_entries WHERE entry_id=?", (entry_id,)).fetchone()
    if not e:
        return {"entry_id": entry_id, "docs": pd.DataFrame(), "derived": {}, "actual": {}, "ties": False}
    eid, ent, at, rtype, rid, nref = e
    actual = {r[0]: (r[1], r[2]) for r in c.execute("SELECT account_code, debit_minor, credit_minor FROM v_ledger_lines WHERE entry_id=?", (entry_id,))}
    docs, derived = pd.DataFrame(), {}
    fee_map = _fee_account_map(ent, as_of)
    if rtype == "assessment":
        docs = pd.read_sql_query("SELECT assessment_id AS doc_id, 'fee assessment' AS doc_type, fee_code, amount_ngn_minor AS amount_minor, nsw_ref FROM v_assessments "
                                 "WHERE nsw_ref=? AND entity_id=? AND occurred_at=?", c, params=(rid.split("|")[0], ent, at))
        derived["1200"] = (int(docs["amount_minor"].sum()), 0)
        acct_of = {v: k for k, v in fee_map.items()}
        for fc, g in docs.groupby("fee_code"):
            derived[acct_of.get(fc, "?")] = (0, int(g["amount_minor"].sum()))
    elif rtype == "payment" and ent != "CBN":
        pid = rid.split("|")[0]
        docs = pd.read_sql_query("SELECT a.alloc_id AS doc_id, 'payment allocation' AS doc_type, a.fee_code, a.amount_ngn_minor AS amount_minor, a.nsw_ref, f.amount_ngn_minor AS assessed_minor "
                                 "FROM v_collections a JOIN v_assessments f ON f.assessment_id=a.assessment_id WHERE a.payment_id=? AND a.entity_id=?", c, params=(pid, ent))
        paid, assessed = int(docs["amount_minor"].sum()), int(docs["assessed_minor"].sum())
        derived = {"1105": (paid, 0), "1200": (0, assessed)}
        if paid > assessed:
            derived["4950"] = (0, paid - assessed)
        elif paid < assessed:
            derived["5950"] = (assessed - paid, 0)
    elif rtype == "payment":      # CBN receipt
        docs = pd.read_sql_query("SELECT payment_id AS doc_id, 'payment' AS doc_type, payment_ref AS fee_code, amount_ngn_minor AS amount_minor, nsw_ref FROM v_payments WHERE payment_id=?", c, params=(rid,))
        t = int(docs["amount_minor"].sum())
        derived = {"1100": (t, 0), "2100": (0, t)}
    elif rtype == "settlement":
        docs = pd.read_sql_query("SELECT settlement_id AS doc_id, 'settlement' AS doc_type, batch_id AS fee_code, amount_ngn_minor AS amount_minor, collection_cost_ngn_minor AS cost_minor, payment_id AS nsw_ref "
                                 "FROM v_settlements WHERE settlement_id=?", c, params=(rid,))
        g, cst = int(docs["amount_minor"].sum()), int(docs["cost_minor"].sum())
        derived = {"1100": (g - cst, 0), "5500": (cst, 0), "1105": (0, g)}
    elif rtype in ("expense", "depreciation"):
        docs = pd.read_sql_query("SELECT expense_id AS doc_id, 'expense' AS doc_type, category AS fee_code, amount_ngn_minor AS amount_minor, memo AS nsw_ref FROM v_expenses WHERE expense_id=?", c, params=(rid,))
        a = int(docs["amount_minor"].sum())
        derived = {k: (v[0] and a, v[1] and a) for k, v in actual.items()}
    elif rtype == "funding":
        docs = pd.read_sql_query("SELECT receipt_id AS doc_id, 'funding receipt' AS doc_type, facility AS fee_code, amount_ngn_minor AS amount_minor, source_country AS nsw_ref FROM v_funding WHERE receipt_id=?", c, params=(rid,))
        a = int(docs["amount_minor"].sum())
        derived = {k: (v[0] and a, v[1] and a) for k, v in actual.items()}
    elif rtype in ("remittance", "remittance_paid", "remittance_received"):
        docs = pd.read_sql_query("SELECT remittance_id AS doc_id, 'remittance' AS doc_type, period AS fee_code, amount_ngn_minor AS amount_minor, entity_id AS nsw_ref FROM v_remittances WHERE remittance_id=?", c, params=(rid,))
        a = int(docs["amount_minor"].sum())
        derived = {k: (v[0] and a, v[1] and a) for k, v in actual.items()}
    elif rtype in ("refund", "unapplied_refund", "unapplied_receipt"):
        key = rid if rtype != "refund" else rid
        docs = pd.read_sql_query("SELECT refund_id AS doc_id, 'refund' AS doc_type, fee_code, amount_ngn_minor AS amount_minor, memo AS nsw_ref FROM v_refunds WHERE refund_id IN (?,?)", c, params=(rid, f"RFU-{rid}"))
        if docs.empty:
            docs = pd.read_sql_query("SELECT payment_id AS doc_id, 'payment' AS doc_type, payment_ref AS fee_code, amount_ngn_minor AS amount_minor, nsw_ref FROM v_payments WHERE payment_id=?", c, params=(rid,))
        a = int(docs["amount_minor"].sum())
        derived = {k: (v[0] and a, v[1] and a) for k, v in actual.items()}
    elif rtype == "opening":
        a = sum(v[0] for v in actual.values())
        docs = pd.DataFrame([{"doc_id": rid, "doc_type": "opening balance", "fee_code": "", "amount_minor": a, "nsw_ref": ""}])
        derived = dict(actual)
    else:
        derived = dict(actual)
    ties = all(derived.get(k, (0, 0)) == v for k, v in actual.items()) and set(derived) <= set(actual)
    return {"entry_id": entry_id, "ref_type": rtype, "ref_id": rid, "nsw_ref": nref, "docs": docs, "derived": derived, "actual": actual, "ties": bool(ties)}


def tie_out(entities: list[str], account: str, start: str | None, end: str, kind: str = "flow", as_of: str | None = None, max_entries: int = 400) -> dict:
    """Check that line total == sum of L2 entries == sum of L3 derived amounts (the 'ties out' badge)."""
    l1 = l1_accounts(entities, [account], start, end, kind, as_of)
    l1_total = int(l1["net_debit_minor"].sum()) if not l1.empty else 0
    rows, n, l2_total = l2_entries(entities, account, start, end, kind, 0, max_entries, as_of)
    checked = rows["entry_id"].tolist() if n <= max_entries else []
    l3_total, all_tie = 0, True
    for eid in checked:
        s = l3_sources(eid, as_of)
        d = s["derived"].get(account, (0, 0))
        l3_total += d[0] - d[1]
        all_tie &= s["ties"]
    return {"account": account, "l1_total": l1_total, "l2_total": l2_total, "l2_rows": n, "l3_total": l3_total if checked else None,
            "l3_checked": len(checked), "ties_l1_l2": l1_total == l2_total, "ties_l2_l3": (l3_total == l2_total and all_tie) if checked else None}
