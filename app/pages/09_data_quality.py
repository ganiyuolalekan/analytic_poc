import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
import streamlit as st  # noqa: E402

from app.components import cards, charts, header, logos, state, tooltips  # noqa: E402
from nsw_sim.analytics import quality  # noqa: E402
from nsw_sim.money import fmt_pct  # noqa: E402

header.page_header("Data Quality & Onboarding", "How complete, timely and consistent each agency's data is, how far onboarding has progressed (the share of activity arriving digitally), and where "
                   "gaps appeared. The FAAN timestamp gap at the end of September is visible here.", ("Origin", "Mode", "Port", "Commodity", "Process", "Currency"))
f = state.filters()
sc = state.cc(quality.scorecard)
tooltips.title("data_confidence_score")
st.dataframe(pd.DataFrame({"logo": [logos.uri(e, 32) for e in sc["entity"]], "entity": sc["entity"], "score": sc["score"], "status": sc["rag"].map(lambda r: f"{charts.GLYPH[r]} {r}"),
                           "completeness": sc["completeness"], "timeliness": sc["timeliness"], "consistency": sc["consistency"], "punctuality": sc["punctuality"], "stage records (7 d)": sc["events"],
                           "basis": sc["completeness_basis"]}), hide_index=True, width="stretch", column_config={
    "logo": st.column_config.ImageColumn("", width="small"), "score": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f", help=tooltips.tip("data_confidence_score")),
    "completeness": st.column_config.ProgressColumn(min_value=0, max_value=1, format="%.0f%%", help=tooltips.tip("score_completeness")),
    "timeliness": st.column_config.ProgressColumn(min_value=0, max_value=1, format="%.0f%%", help=tooltips.tip("score_timeliness")),
    "consistency": st.column_config.ProgressColumn(min_value=0, max_value=1, format="%.0f%%", help=tooltips.tip("score_consistency")),
    "punctuality": st.column_config.ProgressColumn(min_value=0, max_value=1, format="%.0f%%", help=tooltips.tip("score_punctuality"))})
a, b = st.columns(2)
tr = state.cc(quality.completeness_trend, days=95)
with a:
    tooltips.title("score_completeness", 5)
    ents = st.multiselect("Agencies", sorted(tr["entity"].unique()), default=["FAAN", "NCS", "NAFDAC"], key="dq_ents")
    fig = go.Figure()
    for e, g in tr[tr["entity"].isin(ents)].groupby("entity"):
        fig.add_trace(go.Scatter(x=g["day"], y=g["completeness"] * 100, name=e, mode="lines", line=dict(color=logos.colour(e)), hovertemplate=f"{e} %{{x}}<br>completeness %{{y:.1f}}%<extra></extra>"))
    fig.add_hline(y=90, line_dash="dash", line_color=charts.RED, annotation_text="R-DQ-01 threshold 90%")
    fig.update_yaxes(title="% of records complete", range=[40, 101])
    charts.show(fig, "dq_trend", 340)
with b:
    tooltips.title("onboarding_score", 5)
    fig = go.Figure()
    for e, g in tr.groupby("entity"):
        if e == "CBN":
            continue
        g = g.assign(week=pd.to_datetime(g["day"]).dt.to_period("W").dt.start_time).groupby("week")["digital_share"].mean().reset_index()
        fig.add_trace(go.Scatter(x=g["week"], y=g["digital_share"] * 100, name=e, mode="lines", line=dict(color=logos.colour(e))))
    fig.add_hline(y=80, line_dash="dash", line_color=charts.AMBER, annotation_text="R-ONB-01 threshold 80%")
    fig.update_yaxes(title="% captured digitally", range=[60, 101])
    charts.show(fig, "onb_trend", 340)
ent = st.selectbox("Field-level completeness for", sorted(sc["entity"]), key="dq_field_ent")
fc = state.cc(quality.field_completeness, ent, days=30)
if not fc.empty:
    fc = fc.assign(share=fc["present"] / fc["total"])
    st.dataframe(fc, hide_index=True, width="stretch", column_config={"share": st.column_config.ProgressColumn("completeness", min_value=0, max_value=1, format="%.1f%%")})
ld = state.cc(quality.late_data, days=14)
if not ld.empty:
    st.markdown("**Records captured manually (late-arriving / not yet onboarded), last 14 days**")
    pv = ld.pivot_table(index="day", columns="entity", values="manual", aggfunc="sum").fillna(0)
    st.bar_chart(pv)
header.methodology_footer([("Score", "40% completeness + 25% timeliness + 20% consistency + 15% remittance punctuality over the last 7 days; weights are illustrative and configurable."),
                           ("Completeness", "Share of an agency's stage records that carry their timestamps and fields; agencies that supply only fee data score 100% on this component."),
                           ("Onboarding", "Share of stage records captured digitally rather than manually; improving over time in the simulation.")])
cards.disclaimer_footer()
