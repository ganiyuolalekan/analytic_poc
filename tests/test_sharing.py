"""Safeguards for sharing the app from the presenter's machine: access code, view-only mode, daily token cap, and the launcher."""
import re
import subprocess
from pathlib import Path

import pytest

from nsw_sim import config, db
from nsw_sim.sim import control

ROOT = Path(__file__).resolve().parent.parent
REAL = ROOT / "data" / "nsw.db"
CODE = "review-code-used-only-in-tests"


def test_access_code_gate_hides_everything_until_the_right_code_is_entered(conn, monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv("NSW_ACCESS_CODE", CODE)
    at = AppTest.from_file(str(ROOT / "app" / "main.py"), default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.text_input(key="_access_code").proto.type == 1                                  # a password field (input is masked)
    assert not any("No simulated data yet" in t.value for t in at.title)                    # nothing behind the gate is rendered
    assert any("SYNTHETIC DATA" in m.value for m in at.markdown)                              # the synthetic-data ribbon is still shown
    at.text_input(key="_access_code").set_value("not-the-code").run()
    assert [e.value for e in at.error] == ["That code is not right."] and not any("No simulated data yet" in t.value for t in at.title)
    at.text_input(key="_access_code").set_value(CODE).run()
    assert any("No simulated data yet" in t.value for t in at.title)                         # through the gate (the empty test database shows its own screen)
    assert not any(CODE in str(m.value) for m in at.markdown)                                 # the code is never echoed back


def test_without_an_access_code_there_is_no_prompt(conn, monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.delenv("NSW_ACCESS_CODE", raising=False)
    at = AppTest.from_string("from app.components import gate\nassert gate.passed()\nimport streamlit as st\nst.write('open')", default_timeout=30).run()
    assert not at.exception and not at.text_input


def test_view_only_mode_has_no_live_generation_switch_and_a_forged_callback_cannot_flip_it(conn, monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv("NSW_VIEW_ONLY", "1")
    at = AppTest.from_string("from app.components import header\nheader.clock_strip()", default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    assert not at.toggle                                                                      # reviewers get a plain top bar: no engine controls at all
    assert any("Data as of" in m.value for m in at.markdown)
    import streamlit as st

    from app.components import header
    st.session_state["live_switch"] = True
    header._flip_live()                                                                        # even a forged callback cannot flip it
    assert control.is_enabled() is False


def test_the_switch_is_live_again_when_view_only_is_off(conn, monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.delenv("NSW_VIEW_ONLY", raising=False)
    at = AppTest.from_string("from app.components import header\nheader.clock_strip()", default_timeout=60).run()
    assert at.toggle[0].disabled is False


def test_token_cap_from_the_environment_overrides_the_daily_budget(monkeypatch):
    monkeypatch.setenv("NSW_TOKEN_BUDGET_DAY", "250000")
    config.settings.cache_clear()
    try:
        assert config.settings()["llm"]["budget_tokens_per_day"] == 250000
    finally:
        monkeypatch.delenv("NSW_TOKEN_BUDGET_DAY")
        config.settings.cache_clear()
    assert config.settings()["llm"]["budget_tokens_per_day"] == 10_000_000                    # the shipped default is untouched


def test_launcher_refuses_to_share_without_an_access_code_and_is_valid_bash():
    script = str(ROOT / "scripts" / "share.sh")
    assert subprocess.run(["bash", "-n", script]).returncode == 0
    r = subprocess.run(["bash", script], stdin=subprocess.DEVNULL, capture_output=True, text=True, env={"PATH": "/usr/bin:/bin", "HOME": str(ROOT), "NSW_ACCESS_CODE": ""}, timeout=30)
    assert r.returncode == 1 and "access code is required" in r.stderr


@pytest.mark.slow
def test_admin_page_is_not_shown_in_view_only_mode_even_in_presenter_mode(monkeypatch):
    if not REAL.exists():
        pytest.skip("run `make seed` first")
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv("NSW_DB_PATH", str(REAL))
    monkeypatch.setenv("NSW_VIEW_ONLY", "1")
    db.close_reader()
    at = AppTest.from_file(str(ROOT / "app" / "screens" / "11_admin.py"), default_timeout=180)
    at.session_state["presenter"] = True
    at.run()
    assert not at.exception, [e.value[:200] for e in at.exception]
    assert any("for the presenter" in i.value for i in at.info)                                # a review link never shows the engine room, even by direct address
    assert not [b for b in at.button if str(b.key or "").startswith("inj_")] and not at.dataframe


@pytest.mark.slow
def test_admin_engine_controls_are_disabled_while_generation_is_off_on_the_presenters_machine(monkeypatch):
    if not REAL.exists():
        pytest.skip("run `make seed` first")
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv("NSW_DB_PATH", str(REAL))
    monkeypatch.delenv("NSW_VIEW_ONLY", raising=False)
    db.close_reader()
    at = AppTest.from_file(str(ROOT / "app" / "screens" / "11_admin.py"), default_timeout=180)
    at.session_state["presenter"] = True
    at.run()
    assert not at.exception, [e.value[:200] for e in at.exception]
    inject = [b for b in at.button if str(b.key or "").startswith("inj_")]
    assert len(inject) == 6 and all(b.disabled for b in inject)


PAGES = sorted((ROOT / "app" / "screens").glob("*.py"))


@pytest.mark.parametrize("page", PAGES, ids=[p.name for p in PAGES])
def test_a_page_opened_directly_still_asks_for_the_access_code_and_shows_no_data(page, conn, monkeypatch):
    """Streamlit serves /supervision etc. straight from the page script, bypassing main.py, so every page must enforce the gate itself."""
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv("NSW_ACCESS_CODE", CODE)
    at = AppTest.from_file(str(page), default_timeout=60).run()
    assert not at.exception, [e.value[:200] for e in at.exception]
    assert at.text_input(key="_access_code").proto.type == 1
    assert not at.dataframe and not at.metric and not at.tabs and not at.get("plotly_chart") and not at.expander


def test_every_page_calls_page_header_before_it_touches_any_data():
    """The gate lives in header.page_header, so no page may query data (or start the service) before calling it."""
    data_call = re.compile(r"(state\.(cc|call|filters|service|as_of|live_as_of|writer)\(|queries\.\w+\(|db\.(reader|connect)\(|reviews\.\w+\(|ledger\.\w+\()")
    for page in PAGES:
        lines = page.read_text(encoding="utf-8").splitlines()
        gate_line = next((i for i, ln in enumerate(lines) if "header.page_header(" in ln), None)
        assert gate_line is not None, f"{page.name} never calls header.page_header"
        early = [ln for ln in lines[:gate_line] if data_call.search(ln) and not ln.lstrip().startswith(("from ", "import ", "#"))]
        assert not early, (page.name, early)
