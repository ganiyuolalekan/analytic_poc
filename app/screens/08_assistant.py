import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import streamlit as st  # noqa: E402

from app.components import cards, header, qa, state, tooltips  # noqa: E402

SUGGESTED = [("Totals", "What was total assessed vs paid vs settled across all entities in September 2026?"), ("NCS this month", "How much did NCS collect month to date?"),
             ("Agency ranking", "Rank the entities by collections in Q3 2026."), ("Clearance time", "What was median dwell time in July vs September?"),
             ("Biggest delay", "Which stage contributes the most to delay in September?"), ("Serious alerts", "List high-severity open alerts."),
             ("Biggest shortfall", "Which commodity and origin pair shows the biggest assessment shortfall in September?"), ("Last 15 minutes", "What happened in the last 15 minutes?"),
             ("Exchange-rate effect", "How did the FX movement in early September change NPA's naira collections?"), ("How are we doing?", "How are we doing?")]

header.page_header("Assistant", "Ask questions about the numbers in plain language. The AI assistant looks the figures up in the data, and every number in its answer is checked against that data. "
                   "Open 'How this was worked out' under an answer to see the steps.", (), chips=False)
ss = st.session_state
ss.setdefault("chat", [])
svc = state.service()
ctx = qa.context("Assistant")
st.caption(f"Questions use the period you have chosen ({ctx['period_label']}) and any filters in the sidebar, unless you name a period or an agency. " + tooltips.tip("verified_answer_badge")
           + (f" AI model: {svc.llm.cfg.model_chat if not svc.llm.offline else 'offline (built-in summaries only)'}." if state.technical() else ""))
st.pills("Try one of these", [lbl for lbl, _ in SUGGESTED], key="qa_assistant_pills", label_visibility="collapsed", on_change=qa.pick, args=("assistant_pending", "qa_assistant_pills", dict(SUGGESTED)))
for m in ss["chat"]:
    qa.show_turn(m)
q = st.chat_input("Ask about collections, clearance, reconciliation, alerts…") or ss.pop("assistant_pending", None)
if q:
    qa.ask(q, ctx)
    st.rerun()
cards.disclaimer_footer()
