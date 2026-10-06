"""Statement-line trace (Section 11.4 P04): L0 line -> L1 accounts -> L2 journal entries (paged) -> L3 source documents -> L4 consignment.
Totals at every level sum exactly to the level above, with a green 'ties out' check or a red mismatch."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from app.components import fmt, state, tooltips
from nsw_sim import clock
from nsw_sim.analytics import trace

PAGE = 40


def _tie(ok: bool | None, text: str = "") -> str:
    if ok is None:
        return ""
    return f"<span class='tie-{'ok' if ok else 'bad'}'>{'✓ ties out' if ok else '✗ mismatch'}</span> {text}"


def render(desc: dict, key: str = "trace") -> None:
    """``desc`` is the ``trace`` descriptor attached to a statement line (entities, accounts, start, end, kind, sign)."""
    ss = st.session_state
    ents, accts, start, end, kind = desc["entities"], desc["accounts"], desc.get("start"), desc["end"], desc.get("kind", "flow")
    sign = desc.get("sign", 1)
    state.as_of()
    crumbs = ["L0 line"]
    sel_acct = ss.get(f"{key}_acct")
    sel_entry = ss.get(f"{key}_entry")
    if sel_acct:
        crumbs.append(f"L1 account {sel_acct}")
    if sel_entry:
        crumbs += ["L2 entry", "L3 documents"]
    st.markdown("**Trace:** " + " › ".join(crumbs) + "  ·  " + tooltips.tip("trace_levels"))
    if st.button("Back to line", key=f"{key}_back"):
        ss.pop(f"{key}_acct", None), ss.pop(f"{key}_entry", None)
        st.rerun()
    l1 = state.cc(trace.l1_accounts, ents, accts, start, end, kind)
    total = int(l1["net_debit_minor"].sum()) if not l1.empty else 0
    st.markdown(f"**L1 — accounts** · total {fmt.ngn(sign * total, exact=True)}", help=tooltips.tip("ties_out_check"))
    if not l1.empty:
        {r.account_code: r.account_code for r in l1.itertuples()}
        d = l1.assign(amount=[fmt.ngn(sign * v, exact=True) for v in l1["net_debit_minor"]])[["entity_id", "account_code", "amount", "n_lines"]]
        pick = st.dataframe(d, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row", key=f"{key}_l1df")
        if pick.selection.rows:
            ss[f"{key}_acct"] = d.iloc[pick.selection.rows[0]]["account_code"]
            ss.pop(f"{key}_entry", None)
            sel_acct = ss[f"{key}_acct"]
    if sel_acct:
        page = int(ss.get(f"{key}_page", 0))
        rows, n, l2_total = state.cc(trace.l2_entries, ents, sel_acct, start, end, kind, page, PAGE)
        l1_acct = int(l1[l1["account_code"] == sel_acct]["net_debit_minor"].sum())
        st.markdown(f"**L2 — journal entries on {sel_acct}** · {n:,} entries · total {fmt.ngn(sign * l2_total, exact=True)} " + _tie(l1_acct == l2_total, "(L1 = sum of all L2 entries)"), unsafe_allow_html=True)
        c1, c2, c3 = st.columns([0.15, 0.15, 0.7])
        if c1.button("Previous", key=f"{key}_prev", disabled=page == 0):
            ss[f"{key}_page"] = page - 1
            st.rerun()
        if c2.button("Next", key=f"{key}_next", disabled=(page + 1) * PAGE >= n):
            ss[f"{key}_page"] = page + 1
            st.rerun()
        c3.caption(f"Page {page + 1} of {max(1, -(-n // PAGE))}")
        d2 = rows.assign(when=[clock.fmt_wat(t, "%d %b %H:%M") for t in rows["occurred_at"]], amount=[fmt.ngn(sign * v, exact=True) for v in rows["net_debit_minor"]])[
            ["entry_id", "when", "entity_id", "ref_type", "nsw_ref", "amount", "memo"]]
        p2 = st.dataframe(d2, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row", key=f"{key}_l2df")
        if p2.selection.rows:
            ss[f"{key}_entry"] = d2.iloc[p2.selection.rows[0]]["entry_id"]
            sel_entry = ss[f"{key}_entry"]
    if sel_entry:
        s = state.cc(trace.l3_sources, sel_entry)
        st.markdown(f"**L3 — source documents for {sel_entry}** ({s.get('ref_type', '')}) " + _tie(s["ties"], "(amounts re-derived from the documents equal the posted lines)"), unsafe_allow_html=True)
        docs = s["docs"]
        if not docs.empty:
            st.dataframe(docs.assign(amount=[fmt.ngn(v, exact=True) for v in docs["amount_minor"]]).drop(columns=["amount_minor"], errors="ignore"), hide_index=True, width="stretch")
        cmp = pd.DataFrame([{"account": a, "posted_debit": fmt.ngn(v[0]), "posted_credit": fmt.ngn(v[1]), "derived_debit": fmt.ngn(s['derived'].get(a, (0, 0))[0]),
                             "derived_credit": fmt.ngn(s['derived'].get(a, (0, 0))[1])} for a, v in s["actual"].items()])
        st.dataframe(cmp, hide_index=True, width="stretch")
        if s.get("nsw_ref") and st.button(f"L4 — open consignment {s['nsw_ref']}", key=f"{key}_l4"):
            state.goto_trace(s["nsw_ref"])
