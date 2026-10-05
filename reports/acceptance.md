# Acceptance checklist (code.md section 17) with evidence

Updated 2026-10-05 18:50. Evidence sources: `reports/test_summary.md` (134 passed, 0 failed on the last full run, fast + slow), `reports/assistant_eval.md`, `reports/share/`, README.
`[x]` = met and evidenced; `[~]` = met with a stated limitation; `[ ]` = not met.

## Data and simulation
- [x] Data exists for every entity from 1 July to now, day by day, no future-dated facts. Evidence: `tests/test_phase5_beats.py::test_every_entity_has_data_every_day_from_sim_start`, `test_phase3_engine.py::test_no_future_dated_facts`; engine warm-up from 3 June (28 days) so the pipeline is in steady state at 1 July (ASSUMPTIONS.md).
- [x] Catch-up from the watermark, then live; re-running does not duplicate. Evidence: `test_watermark_and_idempotent_rerun`, `test_resume_from_inflight_matches_uninterrupted_run`; live service verified headless (watermark advanced every tick, director wrote model feed items).
- [x] Data varies by entity, process and origin country; partner funding tagged by partner country. Evidence: entity profiles (country sensitivity), `funding_receipts` by `source_country` (Entity Explorer > Funding & expenses).
- [x] The model is used for profiles, weekly plans, ops plans, live director, narrator; llm vs fallback share visible in Admin. Evidence: Admin page "Model vs fallback"; generation_runs sources {'fallback': 40, 'llm': 203}; GPT-5.5 (`us.openai.gpt-5.5`).
- [x] Offline mode works end to end; degraded mode shows a banner and recovers. Evidence: `test_offline_mode_runs_the_whole_pipeline...`, `test_network_loss_degrades_then_recovers...`, `NSW_OFFLINE=1`.
- [x] Scale calibration within bands; beats B1-B10 present and detected. Evidence: `test_calibration_monthly_totals_within_bands`, `test_B1 ... test_B10` (12 tests against the seeded DB); Admin story-beat viewer.
- [x] Identifier formats follow section 6 with valid check digits; mapping layer present. Evidence: `test_phase1_core.py` (ISO 6346, IMO, 12-digit refs), `config/nsw_formats.yaml` (field mapping).

## Accounting and trust
- [x] All journals balance; trial balance zero; balance sheet balances per entity and consolidated. Evidence: `test_every_journal_entry_balances...`, `test_balance_sheet_balances...`, `test_balance_sheet_balances_each_entity_and_consolidated`; 990,826 entries, 0 unbalanced.
- [x] Four-way reconciliation and exception classification work; duplicates detected. Evidence: `test_four_way_chain_is_monotone_and_ties`, `test_duplicates_are_detected_as_exceptions`, B8 beat test.
- [x] Any statement line traces to journal entries, source documents and consignment; totals tie out at each level. Evidence: `test_statement_line_trace_ties_out_for_50_lines`.
- [x] Report fingerprint reproducible with the same as_of. Evidence: `test_report_fingerprint_stable_and_exports_carry_disclaimer`.

## App
- [x] Persistent synthetic-data ribbon; watermark on exports. Evidence: ribbon verified in the browser pane; PDF/Excel/CSV/HTML tests check the disclaimer.
- [x] Logos for every entity (badge fallback); `check_logos.py` works. Evidence: `test_all_entities_match_a_logo...`, `test_logos_matched_and_fallback_badge_when_missing`; `make logos` (12/12 matched; Immigration Service file unused).
- [~] Global filters work and are shareable via URL. Filters change outputs (`test_filters_change_outputs...`); URL mirroring is implemented (`st.query_params`) but was not exercised in a browser test.
- [x] Live feed with pre-filled history and streaming updates; pause/resume; links to Trace. Evidence: fragment `run_every=2s`; verified headless; selection opens Trace.
- [x] All 12 pages implemented. Evidence: `test_page_renders_without_exception_against_real_data[*]` (12 pages).
- [x] Period report computes for any range including the open current day; exports PDF/Excel/CSV/HTML. Evidence: `test_report_handles_tiny_ranges_and_open_day`, export tests.
- [x] Reviews: roles, queue, states, comments, sign-off, audit log. Evidence: `test_role_permissions_lifecycle_and_audit`.
- [x] Tooltip coverage test passes; every metric, column, chart, filter and rule has text. Evidence: `test_phase6_ui.py` (124 catalogue entries; static scan of helper keys; raw `.metric(` forbidden).
- [x] Performance budgets met. Evidence: `test_cached_page_loads_meet_the_two_second_budget`, offline backfill under 5 s per simulated day, full backfill about 160 s, report compute within budget. Note: measured with Streamlit AppTest, not a browser.

## Assistant
- [x] Tool calling works natively (per probe). Evidence: `data/llm_capabilities.json` (tools ok, round trip ok); text tool protocol implemented as fallback (not needed with GPT-5.5).
- [x] 60+ golden questions; eval at least 95%; zero unverified numbers in passing answers. Evidence: 62 questions, base pass rate 100%, paraphrase robustness 92.5% over 174 variants (reported separately), passing answers with unverified numbers: 0.
- [x] Out-of-scope and injection tests pass; SQL guard tests pass. Evidence: `test_assistant_golden.py` (12 injection strings rejected, secret requests refused without a model call, seeded injection row treated as data).
- [x] Tool trace and Verified badge visible in the UI. Evidence: Assistant page ("How I computed this"; badge states).

## Model policy and cost
- [x] No `anthropic` import anywhere. Evidence: `test_no_anthropic_imports_anywhere`.
- [x] All runtime and build-time generation goes through the `.env` model; chosen models recorded. Evidence: `data/llm_capabilities.json`, Admin, share report section 5. Claude Code wrote code and tests only.
- [x] Caching, weekly batching, output caps, budget guard; `make estimate-cost` works; cost ledger in Admin and report. Evidence: `llm/cache.py`, `scripts/seed.py --estimate-only`, `make cost`. Dollar cost is not shown because no prices are configured (tokens only).

## Share report
- [x] `make report` produces the bundle with two storylines and a passing number-verification check. Evidence: `reports/share/` (30 insights, 9 charts, verification passed).
- [~] Screenshots: Playwright is not installed here; `reports/share/screenshots/README.md` lists the pages and clicks instead (as the brief allows).

## Hygiene
- [x] `.env` ignored; key never logged; `.env.example` present. Evidence: `.gitignore`, redaction tests, `test_secret_redaction_in_logs_and_helpers`, capability file contains no key.
- [x] README with setup, run, demo flow; ASSUMPTIONS.md; final test and eval reports saved.

## Known limitations
- A test process once started a second simulation writer against the real database (the lock file lived in a per-test data directory); fixed by keying the lock to the database file and disabling the service under tests (`NSW_NO_SERVICE=1`). The database was verified intact afterwards (trial balance zero, no duplicate IDs).
- Admin has no "export DB snapshot" button (use `make snapshot`); reseed is by command line.
- Dark-mode palette was not visually verified.
- Cold (first) loads of heavy pages take several seconds; cached loads meet the 2 s budget.
- Paraphrase robustness (92.5%) is below the base pass rate: transient timeouts under concurrency and genuinely ambiguous rewordings.
