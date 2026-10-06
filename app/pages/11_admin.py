import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from app.components import cards, header, state, tooltips  # noqa: E402
from nsw_sim import clock, db  # noqa: E402
from nsw_sim.config import db_path, yaml_config  # noqa: E402
from nsw_sim.llm import ledger  # noqa: E402

header.page_header("Admin / Generation Control", "Presenter controls and the engine room: simulation status, AI status and usage, how much generated content came from the AI "
                   "versus the built-in backup, the scripted story beats and whether each detector fired.", ("All filters",), legend=False)
header.technical_only()
svc = state.service()
stt = svc.status()
if not state.presenter():
    st.info("Switch on **Presenter mode** (top right) to use the controls on this page. Status is always visible.")
live = stt["mode"] == "live"
if state.view_only():
    st.info("This deployment is **view-only**: generation, catch-up, speed, offline mode and incident controls are disabled. The presenter controls them from the host machine.")
if live:
    st.success("Live generation is **ON**. Switch it off with the toggle in the top bar or `make live-off`; the data then freezes at its last data time.")
elif stt["mode"] == "catching_up":
    st.info("Live generation is starting: the data is being caught up to the current time. The controls below unlock when it is live.")
else:
    st.warning("Live generation is **OFF**: the data is frozen and nothing is generated. Switch it on with the toggle in the top bar or `make live-on`. "
               "The engine controls below (catch-up, speed, incidents) need it on.")
tooltips.title("admin_controls")
c = st.columns(5)
c[0].markdown(f"**Service**<br>{stt['mode']}", unsafe_allow_html=True)
c[1].markdown(f"**Watermark**<br>{clock.fmt_wat(stt['watermark']) if stt['watermark'] else '–'}", unsafe_allow_html=True)
c[2].markdown(f"**Events / min**<br>{stt['events_per_min']}", unsafe_allow_html=True)
c[3].markdown(f"**AI**<br>{'offline' if stt['offline'] else stt['llm']['state']} · {stt['llm']['avg_latency_ms']} ms", unsafe_allow_html=True)
c[4].markdown(f"**Data model**<br>{stt['model_data']}", unsafe_allow_html=True)
if stt["errors"]:
    st.warning("Recent service errors: " + " | ".join(stt["errors"]))
locked = not state.presenter() or state.view_only()
engine_locked = locked or not live          # catch-up, speed and incidents act on the running engine
a, b, c3, d = st.columns(4)
if a.button("Re-run catch-up", disabled=engine_locked, help="Advance the simulation to the current time."):
    svc.rerun_catchup()
    st.toast("Catch-up queued")
sp = b.selectbox("LIVE_SPEED", [1, 5, 20], index=[1, 5, 20].index(int(stt["speed"])) if int(stt["speed"]) in (1, 5, 20) else 0, disabled=engine_locked, help=tooltips.tip("live_speed"))
if sp != stt["speed"] and not engine_locked:
    svc.set_speed(sp)
off = c3.toggle("AI offline mode (built-in backup only)", value=stt["offline"], disabled=locked, help=tooltips.tip("degraded_mode"))
if off != stt["offline"] and not locked:
    svc.set_offline(off)
    st.rerun()
with d.popover("Reseed…", disabled=locked):
    st.error("Reseeding regenerates entity profiles and deletes simulated facts. Run `scripts/seed.py --reseed --rebuild` from a terminal.")

st.markdown("#### Inject an incident")
st.caption("Injected incidents are real engine inputs: the feed, the situation board, alerts, dashboards and the assistant all react coherently. " + tooltips.tip("directive"))
cols = st.columns(6)
for col, (kind, label) in zip(cols, [("scanner_outage", "Scanner outage at Apapa"), ("permit_backlog", "NAFDAC permit backlog"), ("bank_delay", "Bank settlement delay"), ("fx_shock", "FX shock"),
                                     ("late_remittance", "Late remittance"), ("duplicate_burst", "Duplicate payments burst")]):
    if col.button(label, disabled=engine_locked, key=f"inj_{kind}", width="stretch"):
        svc.inject(kind)
        st.toast(f"Injected: {label}")
if stt["directive"]:
    st.caption("Current director directives: " + str(stt["directive"]))

st.divider()
tooltips.title("llm_vs_fallback")
conn = db.reader()
gr = pd.read_sql_query("SELECT kind, source, COUNT(*) AS runs, SUM(tokens_in) AS tokens_in, SUM(tokens_out) AS tokens_out FROM generation_runs GROUP BY 1,2", conn) if True else None
rows = pd.read_sql_query("SELECT 'consignments' AS content, source, COUNT(*) AS n FROM v_consignments GROUP BY 2 UNION ALL SELECT 'expenses', source, COUNT(*) FROM v_expenses GROUP BY 2 "
                         "UNION ALL SELECT 'funding receipts', source, COUNT(*) FROM v_funding GROUP BY 2 UNION ALL SELECT 'feed items', source_kind, COUNT(*) FROM v_live_events GROUP BY 2", conn)
col1, col2 = st.columns(2)
with col1:
    st.markdown("**Generated rows by source**")
    if not rows.empty:
        pv = rows.pivot_table(index="content", columns="source", values="n", aggfunc="sum").fillna(0)
        pv["% by AI"] = 100 * pv.get("llm", 0) / pv.sum(axis=1)
        st.dataframe(pv, width="stretch")
with col2:
    st.markdown("**Plans and profiles by source**")
    st.dataframe(gr, hide_index=True, width="stretch")

tooltips.title("cost_ledger")
w = db.connect()
sm = ledger.summary(w)
st.markdown(ledger.format_summary(sm).replace("\n", "  \n"))
df = ledger.ledger(w)
w.close()
if not df.empty:
    st.dataframe(df.drop(columns=["validation_issues"]), hide_index=True, width="stretch")

st.divider()
tooltips.title("beat_viewer")
beats = yaml_config("scenario")["beats"]
al = pd.read_sql_query("SELECT rule_code, entity_id, subject, detected_at FROM v_alerts", conn)
sim0 = clock.sim_start()
rows = []
for bid, b_ in beats.items():
    s, e = clock.iso(sim0 + timedelta(days=b_["start_day"] - 1)), clock.iso(sim0 + timedelta(days=b_["end_day"] + 8))
    fired = al[al["rule_code"].isin(b_["detected_by"]) & (al["detected_at"] >= s) & (al["detected_at"] <= e)] if b_["detected_by"] else pd.DataFrame()
    rows.append({"beat": bid, "name": b_["name"], "starts": clock.fmt_wat(sim0 + timedelta(days=b_["start_day"]), "%d %b"), "ends": clock.fmt_wat(sim0 + timedelta(days=b_["end_day"]), "%d %b"),
                 "detector": ", ".join(b_["detected_by"]) or "none (visible in volume chart)", "detector fired": ("● yes" if len(fired) else "–") if b_["detected_by"] else "n/a",
                 "first detection": clock.fmt_wat(fired["detected_at"].min(), "%d %b %H:%M") if len(fired) else "–"})
st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
st.caption(f"Database: {db_path().name} · {db_path().stat().st_size / 1e9:.2f} GB · schema v{db.SCHEMA_VERSION}")
cards.disclaimer_footer()
