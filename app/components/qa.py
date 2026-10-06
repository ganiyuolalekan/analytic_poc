"""The AI assistant as one component: the landing page, the key pages and the Assistant page all use it, so one conversation follows the viewer around.
Wording is for a non-technical audience ('AI', never 'model', 'tool' or 'token'); the technical detail appears only when technical pages are switched on."""
from __future__ import annotations

import streamlit as st

from app.components import fmt, state
from nsw_sim import CHAT_FOOTER, clock
from nsw_sim.assistant.agent import Agent

BADGE = {"verified": ("● Checked", "#2E7D32", "Every number in this answer was checked against the data."),
         "partial": ("● Partly checked", "#B7791F", "Some numbers in this answer could not be checked against the data."),
         "unverified": ("● Not checked", "#B03A2E", "The numbers in this answer could not be checked against the data, so treat them with care."),
         "refused": ("● Can't help with that", "#2F5D9B", "That is outside the data, or it is private information."),
         "error": ("● Unavailable", "#B03A2E", "The AI assistant could not be reached. Please try again in a moment.")}
STEPS = {"aggregate": "Added up the figures", "top_n": "Ranked the results", "compare": "Compared two periods", "trend": "Looked at the trend over time", "compute": "Did the arithmetic",
         "resolve_period": "Worked out the dates", "get_reconciliation": "Followed the money from charge to payment to settlement", "get_clearance_stats": "Looked at clearance times",
         "list_alerts": "Looked up the alerts", "get_alert": "Opened the alert", "shortfall_episodes": "Looked for stretches of under-charging", "trace": "Followed one consignment",
         "get_live_snapshot": "Looked at the latest activity", "get_statement": "Read the financial statement", "query_readonly": "Looked up the records"}
ASK = "Ask in your own words, for example: how much did NCS collect this month?"
DEFAULT_PROMPT = "How are we doing?"          # filled in the first time, so a first-time viewer only has to press Ask

# One-click questions for the key pages (wording of the verified test questions, so they answer reliably)
PAGE_STARTERS = {
    "command": [("How are we doing?", "How are we doing?"), ("Last 15 minutes", "What happened in the last 15 minutes?"), ("Top agencies", "Rank the entities by collections since 1 July."),
                ("Serious alerts", "List high-severity open alerts.")],
    "clearance": [("Median clearance time", "What was median dwell time in July vs September 2026?"), ("Biggest delay", "Which stage contributes the most to delay in September 2026 (mean hours per consignment)?"),
                  ("Digital vs physical", "What share of total time is digital versus physical in September 2026?")],
    "entities": [("Agency ranking", "Rank the entities by collections since 1 July."), ("Cost to collect", "Which entity has the highest collection cost per ₦100 in Q3 2026, and what is it?"),
                 ("Fastest growth", "Which entity grew the most in August vs July (collections), and by what percentage?")],
    "reconciliation": [("Money in transit", "How much was paid but not yet settled as at now?"),
                       ("Biggest shortfall", "Which commodity and origin pair shows the biggest assessment shortfall in September 2026, and how large is it?"),
                       ("Duplicate payments", "How many duplicate payments were detected on 2 October 2026 and what value?")],
    "supervision": [("Serious alerts", "List high-severity open alerts."), ("Late remittance", "Which entity has the latest remittance and by how many days?")]}

PAGE_SCOPES = {"command_center": "command", "clearance": "clearance", "entities": "entities", "reconciliation": "reconciliation", "supervision": "supervision"}
GENERAL = [("How are we doing?", "How are we doing?"), ("Top agencies", "Rank the entities by collections since 1 July."), ("Serious alerts", "List high-severity open alerts.")]

# The landing page's period dropdown: label -> how the period reads inside a question
HOME_PERIODS = {"Since 1 July": "since 1 July", "Month to date": "month to date", "Last 7 days": "over the last 7 days", "Yesterday": "yesterday", "Last month": "last month",
                "Quarter to date": "quarter to date"}


def home_starters(agency: str | None, period: str) -> list[tuple[str, str]]:
    """One-click questions for the landing page, scoped by its two dropdowns (agency None = all agencies)."""
    when, who = HOME_PERIODS[period], (f" for {agency}" if agency else "")
    everyone = " (all)" if agency else ""          # clearance times and money in transit are not agency measures: say so, so the answer does not claim to be about one agency
    return [("Collections", f"How much was assessed, paid and settled{who or ' across all entities'}, {when}?"),
            ("Top origins", f"Which 5 origin countries contributed the most assessed fees{who}, {when}?"),
            (f"Clearance speed{everyone}", f"What was the median dwell time {when} across all agencies, and which stage contributes the most to delay?"),
            ("Biggest shortfall", f"Which commodity and origin pair shows the biggest assessment shortfall {when}{who}, and how large is it?"),
            ("Open alerts", f"List the open alerts{who}."),
            (f"Money in transit{everyone}", "How much was paid but not yet settled as at now across all agencies, and which bank holds the most?")]


def context(hint: str = "") -> dict:
    """What the assistant should assume when the question names no period or filters: the viewer's global filters and the page they are on."""
    f = state.filters()
    return {"period_label": f"{clock.fmt_wat(f.start, '%d %b %Y')} → {clock.fmt_wat(f.end, '%d %b %Y %H:%M')}" if f.start else None,
            "filters_desc": f.describe() + (f"; the viewer is looking at the {hint} page" if hint else "")}


def _footer(meta: dict) -> None:
    label, col, hint = BADGE.get(meta["status"], BADGE["unverified"])
    st.markdown(f"<span style='color:{col};font-weight:700' title='{hint}'>{label}</span>", unsafe_allow_html=True)
    if meta["unmatched"]:
        st.warning("These figures could not be checked against the data: " + ", ".join(fmt.plain(u) for u in meta["unmatched"]))
    if meta.get("unreconciled"):
        st.warning("You mentioned " + ", ".join(fmt.plain(u) for u in meta["unreconciled"]) + ", which we could not find in the data. The answer may cover a different period or slice than you mean.")
    if meta["tool_trace"]:
        with st.expander("How this was worked out"):
            for t in meta["tool_trace"]:
                st.markdown(f"- {STEPS.get(t['name'], 'Looked at the data')}" + ("" if t["ok"] else " (this step did not work)"))
            if state.technical():
                for t in meta["tool_trace"]:
                    st.markdown(f"**{t['name']}** `{t['arguments']}` · rows {t['row_count']} · {t['duration_ms']} ms" + ("" if t["ok"] else f" · ✗ {t['error']}"))
                    if t.get("sql_or_formula"):
                        st.code(t["sql_or_formula"], language="sql")


def shown(text: str) -> str:
    """An answer as displayed: no naira sign on figures, and without the 'illustrative data' line (the page already says it)."""
    return fmt.plain(text).replace(CHAT_FOOTER, "").strip()


def show_turn(m: dict) -> None:
    with st.chat_message(m["role"]):
        st.markdown(shown(m["content"]))
        if m["role"] == "assistant" and m.get("meta"):
            _footer(m["meta"])


def ask(question: str, defaults: dict | None = None) -> None:
    """Run the assistant for one question, streaming the answer into the page, and add both turns to the shared conversation."""
    ss = st.session_state
    ss.setdefault("chat", [])
    ss["chat"].append({"role": "user", "content": question})
    show_turn(ss["chat"][-1])
    svc = state.service()
    with st.chat_message("assistant"):
        box, text, steps = st.empty(), "", st.empty()
        box.caption("Looking at the data…")
        final = None
        hist = [{"role": m["role"], "content": m["content"]} for m in ss["chat"][:-1]]
        for kind, payload in Agent(svc.llm, as_of=state.as_of()).run(question, hist, defaults or context()):
            if kind == "delta":
                text += payload
                box.markdown(shown(text) + "▌")
            elif kind == "tool":
                steps.caption(STEPS.get(payload["name"], "Looking at the data") + "…")
            else:
                final = payload
        steps.empty()
        box.markdown(shown(final.text))
        meta = {"status": final.status, "unmatched": final.unmatched, "tool_trace": final.tool_trace, "period": final.period, "latency_ms": final.latency_ms, "unreconciled": final.unreconciled}
        _footer(meta)
    ss["chat"].append({"role": "assistant", "content": final.text, "meta": meta})


def pick(pending_key: str, pills_key: str, questions: dict) -> None:
    ss = st.session_state
    choice = ss.get(pills_key)
    if choice in questions:
        ss[pending_key] = questions[choice]
    ss[pills_key] = None


@st.fragment
def panel(scope: str, starters: list[tuple[str, str]], hint: str = "", defaults: dict | None = None, placeholder: str = ASK, default: str = "") -> None:
    """Question box, one-click questions and the newest answer. A fragment, so asking never redraws or delays the rest of the page."""
    ss = st.session_state
    pending_key, pills_key = f"qa_{scope}_pending", f"qa_{scope}_pills"
    with st.form(f"qa_{scope}_form", clear_on_submit=True, border=False):
        c1, c2 = st.columns([0.84, 0.16], vertical_alignment="center")
        typed = c1.text_input("Your question", value="" if ss.get("chat") else default, placeholder=placeholder, label_visibility="collapsed", key=f"qa_{scope}_text")
        sent = c2.form_submit_button("Ask", type="primary", width="stretch")
    if starters:
        st.pills("Or try one of these", [lbl for lbl, _ in starters], key=pills_key, label_visibility="collapsed", on_change=pick, args=(pending_key, pills_key, dict(starters)))
    question = ss.pop(pending_key, None) or (typed.strip() if sent and typed.strip() else None)
    latest, earlier = st.container(), st.container()
    chat = ss.setdefault("chat", [])
    with latest:
        if question:
            ask(question, defaults or context(hint))
            chat = ss["chat"]
        elif chat:
            for m in chat[-2:]:
                show_turn(m)
    if len(chat) > 2:
        with earlier, st.expander(f"Earlier questions ({len(chat) // 2 - 1})"):
            for m in chat[:-2]:
                show_turn(m)


def floating(page: str) -> None:
    """The 'Ask AI' button pinned to the corner of every page except the landing page and the Assistant page. It opens the same conversation, with questions that suit the page."""
    scope = PAGE_SCOPES.get(page, "general")
    with st.container(key="ai_fab"), st.popover("Ask AI", icon=":material/smart_toy:"):
        st.markdown("**Ask the AI about this page**")
        panel("float", PAGE_STARTERS.get(scope, GENERAL), page.replace("_", " "), default=DEFAULT_PROMPT)
