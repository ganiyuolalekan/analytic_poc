"""NSW Intelligence Console: Streamlit shell. SYNTHETIC DATA. Run with ``make run``."""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

st.set_page_config(layout="wide", page_title="NSW Intelligence Console", initial_sidebar_state="expanded")

from app.components import cloud  # noqa: E402

cloud.secrets_to_env()          # on Streamlit Community Cloud the settings arrive as secrets: make them environment variables before anything reads them

from app.components import filters, gate, header, qa, state  # noqa: E402
from nsw_sim import db, dbfetch  # noqa: E402
from nsw_sim.config import db_path  # noqa: E402

PAGES = [("screens/00_home.py", "Home"), ("screens/01_command_center.py", "Command Center"), ("screens/02_clearance.py", "Clearance Journey"), ("screens/03_entities.py", "Entity Explorer"),
         ("screens/04_trace.py", "Trace Workbench"), ("screens/05_reconciliation.py", "Reconciliation"), ("screens/06_supervision.py", "Supervision"),
         ("screens/07_reports.py", "Reports"), ("screens/08_assistant.py", "Assistant"), ("screens/09_data_quality.py", "Data Quality & Onboarding"),
         ("screens/10_architecture.py", "Architecture & Integration"), ("screens/11_admin.py", "Admin / Generation Control"), ("screens/12_methodology.py", "Methodology & Glossary")]
SECTIONS = {"screens/00_home.py", "screens/03_entities.py", "screens/04_trace.py", "screens/06_supervision.py"}          # all a reviewer sees in the sidebar; the other pages are for the presenter ("More pages")


def _fetch_screen() -> None:
    """Shown while a new host downloads and prepares the database from the private dataset (first start only)."""
    s = dbfetch.status()
    st.title("Getting the data ready")
    if s["state"] == "error":
        st.error(s["message"])
        if st.button("Try again", type="primary"):
            dbfetch.retry()
            st.rerun()
        return
    st.progress(min(1.0, s["fraction"]), text=s["label"] + "…")
    st.caption("The first start on a new host takes a minute or two. This page refreshes by itself.")
    time.sleep(2)
    st.rerun()


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
dbfetch.start()          # a new host starts fetching its database as soon as anyone opens the app, even before the access code is entered (does nothing unless NSW_DB_REPO is set)
if not gate.passed():
    st.stop()
if dbfetch.needed():
    _fetch_screen()
elif _bootstrap_screen():
    filters.sidebar()
    header.top_bar()
    if not state.view_only():          # the presenter's own machine only: a shared review link has no way to reveal the technical pages
        st.sidebar.toggle("Show all pages", value=True, key="show_tech", help="The other pages, engine status and integration details, for the presenter and engineers.")
    main_pages = [st.Page(p, title=t, default=(p == "screens/00_home.py")) for p, t in PAGES if p in SECTIONS]
    more_pages = [st.Page(p, title=t) for p, t in PAGES if p not in SECTIONS] if state.technical() else []
    nav = st.navigation({"": main_pages, "More pages (presenter)": more_pages} if more_pages else main_pages, position="sidebar")
    if nav.url_path:          # the landing page stays simple: the 'since your last session' card appears on the other pages
        from app.components import digest
        digest.since_last_session()
    if nav.url_path not in ("", "assistant"):          # the landing page has the assistant on it, and the Assistant page is the assistant
        qa.floating(nav.url_path)
    nav.run()
