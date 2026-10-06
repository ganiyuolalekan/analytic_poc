import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
import streamlit as st  # noqa: E402

from app.components import cards, charts, feed, fmt, header, logos, state, tooltips  # noqa: E402
from nsw_sim import clock  # noqa: E402
from nsw_sim.analytics import quality, queries, reports  # noqa: E402
from nsw_sim.money import fmt_days, fmt_pct  # noqa: E402

ISO3 = {"CN": "CHN", "IN": "IND", "NL": "NLD", "US": "USA", "GB": "GBR", "DE": "DEU", "AE": "ARE", "TR": "TUR", "BR": "BRA", "ZA": "ZAF", "BE": "BEL", "FR": "FRA",
        "IT": "ITA", "ES": "ESP", "JP": "JPN", "KR": "KOR", "GH": "GHA", "BJ": "BEN", "CI": "CIV", "SA": "SAU"}

header.page_header("Command Center", "The live view of the simulated NSW channel: headline money flows with change versus the previous period, a live event feed, "
                   "the last hour of collections by agency, agency tiles with status, where the money comes from by origin country, and active incidents.",
                   ("Mode", "Port", "Commodity", "Process", "Origin (live panels only)"))
f = state.filters()
as_of = state.as_of()
live_t = state.live_as_of()
prev = queries.previous_period(f) if state.compare_on() else None

# ---------------------------------------------------------------- KPI strip
k = state.cc(reports.kpis, f, compare=prev)
cur, pv = k["current"], k["previous"] or {}
r1 = st.columns(5)
for c, (key, _label) in zip(r1, [("assessed", None), ("paid", None), ("settled", None), ("remitted", None), ("outstanding", None)]):
    cards.money_kpi(c, key, cur[key], pv.get(key))
r2 = st.columns(5)
cards.kpi(r2[0], "dwell_time", fmt_days(cur["median_dwell_days"]), cards.delta_str(cur["median_dwell_days"], pv.get("median_dwell_days")), delta_color="inverse", label="Median dwell (days)")
cards.kpi(r2[1], "sla_breach_rate", fmt_pct(cur["sla_breach_rate"]), cards.delta_str(cur["sla_breach_rate"], pv.get("sla_breach_rate")), delta_color="inverse")
cards.kpi(r2[2], "collection_cost_per_100", f"{cur['cost_per_100']:.2f}" if cur["cost_per_100"] is not None else "–", label="Cost of collection per 100 collected")
cards.kpi(r2[3], "kpi_open_alerts", str(cur["open_high_alerts"]), None)
cards.kpi(r2[4], "data_confidence_score", f"{cur['confidence_score']:.0f}/100" if cur["confidence_score"] is not None else "–")
st.caption("Amounts are NSW-channel collections only (synthetic). In transit: " + fmt.ngn(cur["in_transit"]) + " · " + tooltips.tip("in_transit"))

# ---------------------------------------------------------------- live feed + live money
left, right = st.columns([0.58, 0.42])
with left:
    tooltips.title("live_feed")
    feed.live_feed(height=420)
with right:
    tooltips.title("paid", 4)
    feed.live_counters()
    m = queries.minute_series(60, live_t, "paid")
    if not m.empty:
        m = m.assign(entity=m["entity_id"])
        fig = charts.entity_area(m, "minute", "value", "entity", 1e8, "m")
        charts.show(fig, "live_money", 250)
    rt = queries.todays_running_total(live_t)
    c1, c2 = st.columns(2)
    cards.kpi(c1, "paid", fmt.ngn(rt["today"]), cards.delta_str(rt["today"], rt["yesterday_same_time"]), label="Today so far")
    cards.kpi(c2, "paid", fmt.ngn(rt["yesterday_same_time"]), label="Yesterday, same time")

# ---------------------------------------------------------------- entity tiles
tooltips.title("entity_tile")
mix = state.cc(queries.entity_mix, f)
sc = state.cc(quality.scorecard).set_index("entity")
spark = state.cc(queries.aggregate, "paid", ["entity", "day"], f.with_(start=clock.iso(clock.to_dt(as_of).timestamp() - 14 * 86400), end=None, entities=()))
ents = [e for e in ["NCS", "NRS", "NPA", "NIMASA", "SON", "NAFDAC", "NAQS", "NESREA", "FAAN", "NSW"] if not f.entities or e in f.entities]
for i in range(0, len(ents), 5):
    cols = st.columns(5)
    for c, e in zip(cols, ents[i:i + 5]):
        row = mix[mix["entity"] == e]
        assessed = float(row["assessed"].iloc[0]) if not row.empty else 0
        rag = sc.at[e, "rag"] if e in sc.index else "grey"
        with c:
            with st.container(border=True):
                st.markdown(f"{logos.img(e, 36)} **{e}** {charts.rag_dot(rag)}", unsafe_allow_html=True)
                st.markdown(f"<span style='font-size:1.25em;font-weight:700'>{fmt.ngn(assessed)}</span>", unsafe_allow_html=True)
                s = spark[spark["entity"] == e].sort_values("day")
                if len(s) > 2:
                    st.plotly_chart(charts.sparkline(list(s["value"]), logos.colour(e)), width="stretch", key=f"sp_{e}", config={"displayModeBar": False})
                if st.button("Open", key=f"open_{e}", width="stretch"):
                    st.session_state["entity_sel"] = e
                    st.switch_page("pages/03_entities.py")

# ---------------------------------------------------------------- origin map + top 10
c1, c2 = st.columns([0.55, 0.45])
by = c2.selectbox("Break down by", ["none", "entity", "process"], key="cc_by")
metric = c2.radio("Measure", ["assessed", "paid"], horizontal=True, key="cc_metric")
with c1:
    tooltips.title("country_map")
    cdf = state.cc(queries.country_collections, f, metric)
    fig = go.Figure(go.Choropleth(locations=[ISO3.get(c, c) for c in cdf["origin_country"]], z=cdf["value"] / 1e11, colorscale="Greens", marker_line_color="#fff",
                                  text=cdf["origin_country"], hovertemplate="%{text}: %{z:,.2f}bn<extra></extra>", colorbar_title="bn"))
    fig.update_geos(showframe=False, showcoastlines=False, projection_type="natural earth", lataxis_range=[-35, 65], lonaxis_range=[-30, 150])
    charts.show(fig, "origin_map", 330, legend=False)
with c2:
    top = state.cc(queries.aggregate, metric, ["origin_country"] + ([] if by == "none" else [by]), f)
    totals = top.groupby("origin_country")["value"].sum().sort_values(ascending=False).head(10).index
    top = top[top["origin_country"].isin(totals)]
    if by == "none":
        fig = go.Figure(go.Bar(x=top["value"] / 1e11, y=top["origin_country"], orientation="h", marker_color=charts.GREEN, hovertemplate="%{y}: %{x:,.2f}bn<extra></extra>"))
    else:
        fig = go.Figure()
        for g, d in top.groupby(by):
            fig.add_trace(go.Bar(x=d["value"] / 1e11, y=d["origin_country"], orientation="h", name=g, marker_color=logos.colour(g) if by == "entity" else None))
        fig.update_layout(barmode="stack")
    fig.update_yaxes(autorange="reversed")
    st.markdown("**Top 10 origins**")
    charts.show(fig, "top_origins", 330, legend=by != "none")

# ---------------------------------------------------------------- situation board
tooltips.title("situation_board")
sb = state.cc(queries.situation_board)
c1, c2, c3 = st.columns(3)
with c1:
    st.markdown("**Scanner status by port**")
    svc = state.service()
    eng = getattr(svc, "engine", None)
    ports = ["NGAPP", "NGTIN", "NGLEK", "NGONN", "NGPHC", "LOS"]
    rows = []
    for p in ports:
        if eng:
            off = eng.cond.scanners_offline(p, eng.t)
            tot = eng.cond.scanners.get(p, 2)
        else:
            off, tot = 0, 2
        rows.append({"port": p, "working": f"{tot - off} of {tot}", "status": "▲ reduced" if off else "● normal"})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    ex = sb["exam_waits"]
    if not ex.empty:
        st.caption("Planned exam wait (h, last 12 h): " + ", ".join(f"{r.port} {r.avg_planned_wait_h}" for r in ex.itertuples()))
with c2:
    st.markdown("**Permit queues (planned approval time, last 24 h)**")
    q = sb["permit_queues"]
    st.dataframe(q.rename(columns={"avg_planned_h": "avg hours", "n": "permits"}), hide_index=True, width="stretch")
with c3:
    st.markdown("**Settlement lag per bank (last 3 days)**")
    st.dataframe(sb["settlement_lag"].rename(columns={"avg_lag_h": "avg lag (h)"}), hide_index=True, width="stretch")
if not sb["incidents"].empty:
    st.warning("Active incidents: " + "; ".join(f"{r.label or r.kind}" for r in sb["incidents"].itertuples()))

header.methodology_footer([("KPIs", "Each KPI is a SQL aggregate over facts at or before the data time; deltas compare with the equal-length previous period."),
                           ("Live panels", "Read from one-minute rollups written by the engine as money is confirmed."),
                           ("Entity tiles", "Assessed in the selected period; status from the data confidence scorecard (green 90+, amber 75-90, red below 75)."),
                           ("Map", "Assessed or paid amounts grouped by the origin country tag on each assessment.")])
cards.disclaimer_footer()
