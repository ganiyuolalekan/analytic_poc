import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
import streamlit as st  # noqa: E402

from app.components import cards, charts, header, logos, state, tooltips, trace_panel  # noqa: E402
from nsw_sim.analytics import clearance, quality, queries, statements  # noqa: E402
from nsw_sim.config import yaml_config  # noqa: E402
from nsw_sim.money import fmt_ngn, fmt_pct  # noqa: E402

header.page_header("Entity Explorer", "Pick an agency from the logo grid to see its mandate, revenue by stream, country and process, its financial statements (every line can be traced "
                   "down to a consignment), processes, ledger, funding and expenses, and its data-confidence scorecard.", ("Mode", "Port", "Commodity (statement figures)"))
f = state.filters()
as_of = state.as_of()
cards_ = yaml_config("entities")["entities"]
ss = st.session_state
sel = logos.grid(ss.get("entity_sel", "NCS"), key="ent_pick")
if sel != ss.get("entity_sel"):
    ss["entity_sel"] = sel
    st.rerun()
ent = ss.get("entity_sel", "NCS")
card = cards_[ent]
prev = queries.previous_period(f) if state.compare_on() else None

with st.container(border=True):
    c1, c2 = st.columns([0.08, 0.92])
    c1.markdown(logos.img(ent, 72), unsafe_allow_html=True)
    c2.markdown(f"### {card['name']}  ·  `{ent}`  ·  {card['type']}")
    c2.markdown(card["mandate"])
    c2.markdown("**Processes:** " + " · ".join(f"{k} {v}" for k, v in card["processes"].items()))
    c2.caption(f"Remittance rule (simulated): {card['remittance']}")


def lines_table(lines: list[dict], key: str, show_prev: bool) -> dict | None:
    """Statement lines with change vs the previous period; selecting a row returns the line (for tracing)."""
    rows = [{"section": x["section"], "line": x["label"], "amount": fmt_ngn(x["amount"] * 1, exact=False), "amount_minor": x["amount"],
             **({"previous": fmt_ngn(x["prev"]) if x["prev"] is not None else "–", "change": (f"{(x['amount'] - x['prev']) / abs(x['prev']) * 100:+.1f}%" if x["prev"] else "–")} if show_prev else {})} for x in lines]
    d = pd.DataFrame(rows).drop(columns=["amount_minor"])
    sel = st.dataframe(d, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row", key=key)
    return lines[sel.selection.rows[0]] if sel.selection.rows else None


if card["type"] == "settlement":                 # CBN: settlement ledger view
    st.subheader("Settlement rail (CBN)")
    sb = state.cc(queries.settlement_batches, f)
    c = st.columns(4)
    cards.kpi(c[0], "settlement_lag", f"{sb['lag_hours'].mean():.1f} h" if not sb.empty else "–", label="Average settlement lag")
    cards.kpi(c[1], "in_transit", fmt_ngn(sb[sb['status'] == 'closed']['total_ngn_minor'].sum()), label="Batches awaiting completion")
    cards.kpi(c[2], "settled", fmt_ngn(sb['total_ngn_minor'].sum()), label="Batch value in period")
    cards.kpi(c[3], "fx_effect", fmt_ngn(float(state.cc(queries.aggregate, "assessed", [], f.with_(fee_codes=("NPA-PORTDUES", "NIMASA-LEVY")))["value"].iloc[0])), label="USD-denominated fees converted")
    if not sb.empty:
        fig = go.Figure()
        for bank, g in sb.groupby("bank"):
            fig.add_trace(go.Scatter(x=g["closed_at"], y=g["lag_hours"], name=f"Bank {bank}", mode="lines"))
        fig.add_hline(y=48, line_dash="dash", line_color=charts.RED, annotation_text="48 h alert threshold")
        fig.update_yaxes(title="lag (hours)")
        tooltips.title("settlement_lag")
        charts.show(fig, "cbn_lag", 340)
        st.dataframe(sb.assign(value=[fmt_ngn(v) for v in sb["total_ngn_minor"]]).drop(columns=["total_ngn_minor"]).head(100), hide_index=True, width="stretch")
elif card["type"] == "treasury":                 # MOF-FA: remittances received
    st.subheader("Remittances received (Federation Account)")
    rm = state.cc(queries.remittances)
    paid = rm[rm["status"] == "paid"]
    cards.kpi(st.columns(3)[0], "remitted", fmt_ngn(paid["amount_ngn_minor"].sum()), label="Total received to date")
    st.dataframe(rm.assign(amount=[fmt_ngn(v) for v in rm["amount_ngn_minor"]]).drop(columns=["amount_ngn_minor", "occurred_at"]), hide_index=True, width="stretch")
else:
    t_over, t_stmt, t_proc, t_led, t_fund, t_score = st.tabs(["Overview", "Financial statements", "Processes", "Ledger", "Funding & expenses", "Scorecard & alerts"])
    fe = f.with_(entities=(ent,))
    with t_over:
        perf = state.cc(statements.performance, ent, f, compare=prev)
        c = st.columns(5)
        mix = state.cc(queries.entity_mix, fe)
        row = mix.iloc[0] if not mix.empty else None
        if row is not None:
            cards.kpi(c[0], "assessed", fmt_ngn(row["assessed"]))
            cards.kpi(c[1], "paid", fmt_ngn(row["paid"]))
            cards.kpi(c[2], "settled", fmt_ngn(row["settled"]))
            cards.kpi(c[3], "cost_of_collection", fmt_ngn(row["collection_cost"]))
            cards.kpi(c[4], "remitted", fmt_ngn(row["remitted"]))
        a, b = st.columns(2)
        with a:
            tooltips.title("assessed", 5)
            rv = pd.DataFrame([{"stream": x["label"], "value": x["amount"]} for x in perf["revenue"] if x["amount"]])
            if not rv.empty:
                fig = go.Figure(go.Bar(x=rv["value"] / 1e11, y=rv["stream"], orientation="h", marker_color=logos.colour(ent), hovertemplate="%{y}: ₦%{x:,.3f}bn<extra></extra>"))
                fig.update_yaxes(autorange="reversed")
                charts.show(fig, "ov_streams", 300, legend=False)
        with b:
            tooltips.title("origin_country", 5)
            oc = state.cc(statements.revenue_breakdown, ent, f, "origin_country").head(12)
            fig = go.Figure(go.Bar(x=oc["origin_country"], y=oc["revenue_minor"] / 1e11, marker_color=logos.colour(ent), hovertemplate="%{x}: ₦%{y:,.3f}bn<extra></extra>"))
            fig.update_yaxes(title="₦bn")
            charts.show(fig, "ov_country", 300, legend=False)
        c1, c2 = st.columns(2)
        with c1:
            tooltips.title("process_code", 5)
            pr = state.cc(statements.revenue_breakdown, ent, f, "process_code")
            st.dataframe(pr.assign(revenue=[fmt_ngn(v) for v in pr["revenue_minor"]]).drop(columns=["revenue_minor"]), hide_index=True, width="stretch")
        with c2:
            tooltips.title("remittance_monitor", 5)
            rm = state.cc(queries.remittances, (ent,))
            if rm.empty:
                st.caption("No remittances under this agency's rule.")
            else:
                st.dataframe(rm.assign(amount=[fmt_ngn(v) for v in rm["amount_ngn_minor"]])[["period", "due_date", "paid_at", "amount", "status", "days_late"]], hide_index=True, width="stretch")
    with t_stmt:
        st.caption("Select any line to trace it: L0 line › L1 accounts › L2 journal entries › L3 source documents › L4 consignment. " + tooltips.tip("ties_out_check"))
        which = st.radio("Statement", ["Performance", "Position", "Cash flow", "Revenue collection & remittance"], horizontal=True, key="stmt_which")
        traced = None
        if which == "Performance":
            lines = perf["revenue"] + [{"key": "tot_rev", "label": "Total revenue", "section": "Revenue", "amount": perf["total_revenue"], "prev": perf["total_revenue_prev"], "trace": None}] + perf["expenses"] + [
                {"key": "tot_exp", "label": "Total expenses", "section": "Expenses", "amount": perf["total_expenses"], "prev": perf["total_expenses_prev"], "trace": None},
                {"key": "surplus", "label": "Surplus / (deficit)", "section": "Result", "amount": perf["surplus"], "prev": perf["surplus_prev"], "trace": None}, perf["distributions"]]
            traced = lines_table(lines, "stmt_perf", prev is not None)
        elif which == "Position":
            pos = state.cc(statements.position, ent, f.end, compare_at=prev.end if prev else None)
            lines = pos["assets"] + [{"key": "ta", "label": "Total assets", "section": "Assets", "amount": pos["total_assets"], "prev": pos["total_assets_prev"], "trace": None}] + pos["liabilities"] + pos["equity"] + [
                {"key": "tle", "label": "Total liabilities and equity", "section": "Check", "amount": pos["total_liabilities"] + pos["total_equity"], "prev": None, "trace": None}]
            traced = lines_table(lines, "stmt_pos", prev is not None)
            st.markdown(("<span class='tie-ok'>✓ Balance sheet balances</span>" if pos["balances"] else f"<span class='tie-bad'>✗ difference {pos['difference']}</span>"), unsafe_allow_html=True)
        elif which == "Cash flow":
            cf = state.cc(statements.cash_flow, ent, f)
            st.markdown(f"Opening bank balance **{fmt_ngn(cf['opening'])}** · net change **{fmt_ngn(cf['net_change'])}** · closing **{fmt_ngn(cf['closing'])}**")
            traced = lines_table([{**x, "section": "Cash", "prev": None} for x in cf["lines"]], "stmt_cf", False)
        else:
            cr = state.cc(statements.collection_remittance, ent, f)
            st.dataframe(pd.DataFrame({"fee": cr["fee_code"], "assessed": [fmt_ngn(v) for v in cr["assessed"]], "paid": [fmt_ngn(v) for v in cr["paid"]], "settled (gross)": [fmt_ngn(v) for v in cr["settled_gross"]],
                                       "outstanding": [fmt_ngn(v) for v in cr["outstanding"]], "collection efficiency": cr["collection_efficiency"], "cost per ₦100": cr["cost_per_100"]}),
                         hide_index=True, width="stretch", column_config={"collection efficiency": st.column_config.ProgressColumn(format="%.0f%%", min_value=0, max_value=1, help=tooltips.tip("collection_efficiency")),
                                                                                  "cost per ₦100": st.column_config.NumberColumn(format="₦%.2f", help=tooltips.tip("collection_cost_per_100"))})
        if traced and traced.get("trace"):
            st.markdown(f"##### Trace: {traced['label']}")
            trace_panel.render(traced["trace"], key="stmt_trace")
    with t_proc:
        tooltips.title("process_code", 5)
        sm = state.cc(queries.aggregate, "assessed", ["process"], fe)
        sla = state.cc(clearance.sla_breach_by_entity, fe.with_(entities=()))
        mine = sla[sla["entity"] == ent]
        st.dataframe(sm.assign(**{"fee yield": [fmt_ngn(v) for v in sm["value"]], "assessments": sm["rows"]}).drop(columns=["value", "rows"]), hide_index=True, width="stretch")
        if not mine.empty:
            st.markdown("**SLA performance for stages this agency owns**")
            st.dataframe(mine.rename(columns={"breach_rate": "SLA breach rate"}), hide_index=True, width="stretch", column_config={"SLA breach rate": st.column_config.ProgressColumn(format="%.1f%%", min_value=0, max_value=0.5, help=tooltips.tip("sla_breach_rate"))})
        mt = state.cc(queries.process_matrix, f, ent)
        if not mt.empty:
            tooltips.title("country_sensitivity", 5)
            pv = mt.pivot_table(index="process", columns="origin_country", values="value", aggfunc="sum").fillna(0) / 1e8
            fig = go.Figure(go.Heatmap(z=pv.values, x=pv.columns, y=pv.index, colorscale="Greens", hovertemplate="%{y} × %{x}: ₦%{z:,.1f}m<extra></extra>"))
            charts.show(fig, "proc_matrix", 300, legend=False)
    with t_led:
        tb = state.cc(statements.trial_balance, ent, f.end)
        st.markdown(f"**Trial balance** · debits {fmt_ngn(tb['debit_minor'].sum())} = credits {fmt_ngn(tb['credit_minor'].sum())} " + ("<span class='tie-ok'>✓ balanced</span>" if tb["debit_minor"].sum() == tb["credit_minor"].sum() else "<span class='tie-bad'>✗</span>"), unsafe_allow_html=True, help=tooltips.tip("ledger_trial_balance"))
        pick = st.dataframe(tb.assign(debit=[fmt_ngn(v, exact=True) for v in tb["debit_minor"]], credit=[fmt_ngn(v, exact=True) for v in tb["credit_minor"]])[["account_code", "name", "class", "debit", "credit"]],
                            hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row", key="tb_pick")
        if pick.selection.rows:
            acct = tb.iloc[pick.selection.rows[0]]["account_code"]
            st.markdown(f"##### Journal browser: account {acct}")
            trace_panel.render({"entities": [ent], "accounts": [acct], "start": None, "end": f.end, "kind": "balance", "sign": 1}, key="ledger_trace")
    with t_fund:
        fd = state.cc(queries.funding, fe)
        a, b = st.columns(2)
        with a:
            tooltips.title("partner_country", 5)
            if fd.empty:
                st.caption("No funding receipts in this period.")
            else:
                g = fd.groupby("source_country")["value"].sum().reset_index()
                fig = go.Figure(go.Bar(x=g["source_country"], y=g["value"] / 1e11, marker_color=logos.colour(ent), hovertemplate="%{x}: ₦%{y:,.3f}bn<extra></extra>"))
                fig.update_yaxes(title="₦bn")
                charts.show(fig, "fund_country", 280, legend=False)
                st.dataframe(fd.assign(amount=[fmt_ngn(v) for v in fd["value"]]).drop(columns=["value", "entity"]), hide_index=True, width="stretch")
        with b:
            tooltips.title("appropriation", 5)
            ex = state.cc(queries.aggregate, "expenses", ["week", "category"], fe)
            if not ex.empty:
                fig = go.Figure()
                for cat, g in ex.groupby("category"):
                    fig.add_trace(go.Bar(x=g["week"], y=g["value"] / 1e8, name=cat))
                fig.update_layout(barmode="stack")
                fig.update_yaxes(title="₦m per week")
                charts.show(fig, "exp_cats", 300)
    with t_score:
        s = state.cc(quality.score_for, ent)
        if s:
            c = st.columns(5)
            cards.kpi(c[0], "data_confidence_score", f"{s['score']:.0f}/100")
            cards.kpi(c[1], "score_completeness", fmt_pct(s["completeness"]))
            cards.kpi(c[2], "score_timeliness", fmt_pct(s["timeliness"]))
            cards.kpi(c[3], "score_consistency", fmt_pct(s["consistency"]))
            cards.kpi(c[4], "score_punctuality", fmt_pct(s["punctuality"]))
            st.caption(f"Status: {charts.rag_dot(s['rag'])} {s['rag']} · basis: {s.get('completeness_basis')}", unsafe_allow_html=True)
        al = state.cc(queries.alerts, f.with_(start=None), entities=(ent,), limit=50)
        tooltips.title("alert_board", 5)
        st.dataframe(al.drop(columns=["details_json", "window_start", "window_end"]), hide_index=True, width="stretch")

header.methodology_footer([("Statements", "Computed from the double-entry ledger by SQL (never stored); whole days use a daily rollup, partial days use journal lines; both are tested to be identical."),
                           ("Trace", "Each line expands to accounts, journal entries and source documents; totals at every level sum exactly to the level above."),
                           ("Funding by country", "Receipts tagged with the partner country (grants and concessional loans) or NG for domestic appropriation.")])
cards.disclaimer_footer()
