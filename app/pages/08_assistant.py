import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import streamlit as st  # noqa: E402

from app.components import cards, header, logos, state, tooltips  # noqa: E402
from nsw_sim import clock  # noqa: E402
from nsw_sim.assistant.agent import Agent  # noqa: E402
from nsw_sim.llm.client import LLM  # noqa: E402

SUGGESTED = ["What was total assessed vs paid vs settled across all entities in September 2026?", "How much did NCS collect month to date?",
             "Rank the entities by collections in Q3 2026.", "What was median dwell time in July vs September?", "Which stage contributes the most to delay in September?",
             "List high-severity open alerts.", "Which commodity and origin pair shows the biggest assessment shortfall in September?", "What happened in the last 15 minutes?",
             "How did the FX movement in early September change NPA's naira collections?", "How are we doing?"]
BADGE = {"verified": ("● Verified", "#2E7D32", "Every number in this answer matched a tool result."), "partial": ("● Partially verified", "#B7791F", "Some numbers could not be matched to tool results."),
         "unverified": ("● Unverified", "#B03A2E", "The numbers in this answer could not be matched to tool results."), "refused": ("● Declined", "#2F5D9B", "Outside the dataset or protected information."),
         "error": ("● Error", "#B03A2E", "The model was unavailable.")}

header.page_header("Assistant", "Ask questions about the numbers in plain language. The assistant chooses tools, the tools compute every figure from the database, and a verifier checks that "
                   "every number in the answer matches a tool result. Open 'How I computed this' to audit any answer.", ())
ss = st.session_state
ss.setdefault("chat", [])
f = state.filters()
defaults = {"period_label": f"{clock.fmt_wat(f.start, '%d %b %Y')} → {clock.fmt_wat(f.end, '%d %b %Y %H:%M')}" if f.start else None, "filters_desc": f.describe()}
svc = state.service()
agent = Agent(svc.llm, as_of=state.as_of())
st.caption(f"Using the global filters as default context ({defaults['filters_desc']}). Model: {svc.llm.cfg.model_chat if not svc.llm.offline else 'offline (deterministic summaries only)'}. " + tooltips.tip("verified_answer_badge"))

c = st.columns(5)
for i, q in enumerate(SUGGESTED):
    if c[i % 5].button(q, key=f"sg_{i}", width="stretch"):
        ss["assistant_pending"] = q
for m in ss["chat"]:
    with st.chat_message(m["role"]):
        st.markdown(m["content"])
        if m["role"] == "assistant" and m.get("meta"):
            meta = m["meta"]
            label, col, hint = BADGE.get(meta["status"], BADGE["unverified"])
            st.markdown(f"<span style='color:{col};font-weight:700' title='{hint}'>{label}</span> · {meta.get('period') or 'period as stated'} · {meta['latency_ms'] / 1000:.1f}s", unsafe_allow_html=True)
            if meta["unmatched"]:
                st.warning("Figures not matched to a tool result: " + ", ".join(meta["unmatched"]))
            if meta["tool_trace"]:
                with st.expander(tooltips.label("assistant_basis")):
                    st.caption(tooltips.tip("assistant_basis"))
                    for t in meta["tool_trace"]:
                        st.markdown(f"**{t['name']}** `{t['arguments']}` · rows {t['row_count']} · {t['duration_ms']} ms" + ("" if t["ok"] else f" · ✗ {t['error']}"))
                        if t.get("sql_or_formula"):
                            st.code(t["sql_or_formula"], language="sql")
q = st.chat_input("Ask about collections, clearance, reconciliation, alerts…") or ss.pop("assistant_pending", None)
if q:
    ss["chat"].append({"role": "user", "content": q})
    with st.chat_message("user"):
        st.markdown(q)
    with st.chat_message("assistant"):
        box, text, trace_box = st.empty(), "", st.container()
        final = None
        hist = [{"role": m["role"], "content": m["content"]} for m in ss["chat"][:-1]]
        for kind, payload in agent.run(q, hist, defaults):
            if kind == "delta":
                text += payload
                box.markdown(text + "▌")
            elif kind == "tool":
                trace_box.caption(f"tool: {payload['name']} ({payload['row_count']} rows)")
            else:
                final = payload
        box.markdown(final.text)
    ss["chat"].append({"role": "assistant", "content": final.text, "meta": {"status": final.status, "unmatched": final.unmatched, "tool_trace": final.tool_trace, "period": final.period, "latency_ms": final.latency_ms}})
    st.rerun()
cards.disclaimer_footer()
