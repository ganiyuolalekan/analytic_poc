"""UI: static tooltip-coverage scan, tooltip catalogue integrity, page rendering with AppTest (slow, real DB), filters and logos."""
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"
REQUIRED_FIELDS = ("label", "short", "long", "needs", "sources")
PAGES = sorted((APP / "pages").glob("*.py"))


@pytest.fixture(scope="module")
def tips():
    return yaml.safe_load((ROOT / "config" / "tooltips.yaml").read_text(encoding="utf-8"))


def test_every_tooltip_entry_has_all_fields_and_a_substantive_long(tips):
    for k, v in tips.items():
        for f in REQUIRED_FIELDS:
            assert v.get(f), (k, f)
        assert len(re.findall(r"[.!?](?:\s|$)", v["long"])) >= 4, (k, "long needs at least four sentences")
        assert v["needs"] and set(v["needs"]) <= {"speed", "trust", "supervision"}, k


def test_required_tooltip_keys_exist(tips):
    required = ("assessed paid settled remitted outstanding in_transit collection_efficiency cost_of_collection collection_cost_per_100 refunds fx_effect funds_in_transit remittance_payable "
                "distribution_to_treasury appropriation partner_grants four_way_match variance unpaid part_paid overpaid duplicate_payment orphan_payment assessment_mismatch leakage_indicator expected_amount "
                "dwell_time clearance_time release_to_exit stage_wait handoff_wait p50 p90 sla_breach_rate digital_vs_physical risk_lane scanner_utilisation target_tracker projection_method cost_of_delay "
                "peer_benchmarks data_confidence_score ties_out_check report_fingerprint as_of ledger_trial_balance journal_entry trace_levels verified_answer_badge synthetic_data alert_severity review_states "
                "roles onboarding_score remittance_lateness settlement_lag audit_log origin_country country_sensitivity process_code fee_code commodity_group hs_code partner_country live_feed "
                "events_per_minute live_speed degraded_mode directive compare_previous_period currency_view export_pdf export_excel export_csv export_html").split()
    missing = [k for k in required if k not in tips]
    assert not missing, missing
    for rule in ("R-SLA-01 R-PHYS-01 R-FEE-01 R-REC-01 R-SET-01 R-REM-01 R-DUP-01 R-FX-01 R-DQ-01 R-CASH-01 R-TGT-01 R-ONB-01").split():
        assert rule in tips, rule
    assert sum(1 for k in tips if k.startswith("filter_")) >= 8


def test_every_helper_key_used_in_the_app_exists_in_the_catalogue(tips):
    """Static scan: every literal key passed to the tooltip/kpi helpers must exist; raw st.metric / .metric( is forbidden outside cards.py."""
    pat = re.compile(r"""(?:tooltips\.(?:tip|label|title|info|col)|cards\.(?:kpi|money_kpi)|\bkpi)\(\s*(?:[a-zA-Z_\[\]0-9\.]+,\s*)?["']([A-Za-z0-9_\-]+)["']""")
    bad = []
    for f in [*APP.rglob("*.py")]:
        src = f.read_text(encoding="utf-8")
        for m in pat.finditer(src):
            if m.group(1) not in tips:
                bad.append((f.name, m.group(1)))
        if f.name != "cards.py" and re.search(r"\.metric\(", src):
            bad.append((f.name, "raw .metric( call: use cards.kpi so the metric has help text"))
    assert not bad, bad


def test_every_dataframe_column_config_uses_help_or_catalogue():
    """Columns that show metrics should carry help= text: scan for column_config entries with neither help= nor a catalogue helper."""
    offenders = []
    for f in (APP / "pages").glob("*.py"):
        for m in re.finditer(r"st\.column_config\.(?:Number|Progress)Column\(([^)]*)\)", f.read_text(encoding="utf-8")):
            args = m.group(1)
            if "help=" not in args and "format=" in args and "min_value" in args:
                offenders.append((f.name, args[:60]))
    assert len(offenders) <= 12, offenders        # a few self-explanatory percent bars are allowed; most carry help text


def test_no_emoji_in_ui_source():
    emoji = re.compile("[\U0001F300-\U0001FAFF\u2600-\u26FF]")
    for f in APP.rglob("*.py"):
        assert not emoji.search(f.read_text(encoding="utf-8")), f.name


def test_no_raw_base_table_access_in_app_or_assistant():
    """Section 8.1: UI and assistant read only through analytics/queries (views). Base tables must not appear in FROM/JOIN clauses."""
    base = "consignments inflight stage_events fee_assessments payments payment_allocations settlement_batches settlements remittances funding_receipts expenses refunds chart_of_accounts journal_entries journal_lines live_events alerts reviews audit_log saved_reports sim_state generation_runs llm_calls entities countries parties fx_rates incidents directives".split()
    pat = re.compile(r"\b(?:FROM|JOIN)\s+(" + "|".join(base) + r")\b", re.I)
    offenders = []
    for folder in (APP, ROOT / "nsw_sim" / "assistant"):
        for f in folder.rglob("*.py"):
            for m in pat.finditer(f.read_text(encoding="utf-8")):
                line = f.read_text(encoding="utf-8")[: m.start()].count("\n") + 1
                offenders.append((f.relative_to(ROOT).as_posix(), line, m.group(0)))
    # writes (reviews, saved reports, audit) are allowed where they go through supervision/ or analytics.reports; reads of base tables are not
    allowed = {("app/pages/07_reports.py", "saved_reports"), ("app/pages/11_admin.py", "generation_runs")}
    offenders = [o for o in offenders if (o[0], o[2].split()[-1]) not in allowed]
    assert not offenders, offenders


def test_logos_matched_and_fallback_badge_when_missing(monkeypatch, tmp_path):
    from nsw_sim import logos
    logos.refresh()
    assert not logos.match_logos()["missing_entities"]
    monkeypatch.setattr(logos, "LOGO_DIR", tmp_path)
    logos.refresh()
    assert len(logos.match_logos()["missing_entities"]) == 12
    assert logos.logo_data_uri("NCS").startswith("data:image/svg+xml")
    logos.refresh()


@pytest.mark.slow
@pytest.mark.parametrize("page", PAGES, ids=[p.name for p in PAGES])
def test_page_renders_without_exception_against_real_data(page, monkeypatch):
    real = ROOT / "data" / "nsw.db"
    if not real.exists():
        pytest.skip("run `make seed` first")
    from streamlit.testing.v1 import AppTest

    from nsw_sim import db
    monkeypatch.setenv("NSW_DB_PATH", str(real))
    db.close_reader()
    at = AppTest.from_file(str(page), default_timeout=180).run()
    assert not at.exception, [e.value[:300] for e in at.exception]


@pytest.mark.slow
def test_filters_change_outputs_and_pages_work_without_logos(monkeypatch, tmp_path):
    real = ROOT / "data" / "nsw.db"
    if not real.exists():
        pytest.skip("run `make seed` first")
    from streamlit.testing.v1 import AppTest

    from nsw_sim import db, logos
    monkeypatch.setenv("NSW_DB_PATH", str(real))
    db.close_reader()
    at = AppTest.from_file(str(PAGES[0]), default_timeout=180)
    at.session_state["f_entities"] = ["NPA"]
    at.run()
    assert not at.exception
    v_npa = [m.value for m in at.metric if m.label == "Assessed"][0]
    at2 = AppTest.from_file(str(PAGES[0]), default_timeout=180)
    at2.session_state["f_entities"] = ["NCS"]
    at2.run()
    v_ncs = [m.value for m in at2.metric if m.label == "Assessed"][0]
    assert v_npa != v_ncs
    monkeypatch.setattr(logos, "LOGO_DIR", tmp_path)
    logos.refresh()
    at3 = AppTest.from_file(str(PAGES[2]), default_timeout=180).run()          # Entity Explorer without any logo files
    assert not at3.exception
    logos.refresh()
