import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
import streamlit as st  # noqa: E402

from app.components import cards, charts, fmt, header, logos, qa, state  # noqa: E402
from nsw_sim import clock  # noqa: E402
from nsw_sim.analytics import queries  # noqa: E402
from nsw_sim.analytics.queries import Filters  # noqa: E402
from nsw_sim.sim.reference import entity_codes, ref  # noqa: E402

header.page_header("Home", "A quick way in. Choose an agency and a period, then ask the AI assistant a question in your own words or pick a suggestion. The cards and charts below follow your "
                   "choice, and the three sections in the sidebar go deeper.", chips=False, show_title=False)
ALL = "All agencies"
with st.container(key="home_hero"):
    st.markdown(f"<div class='hero-kicker'>Nigeria's Single Window · data as of {clock.fmt_wat(state.live_as_of(), '%d %b %Y')} · demo data</div><div class='hero-title'>What would you like to know?</div>"
                "<p class='hero-sub'>Ask the AI about the money moving through the Single Window: what is coming in, where it goes and what needs attention. Plain words, checked answers.</p>",
                unsafe_allow_html=True)
    c1, c2, _ = st.columns([0.42, 0.32, 0.26])
    agency = c1.selectbox("Agency", [ALL, *entity_codes()], key="home_agency", format_func=lambda c: c if c == ALL else f"{c} · {logos.name(c)}")
    period = c2.selectbox("Period", list(qa.HOME_PERIODS), key="home_period")
    start, end = state.periods()[period]
    f = Filters(start=start, end=end or state.as_of(), entities=() if agency == ALL else (agency,), currency_view=st.session_state.get("f_ccy", "NGN"))
    qa.panel("home", qa.home_starters(None if agency == ALL else agency, period), default=qa.DEFAULT_PROMPT,
             defaults={"period_label": f"{clock.fmt_wat(f.start, '%d %b %Y')} → {clock.fmt_wat(f.end, '%d %b %Y %H:%M')}", "filters_desc": f.describe()})


def money_over_time(metric: str) -> pd.DataFrame:
    """The metric by hour, day or week, whichever suits the length of the period, with a ``when`` column (WAT). Weekly points keep complete weeks only: a part week at either
    end would read as a ramp-up or a collapse."""
    days = (clock.to_dt(f.end) - clock.to_dt(f.start)).total_seconds() / 86400
    grain = "hour" if days <= 2 else "day" if days <= 31 else "week"
    d = state.cc(queries.aggregate, metric, [grain], f)
    d = d.assign(when=pd.to_datetime(d[grain] + (":00:00" if grain == "hour" else ""), utc=grain == "hour"))
    if grain == "hour":
        return d.assign(when=d["when"].dt.tz_convert("Africa/Lagos").dt.tz_localize(None))
    if grain == "week":
        first, last = (pd.Timestamp(clock.wat(t).replace(tzinfo=None)).normalize() for t in (f.start, f.end))
        return d[(d["when"] >= first) & (d["when"] + pd.Timedelta(days=7) <= last + pd.Timedelta(days=1))]
    return d


mix = state.cc(queries.entity_mix, f)
tot = mix[["paid", "settled", "outstanding"]].sum()
st.write("")
flow = st.container(key="home_flow")
for col, key, metric, label, note in zip(flow.columns(3, gap="medium"), ("flow_in", "flow_out", "flow_due"), ("paid", "settled", "outstanding"), ("Coming in", "Going out", "Still to come in"),
                                         ("paid in by traders and agents", "paid out to the agencies", "charged but not yet paid")):
    with col, st.container(key=key):
        ahead = metric == "outstanding" and tot[metric] < 0          # payments ahead of charges: show nothing owed rather than a negative that reads like a fault
        cards.kpi(st, metric, fmt.ngn(0 if ahead else tot[metric]), label=label)
        st.caption(f"nothing owed; payments are {fmt.ngn(-tot[metric])} ahead of charges" if ahead else note)
st.caption(f"{agency} · {period} · {clock.fmt_wat(f.start, '%d %b %Y')} to {clock.fmt_wat(f.end, '%d %b %Y')}")

st.write("")
charts_row = st.container(key="home_charts")
left, right = charts_row.columns([0.6, 0.4], gap="large")
with left, st.container(border=True):
    st.markdown("#### Money coming in and going out")
    paid, settled = money_over_time("paid"), money_over_time("settled")
    if paid.empty and settled.empty:
        st.caption("No activity for this choice.")
    else:
        charts.show(charts.money_in_out(paid, settled), "home_in_out", 300)
with right, st.container(border=True):
    if agency == ALL:
        st.markdown("#### Who collects what")
        d = mix[mix["paid"] > 0][["entity", "paid"]].sort_values("paid", ascending=False)
        div, unit = charts.naira_axis(d["paid"])
        fig = charts.entity_bar(d, "entity", "paid", div, unit, horizontal=True)
        fig.update_yaxes(autorange="reversed")
        fig.update_xaxes(title=unit or None)
    else:
        st.markdown(f"#### Where {agency}'s money comes from")
        d = state.cc(queries.country_collections, f, "paid").nlargest(6, "value")
        names = {c: v["name"] for c, v in ref()["countries"].items()}
        div, unit = charts.naira_axis(d["value"])
        fig = go.Figure(go.Bar(x=d["value"] / div, y=[names.get(c, c) for c in d["origin_country"]], orientation="h", marker_color=charts.GREEN, hovertemplate="%{y}: %{x:,.2f} " + unit + "<extra></extra>"))
        fig.update_yaxes(autorange="reversed")
        fig.update_xaxes(title=unit or None)
    charts.show(fig, "home_who", 300, legend=False)

st.write("")
st.markdown("#### Explore an agency")
agencies = st.container(key="home_agencies")
codes = [c for c in ("NCS", "NRS", "NPA", "NIMASA", "SON", "NAFDAC", "NAQS", "NESREA", "FAAN", "NSW") if c in entity_codes()]
for i in range(0, len(codes), 5):
    for col, code in zip(agencies.columns(5), codes[i:i + 5]):
        with col, st.container(border=True):
            st.markdown(f"{logos.img(code, 42)}<br><b>{code}</b><br><span style='color:#55655d;font-size:.85rem'>{logos.name(code)}</span>", unsafe_allow_html=True)
            if st.button("Open", key=f"open_{code}", width="stretch"):
                st.session_state["entity_sel"] = code
                st.switch_page("screens/03_entities.py")

st.write("")
st.markdown("#### Go deeper")
for col, (path, label, note) in zip(st.columns(3), [("screens/03_entities.py", "Entity Explorer", "agency by agency: what each one charges, collects and reports"),
                                                    ("screens/04_trace.py", "Trace Workbench", "follow any payment from the trader to the agency"),
                                                    ("screens/06_supervision.py", "Supervision", "alerts, reviews and who is acting on them")]):
    with col, st.container(border=True):
        if st.button(label, key=f"go_{path}", width="stretch", type="primary"):
            st.switch_page(path)
        st.caption(note)
cards.disclaimer_footer()
