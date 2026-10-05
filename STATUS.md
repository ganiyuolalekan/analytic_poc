# STATUS (resume notes for the build)

Updated: 2026-10-05. Brief: `code.md`. Decisions: `ASSUMPTIONS.md`. Cost log: `reports/build_cost_log.md`.

## Done (all committed)
- Phase 0 scaffold + LLM layer (GPT-5.5 = `us.openai.gpt-5.5` on the Bedrock runtime route; probe in `data/llm_capabilities.json`)
- Phase 1 DB (as_of temp views via `db.reader`), ids, logos, money, clock
- Phase 2 profiles / weekly flow plans / ops plans (validate -> clamp -> repair -> fallback); real plans cached in `data/nsw.db` + `data/llm_cache.sqlite`
- Phase 3 engine + ledger + calibration (`scripts/calibrate.py`): p0 15.0 d / 37.8% digital, p1 10.3 d / 18.0%
- Phase 4 backfill (`scripts/seed.py`, `--rebuild` keeps plans), live service + director (`nsw_sim/sim/service.py`, `director.py`) - service not yet exercised end to end
- Phase 5 analytics (queries, clearance, forecast, quality, statements, reconcile, trace, reports) + supervision (rules, alerts, reviews, audit, replay)

## Also done since: Phase 6 UI (shell, components, 11 pages incl. tooltips.yaml 124 keys via scripts/build_tooltips.py), tests for phases 3/5 + beats (slow)

## Done since: Phase 6 UI complete (all 12 pages, AppTest + tooltip coverage tests pass), Phase 7 assistant built
(nsw_sim/assistant: tools, guardrails, verifier, agent, narrator/digest; live GPT-5.5 smoke questions verified).
Live service verified headless (watermark advances, director writes llm feed items).

## ALL PHASES DONE (0-9). Final state
- Tests: see reports/test_summary.md (all passing); assistant eval 62/62 base, paraphrases ~92%; share report in reports/share/ (bundle + md + pdf); acceptance evidence in reports/acceptance.md.
- Run the app: `make run` (http://localhost:8501) or `.venv/bin/streamlit run app/main.py --server.port 8599`; the service starts on first page load; `data/nsw.db` is ~1.6 GB (`make seed` / `scripts/seed.py --rebuild` rebuilds it).
- Possible follow-ups: Playwright screenshots for the share report; browser-level URL-filter test; dark-mode check; an Admin button for DB snapshot.
