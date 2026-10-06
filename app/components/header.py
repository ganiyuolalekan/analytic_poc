"""Persistent synthetic-data ribbon, top bar (NSW logo, AI status, WAT clock with live pulse, data watermark), role + presenter."""
from __future__ import annotations

import streamlit as st

from app.components import fmt, gate, logos, state, tooltips
from nsw_sim import DISCLAIMER, clock
from nsw_sim.llm.client import STATUS
from nsw_sim.sim import control

CSS = """
<style>
html, body, [class*="css"] { font-size: 17px; }
.block-container, [data-testid="stMainBlockContainer"] { padding: 3.2rem 1.6rem 4rem 1.6rem !important; max-width: 100% !important; }
.st-key-home_hero { background: linear-gradient(135deg, #DDF0E4 0%, #F1F8F3 55%, #FFFFFF 100%); border: 1px solid #CFE3D6; border-radius: 22px; padding: 1.5rem 2rem 1.3rem; }
.hero-kicker { color: #0B5D3B; font-weight: 700; letter-spacing: .09em; text-transform: uppercase; font-size: .78rem; }
.hero-title { font-size: 2.7rem; font-weight: 800; line-height: 1.12; margin: .15rem 0 .35rem; color: #10261B; }
.hero-sub { color: #44574D; font-size: 1.12rem; margin: 0 0 .7rem; }
.st-key-home_hero input { font-size: 1.15rem; padding-top: .7rem; padding-bottom: .7rem; }
.st-key-flow_in, .st-key-flow_out, .st-key-flow_due { background: #fff; border-radius: 16px; padding: .9rem 1.3rem .7rem; box-shadow: 0 2px 12px rgba(16,38,27,.09); border-left: 7px solid #0B5D3B; }
.st-key-flow_out { border-left-color: #2F5D9B; } .st-key-flow_due { border-left-color: #B7791F; }
.st-key-flow_in [data-testid="stMetricValue"], .st-key-flow_out [data-testid="stMetricValue"], .st-key-flow_due [data-testid="stMetricValue"] { font-size: 2.4rem; font-weight: 800; }
.st-key-home_agencies [data-testid="stImage"] img, .st-key-home_agencies img { max-height: 44px; width: auto; object-fit: contain; }
[data-testid="stChatMessage"] p, [data-testid="stChatMessage"] li { font-size: 1.04rem; line-height: 1.6; }
[data-testid="stChatMessage"] ul { margin: .15rem 0 .7rem; padding-left: 1.2rem; } [data-testid="stChatMessage"] li { margin-bottom: .3rem; }
[data-testid="stChatMessage"] strong { color: #10261B; }
.ribbon { position: fixed; top: 0; left: 0; right: 0; z-index: 999990; background: #B03A2E; color: #fff; text-align: center; font-weight: 700; letter-spacing: .04em; padding: 6px 10px; font-size: 14px; }
[data-testid="stMetricValue"] { font-size: 1.8rem; }
[data-testid="stMetricValue"], [data-testid="stMetricValue"] > div { overflow: visible !important; text-overflow: clip !important; }
.st-key-ai_fab { position: fixed; bottom: 1.4rem; right: 1.6rem; z-index: 999980; width: auto !important; }
.st-key-ai_fab button { border-radius: 28px; background: #0B5D3B; color: #fff; font-weight: 700; padding: .5rem 1.3rem; box-shadow: 0 4px 14px rgba(0,0,0,.28); border: 0; }
.st-key-ai_fab button:hover { background: #0E7A4E; color: #fff; }
[data-testid="stFormSubmitButton"] button { min-width: 4.6rem; }
@media (max-width: 1100px) { .st-key-home_charts > [data-testid="stLayoutWrapper"] > [data-testid="stHorizontalBlock"], .st-key-home_flow > [data-testid="stLayoutWrapper"] > [data-testid="stHorizontalBlock"] { flex-wrap: wrap; }
  .st-key-home_charts > [data-testid="stLayoutWrapper"] > [data-testid="stHorizontalBlock"] > [data-testid="stColumn"], .st-key-home_flow > [data-testid="stLayoutWrapper"] > [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] { flex: 1 1 100% !important; width: 100% !important; min-width: 100% !important; } }
.card { background: var(--secondary-background-color, #F2F5F3); border-radius: 10px; padding: 12px 14px; margin-bottom: 10px; }
.chip { display: inline-block; background: var(--secondary-background-color, #F2F5F3); border: 1px solid #d7dfda; border-radius: 14px; padding: 1px 9px 1px 4px; margin: 1px 2px; font-size: 13px; }
.pulse { display:inline-block; width:10px; height:10px; border-radius:50%; background:#2E7D32; margin-right:6px; animation: pulse 1.6s infinite; }
@keyframes pulse { 0% { box-shadow: 0 0 0 0 rgba(46,125,50,.6);} 70% { box-shadow: 0 0 0 9px rgba(46,125,50,0);} 100% { box-shadow: 0 0 0 0 rgba(46,125,50,0);} }
.dot-off { display:inline-block; width:10px; height:10px; border-radius:50%; background:#9aa8a1; margin-right:6px; }
.llm-live { color:#2E7D32; font-weight:700 } .llm-degraded { color:#B7791F; font-weight:700 } .llm-offline { color:#B03A2E; font-weight:700 }
.tie-ok { color:#2E7D32; font-weight:700 } .tie-bad { color:#B03A2E; font-weight:700 }
.feed-row { border-left: 5px solid #ccc; padding: 4px 8px; margin: 3px 0; background: rgba(120,140,130,.07); border-radius: 4px; font-size: 15px; }
.sev-high { border-color:#B03A2E } .sev-medium { border-color:#B7791F } .sev-low { border-color:#2F5D9B } .sev-info { border-color:#9aa8a1 }
</style>
"""


def ribbon() -> None:
    st.markdown(CSS, unsafe_allow_html=True)
    st.markdown(f'<div class="ribbon">{DISCLAIMER}</div>', unsafe_allow_html=True)


AI_WORDS = {"live": "ready", "degraded": "limited", "offline": "offline"}


def llm_chip() -> str:
    svc = state.service()
    snap = STATUS.snapshot()
    s = "offline" if svc.llm.offline else snap["state"]
    return f'<span class="llm-{s}">● AI assistant {AI_WORDS.get(s, s)}</span>'


def _flip_live() -> None:
    if state.view_only():
        return
    control.set_enabled(bool(st.session_state.get("live_switch")), "app")


def _behind(wm: str) -> str:
    secs = max(0, int(clock.utcnow().timestamp() - clock.to_epoch(wm)))
    h, m = secs // 3600, secs % 3600 // 60
    return f"{h} h {m:02d} min" if h else f"{m} min"


@st.fragment(run_every="2s")
def clock_strip() -> None:
    svc = state.service()
    stt = svc.status()
    on = control.is_enabled()
    st.session_state["live_switch"] = on          # the switch can also be flipped from the terminal: re-sync before the widget is built
    mode = stt["mode"]
    wm = state.live_as_of()
    now = clock.fmt_wat(wm, "%d %b %Y %H:%M:%S WAT")
    if not state.technical():          # a plain top bar for reviewers: the data time and whether the AI is available, nothing about the engine
        st.markdown(f"<b>Data as of {now}</b><br><span style='color:#55655d;font-size:13px'>demo data · {llm_chip()}</span>", unsafe_allow_html=True)
        if STATUS.state != "live" or svc.llm.offline:
            st.info("The AI assistant is limited at the moment, so some answers may be simpler. The figures on screen are not affected.")
        return
    if mode == "live":
        speed = f" · LIVE ×{stt['speed']:g}" if stt["speed"] != 1 else ""
        head = f'<span class="pulse"></span><b>{now}</b>{speed}'
        sub = f"data as of {now} · live generation ON"
    elif mode == "catching_up" or (on and mode in ("off", "stopped")):
        head = f'<span class="dot-off"></span><b>{now}</b>'
        sub = "live generation starting: catching the data up to the current time"
    elif mode == "follower":
        head = f'<span class="dot-off"></span><b>{now}</b>'
        sub = "another process is generating the data; this app is read-only"
    elif mode == "error":
        head = f'<span class="dot-off"></span><b>{now}</b>'
        sub = "the generator hit an error and switched itself off (see Admin)"
    else:
        head = f'<span class="dot-off"></span><b>Data frozen at {now}</b>'
        sub = f"live generation OFF · {_behind(wm)} behind real time" if not on else "live generation stopping"
    st.markdown(f"{head}<br><span style='color:#55655d;font-size:13px'>{sub} · {llm_chip()}</span>", unsafe_allow_html=True)
    if mode == "catching_up":
        prog = stt["progress"]
        st.progress(min(1.0, prog["fraction"]), text=prog["label"] or "Catching up…")
    st.toggle("Live generation", key="live_switch", on_change=_flip_live, disabled=state.view_only(),
              help=tooltips.tip("live_generation") + (" This deployment is view-only: the presenter controls generation." if state.view_only() else ""))
    if STATUS.state != "live" or svc.llm.offline:
        st.warning(f"The AI assistant is limited: {STATUS.reason or ('offline' if svc.llm.offline else 'unavailable')}. The built-in backup keeps everything else running. ({tooltips.tip('degraded_mode')})")


def top_bar() -> None:
    c1, c2, c3, c4 = st.columns([0.1, 0.42, 0.28, 0.2])
    with c1:
        st.markdown(logos.img("NSW", 56), unsafe_allow_html=True)
    with c2:
        st.markdown("## NSW Intelligence Console")
        st.caption("Live, traceable, supervised financial view across NSW agencies (concept demo)")
    with c3:
        clock_strip()
    with c4:
        st.selectbox("Role", state.ROLES, index=1, key="role", help=tooltips.tip("roles"))
        st.toggle("Presenter mode", key="presenter", help=tooltips.tip("admin_controls"))


def page_header(title: str, explain: str, ignores: tuple[str, ...] = (), legend: bool = True, chips: bool = True, show_title: bool = True) -> None:
    if gate.required() and not st.session_state.get("_access_ok"):
        # Streamlit lets a browser open a page script directly (/supervision) without going through main.py, so the access code is enforced here too:
        # every page calls this before it touches any data (tests/test_sharing.py checks both facts).
        ribbon()
        gate.passed()
        st.stop()
    c1, c2 = st.columns([0.9, 0.1])
    if show_title:
        c1.title(title)
    if legend:
        c1.caption(fmt.legend())
    with c2:
        with st.popover("Explain this page"):
            st.markdown(explain)
            st.caption("All data is synthetic. Hover any ⓘ for definitions.")
    from app.components import filters
    if chips:
        filters.chips()
    if ignores:
        filters.ignored(*ignores)


def technical_only() -> None:
    """Pages for the presenter and engineers. A shared review link does not list them, and a direct address shows this notice instead."""
    if not state.technical():
        st.info("This page is for the presenter, so it is not part of the review.")
        st.stop()


def methodology_footer(items: list[tuple[str, str]]) -> None:
    with st.expander("How these numbers are computed"):
        for label, text in items:
            st.markdown(f"**{label}.** {text}")
        st.caption("Every figure is calculated directly from the data at the data time shown above; the AI never calculates a number that appears on screen.")
