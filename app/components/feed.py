"""Live feed fragment (st.fragment run_every=2s): pre-fills the last 50 events then streams new ones; pause/resume; links to Trace."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from app.components import logos, state, tooltips
from nsw_sim import clock
from nsw_sim.analytics import queries

ICON = {"nsw.payment": "₦", "nsw.settlement": "⇄", "nsw.remittance": "↦", "nsw.exam": "◎", "nsw.release": "✓", "nsw.gate": "→", "nsw.permit": "▤",
        "nsw.declaration": "▣", "nsw.manifest": "▤", "nsw.arrival": "▼", "ops.": "▲", "supervision": "!", "system": "●"}
SEV = {"high": "▲", "medium": "▲", "low": "●", "info": "·"}


def _icon(t: str) -> str:
    return next((v for k, v in ICON.items() if t.startswith(k)), "·")


@st.fragment(run_every="2s")
def live_feed(height: int = 430, key: str = "feed") -> None:
    c1, c2, c3 = st.columns([0.2, 0.4, 0.4])
    paused = st.session_state.get(f"{key}_paused", False)
    if c1.button("Resume" if paused else "Pause", key=f"{key}_pp"):
        st.session_state[f"{key}_paused"] = not paused
        paused = not paused
    sev = c2.multiselect("Severity", ["high", "medium", "low", "info"], key=f"{key}_sev", label_visibility="collapsed", placeholder="Severity: all", help=tooltips.tip("filter_severity"))
    mine = c3.toggle("Only my filters", key=f"{key}_mine")
    f = state.filters()
    if paused and f"{key}_frozen" in st.session_state:
        df = st.session_state[f"{key}_frozen"]
    else:
        df = queries.live_events(60, entities=f.entities if mine else (), severities=tuple(sev), as_of=state.live_as_of())
        st.session_state[f"{key}_frozen"] = df
    if df.empty:
        st.info("No events yet.")
        return
    d = pd.DataFrame({"logo": [logos.uri(e, 32) for e in df["entity_id"]], "when (WAT)": [clock.fmt_wat(t, "%d %b %H:%M:%S") for t in df["occurred_at"]],
                      "": [f"{SEV.get(s, '·')} {_icon(t)}" for s, t in zip(df["severity"], df["type"])], "event": df["headline"], "detail": df["description"], "reference": df["subject"]})
    sel = st.dataframe(d, hide_index=True, width="stretch", height=height, on_select="rerun", selection_mode="single-row", key=f"{key}_df",
                       column_config={"logo": st.column_config.ImageColumn("Entity", width="small"), "event": st.column_config.TextColumn("Event", width="medium"),
                                      "detail": st.column_config.TextColumn("Detail", width="large"), "reference": st.column_config.TextColumn("Reference", help="Select the row and open it in the Trace Workbench.")})
    rows = sel.selection.rows if sel and sel.selection else []
    if rows:
        ref = d.iloc[rows[0]]["reference"]
        if ref and ref.startswith("NSW-") and st.button(f"Open {ref} in Trace", key=f"{key}_trace"):
            state.goto_trace(ref)


def live_counters() -> None:
    m = queries.minute_series(60, state.live_as_of(), "paid")
    last60 = float(m["value"].sum()) if not m.empty else 0.0
    ev = queries.events_per_minute(state.live_as_of(), 30)
    from app.components import cards
    from nsw_sim.money import fmt_ngn
    c1, c2 = st.columns(2)
    cards.kpi(c1, "events_per_minute", f"{(ev['n'].tail(5).mean() if not ev.empty else 0):.1f}")
    cards.kpi(c2, "paid", fmt_ngn(last60), label="Payments confirmed, last 60 min")
