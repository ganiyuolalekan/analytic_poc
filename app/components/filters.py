"""Global filters (sidebar): persisted in session_state and mirrored to the URL so a view is shareable (Section 11.3)."""
from __future__ import annotations

from datetime import timedelta

import streamlit as st

from app.components import logos, state, tooltips
from nsw_sim import clock
from nsw_sim.analytics import queries
from nsw_sim.sim.reference import country_codes, entity_codes, ref

PARAMS = {"f_period": "period", "f_entities": "entities", "f_origin": "origin", "f_mode": "mode", "f_port": "port", "f_commodity": "commodity",
          "f_process": "process", "f_ccy": "ccy", "f_compare": "compare"}
LISTY = {"f_entities", "f_origin", "f_mode", "f_port", "f_commodity", "f_process"}


@st.cache_data(ttl=600, show_spinner=False)
def _options() -> dict:
    cfg = ref()
    return {"countries": {c: f"{c} · {cfg['countries'][c]['name']}" for c in country_codes()},
            "ports": [*cfg["ports"]["sea"], *cfg["ports"]["air"]], "commodities": list(cfg["commodity_groups"]),
            "processes": queries.distinct_values("process_code", "v_assessments") if queries.watermark() else []}


def _load_from_url() -> None:
    if st.session_state.get("_filters_loaded"):
        return
    qp = st.query_params
    for k, p in PARAMS.items():
        if p in qp:
            v = qp[p]
            st.session_state[k] = (v.split(",") if k in LISTY else (v == "1" if k == "f_compare" else v))
    st.session_state["_filters_loaded"] = True


def _mirror_to_url() -> None:
    ss, qp = st.session_state, st.query_params
    for k, p in PARAMS.items():
        v = ss.get(k)
        if v in (None, [], "", False) or (k == "f_period" and v == "Since 1 July") or (k == "f_ccy" and v == "NGN"):
            qp.pop(p, None)
        else:
            qp[p] = ",".join(v) if isinstance(v, list) else ("1" if v is True else str(v))


def sidebar() -> None:
    _load_from_url()
    opts = _options()
    st.sidebar.markdown("### Filters")
    periods = list(state.periods())
    st.sidebar.selectbox("Period", periods, index=periods.index("Since 1 July"), key="f_period", help=tooltips.tip("filter_period"))
    if st.session_state.get("f_period") == "Custom range":
        today = clock.wat(state.as_of()).date()
        st.sidebar.date_input("Custom range (WAT)", value=(clock.wat(clock.sim_start()).date(), today), key="f_custom")
    st.sidebar.toggle("Compare to previous period", key="f_compare", help=tooltips.tip("compare_previous_period"))
    st.sidebar.multiselect("Entities", entity_codes(), key="f_entities", help=tooltips.tip("filter_entities"), format_func=lambda c: f"{c} · {logos.name(c)}")
    st.sidebar.multiselect("Origin country", list(opts["countries"]), key="f_origin", help=tooltips.tip("filter_origin"), format_func=lambda c: opts["countries"][c])
    st.sidebar.multiselect("Mode", ["sea", "air"], key="f_mode", help=tooltips.tip("filter_mode"))
    st.sidebar.multiselect("Port / airport", opts["ports"], key="f_port", help=tooltips.tip("filter_port"))
    st.sidebar.multiselect("Commodity group", opts["commodities"], key="f_commodity", help=tooltips.tip("filter_commodity"))
    st.sidebar.multiselect("Process / fee type", opts["processes"], key="f_process", help=tooltips.tip("filter_process"))
    st.sidebar.radio("Currency view", ["NGN", "USD"], horizontal=True, key="f_ccy", help=tooltips.tip("currency_view"))
    _mirror_to_url()


def chips() -> None:
    """'Filters applied' chip row with a clear-all button."""
    ss = st.session_state
    parts = []
    for e in ss.get("f_entities", []):
        parts.append(logos.chip(e))
    for key, label in (("f_origin", "origin"), ("f_mode", "mode"), ("f_port", "port"), ("f_commodity", "commodity"), ("f_process", "process")):
        for v in ss.get(key, []):
            parts.append(f'<span class="chip">{label}: {v}</span>')
    if ss.get("f_period", "Since 1 July") != "Since 1 July":
        parts.append(f'<span class="chip">period: {ss["f_period"]}</span>')
    if ss.get("f_ccy", "NGN") != "NGN":
        parts.append('<span class="chip">USD view</span>')
    if parts:
        c1, c2 = st.columns([0.88, 0.12])
        c1.markdown("<span style='color:#55655d'>Filters applied:</span> " + " ".join(parts), unsafe_allow_html=True)
        if c2.button("Clear all", key="clear_filters"):
            for k in PARAMS:
                st.session_state.pop(k, None)
            st.rerun()


def ignored(*names: str) -> None:
    """State which global filters a page ignores and why (tooltip on a small caption)."""
    st.caption(f"Ignores: {', '.join(names)}.", help="This page's measure has no such dimension, so the filter cannot apply.")
