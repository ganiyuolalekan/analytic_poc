"""Logo rendering: every place an entity appears shows its logo or a generated badge (never crashes)."""
from __future__ import annotations

import streamlit as st

from nsw_sim import logos as _logos
from nsw_sim.config import yaml_config

ENT = yaml_config("entities")["entities"]


@st.cache_data(show_spinner=False)
def uri(code: str, px: int = 64) -> str:
    return _logos.logo_data_uri(code, px)


def colour(code: str) -> str:
    return ENT.get(code, {}).get("colour", "#555555")


def img(code: str, px: int = 28) -> str:
    return f'<img src="{uri(code, px)}" width="{px}" height="{px}" alt="{code}" title="{ENT.get(code, {}).get("name", code)}" style="object-fit:contain;border-radius:{px // 6}px;vertical-align:middle;background:#fff;border:1px solid #e3e8e5"/>'


def chip(code: str, text: str | None = None, px: int = 22) -> str:
    return f'<span class="chip">{img(code, px)}&nbsp;{text if text is not None else code}</span>'


def name(code: str) -> str:
    return ENT.get(code, {}).get("name", code)


def grid(selected: str | None, key: str = "ent_pick", per_row: int = 6) -> str | None:
    """Logo grid picker: a button under each logo. Returns the (newly) selected entity code."""
    codes = list(ENT)
    chosen = selected
    for i in range(0, len(codes), per_row):
        cols = st.columns(per_row)
        for c, code in zip(cols, codes[i:i + per_row]):
            with c:
                st.markdown(f"<div style='text-align:center;{'outline:3px solid ' + colour(code) + ';border-radius:10px;padding:2px' if code == selected else ''}'>{img(code, 54)}</div>", unsafe_allow_html=True)
                if st.button(code, key=f"{key}_{code}", width="stretch", type="primary" if code == selected else "secondary"):
                    chosen = code
    return chosen
