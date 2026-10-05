import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import streamlit as st  # noqa: E402

from app.components import cards, header, tooltips  # noqa: E402
from nsw_sim import DISCLAIMER  # noqa: E402

header.page_header("Methodology & Glossary", "Definitions, formulas, what is simulated, the published benchmarks used for calibration, and a searchable glossary of every term in the app.", ("All filters",))
st.error(DISCLAIMER)
t1, t2, t3, t4 = st.tabs(["Definitions & formulas", "What is simulated", "Benchmarks & sources", "Glossary"])
with t1:
    st.markdown("""
**Dwell time** = vessel or flight arrival → gate-out. **Clearance time** = declaration filed → customs release. **Release-to-exit** = release → gate-out. **Stage wait** = time between a party being able to start and starting.

**Money chain.** Assessed → Paid → Settled (net of collection costs) → Remitted. Variances are classified as unpaid, part-paid, overpaid, in transit, duplicate, orphan, or mismatch with the fee-rule expectation.

**Ledger.** Every posting is a balanced journal entry (debits = credits). Fee assessed: Dr receivable / Cr revenue. Payment confirmed: Dr funds in transit / Cr receivable. Settlement: Dr bank + collection cost / Cr funds in transit. Remittance obligation: Dr distributions / Cr remittance payable; paid: Dr payable / Cr bank. Statements are computed from the ledger by SQL and never stored.

**As-of rule.** Every query filters on facts that occurred at or before the data time. A fixed as-of reproduces a past view exactly.

**Data confidence score** = 40% completeness + 25% timeliness + 20% consistency + 15% remittance punctuality.

**Projection.** Least-squares line through the last eight complete weekly medians; 95% band from residual error.
""")
with t2:
    st.markdown("""
Everything is synthetic. Specifically simulated approximations: fee names and rates, remittance rules, account codes, identifier formats (reference numbers, permits, container and vessel numbers with valid check digits), settlement banks ("Bank A"–"Bank F"), traders and agents (obviously fictional names), partner facilities (generic names), stage durations, risk lanes, incidents.

The simulation is a discrete-event engine; a language model (OpenAI GPT-5.5 via the configured endpoint) writes entity profiles, weekly flow plans, weekly agency operations plans and the live event feed within code-enforced bounds. Every model output is schema-validated, clamped, repaired once, then replaced by a deterministic fallback; each row records its source. Numbers shown on screen are always computed by code.

Agency names and logos illustrate structure only. Anomalies are process anomalies expressed neutrally (late settlement, permit backlog, fee mismatch), not statements about any real agency.
""")
with t3:
    st.markdown("""
Published figures used for calibration (**verify before quoting**): dwell 18-21 days; global benchmark about 4 days; Ghana 5-7 days; Benin Republic about 4 days; Rwanda 11 days to about 1.5 days; target under 7 days by end 2026; secretariat aspiration 24-48 hours.
Collections scale: public reporting cited roughly ₦12.59bn of regulatory payments facilitated by the NSW in an undated report; customs and tax lines are modelled for the NSW channel only.
""")
with t4:
    tooltips.title("glossary")
    q = st.text_input("Search the glossary", key="gl_q")
    for k, v in tooltips.search(q):
        with st.expander(f"{v['label']}  ·  {k}"):
            st.markdown(v["long"])
cards.disclaimer_footer()
