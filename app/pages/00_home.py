import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from app.components import cards, charts, fmt, header, logos, qa, state  # noqa: E402
from nsw_sim import clock  # noqa: E402
from nsw_sim.analytics import queries  # noqa: E402
from nsw_sim.analytics.queries import Filters  # noqa: E402
from nsw_sim.sim.reference import entity_codes  # noqa: E402

header.page_header("What would you like to know?", "A quick way in. Choose an agency and a period, then ask the AI assistant a question in your own words or pick a suggestion. "
                   "The chart shows the money coming in and going out for your choice, and the other pages go deeper.", chips=False)
ALL = "All agencies"
c1, c2, _ = st.columns([0.3, 0.22, 0.48])
agency = c1.selectbox("Agency", [ALL, *entity_codes()], key="home_agency", format_func=lambda c: c if c == ALL else f"{c} · {logos.name(c)}")
period = c2.selectbox("Period", list(qa.HOME_PERIODS), key="home_period")
start, end = state.periods()[period]
f = Filters(start=start, end=end or state.as_of(), entities=() if agency == ALL else (agency,), currency_view=st.session_state.get("f_ccy", "NGN"))


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


cards_row = st.container(key="home_cards")          # the two cards sit side by side on a wide screen and stack on a narrow one (see the CSS in header.py)
left, right = cards_row.columns([0.55, 0.45], gap="large")
with left, st.container(border=True):
    st.markdown("#### Ask the AI")
    qa.panel("home", qa.home_starters(None if agency == ALL else agency, period), default=qa.DEFAULT_PROMPT,
             defaults={"period_label": f"{clock.fmt_wat(f.start, '%d %b %Y')} → {clock.fmt_wat(f.end, '%d %b %Y %H:%M')}", "filters_desc": f.describe()})
with right, st.container(border=True):
    st.markdown("#### Money coming in and going out")
    paid, settled = money_over_time("paid"), money_over_time("settled")
    if paid.empty and settled.empty:
        st.caption("No activity for this choice.")
    else:
        charts.show(charts.money_in_out(paid, settled), "home_in_out", 250)
    tot = state.cc(queries.entity_mix, f)[["paid", "settled", "outstanding"]].sum()
    a, b, c = st.columns(3)
    cards.kpi(a, "paid", fmt.ngn(tot["paid"]), label="Coming in")
    cards.kpi(b, "settled", fmt.ngn(tot["settled"]), label="Going out")
    cards.kpi(c, "outstanding", fmt.ngn(tot["outstanding"]), label="Still to come in")
    st.caption(f"{agency} · {period} · {clock.fmt_wat(f.start, '%d %b %Y')} to {clock.fmt_wat(f.end, '%d %b %Y')}")
st.markdown("#### Go deeper")
for col, (path, label) in zip(st.columns(5), [("pages/01_command_center.py", "Command Center"), ("pages/02_clearance.py", "Clearance Journey"), ("pages/03_entities.py", "Entity Explorer"),
                                              ("pages/05_reconciliation.py", "Reconciliation"), ("pages/06_supervision.py", "Supervision")]):
    if col.button(label, key=f"go_{path}", width="stretch"):
        st.switch_page(path)
cards.disclaimer_footer()
