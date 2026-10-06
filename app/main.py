"""NSW Intelligence Console: Streamlit shell. SYNTHETIC DATA. Run with ``make run``."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

st.set_page_config(layout="wide", page_title="NSW Intelligence Console", initial_sidebar_state="expanded")

from app.components import filters, gate, header, qa, state  # noqa: E402
from nsw_sim import db  # noqa: E402
from nsw_sim.config import db_path  # noqa: E402

PAGES = [("pages/00_home.py", "Home"), ("pages/01_command_center.py", "Command Center"), ("pages/02_clearance.py", "Clearance Journey"), ("pages/03_entities.py", "Entity Explorer"),
         ("pages/04_trace.py", "Trace Workbench"), ("pages/05_reconciliation.py", "Reconciliation"), ("pages/06_supervision.py", "Supervision"),
         ("pages/07_reports.py", "Reports"), ("pages/08_assistant.py", "Assistant"), ("pages/09_data_quality.py", "Data Quality & Onboarding"),
         ("pages/10_architecture.py", "Architecture & Integration"), ("pages/11_admin.py", "Admin / Generation Control"), ("pages/12_methodology.py", "Methodology & Glossary")]
TECHNICAL = {"pages/10_architecture.py", "pages/11_admin.py"}          # for the presenter and engineers: hidden from a shared review link until 'Show technical pages' is switched on


def _bootstrap_screen() -> bool:
    """Gate screen while the database has no data yet. Returns True when the UI may render."""
    svc = state.service()
    wm = None
    if db_path().exists():
        try:
            wm = state.as_of() if db.kv_get(db.reader(), "watermark_utc") else None
        except Exception:  # noqa: BLE001
            wm = None
    if wm is not None:
        return True
    stt = svc.status()
    if not stt["enabled"]:
        st.title("No simulated data yet")
        st.info("Live generation is off and the database is empty. Build the history from a terminal with `make seed` (a few minutes; cached AI plans are free), "
                "or build it here, which switches live generation on.")
        if st.button("Build the data now (switches live generation on)", type="primary"):
            svc.set_live(True, "app")
            st.rerun()
        return False
    st.title("Preparing the simulated NSW channel")
    st.progress(min(1.0, stt["progress"]["fraction"]), text=stt["progress"]["label"] or "Starting…")
    st.caption("First start builds the world (profiles, plans, history). Run `make seed` ahead of a meeting to do this in advance.")
    import time
    time.sleep(2)
    st.rerun()
    return False


header.ribbon()
if not gate.passed():
    st.stop()
if _bootstrap_screen():
    filters.sidebar()
    header.top_bar()
    if not state.view_only():          # the presenter's own machine only: a shared review link has no way to reveal the technical pages
        st.sidebar.toggle("Show technical pages", value=True, key="show_tech", help="Engine status, AI usage and integration details, for the presenter and engineers.")
    nav = st.navigation([st.Page(p, title=t, default=(i == 0)) for i, (p, t) in enumerate(PAGES) if p not in TECHNICAL or state.technical()], position="sidebar")
    if nav.url_path:          # the landing page stays simple: the 'since your last session' card appears on the other pages
        from app.components import digest
        digest.since_last_session()
    if nav.url_path not in ("", "assistant"):          # the landing page has the assistant on it, and the Assistant page is the assistant
        qa.floating(nav.url_path)
    nav.run()
