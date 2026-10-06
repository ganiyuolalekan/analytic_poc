"""Review round: no currency sign on figures (one legend instead), the AI on the landing page and as a floating button, plain wording for a non-technical audience,
and engine detail kept out of a shared review link."""
import ast
import re
from pathlib import Path

import pytest
import yaml

from app.components import fmt, qa
from nsw_sim import db
from nsw_sim.assistant import tools
from nsw_sim.assistant.agent import AnswerResult

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"
REAL = ROOT / "data" / "nsw.db"
TECHNICAL_PAGES = {"10_architecture.py", "11_admin.py"}


def test_ngn_has_no_currency_sign_but_keeps_the_scale_and_the_sign_of_the_number():
    assert fmt.ngn(123456789012) == "1.23bn" and fmt.ngn(-4_500_000_00) == "-4.5m" and fmt.ngn(1234567, exact=True) == "12,345.67"
    assert fmt.ngn(250_000_00, signed=True) == "+250,000" and fmt.ngn(None) == "–"
    assert "₦" not in fmt.ngn(10 ** 14)


def test_plain_drops_the_naira_sign_from_figures_only():
    assert fmt.plain("assessed ₦3.27bn vs expected ₦3.30bn") == "assessed 3.27bn vs expected 3.30bn"
    assert fmt.plain("down -₦1,000 and ₦0") == "down -1,000 and 0"
    assert fmt.plain("amounts in ₦ are shown") == "amounts in ₦ are shown"                      # a sign that is not on a figure is left alone
    assert fmt.plain(None) == ""


def test_the_legend_follows_the_currency_view():
    from streamlit.testing.v1 import AppTest
    script = "import streamlit as st\nfrom app.components import fmt\nst.write(fmt.legend())"
    assert "naira" in AppTest.from_string(script).run().markdown[0].value
    at = AppTest.from_string(script)
    at.session_state["f_ccy"] = "USD"
    assert "US dollars" in at.run().markdown[0].value


def test_no_naira_sign_in_the_ui_source_except_where_it_is_the_subject():
    offenders = []
    for f in APP.rglob("*.py"):
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if "₦" in line and f.name != "fmt.py" and "collection cost per ₦100" not in line:      # the verified test question is shown through fmt.plain
                offenders.append((f.relative_to(ROOT).as_posix(), n, line.strip()[:80]))
    assert not offenders, offenders


def test_user_facing_text_has_no_ai_jargon():
    banned = re.compile(r"\bLLM\b|language[- ]model|\btokens?\b|deterministic|\bfallback\b|\bverifier\b|\bGPT")
    offenders = []
    for f in APP.rglob("*.py"):
        if f.name in TECHNICAL_PAGES:
            continue
        tree = ast.parse(f.read_text(encoding="utf-8"))
        docstrings = {id(n.body[0].value) for n in ast.walk(tree) if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef)) and n.body and isinstance(n.body[0], ast.Expr)
                      and isinstance(getattr(n.body[0], "value", None), ast.Constant)}
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings and banned.search(node.value):
                offenders.append((f.relative_to(ROOT).as_posix(), node.lineno, node.value[:70]))
    assert not offenders, offenders


def test_glossary_entries_outside_the_engine_terms_use_plain_wording():
    tips = yaml.safe_load((ROOT / "config" / "tooltips.yaml").read_text(encoding="utf-8"))
    engine = set(re.search(r"TECHNICAL_TERMS = \{([^}]*)\}", (APP / "screens" / "12_methodology.py").read_text(encoding="utf-8")).group(1).replace('"', "").replace(" ", "").split(","))
    banned = re.compile(r"\bLLM\b|language[- ]model|\btokens?\b|deterministic|\bverifier\b|₦", re.I)
    offenders = [(k, f) for k, v in tips.items() if k not in engine for f in ("label", "short", "long") if banned.search(v[f])]
    assert not offenders, offenders


def test_home_questions_follow_the_two_dropdowns():
    qs = dict(qa.home_starters("NCS", "Last 7 days"))
    assert "for NCS" in qs["Collections"] and "over the last 7 days" in qs["Collections"] and "for NCS" in qs["Open alerts"]
    assert "across all entities" in dict(qa.home_starters(None, "Since 1 July"))["Collections"]
    assert "across all agencies" in qs["Clearance speed (all)"] and "NCS" not in qs["Clearance speed (all)"]      # not agency measures: the chip and the question say they cover everyone
    assert "across all agencies" in qs["Money in transit (all)"] and "for NCS" in qs["Biggest shortfall"]
    assert "Clearance speed" in dict(qa.home_starters(None, "Since 1 July"))


@pytest.mark.parametrize("period", list(qa.HOME_PERIODS))
def test_every_home_period_phrase_is_understood_by_the_assistant_tools(period):
    asked = dict(qa.home_starters(None, period))["Collections"]
    phrase = asked.split(",", 1)[1].rstrip("?").strip()           # the text after the comma is the period phrase
    res = tools.resolve_period(phrase, "2026-10-05T18:09:00Z")
    assert not res["assumption"], (period, phrase, res)                                          # no "I assumed this month" fallback: the dropdown means what it says


def test_the_sidebar_lists_home_and_three_sections_and_everything_else_is_for_the_presenter():
    main = (APP / "main.py").read_text(encoding="utf-8")
    pages = re.findall(r'\("screens/([\w.]+)", "([^"]+)"\)', main)
    assert pages[0] == ("00_home.py", "Home")
    sections = set(re.search(r"SECTIONS = \{([^}]*)\}", main).group(1).replace('"', "").replace("screens/", "").replace(" ", "").split(","))
    assert sections == {"00_home.py", "03_entities.py", "04_trace.py", "06_supervision.py"}
    assert dict(pages)["03_entities.py"] == "Entity Explorer" and dict(pages)["04_trace.py"] == "Trace Workbench" and dict(pages)["06_supervision.py"] == "Supervision"
    assert 'if p in SECTIONS' in main and 'if p not in SECTIONS] if state.technical()' in main          # the rest is registered only when technical() is true


def test_the_page_fills_the_width_with_a_slim_margin():
    from app.components import header
    assert "max-width: 100% !important" in header.CSS and "padding: 3.2rem 1.6rem 4rem 1.6rem !important" in header.CSS


def test_sidebar_filters_sit_in_one_collapsed_panel(conn):
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_string("from app.components import filters\nfilters.sidebar()", default_timeout=60).run()
    assert not at.exception, [e.value[:200] for e in at.exception]
    assert [e.label for e in at.sidebar.expander] == ["Filters"] and at.sidebar.expander[0].proto.expanded is False
    assert not [w for w in at.sidebar.selectbox if w not in at.sidebar.expander[0].selectbox]       # nothing sits outside the panel


def test_split_basis_separates_the_closing_line_from_the_answer():
    assert qa.split_basis("Paid was 5bn.\n\nBasis: 1 July to 5 October, all agencies.") == ("Paid was 5bn.", "1 July to 5 October, all agencies.")
    assert qa.split_basis("Body\n\n**Basis:** one\ntwo") == ("Body", "one two")
    assert qa.split_basis("No closing line here.") == ("No closing line here.", "")


def test_charts_come_from_tool_results_and_prefer_agencies_over_fee_codes():
    from nsw_sim.assistant.agent import visuals_from
    row = lambda dim, k, v: {dim: k, "value": v, "naira": v, "display": "x"}                                 # noqa: E731
    top_entity = {"ok": True, "data": {"metric": "paid", "group_by": ["entity"], "rows": [row("entity", "NCS", 5e9), row("entity", "NPA", 2e9), row("entity", "SON", 1e9)]}}
    top_fee = {"ok": True, "data": {"metric": "paid", "group_by": ["fee_code"], "rows": [row("fee_code", "NCS-DUTY", 4e9), row("fee_code", "NRS-VAT", 3e9)]}}
    series = {"ok": True, "data": {"metric": "paid", "group_by": ["week"], "series": [row("week", f"2026-07-{d:02d}", 1e9 + d) for d in (6, 13, 20, 27)]}}
    single = {"ok": True, "data": {"metric": "paid", "group_by": ["entity"], "rows": [row("entity", "NCS", 5e9)]}}
    v = visuals_from([top_entity, top_fee, series, single])
    assert [x["kind"] for x in v] == ["bar", "line"] and v[0]["dim"] == "entity" and v[0]["labels"] == ["NCS", "NPA", "SON"]
    assert visuals_from([top_fee])[0]["dim"] == "fee_code"                                                 # fee codes still chart when nothing friendlier was ranked
    assert visuals_from([single, {"ok": False}]) == [] and visuals_from([]) == []
    from app.components import charts
    assert len(charts.answer_chart(v[0]).data) == 1 and len(charts.answer_chart(v[1]).data) == 1

def test_technical_detail_is_never_on_in_a_shared_link_even_if_the_flag_is_forged(monkeypatch):
    from streamlit.testing.v1 import AppTest
    script = "import streamlit as st\nfrom app.components import state\nst.write(str(state.technical()))"
    monkeypatch.setenv("NSW_VIEW_ONLY", "1")
    at = AppTest.from_string(script)
    at.session_state["show_tech"] = True
    assert at.run().markdown[0].value == "False"
    monkeypatch.delenv("NSW_VIEW_ONLY")
    assert AppTest.from_string(script).run().markdown[0].value == "True"


@pytest.mark.parametrize("page", sorted(TECHNICAL_PAGES))
def test_technical_pages_show_only_a_notice_in_a_shared_link(page, conn, monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv("NSW_VIEW_ONLY", "1")
    at = AppTest.from_file(str(APP / "screens" / page), default_timeout=60).run()
    assert not at.exception, [e.value[:200] for e in at.exception]
    assert any("for the presenter" in i.value for i in at.info)
    assert not at.dataframe and not at.metric and not at.radio and not at.get("graphviz_chart")


FAKE = '''
import streamlit as st
from app.components import qa
from nsw_sim.assistant.agent import AnswerResult
class FakeAgent:
    def __init__(self, llm, as_of=None): pass
    def run(self, question, history=None, defaults=None):
        yield ("tool", {"name": "aggregate"})
        yield ("delta", "NCS collected ₦6.39bn")
        yield ("final", AnswerResult("NCS collected ₦6.39bn.\\n\\nBasis: month to date.\\n\\nIllustrative synthetic data.", "verified",
                                     tool_trace=[{"name": "aggregate", "arguments": {"metric": "paid"}, "ok": True, "row_count": 1, "duration_ms": 3, "sql_or_formula": "SELECT 1", "error": None}], period="p",
                                     visuals=[{"kind": "bar", "title": "Paid by agency", "labels": ["NCS", "NPA"], "values": [5e9, 3e9], "money": True, "unit": "", "dim": "entity"}]))
qa.Agent = FakeAgent
qa.ask("How much did NCS collect?", {"period_label": None, "filters_desc": ""})
'''


def test_an_answer_is_shown_without_the_naira_sign_but_stored_with_it_for_the_checker(conn, monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv("NSW_VIEW_ONLY", "1")
    at = AppTest.from_string(FAKE, default_timeout=60).run()
    assert not at.exception, [e.value[:300] for e in at.exception]
    shown = " ".join(m.value for c in at.chat_message for m in c.markdown)
    assert "6.39bn" in shown and "₦" not in shown and "Illustrative synthetic data" not in shown and "Checked" in shown
    assert at.session_state["chat"][-1]["content"].startswith("NCS collected ₦6.39bn")
    assert len(at.get("plotly_chart")) == 1 and any("Paid by agency" in m.value for m in at.markdown)                                 # a chart built from the data sits under the answer
    assert [c.value for c in at.caption if c.value.startswith("Based on")] == ["Based on: month to date."]                           # the closing line is a quiet note, not part of the answer
    assert "Basis" not in shown
    assert any("Added up the figures" in m.value for m in at.markdown)                           # the steps are in plain words
    assert "SELECT 1" not in " ".join(c.value for c in at.code) and "aggregate" not in shown + " ".join(m.value for m in at.markdown)


def test_the_presenter_still_gets_the_step_by_step_detail(conn, monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.delenv("NSW_VIEW_ONLY", raising=False)
    at = AppTest.from_string(FAKE, default_timeout=60).run()
    assert any("SELECT 1" in c.value for c in at.code) and any("aggregate" in m.value for m in at.markdown)


def test_badges_are_in_plain_words():
    assert {v[0] for v in qa.BADGE.values()} == {"● Checked", "● Partly checked", "● Not checked", "● Can't help with that", "● Unavailable"}
    assert set(qa.PAGE_SCOPES.values()) <= set(qa.PAGE_STARTERS)
    assert all(label and q for starters in qa.PAGE_STARTERS.values() for label, q in starters)


@pytest.mark.slow
def test_the_landing_page_asks_the_assistant_with_the_dropdown_choices_as_context(monkeypatch):
    if not REAL.exists():
        pytest.skip("run `make seed` first")
    from streamlit.testing.v1 import AppTest
    seen = {}

    class FakeAgent:
        def __init__(self, llm, as_of=None): pass

        def run(self, question, history=None, defaults=None):
            seen.update(question=question, defaults=defaults)
            yield ("final", AnswerResult("All good: ₦1.00bn.", "verified"))
    monkeypatch.setattr(qa, "Agent", FakeAgent)
    monkeypatch.setenv("NSW_DB_PATH", str(REAL))
    db.close_reader()
    at = AppTest.from_file(str(APP / "screens" / "00_home.py"), default_timeout=180)
    at.session_state["home_agency"], at.session_state["home_period"] = "NCS", "Last 7 days"
    at.run()
    assert not at.exception, [e.value[:300] for e in at.exception]
    assert at.text_input(key="qa_home_text").value == qa.DEFAULT_PROMPT                         # a first-time viewer only has to press Ask
    assert [m.label for m in at.metric] == ["Coming in", "Going out", "Still to come in"]
    next(b for b in at.button if b.label == "Ask").click()
    at.run()
    assert not at.exception, [e.value[:300] for e in at.exception]
    assert seen["question"] == qa.DEFAULT_PROMPT and "NCS" in seen["defaults"]["filters_desc"]
    assert len(at.chat_message) == 2 and "1.00bn" in at.chat_message[1].markdown[0].value and "₦" not in at.chat_message[1].markdown[0].value


def test_a_one_click_question_is_asked_without_the_earlier_turns_but_a_follow_up_keeps_them(conn, monkeypatch):
    from streamlit.testing.v1 import AppTest
    script = FAKE.split("qa.Agent = FakeAgent")[0].replace("def run(self, question, history=None, defaults=None):", "def run(self, question, history=None, defaults=None):\n        seen.append(list(history or []))") + """
seen = st.session_state.setdefault("seen", [])
qa.Agent = FakeAgent
st.session_state.setdefault("chat", [{"role": "user", "content": "earlier"}, {"role": "assistant", "content": "earlier answer", "meta": None}])
qa.ask("one click", None, standalone=True)
qa.ask("follow up", None)
"""
    at = AppTest.from_string(script, default_timeout=60).run()
    assert not at.exception, [e.value[:300] for e in at.exception]
    first, second = at.session_state["seen"]
    assert first == [] and [m["content"] for m in second][:2] == ["earlier", "earlier answer"] and len(second) == 4


def test_the_same_answer_twice_on_one_page_does_not_clash(conn, monkeypatch):
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_string(FAKE.replace('qa.ask("How much did NCS collect?", {"period_label": None, "filters_desc": ""})',
                                          'for _ in range(2):\n    qa.ask("How much did NCS collect?", {"period_label": None, "filters_desc": ""}, standalone=True)'), default_timeout=60).run()
    assert not at.exception, [e.value[:300] for e in at.exception]
    assert len(at.get("plotly_chart")) == 2 and len(at.chat_message) == 4
