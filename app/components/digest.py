"""'Since your last session' card (dismissible): facts computed from the database, wording by the AI."""
from __future__ import annotations

import streamlit as st

from app.components import fmt


def since_last_session() -> None:
    if st.session_state.get("digest_dismissed") or st.session_state.get("digest_shown"):
        return
    st.session_state["digest_shown"] = True
    try:
        from nsw_sim.assistant import narrator
        card = narrator.digest()
    except Exception:  # noqa: BLE001 - the card is optional
        return
    if not card:
        return
    with st.container(border=True):
        c1, c2 = st.columns([0.92, 0.08])
        c1.markdown(f"**Since your last session.** {fmt.plain(card['headline'])}")
        for b in card["bullets"]:
            c1.markdown(f"- {fmt.plain(b)}")
        c1.caption(f"{card['source']} · Illustrative synthetic data.")
        if c2.button("Dismiss", key="digest_x"):
            st.session_state["digest_dismissed"] = True
            st.rerun()
