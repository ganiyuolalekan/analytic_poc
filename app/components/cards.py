"""KPI strip and small cards. ``st.metric`` is only called here so the tooltip-coverage test can guarantee help text."""
from __future__ import annotations

import streamlit as st

from app.components import fmt, logos, tooltips


def kpi(container, key: str, value: str, delta: str | None = None, delta_color: str = "normal", label: str | None = None) -> None:
    container.metric(label or tooltips.label(key), value, delta=delta, delta_color=delta_color, help=tooltips.tip(key))


def delta_str(cur: float | None, prev: float | None, kind: str = "pct") -> str | None:
    if cur is None or prev in (None, 0):
        return None
    d = (cur - prev) / abs(prev)
    return f"{d * 100:+.1f}%"


def money_kpi(container, key: str, cur: float, prev: float | None, label: str | None = None) -> None:
    kpi(container, key, fmt.ngn(cur), delta_str(cur, prev), label=label)


def disclaimer_footer() -> None:
    st.caption("Illustrative synthetic data.")


def entity_card(code: str, lines: list[str], badge: str = "") -> None:
    st.markdown(f"<div class='card' style='border-left:6px solid {logos.colour(code)}'><div>{logos.img(code, 40)} <b style='font-size:1.15em'>&nbsp;{logos.name(code)}</b> {badge}</div>"
                f"<div style='margin-top:6px;color:#44524b'>{'<br>'.join(lines)}</div></div>", unsafe_allow_html=True)
