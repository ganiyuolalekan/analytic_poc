"""Session state, the as_of instant, cached data access, and the background service."""
from __future__ import annotations

import types
from datetime import timedelta

import streamlit as st

from nsw_sim import clock, db
from nsw_sim.analytics import queries
from nsw_sim.analytics.queries import Filters

ROLES = ["Analyst", "Supervisor", "Director"]


@st.cache_resource(show_spinner=False)
def service():
    """Start the simulation service once per process (guarded by a file lock: other processes become read-only followers)."""
    from nsw_sim.sim.service import get_service
    svc = get_service()
    svc.start()
    return svc


def role() -> str:
    return st.session_state.get("role", "Supervisor")


def presenter() -> bool:
    return bool(st.session_state.get("presenter", False))


def as_of() -> str:
    """The data time: the simulation watermark (facts only exist up to here)."""
    wm = queries.watermark()
    return wm or clock.iso(clock.utcnow())


def bucket() -> str:
    """Cache bucket: changes every 15 seconds so live pages refresh without recomputing on every rerun."""
    return clock.iso(int(clock.to_epoch(as_of()) // 15) * 15)


@st.cache_data(ttl=60, show_spinner=False, hash_funcs={types.FunctionType: lambda f: f"{f.__module__}.{f.__qualname__}"})
def call(fn, *args, _bucket: str = "", **kwargs):
    """Cached analytics call. ``fn`` is an analytics function; the 15 s bucket keeps live pages fresh."""
    return fn(*args, **kwargs)


def cc(fn, *args, **kwargs):
    """Shorthand: cached call bound to the current as_of."""
    return call(fn, *args, as_of=as_of(), _bucket=bucket(), **kwargs)


def writer():
    return db.connect()


def periods() -> dict[str, tuple[str | None, str | None]]:
    now = clock.to_dt(as_of())
    today = clock.wat(now).date()
    mid = lambda d: clock.iso(clock.wat_midnight_utc(d))  # noqa: E731
    wd = today.weekday()
    first_this_month = today.replace(day=1)
    last_month_end = first_this_month
    last_month_start = (first_this_month - timedelta(days=1)).replace(day=1)
    q_start_month = 3 * ((today.month - 1) // 3) + 1
    return {
        "Live / Today": (mid(today), None), "Yesterday": (mid(today - timedelta(days=1)), mid(today)), "Last 7 days": (mid(today - timedelta(days=6)), None),
        "This week": (mid(today - timedelta(days=wd)), None), "Month to date": (mid(first_this_month), None),
        "Last month": (mid(last_month_start), mid(last_month_end)), "Quarter to date": (mid(today.replace(month=q_start_month, day=1)), None),
        "Since 1 July": (clock.iso(clock.sim_start()), None), "Custom range": (None, None)}


def filters() -> Filters:
    """The global filters as a Filters object (period resolved to UTC; open end means 'to the data time')."""
    ss = st.session_state
    p = ss.get("f_period", "Since 1 July")
    start, end = periods().get(p, (clock.iso(clock.sim_start()), None))
    if p == "Custom range":
        rng = ss.get("f_custom")
        if rng and len(rng) == 2:
            start = clock.iso(clock.wat_midnight_utc(rng[0]))
            end = clock.iso(clock.wat_midnight_utc(rng[1] + timedelta(days=1)))
    return Filters(start=start, end=end or as_of(), entities=tuple(ss.get("f_entities", ())), origins=tuple(ss.get("f_origin", ())),
                   modes=tuple(ss.get("f_mode", ())), ports=tuple(ss.get("f_port", ())), commodities=tuple(ss.get("f_commodity", ())),
                   processes=tuple(ss.get("f_process", ())), currency_view=ss.get("f_ccy", "NGN"), severities=tuple(ss.get("f_severity", ())),
                   statuses=tuple(ss.get("f_status", ())))


def compare_on() -> bool:
    return bool(st.session_state.get("f_compare", False))


def goto_trace(ref: str) -> None:
    st.session_state["trace_query"] = ref
    st.switch_page("pages/04_trace.py")
