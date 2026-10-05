"""Plotly helpers: consistent entity colours, units in hover, accessible contrast, download buttons."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from app.components import logos
from nsw_sim.money import fmt_ngn

GREEN, AMBER, RED, BLUE, GREY = "#0B5D3B", "#B7791F", "#B03A2E", "#2F5D9B", "#6B7280"
RAG = {"green": "#2E7D32", "amber": GREEN and AMBER, "red": RED, "grey": GREY}
GLYPH = {"green": "●", "amber": "●", "red": "●", "grey": "●"}


def layout(fig: go.Figure, height: int = 340, legend: bool = True) -> go.Figure:
    fig.update_layout(height=height, margin=dict(l=8, r=8, t=28, b=8), font=dict(size=14), legend=dict(orientation="h", y=-0.2) if legend else dict(visible=False),
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", hoverlabel=dict(font_size=13))
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(gridcolor="#e8ece9")
    return fig


def show(fig: go.Figure, key: str, height: int = 340, legend: bool = True) -> None:
    st.plotly_chart(layout(fig, height, legend), use_container_width=True, key=key, config={"displaylogo": False, "toImageButtonOptions": {"filename": key}})


def naira_axis(values: pd.Series) -> tuple[float, str]:
    m = float(values.abs().max() or 1)
    return (1e11, "₦bn") if m >= 1e11 else ((1e8, "₦m") if m >= 1e8 else (100.0, "₦"))


def entity_area(df: pd.DataFrame, x: str, y: str, color: str = "entity", unit_div: float = 1e11, title_unit: str = "₦bn") -> go.Figure:
    fig = go.Figure()
    for ent, g in df.groupby(color):
        fig.add_trace(go.Scatter(x=g[x], y=g[y] / unit_div, name=ent, mode="lines", stackgroup="one", line=dict(width=0.5, color=logos.colour(ent)),
                                 hovertemplate=f"{ent}<br>%{{x}}<br>%{{y:,.3f}} {title_unit}<extra></extra>"))
    fig.update_yaxes(title=title_unit)
    return fig


def entity_bar(df: pd.DataFrame, x: str, y: str, unit_div: float = 1e11, title_unit: str = "₦bn", horizontal: bool = False) -> go.Figure:
    colors = [logos.colour(e) for e in df[x]]
    fig = go.Figure(go.Bar(x=df[y] / unit_div if horizontal else df[x], y=df[x] if horizontal else df[y] / unit_div, marker_color=colors, orientation="h" if horizontal else "v",
                           customdata=[fmt_ngn(v, exact=True) for v in df[y]], hovertemplate="%{x}<br>%{customdata}<extra></extra>" if not horizontal else "%{y}<br>%{customdata}<extra></extra>"))
    fig.update_yaxes(title=None if horizontal else title_unit)
    return fig


def rag_dot(rag: str) -> str:
    return f'<span style="color:{RAG.get(rag, GREY)};font-size:1.1em">{GLYPH.get(rag, "●")}</span>'


def sparkline(values: list[float], colour: str = GREEN, h: int = 36) -> go.Figure:
    fig = go.Figure(go.Scatter(y=values, mode="lines", line=dict(color=colour, width=2), fill="tozeroy", fillcolor="rgba(11,93,59,0.08)", hoverinfo="skip"))
    fig.update_layout(height=h, margin=dict(l=0, r=0, t=0, b=0), xaxis=dict(visible=False), yaxis=dict(visible=False), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", showlegend=False)
    return fig
