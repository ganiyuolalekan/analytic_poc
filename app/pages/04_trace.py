import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
import streamlit as st  # noqa: E402

from app.components import cards, charts, header, logos, state, tooltips  # noqa: E402
from nsw_sim import clock  # noqa: E402
from nsw_sim.analytics import trace  # noqa: E402
from nsw_sim.money import fmt_ngn  # noqa: E402

header.page_header("Trace Workbench", "Trust: search any reference (NSW reference, declaration, payment reference, container, permit number, journal entry, alert) and see the consignment's "
                   "timeline across agencies, where every naira went, and every ledger posting. Statement lines trace here from the Entity Explorer.", ("All filters (a trace is about one reference)",))
ss = st.session_state
q = st.text_input("Search by reference", value=ss.get("trace_query", ""), placeholder="NSW-202609-NGAPP-0001423, C012345/26, a 12-digit payment reference, a container number, a permit number, JE-… or ALT-…", key="trace_input")
if q != ss.get("trace_query"):
    ss["trace_query"] = q
as_of = state.as_of()
hits = trace.search(q, as_of) if q else []
if not q:
    st.info("Enter a reference above, or open a consignment from the Command Center feed, the Clearance page or a reconciliation exception.")
    recent = state.cc(lambda as_of=None: pd.read_sql_query("SELECT nsw_ref, port, origin_country, commodity_group FROM v_consignments WHERE gate_out_at IS NOT NULL ORDER BY gate_out_at DESC LIMIT 8",
                                                            __import__("nsw_sim.db", fromlist=["db"]).reader(as_of)))
    st.markdown("**Recently completed consignments**")
    for r in recent.itertuples():
        if st.button(f"{r.nsw_ref} · {r.port} · {r.origin_country} · {r.commodity_group}", key=f"rec_{r.nsw_ref}"):
            ss["trace_query"] = r.nsw_ref
            st.rerun()
    st.stop()
if not hits:
    st.warning("No match for that reference at the current data time.")
    st.stop()
if len(hits) > 1:
    choice = st.selectbox("Several matches", [h["label"] for h in hits])
    hit = hits[[h["label"] for h in hits].index(choice)]
else:
    hit = hits[0]
if hit["kind"] == "journal":
    st.markdown(f"**{hit['label']}**")
    s = trace.l3_sources(hit["ref"], as_of)
    st.markdown(("<span class='tie-ok'>✓ ties out</span>" if s["ties"] else "<span class='tie-bad'>✗ mismatch</span>") + " · source documents behind this entry", unsafe_allow_html=True)
    st.dataframe(s["docs"], hide_index=True, width="stretch")
    if s.get("nsw_ref"):
        ss["trace_query"] = s["nsw_ref"]
        st.button("Open the consignment", on_click=lambda: None)
    st.stop()
if hit["kind"] in ("alert", "remittance"):
    st.markdown(f"**{hit['label']}**. Open it on the Supervision page for the full drill-through.")
    st.stop()

ref = hit["ref"]
t = state.cc(trace.consignment_trace, ref)
h = t["consignment"]
st.subheader(f"{ref}")
c = st.columns(5)
c[0].markdown(f"**{h['mode'].title()}** · {h['port']}<br>from {h['origin_country']} · {h['commodity_group']}<br>HS {h['hs_code']}", unsafe_allow_html=True)
c[1].markdown(f"**Importer**<br>{h.get('importer_name') or h['importer_id']}<br>**Agent** {h.get('agent_name') or h['agent_id']}", unsafe_allow_html=True)
c[2].markdown(f"**IDs**<br>Rotation {h['rotation_no']}<br>Form M {h['form_m']} · PAAR {h['paar']}<br>Declaration {h['declaration_no'] or '–'}", unsafe_allow_html=True)
c[3].markdown(f"**Transport**<br>B/L {h['bl_no']}<br>Container {h['container_no'] or 'air cargo'}<br>IMO {h['vessel_imo'] or '–'}", unsafe_allow_html=True)
c[4].markdown(f"**Risk lane** {h['risk_lane']}<br>CIF {fmt_ngn(h['cif_value_ngn_minor'])}<br>Status **{h['status']}**" if 'status' in h else f"**Risk lane** {h['risk_lane']}<br>CIF {fmt_ngn(h['cif_value_ngn_minor'])}", unsafe_allow_html=True)

tabs = st.tabs(["Timeline", "Money trace", "Ledger trace", "Documents & alerts"])
with tabs[0]:
    tooltips.title("stage_waterfall", 5)
    stg = t["stages"][t["stages"]["status"] != "queued"].copy()
    fig = go.Figure()
    if not stg.empty:
        lanes = list(dict.fromkeys(stg["owner_entity"]))
        for r in stg.itertuples():
            if r.started_at is None or pd.isna(r.started_at):
                continue
            s0, s1 = clock.to_dt(r.started_at), clock.to_dt(r.occurred_at)
            fig.add_trace(go.Bar(base=[s0], x=[(s1 - s0).total_seconds() * 1000], y=[r.owner_entity], orientation="h", marker_color=charts.BLUE if r.system_type == "D" else charts.AMBER,
                                 name="Digital" if r.system_type == "D" else "Physical", showlegend=False, text=r.stage, textposition="inside",
                                 hovertemplate=f"{r.stage} {r.owner_entity}<br>{clock.fmt_wat(r.started_at)} → {clock.fmt_wat(r.occurred_at)}<br>wait {r.wait_h:.1f} h · processing {r.dur_h:.1f} h" + (f"<br>SLA {r.sla_hours:g} h {'BREACHED' if r.sla_breach else 'met'}" if r.sla_hours == r.sla_hours and r.sla_hours else "") + "<extra></extra>"))
        fig.update_xaxes(type="date")
        fig.update_layout(barmode="overlay")
    charts.show(fig, "timeline", 360, legend=False)
    st.caption("Blue = digital stages (NSW-controlled), amber = physical stages. Hover for waits, processing time and SLA.")
    show = stg.assign(started=[clock.fmt_wat(x) if isinstance(x, str) else "–" for x in stg["started_at"]], completed=[clock.fmt_wat(x) for x in stg["occurred_at"]])[
        ["stage", "owner_entity", "system_type", "started", "completed", "wait_h", "dur_h", "sla_hours", "status", "doc_no"]]
    st.dataframe(show, hide_index=True, width="stretch", column_config={"wait_h": st.column_config.NumberColumn("wait (h)", format="%.1f", help=tooltips.tip("stage_wait")),
                                                                                "dur_h": st.column_config.NumberColumn("processing (h)", format="%.1f")})
with tabs[1]:
    sk = t["sankey"]
    tooltips.title("four_way_match", 5)
    if sk["labels"]:
        cols = {"paid": "rgba(47,93,155,.45)", "settled": "rgba(11,93,59,.45)", "in transit": "rgba(183,121,31,.55)", "per remittance rule": "rgba(93,64,55,.45)"}
        fig = go.Figure(go.Sankey(node=dict(label=sk["labels"], pad=18, thickness=16, color=[logos.colour(l.split(" ")[0]) if l.split(" ")[0] in logos.ENT else "#9aa8a1" for l in sk["labels"]]),
                                  link=dict(source=sk["source"], target=sk["target"], value=[v / 100 for v in sk["value"]], color=[cols.get(s, "rgba(150,150,150,.4)") for s in sk["status"]],
                                            customdata=[f"{fmt_ngn(v, exact=True)} · {s}" for v, s in zip(sk["value"], sk["status"])], hovertemplate="%{customdata}<extra></extra>")))
        charts.show(fig, "sankey", 420, legend=False)
    rs = t["recon"]
    if not rs.empty:
        st.dataframe(rs.assign(assessed=[fmt_ngn(v) for v in rs["assessed"]], paid=[fmt_ngn(v) for v in rs["paid"]], settled=[fmt_ngn(v) for v in rs["settled"]], cost=[fmt_ngn(v) for v in rs["cost"]]),
                     hide_index=True, width="stretch")
    st.markdown("**Fees by agency**")
    fe = t["fees"]
    st.dataframe(fe.assign(assessed=[fmt_ngn(v, exact=True) for v in fe["assessed_ngn_minor"]], expected=[fmt_ngn(v, exact=True) for v in fe["expected_amount_ngn_minor"]],
                           paid=[fmt_ngn(v, exact=True) for v in fe["paid_ngn_minor"]], settled=[fmt_ngn(v, exact=True) for v in fe["settled_ngn_minor"]])[["entity_id", "fee_code", "assessed", "expected", "paid", "settled", "currency"]],
                 hide_index=True, width="stretch")
    st.markdown("**Payments and settlements**")
    st.dataframe(t["payments"], hide_index=True, width="stretch")
    if not t["settlements"].empty:
        st.dataframe(t["settlements"], hide_index=True, width="stretch")
with tabs[2]:
    je, jl = t["journal_entries"], t["journal_lines"]
    if je.empty:
        st.info("No journal entries yet for this consignment.")
    for ent, g in je.groupby("entity_id"):
        with st.expander(f"{ent} · {len(g)} entries", expanded=ent in ("NCS",)):
            st.markdown(logos.chip(ent), unsafe_allow_html=True)
            for r in g.itertuples():
                ls = jl[jl["entry_id"] == r.entry_id]
                st.markdown(f"`{r.entry_id}` · {clock.fmt_wat(r.occurred_at)} · {r.ref_type}")
                st.dataframe(ls.assign(debit=[fmt_ngn(v, exact=True) if v else "" for v in ls["debit_minor"]], credit=[fmt_ngn(v, exact=True) if v else "" for v in ls["credit_minor"]])[["account_code", "account_name", "debit", "credit"]],
                             hide_index=True, width="stretch")
with tabs[3]:
    docs = t["stages"][t["stages"]["doc_no"].notna()][["stage", "owner_entity", "doc_no"]]
    st.markdown("**Permits and certificates issued**")
    st.dataframe(docs, hide_index=True, width="stretch")
    al = t["alerts"]
    st.markdown("**Linked alerts**")
    st.dataframe(al, hide_index=True, width="stretch") if not al.empty else st.caption("No alerts reference this consignment.")

# exports + link
buf = io.StringIO()
for name in ("stages", "fees", "payments", "settlements", "journal_entries", "journal_lines"):
    buf.write(f"# {name} (SYNTHETIC DATA: UNOFFICIAL CONCEPT DEMO)\n" + t[name].to_csv(index=False) + "\n")
c1, c2 = st.columns(2)
c1.download_button("Export trace (CSV)", buf.getvalue(), file_name=f"trace_{ref}.csv", mime="text/csv", help="CSV of timeline, fees, payments, settlements and journal entries with the synthetic-data disclaimer.")
c2.code(f"?trace={ref}", language="text")
c2.caption("Copy trace link: add this to the app URL.")
header.methodology_footer([("Timeline", "Stage events for this reference; blue is digital, amber is physical; waits and processing times come from the stage records."),
                           ("Money trace", "Assessments, payment allocations and settlements linked by IDs; the Sankey legs are amounts from those tables."),
                           ("Ledger trace", "Journal entries whose reference is this consignment or its settlements; each entry is balanced.")])
cards.disclaimer_footer()
