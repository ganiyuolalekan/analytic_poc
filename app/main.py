"""NSW Intelligence Console: Streamlit shell. SYNTHETIC DATA. Run with ``make run``."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

st.set_page_config(layout="wide", page_title="NSW Intelligence Console", initial_sidebar_state="expanded")

from app.components import chat, filters, header, state  # noqa: E402
from nsw_sim import db  # noqa: E402
from nsw_sim.config import db_path  # noqa: E402

PAGES = [("pages/01_command_center.py", "Command Center"), ("pages/02_clearance.py", "Clearance Journey"), ("pages/03_entities.py", "Entity Explorer"),
         ("pages/04_trace.py", "Trace Workbench"), ("pages/05_reconciliation.py", "Reconciliation"), ("pages/06_supervision.py", "Supervision"),
         ("pages/07_reports.py", "Reports"), ("pages/08_assistant.py", "Assistant"), ("pages/09_data_quality.py", "Data Quality & Onboarding"),
         ("pages/10_architecture.py", "Architecture & Integration"), ("pages/11_admin.py", "Admin / Generation Control"), ("pages/12_methodology.py", "Methodology & Glossary")]


def _bootstrap_screen() -> bool:
    """Progress screen while the database is empty / catching up. Returns True when the UI may render."""
    svc = state.service()
    ready = db_path().exists()
    wm = None
    if ready:
        try:
            wm = state.as_of() if db.kv_get(db.reader(), "watermark_utc") else None
        except Exception:  # noqa: BLE001
            wm = None
    if wm is None or svc.mode in ("starting",) and not wm:
        stt = svc.status()
        st.title("Preparing the simulated NSW channel")
        st.progress(min(1.0, stt["progress"]["fraction"]), text=stt["progress"]["label"] or "Starting…")
        st.caption("First start builds the world (profiles, plans, history). Run `make seed` ahead of a meeting to do this in advance.")
        st.fragment(lambda: None)()
        import time
        time.sleep(2)
        st.rerun()
        return False
    return True


header.ribbon()
if _bootstrap_screen():
    filters.sidebar()
    header.top_bar()
    chat.drawer()
    from app.components import digest
    digest.since_last_session()
    nav = st.navigation([st.Page(p, title=t, default=(i == 0)) for i, (p, t) in enumerate(PAGES)], position="sidebar")
    nav.run()
