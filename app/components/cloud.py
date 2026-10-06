"""Running on Streamlit Community Cloud: settings are kept in Streamlit's secrets, but this app reads environment variables (access code, view-only mode, AI key, database dataset)."""
from __future__ import annotations

import os

import streamlit as st


def secrets_to_env() -> None:
    """Copy the top-level secrets into the environment, never overriding a variable that is already set. Call before anything reads a setting. With no secrets (every local run) it does nothing.
    Write values as strings in the secrets box (``NSW_VIEW_ONLY = "1"``)."""
    try:
        items = dict(st.secrets)
    except Exception:  # noqa: BLE001 - no secrets file or none configured
        return
    for key, value in items.items():
        if isinstance(value, (str, int, float, bool)) and key not in os.environ:
            os.environ[key] = str(value)
