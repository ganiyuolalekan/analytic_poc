"""Tooltip system (Section 12). All text lives in config/tooltips.yaml; every label shown anywhere goes through these helpers."""
from __future__ import annotations

from functools import lru_cache

import streamlit as st
import yaml

from nsw_sim.config import CONFIG_DIR


@lru_cache(maxsize=1)
def _all() -> dict:
    with open(CONFIG_DIR / "tooltips.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def entry(key: str) -> dict:
    t = _all()
    if key not in t:
        raise KeyError(f"missing tooltip key: {key}")
    return t[key]


def label(key: str) -> str:
    return entry(key)["label"]


def tip(key: str, level: str = "short") -> str:
    return entry(key)[level]


def info(key: str, *, help_text: bool = True) -> None:
    """ⓘ popover with the long explanation."""
    with st.popover("ⓘ", help=tip(key) if help_text else None):
        st.markdown(f"**{label(key)}**")
        st.markdown(tip(key, "long"))


def title(key: str, level: int = 4) -> None:
    """Chart / section title with an ⓘ popover beside it."""
    c1, c2 = st.columns([0.94, 0.06])
    c1.markdown(f"{'#' * level} {label(key)}")
    with c2:
        info(key)


def col(key: str, kind: str = "text", fmt: str | None = None, **kw):
    """Streamlit column_config with a ``help=`` tooltip taken from the catalogue."""
    cfg = {"text": st.column_config.TextColumn, "number": st.column_config.NumberColumn, "progress": st.column_config.ProgressColumn,
           "link": st.column_config.LinkColumn, "datetime": st.column_config.DatetimeColumn}[kind]
    args = {"label": label(key), "help": tip(key)}
    if fmt:
        args["format"] = fmt
    return cfg(**args, **kw)


def search(q: str) -> list[tuple[str, dict]]:
    ql = q.lower().strip()
    return [(k, v) for k, v in sorted(_all().items(), key=lambda kv: kv[1]["label"].lower()) if not ql or ql in k.lower() or ql in v["label"].lower() or ql in v["short"].lower()]
