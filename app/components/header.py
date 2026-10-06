"""Persistent synthetic-data ribbon, top bar (NSW logo, LLM chip, WAT clock with live pulse, data watermark), role + presenter."""
from __future__ import annotations

import streamlit as st

from app.components import gate, logos, state, tooltips
from nsw_sim import DISCLAIMER, clock
from nsw_sim.llm.client import STATUS
from nsw_sim.sim import control

CSS = """
<style>
html, body, [class*="css"] { font-size: 17px; }
.block-container { padding-top: 3.2rem; }
.ribbon { position: fixed; top: 0; left: 0; right: 0; z-index: 999990; background: #B03A2E; color: #fff; text-align: center; font-weight: 700; letter-spacing: .04em; padding: 6px 10px; font-size: 14px; }
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


def llm_chip() -> str:
    svc = state.service()
    snap = STATUS.snapshot()
    s = "offline" if svc.llm.offline else snap["state"]
    return f'<span class="llm-{s}">● LLM {s}</span>'


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
        st.warning(f"Degraded mode: {STATUS.reason or ('offline' if svc.llm.offline else 'model unavailable')}. The deterministic fallback keeps everything running. ({tooltips.tip('degraded_mode')})")


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


def page_header(title: str, explain: str, ignores: tuple[str, ...] = ()) -> None:
    if gate.required() and not st.session_state.get("_access_ok"):
        # Streamlit lets a browser open a page script directly (/supervision) without going through main.py, so the access code is enforced here too:
        # every page calls this before it touches any data (tests/test_sharing.py checks both facts).
        ribbon()
        gate.passed()
        st.stop()
    c1, c2 = st.columns([0.9, 0.1])
    c1.title(title)
    with c2:
        with st.popover("Explain this page"):
            st.markdown(explain)
            st.caption("All data is synthetic. Hover any ⓘ for definitions.")
    from app.components import filters
    filters.chips()
    if ignores:
        filters.ignored(*ignores)


def methodology_footer(items: list[tuple[str, str]]) -> None:
    with st.expander("How these numbers are computed"):
        for label, text in items:
            st.markdown(f"**{label}.** {text}")
        st.caption("Every figure is computed by SQL or Python over the database at the data time shown above; the language model never calculates a number that appears on screen.")
