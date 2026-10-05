import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
import streamlit as st  # noqa: E402

from app.components import cards, charts, header, logos, state, tooltips  # noqa: E402
from nsw_sim import clock, db  # noqa: E402
from nsw_sim.analytics import reconcile  # noqa: E402
from nsw_sim.money import fmt_ngn, fmt_pct  # noqa: E402
from nsw_sim.supervision import reviews  # noqa: E402

header.page_header("Reconciliation", "Trust: every fee is followed from assessment to payment to settlement to remittance. Gaps are classified as exceptions you can trace or raise for review; "
                   "leakage indicators show where assessments fall short of what the fee rules expect.", ("Currency (amounts shown in naira)",))
f = state.filters()
fw = state.cc(reconcile.four_way, f)

c = st.columns(4)
cards.money_kpi(c[0], "assessed", fw["assessed"]["amount"], None)
cards.money_kpi(c[1], "paid", fw["paid"]["amount"], None)
cards.money_kpi(c[2], "settled", fw["settled"]["amount"], None, label="Settled (gross)")
cards.money_kpi(c[3], "remitted", fw["remitted"]["amount"], None)
tooltips.title("four_way_match")
stages = [("Assessed", fw["assessed"]["amount"], fw["assessed"]["count"]), ("Paid", fw["paid"]["amount"], fw["paid"]["count"]),
          ("Settled to agencies", fw["settled"]["amount"], fw["settled"]["count"]), ("Remitted to Treasury", fw["remitted"]["amount"], None)]
fig = go.Figure(go.Funnel(y=[s[0] for s in stages], x=[s[1] / 1e11 for s in stages], textinfo="text", text=[f"{fmt_ngn(s[1])}" + (f" · {s[2]:,} items" if s[2] else "") for s in stages],
                          marker={"color": [charts.BLUE, charts.GREEN, "#2E7D32", charts.AMBER]}, hovertemplate="%{y}: ₦%{x:,.2f}bn<extra></extra>"))
charts.show(fig, "funnel", 300, legend=False)
st.caption(f"In transit {fmt_ngn(fw['in_transit']['amount'])} · unpaid or not yet paid {fmt_ngn(fw['unpaid']['amount'])} · settled but awaiting remittance {fmt_ngn(fw['settled_not_remitted']['amount'])}. "
           "Timing differences are shown as in transit, not as errors.")

a, b = st.columns(2)
with a:
    tooltips.title("variance_bridge")
    br = state.cc(reconcile.variance_bridge, f)
    fig = go.Figure(go.Waterfall(x=[x["label"] for x in br], y=[x["amount"] / 1e11 for x in br], measure=["absolute" if x["kind"] == "total" else "relative" for x in br],
                                 connector={"line": {"color": "#9aa8a1"}}, increasing={"marker": {"color": charts.GREEN}}, decreasing={"marker": {"color": charts.AMBER}},
                                 totals={"marker": {"color": charts.BLUE}}, hovertemplate="%{x}<br>₦%{y:,.3f}bn<extra></extra>"))
    fig.update_xaxes(tickangle=-35)
    charts.show(fig, "bridge", 380, legend=False)
with b:
    tooltips.title("leakage_indicator")
    hm = state.cc(reconcile.leakage_heatmap, f)
    if not hm.empty:
        pv = hm.pivot_table(index="commodity_group", columns="origin_country", values="shortfall_pct").fillna(0) * 100
        fig = go.Figure(go.Heatmap(z=pv.values, x=pv.columns, y=pv.index, colorscale=[[0, "#cfe8d8"], [0.5, "#fff3d6"], [1, "#B03A2E"]], zmid=0, zmin=-4, zmax=14,
                                   hovertemplate="%{y} × %{x}<br>shortfall %{z:.1f}%<extra></extra>", colorbar_title="% below expected"))
        charts.show(fig, "heatmap", 380, legend=False)
        worst = hm.iloc[0]
        st.caption(f"Largest shortfall: {worst['commodity_group']} from {worst['origin_country']} at {worst['shortfall_pct'] * 100:.1f}% ({worst['n']} assessments, {fmt_ngn(worst['shortfall_minor'])}). Alert rule R-FEE-01 fires above 8% over three days.")

c1, c2, c3 = st.columns(3)
with c1:
    tooltips.title("ageing", 5)
    ag = state.cc(reconcile.unpaid_ageing, f)
    st.dataframe(ag.assign(amount=[fmt_ngn(v) for v in ag["amount_minor"]]).drop(columns=["amount_minor"]), hide_index=True, width="stretch")
with c2:
    tooltips.title("duplicate_payment", 5)
    dp = state.cc(reconcile.duplicate_payments, f)
    st.markdown(f"{len(dp)} duplicates · {fmt_ngn(dp['amount_ngn_minor'].sum()) if not dp.empty else '₦0'}")
    st.dataframe(dp.assign(amount=[fmt_ngn(v) for v in dp["amount_ngn_minor"]]).drop(columns=["amount_ngn_minor"]).head(15), hide_index=True, width="stretch")
with c3:
    tooltips.title("in_transit", 5)
    it = state.cc(reconcile.in_transit_by_bank)
    st.dataframe(it.assign(value=[fmt_ngn(v) for v in it["in_transit_minor"]]).drop(columns=["in_transit_minor"]), hide_index=True, width="stretch")

st.divider()
tooltips.title("exceptions_table")
summ = state.cc(reconcile.exception_summary, f)
if not summ.empty:
    st.dataframe(summ.assign(amount=[fmt_ngn(v) for v in summ["amount_minor"]]).drop(columns=["amount_minor"]), hide_index=True, width="stretch")
cls = st.multiselect("Show classes", list(reconcile.CLASSES), default=["unpaid", "settled_late", "duplicate", "orphan_payment", "mismatch"], key="rec_cls")
ex = state.cc(reconcile.exceptions, f, limit_per_class=200)
ex = ex[ex["cls"].isin(cls)] if not ex.empty else ex
if ex.empty:
    st.success("No exceptions in this selection.")
else:
    view = pd.DataFrame({"class": ex["cls"], "entity": ex["entity"], "reference": ex["nsw_ref"], "amount": [fmt_ngn(v) for v in ex["amount_minor"]], "age (days)": ex["age_days"], "origin": ex["origin_country"], "commodity": ex["commodity_group"]})
    sel = st.dataframe(view.head(400), hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row", key="exc_sel",
                       column_config={"age (days)": st.column_config.NumberColumn(format="%.1f"), "class": st.column_config.TextColumn(help=tooltips.tip("variance"))})
    if sel.selection.rows:
        r = ex.iloc[sel.selection.rows[0]]
        c1, c2 = st.columns(2)
        if r["nsw_ref"] and str(r["nsw_ref"]).startswith("NSW-") and c1.button("Open trace", key="exc_trace"):
            state.goto_trace(r["nsw_ref"])
        if c2.button("Raise review", key="exc_review"):
            w = db.connect()
            try:
                reviews.act(w, "exception", r["subject"], state.role(), "raise_review", f"{r['cls']} · {r['entity']} · {fmt_ngn(r['amount_minor'])}")
                st.success(f"Review raised on {r['subject']} by the {state.role()} role. It appears in the Supervision review queue.")
            finally:
                w.close()

header.methodology_footer([("Funnel", "For the assessments raised in the period: how much was paid, settled and remitted as of the data time; remitted applies each agency's remittance share to its settled amounts once the period's remittance is paid."),
                           ("Exceptions", "Unpaid = assessed over 72 hours ago with no payment; settled late = paid over 48 hours ago and not settled; duplicate/orphan = payments with no assessment and not yet refunded; mismatch = more than 1% from the fee-rule expectation."),
                           ("Leakage", "(expected − assessed) ÷ expected per commodity and origin pair with at least 15 assessments.")])
cards.disclaimer_footer()
