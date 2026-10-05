"""Chat drawer available on every page (sidebar popover) + the full chat UI used by the Assistant page."""
from __future__ import annotations

import streamlit as st

from app.components import state, tooltips


def drawer() -> None:
    with st.sidebar:
        with st.popover("Ask the assistant", use_container_width=True):
            st.caption("Answers use verified tools over the live data. Open the Assistant page for the full view.")
            q = st.text_input("Question", key="drawer_q", placeholder="How much did NCS collect month to date?")
            if st.button("Ask", key="drawer_ask") and q:
                st.session_state["assistant_pending"] = q
                st.switch_page("pages/08_assistant.py")
