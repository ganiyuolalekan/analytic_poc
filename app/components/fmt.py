"""UI number formatting. Amounts carry no currency sign: every page shows one legend saying what they are in (naira, or US dollars in the USD view)."""
from __future__ import annotations

import re

import streamlit as st

from nsw_sim.money import NAIRA, fmt_ngn

_SIGN = re.compile(NAIRA + r"(?=[\d,.\-]|$)")


def ngn(minor: int | float | None, exact: bool = False, signed: bool = False) -> str:
    """Kobo as 1.23bn / 456.7m / 12,345 (exact: 1,234,567.89), without the naira sign."""
    return fmt_ngn(minor, exact=exact, signed=signed).replace(NAIRA, "")


def plain(text: str | None) -> str:
    """Text for display with the naira sign dropped from figures. Stored text keeps the sign (the answer checker reads ₦ amounts); only what is shown changes."""
    return _SIGN.sub("", text) if text else (text or "")


def legend() -> str:
    ccy = "US dollars" if st.session_state.get("f_ccy") == "USD" else "naira"
    return f"All amounts are in {ccy} (m = million, bn = billion, tn = trillion)."
