"""Optional access code for shared deployments. Set ``NSW_ACCESS_CODE`` and every page asks for it before showing anything
(the code is never logged or displayed). Unset, the app opens without a prompt, as it does on the presenter's own machine."""
from __future__ import annotations

import hmac
import os
import time

import streamlit as st


def required() -> bool:
    return bool(os.environ.get("NSW_ACCESS_CODE", ""))


def passed() -> bool:
    """True when no code is required or this browser session has entered it; otherwise draws the prompt and returns False."""
    code = os.environ.get("NSW_ACCESS_CODE", "")
    if not code or st.session_state.get("_access_ok"):
        return True
    st.title("NSW Intelligence Console")
    st.caption("Synthetic data, concept demo. Enter the access code you were given to continue.")
    entered = st.text_input("Access code", type="password", key="_access_code")
    if entered:
        if hmac.compare_digest(entered.encode(), code.encode()):
            st.session_state["_access_ok"] = True
            st.rerun()
        time.sleep(1.0)                                    # slows down guessing
        st.error("That code is not right.")
    return False
