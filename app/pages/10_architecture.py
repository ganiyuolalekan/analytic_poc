import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import streamlit as st  # noqa: E402

from app.components import cards, header, tooltips  # noqa: E402

header.page_header("Architecture & Integration", "How this console would plug into the NSW: read-only connections, a validated ledger and analytics store, a model-agnostic assistant layer, "
                   "role-based views and an audit trail, with no disruption to live operations.", ("All filters (conceptual page)",))
host = st.radio("Hosting", ["Cloud", "In-country", "On-premises"], horizontal=True, key="arch_host", help="Changes where the console and the language model run in the diagram.")
where = {"Cloud": ("Cloud region (private tenant)", "Hosted model endpoint"), "In-country": ("In-country data centre", "In-country model hosting (open-weight option)"),
         "On-premises": ("NSW secretariat data centre", "Local model on secretariat hardware (open-weight)")}[host]
dot = f"""
digraph G {{ rankdir=LR; node [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=12, fillcolor="#F2F5F3", color="#0B5D3B"]; edge [color="#55655d"];
 subgraph cluster_src {{ label="Live NSW and agency systems (unchanged)"; style=dashed; color="#9aa8a1"; NSW [label="NSW platform"]; AG [label="Agency systems\\n(NCS, NRS, NPA, regulators)"]; BK [label="Banks / CBN rail"]; TM [label="Terminals, scanners"]; }}
 NSW -> RO [label="read-only"]; AG -> RO [label="read-only"]; BK -> RO [label="read-only"]; TM -> RO [label="read-only"];
 RO [label="Read-only connectors\\n(no write access)", fillcolor="#fff3d6"];
 RO -> ING [label="events, extracts"];
 ING [label="Ingestion and validation\\n(schema, ranges, de-duplication)"];
 ING -> LED [label="validated"]; ING -> DQ [label="quality scores"];
 subgraph cluster_core {{ label="{where[0]}"; style=filled; color="#e8f1ec"; LED [label="Double-entry ledger and\\nanalytics store (as-of views)"]; DQ [label="Data quality and\\nconfidence scoring"]; RU [label="Supervision rules\\nand alerts"]; AS [label="Assistant (tools + verifier)"]; UI [label="Console (role-based views)"]; AU [label="Audit log"]; }}
 LED -> RU; LED -> AS; LED -> UI; DQ -> UI; RU -> UI; RU -> AU; UI -> AU;
 AS -> LLM [label="language only\\n(no arithmetic)"]; LLM [label="{where[1]}", fillcolor="#e9eef7"];
 UI -> USERS [dir=both]; USERS [label="Director, supervisors, analysts\\n(SSO, roles)", shape=oval, fillcolor="#fff"];
}}"""
st.graphviz_chart(dot, width="stretch")
tooltips.title("architecture")
st.markdown("""
**Talk track.** The console only *reads*: connectors take a copy of events and extracts, so live operations are never touched. Every number is computed by deterministic code over a validated ledger; the language model is used for wording and tool selection, never for arithmetic. Because the model sits behind one interface it can be swapped: a hosted model, an in-country model or a small open-weight model on local hardware.

**Sovereignty.** Data stays in the chosen hosting zone; prompts contain only compact aggregates, not records; the model endpoint is configurable and the whole system runs offline in degraded mode.
""")
c = st.columns(3)
for col, (title, body) in zip(c, [("Phase 1 · Pilot (4-8 weeks)", "One or two agencies, real extracts under NDA, read-only. Prove the ledger ties out and the clock definitions (arrival, release, gate-out)."),
                                   ("Phase 2 · Agency expansion", "Onboard remaining regulators, the settlement rail and terminal/scanner feeds; turn on alerts and review workflows."),
                                   ("Phase 3 · Full consolidation", "All agencies and banks, remittance monitoring, period reports for the Ministry, public benchmarking views.")]):
    with col:
        with st.container(border=True):
            st.markdown(f"**{title}**")
            st.write(body)
cards.disclaimer_footer()
