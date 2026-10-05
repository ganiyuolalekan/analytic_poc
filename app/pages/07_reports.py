import json
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from app.components import cards, header, state, tooltips  # noqa: E402
from nsw_sim import clock, db  # noqa: E402
from nsw_sim.analytics import reports  # noqa: E402
from nsw_sim.config import REPORTS_DIR  # noqa: E402
from nsw_sim.money import fmt_ngn, fmt_pct  # noqa: E402
from nsw_sim.sim.reference import entity_codes  # noqa: E402
from nsw_sim.supervision import reviews  # noqa: E402

header.page_header("Reports", "Compute a period report for any time range (a single hour, a day, the open current day, since 1 July, or custom): executive KPIs, statements per agency and "
                   "consolidated, reconciliation, clearance, alerts and exceptions. The numbers come from SQL only; the optional narrative is written by the model and checked number by number.",
                   ("(parameters below replace the global period)",))
as_of = state.as_of()
f = state.filters()
tooltips.title("report_params")
c1, c2 = st.columns(2)
preset = c1.selectbox("Period", list(state.periods()), index=list(state.periods()).index("Since 1 July"), key="rep_period", help=tooltips.tip("filter_period"))
start, end = state.periods()[preset]
if preset == "Custom range":
    mode = c2.radio("Range unit", ["Dates", "Last N hours"], horizontal=True, key="rep_unit")
    if mode == "Dates":
        d = c2.date_input("Dates (WAT)", value=(clock.wat(as_of).date() - timedelta(days=1), clock.wat(as_of).date()), key="rep_dates")
        if len(d) == 2:
            start, end = clock.iso(clock.wat_midnight_utc(d[0])), clock.iso(clock.wat_midnight_utc(d[1] + timedelta(days=1)))
    else:
        n = c2.number_input("Hours", 1, 2000, 1, key="rep_hours")
        end, start = as_of, clock.iso(clock.to_epoch(as_of) - n * 3600)
ents = st.multiselect("Entities (empty = consolidated and per agency)", entity_codes(), key="rep_ents", format_func=lambda c: c)
secs = st.multiselect("Sections", reports.SECTIONS, default=reports.SECTIONS, format_func=lambda s: reports.SECTION_LABELS[s], key="rep_secs")
c1, c2, c3 = st.columns(3)
compare = c1.toggle("Compare with previous period", value=True, key="rep_cmp", help=tooltips.tip("compare_previous_period"))
narr = c2.toggle("Narrative (model-written, verified)", key="rep_narr", help=tooltips.tip("report_narrative"))
ccy = c3.radio("Currency view", ["NGN", "USD"], horizontal=True, key="rep_ccy", help=tooltips.tip("currency_view"))

if st.button("Compute report", type="primary", key="rep_go"):
    p = reports.ReportParams(start=start or clock.iso(clock.sim_start()), end=min(end or as_of, as_of), entities=tuple(ents), filters={k: list(v) for k, v in
                             (("origins", f.origins), ("modes", f.modes), ("ports", f.ports), ("commodities", f.commodities), ("processes", f.processes)) if v}, compare=compare, sections=tuple(secs), currency_view=ccy)
    with st.spinner("Computing from the ledger and source tables…"):
        import time
        t0 = time.time()
        rep = reports.ReportEngine.compute(p, as_of)
        rep["_seconds"] = time.time() - t0
    st.session_state["report"] = rep
    st.session_state.pop("report_narrative", None)

rep = st.session_state.get("report")
if rep:
    st.success(f"Computed in {rep['_seconds']:.1f} s · as of {clock.fmt_wat(rep['as_of'])} · period {clock.fmt_wat(rep['period'][0], '%d %b %Y %H:%M')} → {clock.fmt_wat(rep['period'][1], '%d %b %Y %H:%M')}")
    st.markdown(f"**Report fingerprint (SHA-256):** `{rep['fingerprint']}`", help=tooltips.tip("report_fingerprint"))
    narrative = None
    if narr:
        try:
            from nsw_sim.assistant import narrator
            nar = st.session_state.get("report_narrative") or narrator.report_narrative(rep)
            st.session_state["report_narrative"] = nar
            st.markdown("#### Executive summary")
            st.markdown(nar["text"])
            st.caption(f"Verification: **{nar['status']}**" + (f" · removed unverifiable sentences: {len(nar['removed'])}" if nar["removed"] else "") + f" · source: {nar['source']}")
            narrative = nar["text"]
        except Exception as e:  # noqa: BLE001
            st.warning(f"Narrative unavailable: {e}")
    if "kpis" in rep:
        k, pv = rep["kpis"]["current"], rep["kpis"]["previous"] or {}
        c = st.columns(5)
        for col, key in zip(c, ["assessed", "paid", "settled", "remitted", "outstanding"]):
            cards.money_kpi(col, key, k[key], pv.get(key))
    for key, label in reports.SECTION_LABELS.items():
        if key not in rep or key == "kpis":
            continue
        with st.expander(label, expanded=key in ("consolidated", "reconciliation")):
            if key == "consolidated":
                perf, pos = rep[key]["performance"], rep[key]["position"]
                st.markdown(f"Revenue {fmt_ngn(perf['total_revenue'])} · expenses {fmt_ngn(perf['total_expenses'])} · surplus {fmt_ngn(perf['surplus'])} · assets {fmt_ngn(pos['total_assets'])} · "
                            + ("<span class='tie-ok'>✓ balance sheet balances</span>" if pos["balances"] else "<span class='tie-bad'>✗ does not balance</span>"), unsafe_allow_html=True)
                st.dataframe(reports._lines_df(perf["revenue"] + perf["expenses"]), hide_index=True, use_container_width=True)
            elif key == "statements":
                for e, s in rep[key].items():
                    st.markdown(f"**{e}**: revenue {fmt_ngn(s['performance']['total_revenue'])}, surplus {fmt_ngn(s['performance']['surplus'])}, assets {fmt_ngn(s['position']['total_assets'])}, closing cash {fmt_ngn(s['cash_flow']['closing'])}")
            elif key == "reconciliation":
                fw = rep[key]["four_way"]
                st.markdown(f"Assessed {fmt_ngn(fw['assessed']['amount'])} → paid {fmt_ngn(fw['paid']['amount'])} → settled {fmt_ngn(fw['settled']['amount'])} → remitted {fmt_ngn(fw['remitted']['amount'])}")
                st.dataframe(rep[key]["summary"], hide_index=True, use_container_width=True)
            elif key == "clearance":
                c = rep[key]
                st.markdown(f"Digital share of time: {fmt_pct(c['digital_share'])}. Projection: {c['projection'].get('status')}" + (f" ({c['projection']['projected_date']})" if c["projection"].get("projected_date") else ""))
                st.dataframe(c["bottlenecks"], hide_index=True, use_container_width=True)
            elif key == "trace_links":
                st.write(rep[key] or "No exceptions in this period.")
            elif isinstance(rep[key], pd.DataFrame):
                st.dataframe(rep[key], hide_index=True, use_container_width=True)
    # exports + save + sign-off
    st.divider()
    tooltips.title("export_pdf", 5)
    c = st.columns(4)
    pdf, xlsx, zipb, html = reports.to_pdf(rep, narrative), reports.to_excel(rep), reports.to_csv_zip(rep), reports.to_html(rep, narrative)
    tag = rep["fingerprint"][:8]
    c[0].download_button("PDF", pdf, f"nsw_report_{tag}.pdf", "application/pdf", help=tooltips.tip("export_pdf"))
    c[1].download_button("Excel", xlsx, f"nsw_report_{tag}.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", help=tooltips.tip("export_excel"))
    c[2].download_button("CSV bundle", zipb, f"nsw_report_{tag}.zip", "application/zip", help=tooltips.tip("export_csv"))
    c[3].download_button("HTML", html, f"nsw_report_{tag}.html", "text/html", help=tooltips.tip("export_html"))
    if st.button("Save report", key="rep_save"):
        out = REPORTS_DIR / "saved"
        out.mkdir(parents=True, exist_ok=True)
        files = {}
        for ext, data in (("pdf", pdf), ("xlsx", xlsx), ("zip", zipb), ("html", html.encode())):
            pth = out / f"nsw_report_{tag}.{ext}"
            pth.write_bytes(data)
            files[ext] = str(pth)
        w = db.connect()
        rid = reports.save_report(w, rep, reviews.ROLES[state.role()]["name"], files)
        w.close()
        st.session_state["saved_report"] = rid
        st.success(f"Saved as {rid}")

st.divider()
st.markdown("#### Saved reports")
w = db.connect()
saved = pd.read_sql_query("SELECT report_id, created_at, creator, fingerprint, status, as_of FROM saved_reports ORDER BY created_at DESC", w)
w.close()
if saved.empty:
    st.caption("No saved reports yet.")
else:
    pick = st.dataframe(saved, hide_index=True, use_container_width=True, on_select="rerun", selection_mode="single-row", key="saved_pick")
    if pick.selection.rows:
        rid = saved.iloc[pick.selection.rows[0]]["report_id"]
        cm = st.text_input("Sign-off comment", key="so_cm")
        if st.button("Director sign-off", key="so_btn"):
            w = db.connect()
            try:
                reviews.sign_off_report(w, rid, state.role(), cm)
                st.success(f"{rid} signed off.")
                st.rerun()
            except PermissionError as e:
                st.error(str(e))
            finally:
                w.close()
header.methodology_footer([("Compute", "ReportEngine.compute runs deterministic SQL over the as-of views; no model is involved. Short ranges and the open current day use journal lines directly."),
                           ("Fingerprint", "SHA-256 of parameters, as-of time, row counts and totals: re-running with the same as-of reproduces it exactly."),
                           ("Exports", "PDF, Excel, CSV bundle and HTML carry the synthetic-data watermark; Excel and CSV include a metadata sheet/file repeating the disclaimer.")])
cards.disclaimer_footer()
