import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
import streamlit as st  # noqa: E402

from app.components import cards, charts, header, state, tooltips  # noqa: E402
from nsw_sim.analytics import clearance, forecast  # noqa: E402
from nsw_sim.config import settings  # noqa: E402
from nsw_sim.money import fmt_days, fmt_ngn, fmt_pct  # noqa: E402

header.page_header("Clearance Journey", "Speed: where time is lost between a vessel or flight arriving and the cargo leaving the gate. Digital stages are controlled by the NSW platform, "
                   "physical stages (scanners, terminals, trucks) are not. The target tracker shows progress towards the end-2026 goal.", ("Entities", "Process", "Currency"))
f = state.filters()
prev = None
B = settings()["benchmarks"]

# ---- headline
dw = state.cc(clearance.dwell_table, f)
c = st.columns(5)
if not dw.empty and dw["dwell_n"].iloc[0]:
    r = dw.iloc[0]
    cards.kpi(c[0], "dwell_time", fmt_days(r["dwell_p50"]), label="Median dwell (days)")
    cards.kpi(c[1], "p90", fmt_days(r["dwell_p90"]), label="Dwell p90 (days)")
    cards.kpi(c[2], "clearance_time", fmt_days(r["clearance_p50"]), label="Median clearance time (days)")
    cards.kpi(c[3], "release_to_exit", fmt_days(r["exit_p50"]), label="Median release-to-exit (days)")
    cards.kpi(c[4], "digital_vs_physical", fmt_pct(state.cc(clearance.overall_digital_share, f)), label="Digital share of time")
else:
    st.info("No completed consignments in this period yet.")

# ---- stage waterfall + digital/physical
a, b = st.columns(2)
with a:
    tooltips.title("stage_waterfall")
    st_ = state.cc(clearance.stage_summary, f)
    st_ = st_[st_["stage"] != "S01"]
    if not st_.empty:
        fig = go.Figure()
        for typ, col, nm in (("D", charts.BLUE, "Digital (NSW-controlled)"), ("P", charts.AMBER, "Physical (not NSW-controlled)")):
            d = st_[st_["type"] == typ]
            fig.add_trace(go.Bar(x=d["name"], y=d["median_total_h"], name=nm, marker_color=col, customdata=d[["median_wait_h", "median_dur_h", "n"]],
                                 hovertemplate="%{x}<br>median total %{y:.1f} h<br>wait %{customdata[0]:.1f} h · processing %{customdata[1]:.1f} h<br>n=%{customdata[2]:,}<extra></extra>"))
        fig.update_yaxes(title="hours (median per stage)")
        charts.show(fig, "waterfall", 340)
with b:
    tooltips.title("digital_vs_physical")
    dp = state.cc(clearance.digital_physical, f)
    if not dp.empty:
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=dp["period"], y=dp["digital_share"] * 100, name="Digital", stackgroup="s", line=dict(color=charts.BLUE), hovertemplate="%{x}<br>digital %{y:.1f}%<extra></extra>"))
        fig.add_trace(go.Scatter(x=dp["period"], y=dp["physical_share"] * 100, name="Physical", stackgroup="s", line=dict(color=charts.AMBER), hovertemplate="%{x}<br>physical %{y:.1f}%<extra></extra>"))
        fig.update_yaxes(title="% of total time", range=[0, 100])
        charts.show(fig, "dig_phys", 340)

# ---- distributions
tooltips.title("p50")
by = st.selectbox("Break down by", ["none", "port", "mode", "commodity_group", "origin_country", "risk_lane"], key="clr_by")
tbl = state.cc(clearance.dwell_table, f, None if by == "none" else by)
if not tbl.empty:
    show = pd.DataFrame({"group": tbl["group"], "n": tbl["dwell_n"], "dwell p50 (d)": tbl["dwell_p50"], "p75": tbl["dwell_p75"], "p90": tbl["dwell_p90"], "p95": tbl["dwell_p95"],
                         "clearance p50 (d)": tbl["clearance_p50"], "release-to-exit p50 (d)": tbl["exit_p50"], "share over 7 d": tbl["share_over_target"]})
    st.dataframe(show, hide_index=True, width="stretch", column_config={
        "dwell p50 (d)": st.column_config.NumberColumn(help=tooltips.tip("dwell_time"), format="%.1f"), "p75": st.column_config.NumberColumn(format="%.1f"),
        "p90": st.column_config.NumberColumn(help=tooltips.tip("p90"), format="%.1f"), "p95": st.column_config.NumberColumn(format="%.1f"),
        "clearance p50 (d)": st.column_config.NumberColumn(help=tooltips.tip("clearance_time"), format="%.1f"),
        "release-to-exit p50 (d)": st.column_config.NumberColumn(help=tooltips.tip("release_to_exit"), format="%.1f"),
        "share over 7 d": st.column_config.ProgressColumn(help="Share of consignments whose dwell exceeded the 7-day target.", format="%.0f%%", min_value=0, max_value=1),
        "n": st.column_config.NumberColumn(format="%d")})

# ---- target tracker
st.divider()
tooltips.title("target_tracker")
wk = state.cc(clearance.weekly_dwell, f.with_(start=None))
proj = forecast.project_to_target(wk.iloc[:-1] if len(wk) > 1 else wk)
c1, c2 = st.columns([0.62, 0.38])
with c1:
    fig = go.Figure()
    lo, hi = B["baseline_days"]
    fig.add_hrect(y0=lo, y1=hi, fillcolor="rgba(176,58,46,0.10)", line_width=0, annotation_text="Published baseline 18-21 days", annotation_position="top left")
    fig.add_hline(y=B["target_days"], line_dash="dash", line_color=charts.GREEN, annotation_text="Target: under 7 days by end 2026")
    for name, v in {"Global ~4": 4, "Benin ~4": 4, "Rwanda ~1.5": 1.5}.items():
        fig.add_hline(y=v, line_dash="dot", line_color="#9aa8a1", annotation_text=name, annotation_position="bottom right")
    if not wk.empty:
        fig.add_trace(go.Scatter(x=wk["week"], y=wk["median_days"], mode="lines+markers", name="Weekly median dwell", line=dict(color=charts.BLUE, width=3), hovertemplate="week of %{x}<br>%{y:.1f} days<extra></extra>"))
    s = proj.get("series")
    if s is not None and not s.empty:
        pr = s[s["projected"]]
        fig.add_trace(go.Scatter(x=pd.concat([s["week"], s["week"][::-1]]), y=pd.concat([s["hi"], s["lo"][::-1]]), fill="toself", fillcolor="rgba(47,93,155,0.10)", line=dict(width=0), name="95% band", hoverinfo="skip"))
        fig.add_trace(go.Scatter(x=pr["week"], y=pr["fit"], mode="lines", name="Trend projection", line=dict(color=charts.AMBER, dash="dash")))
    fig.update_yaxes(title="days", rangemode="tozero")
    charts.show(fig, "target_tracker", 380)
with c2:
    cur = float(wk["median_days"].iloc[-2]) if len(wk) > 1 else None
    if cur:
        gauge = go.Figure(go.Indicator(mode="gauge+number", value=cur, number={"suffix": " d"}, gauge={"axis": {"range": [0, 21]}, "bar": {"color": charts.BLUE},
                                       "steps": [{"range": [0, 7], "color": "#cfe8d8"}, {"range": [7, 18], "color": "#fbeacc"}, {"range": [18, 21], "color": "#f2cfcb"}],
                                       "threshold": {"line": {"color": charts.GREEN, "width": 4}, "value": 7}}, title={"text": "Latest weekly median dwell"}))
        charts.show(gauge, "gauge", 250, legend=False)
    if proj.get("projected_date"):
        st.success(f"Projected to reach under {B['target_days']} days on **{proj['projected_date']}** (95% band {proj.get('projected_early')} to {proj.get('projected_late')}). "
                   f"Target date {B['target_date']}: **{proj['status']}**.")
    else:
        st.warning(f"Status: {proj.get('status')}. On the current trend the target is not reached within the projection horizon.")
    with st.popover("ⓘ Projection method and limits"):
        st.markdown(tooltips.tip("projection_method", "long"))
        st.caption(tooltips.tip("peer_benchmarks"))
        st.markdown("Peers (published figures, verify before quoting): Ghana 5-7 days, Benin ≈4, Global ≈4, Rwanda ≈1.5; secretariat aspiration 24-48 hours.")

# ---- bottlenecks
st.divider()
tooltips.title("bottleneck_finder")
bn = state.cc(clearance.bottlenecks, f)
if not bn.empty:
    st.dataframe(pd.DataFrame({"stage": bn["name"], "owner": bn["owner"], "type": bn["type"].map({"D": "Digital", "P": "Physical"}), "mean hours per consignment": bn["mean_h_per_consignment"],
                               "share of total delay": bn["share_of_total"], "controllable by NSW?": bn["controllable_by_nsw"].map({True: "Yes (digital)", False: "No (physical)"})}),
                 hide_index=True, width="stretch", column_config={
                     "share of total delay": st.column_config.ProgressColumn(format="%.0f%%", min_value=0, max_value=1, help=tooltips.tip("bottleneck_finder")),
                     "mean hours per consignment": st.column_config.NumberColumn(format="%.1f", help=tooltips.tip("stage_wait"))})
hw = state.cc(clearance.handoff_waits, f)
if not hw.empty:
    with st.expander("Hand-off waits between agencies"):
        st.caption(tooltips.tip("handoff_wait"))
        st.dataframe(hw.rename(columns={"median_handoff_wait_h": "median wait (h)"}), hide_index=True, width="stretch")

# ---- cost of delay
st.divider()
tooltips.title("cost_of_delay")
a1, a2, a3, a4 = st.columns(4)
dem = a1.number_input("Demurrage ₦ per TEU-day", 0, 500_000, 28_000, 1000, key="cod_dem")
sto = a2.number_input("Storage ₦ per tonne-day", 0, 100_000, 4_500, 500, key="cod_sto")
car = a3.number_input("Inventory carrying cost % a year", 0.0, 60.0, 18.0, 1.0, key="cod_car") / 100
ref_d = a4.number_input("Reference dwell (days)", 1.0, 21.0, 7.0, 0.5, key="cod_ref")
cod = state.cc(clearance.cost_of_delay, f, demurrage_per_teu_day=dem, storage_per_tonne_day=sto, carrying_pct_pa=car, reference_days=ref_d)
c = st.columns(4)
cards.kpi(c[0], "cost_of_delay", fmt_ngn(cod["total_ngn"] * 100), label="Illustrative cost of excess dwell")
cards.kpi(c[1], "cost_of_delay", fmt_ngn(cod["demurrage_ngn"] * 100), label="of which demurrage")
cards.kpi(c[2], "cost_of_delay", fmt_ngn(cod["storage_ngn"] * 100), label="of which storage")
cards.kpi(c[3], "cost_of_delay", fmt_ngn(cod["carrying_ngn"] * 100), label="of which carrying cost")
st.caption(f"Assumptions are illustrative and editable; {cod.get('n', 0):,} completed consignments, mean excess {cod.get('mean_excess_days', 0):.1f} days over {ref_d:g} days.")

# ---- drill to consignments
st.divider()
st.markdown("#### Consignments behind the numbers")
done = state.cc(clearance.completed, f)
if not done.empty:
    pick = st.selectbox("Show the slowest consignments for", ["all in period"] + sorted(done["port"].unique()), key="clr_port")
    d = done if pick == "all in period" else done[done["port"] == pick]
    d = d.sort_values("dwell_h", ascending=False).head(200)
    view = pd.DataFrame({"reference": d["nsw_ref"], "port": d["port"], "origin": d["origin_country"], "commodity": d["commodity_group"], "lane": d["risk_lane"], "dwell (d)": d["dwell_h"] / 24})
    sel = st.dataframe(view, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row", key="clr_cons",
                       column_config={"dwell (d)": st.column_config.NumberColumn(format="%.1f", help=tooltips.tip("dwell_time"))})
    if sel.selection.rows and st.button("Open in Trace Workbench", key="clr_trace"):
        state.goto_trace(view.iloc[sel.selection.rows[0]]["reference"])

header.methodology_footer([("Dwell", "gate_out_at minus arrived_at per consignment that left in the period; medians and percentiles are computed in pandas from those rows."),
                           ("Stages", "Each stage has a hand-off wait and a processing time; digital stages are NSW-controlled, physical stages are not."),
                           ("Projection", "Least-squares line through the last eight complete weekly medians; 95% band from residual error; not a forecast of incidents."),
                           ("Cost of delay", "Excess days beyond the reference times demurrage, storage and carrying-cost assumptions you can edit above (illustrative).")])
cards.disclaimer_footer()
