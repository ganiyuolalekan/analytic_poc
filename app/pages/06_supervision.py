import json
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
import streamlit as st  # noqa: E402

from app.components import cards, charts, fmt, header, logos, state, tooltips  # noqa: E402
from nsw_sim import clock, db  # noqa: E402
from nsw_sim.analytics import quality, queries, reconcile  # noqa: E402
from nsw_sim.analytics.queries import Filters  # noqa: E402
from nsw_sim.supervision import audit, reviews  # noqa: E402

header.page_header("Supervision", "Supervision: alerts raised by the rules engine with the evidence behind each one, a review queue with ages, role-based actions (analyst, supervisor, "
                   "director) logged to an audit trail, agency scorecards and the remittance monitor.", ("Mode", "Port", "Commodity", "Process", "Currency"))
f = state.filters()
role = state.role()
st.caption(f"Acting as **{reviews.ROLES[role]['name']}**: can " + ", ".join(sorted(reviews.ROLES[role]["can"])) + ". " + tooltips.tip("roles"))
tab_board, tab_queue, tab_score, tab_rem, tab_audit = st.tabs(["Alert board", "Review queue", "Agency scorecards", "Remittance monitor", "Audit timeline"])

def alert_detail(a) -> None:
    """The full panel for one alert (a row of ``queries.alerts`` or ``queries.alert_by_id``): rule, window, evidence, review actions and comments."""
    det = json.loads(a["details_json"] or "{}")
    with st.container(border=True):
        st.markdown(f"{logos.chip(a['entity'])} **{a['alert_id']} · {a['rule_code']}** · severity **{a['severity']}** · status **{a['status']}**", unsafe_allow_html=True)
        st.markdown(fmt.plain(det.get("summary", "")))
        m1, m2, m3 = st.columns(3)
        metric = "n/a (no projection was possible)" if a["rule_code"] == "R-TGT-01" and not a["metric_value"] else f"{a['metric_value']:.3g}"      # 0 was a placeholder
        m1.markdown(f"**Metric** {metric} vs **threshold** {a['threshold']:.3g}")
        m2.markdown(f"**Window** {clock.fmt_wat(a['window_start'], '%d %b %H:%M')} → {clock.fmt_wat(a['window_end'], '%d %b %H:%M')}")
        m3.markdown("**Condition cleared** " + (clock.fmt_wat(a["cleared_at"], "%d %b %H:%M") if isinstance(a["cleared_at"], str) else "not yet"))
        drill = det.get("drill") or {}
        if a["rule_code"] == "R-FEE-01" and drill.get("view") == "assessments":
            fee_evidence(a, drill)
        with st.expander("Why was this raised? Supporting rows", expanded=False):
            st.caption(tooltips.tip("why_raised"))
            rows = det.get("supporting_rows", [])
            st.write(f"{det.get('n_supporting', len(rows))} supporting records; first {len(rows)} shown.")
            refs = {r: r for r in rows if str(r).startswith("NSW-")} or state.cc(queries.assessment_refs, tuple(rows))
            st.dataframe(pd.DataFrame({"record": rows, "consignment": [refs.get(r, "") for r in rows]}), hide_index=True, width="stretch")
            first = next((refs[r] for r in rows if r in refs), None)
            if first and st.button(f"Trace {first}", key=f"sup_trace_{a['alert_id']}"):
                state.goto_trace(first)
            with st.popover("ⓘ What does this rule mean?"):
                st.markdown(tooltips.tip(a["rule_code"], "long"))
        w = db.connect()
        try:
            for c_, (label, action) in zip(st.columns(5), [("Acknowledge", "acknowledge"), ("Under review", "under_review"), ("Assign to me", "assign"), ("Resolve", "resolve"), ("Dismiss", "dismiss")]):
                if c_.button(label, key=f"act_{action}_{a['alert_id']}", disabled=not reviews.can(role, action)):
                    try:
                        reviews.act(w, "alert", a["alert_id"], role, action, st.session_state.get(f"cm_{a['alert_id']}", ""))
                        st.cache_data.clear()
                        st.rerun()
                    except (PermissionError, ValueError) as e:
                        st.error(str(e))
            cm = st.text_input("Comment", key=f"cm_{a['alert_id']}")
            if st.button("Add comment", key=f"addcm_{a['alert_id']}") and cm:
                reviews.act(w, "alert", a["alert_id"], role, "comment", cm)
                st.rerun()
            for r in reviews.comments(w, "alert", a["alert_id"]):
                st.markdown(f"- **{r['reviewer']}** ({r['role']}, {clock.fmt_wat(r['created_at'], '%d %b %H:%M')}): _{r['decision']}_ {r['comment'] or ''}")
        finally:
            w.close()


def fee_evidence(a, drill: dict) -> None:
    """Assessed vs expected for the alert's slice, day by day around the detection, and the episode over which the shortfall ran."""
    t0 = clock.to_dt(a["detected_at"])
    lo, hi = clock.iso(t0 - timedelta(days=10)), min(state.as_of(), clock.iso(t0 + timedelta(days=10)))
    sl = state.cc(reconcile.slice_daily, drill["entity"], drill["fee_code"], drill["commodity"], drill["origin"], lo, hi)
    flt = Filters(start=lo, end=hi, entities=(drill["entity"],), fee_codes=(drill["fee_code"],), commodities=(drill["commodity"],), origins=(drill["origin"],))
    eps = state.cc(reconcile.shortfall_episodes, flt, min_pct=0.05)
    with st.expander("Evidence: assessed vs expected, day by day", expanded=True):
        if not eps.empty:
            day = clock.wat_day(a["detected_at"])
            hit = eps[(eps["start_day"] <= day) & (eps["end_day"] >= day)]
            e = (hit if not hit.empty else eps).iloc[0]
            st.markdown(f"**Episode {clock.fmt_wat(e['start_day'], '%d %b')} → {clock.fmt_wat(e['end_day'], '%d %b')}** ({int(e['days'])} days, {int(e['n'])} assessments): "
                        f"assessed {fmt.ngn(e['assessed_minor'])} against an expected {fmt.ngn(e['expected_minor'])}, **{e['shortfall_pct']:.1%} below expectation** ({fmt.ngn(e['shortfall_minor'])}).")
            st.caption(f"The alert's metric ({a['metric_value']:.1%}) is the shortfall over the rule's trailing window when it first fired, so it is lower than the full episode. "
                       "Over the whole period the same slice is diluted to a fraction of a percent, because the shortfall is confined to these days.")
        if not sl.empty:
            fig = go.Figure(go.Bar(x=sl["day"], y=sl["shortfall_pct"] * 100, marker_color=["#B03A2E" if v > a["threshold"] else "#9aa8a1" for v in sl["shortfall_pct"].fillna(0)]))
            fig.add_hline(y=a["threshold"] * 100, line_dash="dot", annotation_text="alert threshold")
            fig.update_yaxes(title="below expectation (%)")
            charts.show(fig, f"alert_evidence_{a['alert_id']}", 260, legend=False)
            st.dataframe(pd.DataFrame({"day": sl["day"], "assessments": sl["n"], "expected": [fmt.ngn(v) for v in sl["expected"]], "assessed": [fmt.ngn(v) for v in sl["assessed"]],
                                       "shortfall": [fmt.ngn(v) for v in sl["shortfall_minor"]], "below expectation": sl["shortfall_pct"]}), hide_index=True, width="stretch",
                         column_config={"below expectation": st.column_config.NumberColumn(format="percent", help="Expected minus assessed, divided by expected, for the day.")})


with tab_board:
    tooltips.title("alert_board")
    find = st.text_input("Find an alert by ID", value=st.query_params.get("alert", ""), key="sup_find", placeholder="ALT-20260910-00001",
                         help="Searches every alert, including resolved ones; the filters below do not apply. A link such as /supervision?alert=ALT-20260910-00001 opens it directly.")
    if find.strip():
        found = state.cc(queries.alert_by_id, find.strip())
        if found is None:
            st.warning(f"No alert with id {find.strip()} has been raised as of the data time ({clock.fmt_wat(state.as_of())}). Clear the box to browse the board.")
        else:
            alert_detail(found)
    st.markdown("**Browse the board**")
    c1, c2, c3, c4 = st.columns(4)
    sev = c1.multiselect("Severity", ["high", "medium", "low", "info"], default=list(f.severities), key="sup_sev", help=tooltips.tip("filter_severity"))
    stt = c2.multiselect("Status", ["open", "acknowledged", "under_review", "resolved", "dismissed"], default=["open", "acknowledged", "under_review"], key="sup_stt", help=tooltips.tip("review_states"))
    rule = c3.multiselect("Rule", [f"R-{x}" for x in ("SLA-01", "PHYS-01", "FEE-01", "REC-01", "SET-01", "REM-01", "DUP-01", "FX-01", "DQ-01", "CASH-01", "TGT-01", "ONB-01")], key="sup_rule")
    ents = c4.multiselect("Entity", ["NCS", "NRS", "NPA", "NIMASA", "SON", "NAFDAC", "NAQS", "NESREA", "FAAN", "NSW", "CBN"], default=list(f.entities), key="sup_ent")
    al = state.cc(queries.alerts, f.with_(start=None), statuses=tuple(stt), severities=tuple(sev), entities=tuple(ents), rules=tuple(rule))
    if al.empty:
        st.success("No alerts match. Resolved alerts are hidden by default: add 'resolved' to Status, or search by ID above.")
    else:
        view = pd.DataFrame({"logo": [logos.uri(e, 32) for e in al["entity"]], "alert": al["alert_id"], "rule": al["rule_code"], "severity": al["severity"], "entity": al["entity"], "subject": al["subject"],
                             "detected (WAT)": [clock.fmt_wat(t, "%d %b %H:%M") for t in al["detected_at"]], "status": al["status"], "assigned": al["assigned_to"]})
        sel = st.dataframe(view, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row", key="alert_pick", height=300,
                           column_config={"logo": st.column_config.ImageColumn("", width="small"), "severity": st.column_config.TextColumn(help=tooltips.tip("alert_severity")),
                                          "rule": st.column_config.TextColumn(help="Open an alert to read what the rule means.")})
        st.caption(f"{len(al)} alert(s) match. " + ("Resolved and dismissed alerts are hidden by default: add them to Status." if "resolved" not in stt else ""))
        if sel.selection.rows and not find.strip():
            alert_detail(al.iloc[sel.selection.rows[0]])

with tab_queue:
    tooltips.title("review_queue")
    w = db.connect()
    q = reviews.queue(w, state.as_of(), severities=tuple(f.severities), entities=tuple(f.entities))
    w.close()
    qd = pd.DataFrame(q)
    if qd.empty:
        st.success("Nothing awaiting review.")
    else:
        st.dataframe(qd.sort_values("age_h", ascending=False)[["type", "id", "rule", "severity", "entity", "status", "assigned_to", "age_h"]], hide_index=True, width="stretch",
                     column_config={"age_h": st.column_config.NumberColumn("age (h)", format="%.0f", help="SLA age: hours since the item was raised.")})
        by = qd.groupby("severity")["id"].count()
        st.caption("Open items by severity: " + ", ".join(f"{k} {v}" for k, v in by.items()))

with tab_score:
    sc = state.cc(quality.scorecard)
    tooltips.title("data_confidence_score")
    if not sc.empty:
        st.dataframe(pd.DataFrame({"logo": [logos.uri(e, 32) for e in sc["entity"]], "entity": sc["entity"], "score": sc["score"], "status": sc["rag"].map(lambda r: f"{charts.GLYPH[r]} {r}"),
                                   "completeness": sc["completeness"], "timeliness": sc["timeliness"], "consistency": sc["consistency"], "punctuality": sc["punctuality"],
                                   "digital share (onboarding)": sc["digital_share"]}), hide_index=True, width="stretch", column_config={
            "logo": st.column_config.ImageColumn("", width="small"), "score": st.column_config.ProgressColumn("Confidence score", min_value=0, max_value=100, format="%.0f", help=tooltips.tip("data_confidence_score")),
            "completeness": st.column_config.ProgressColumn(min_value=0, max_value=1, format="%.0f%%", help=tooltips.tip("score_completeness")),
            "timeliness": st.column_config.ProgressColumn(min_value=0, max_value=1, format="%.0f%%", help=tooltips.tip("score_timeliness")),
            "consistency": st.column_config.ProgressColumn(min_value=0, max_value=1, format="%.0f%%", help=tooltips.tip("score_consistency")),
            "punctuality": st.column_config.ProgressColumn(min_value=0, max_value=1, format="%.0f%%", help=tooltips.tip("score_punctuality")),
            "digital share (onboarding)": st.column_config.ProgressColumn(min_value=0, max_value=1, format="%.0f%%", help=tooltips.tip("onboarding_score"))})
        st.caption("Weights: 40% completeness, 25% timeliness, 20% consistency, 15% remittance punctuality (illustrative, configurable).")

with tab_rem:
    tooltips.title("remittance_monitor")
    rm = state.cc(queries.remittances, tuple(f.entities))
    if not rm.empty:
        late = rm[(rm["days_late"].fillna(0) > 3) | (rm["days_overdue"].fillna(0) > 3)]
        c = st.columns(3)
        cards.kpi(c[0], "remittance_lateness", str(len(late)), label="Remittances more than 3 days late")
        cards.kpi(c[1], "remitted", fmt.ngn(rm[rm["status"] == "paid"]["amount_ngn_minor"].sum()), label="Paid to date")
        cards.kpi(c[2], "remittance_payable", fmt.ngn(rm[rm["status"] != "paid"]["amount_ngn_minor"].sum()), label="Due, not yet paid")
        st.dataframe(pd.DataFrame({"entity": rm["entity"], "period": rm["period"], "due": [clock.fmt_wat(d, "%d %b") for d in rm["due_date"]], "paid": [clock.fmt_wat(d, "%d %b") if isinstance(d, str) else "–" for d in rm["paid_at"]],
                                   "amount": [fmt.ngn(v) for v in rm["amount_ngn_minor"]], "status": rm["status"], "days late": rm["days_late"].fillna(rm["days_overdue"])}), hide_index=True, width="stretch")

with tab_audit:
    tooltips.title("audit_log")
    w = db.connect()
    tl = audit.timeline(w, limit=200)
    w.close()
    st.dataframe(pd.DataFrame([{"when (WAT)": clock.fmt_wat(t["ts"], "%d %b %H:%M"), "actor": t["actor"], "role": t["role"], "action": t["action"], "target": f"{t['target_type']} {t['target_id']}"} for t in tl]),
                 hide_index=True, width="stretch")

header.methodology_footer([("Alerts", "Rules R-SLA-01 … R-ONB-01 read the as-of views at fixed checkpoints (06:00, 12:00, 18:00 and midnight WAT) in history and every ~30 s live; each stores its metric, threshold, window and supporting rows."),
                           ("Roles", "Analyst can acknowledge and comment; Supervisor can assign, review and resolve; Director can also sign off period reports. Every action writes a review and an audit row."),
                           ("Scorecards", "Data confidence = 40% completeness + 25% timeliness + 20% consistency + 15% remittance punctuality over the last 7 days (120 days for remittances).")])
cards.disclaimer_footer()
