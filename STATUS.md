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

## Next (in order)
1. Golden questions + oracle + eval: `nsw_sim/assistant/questions.py`, `tests/oracle.py`, `scripts/run_assistant_eval.py`, `scripts/gen_question_variants.py`, `tests/test_assistant_golden.py` (stub-LLM fast subset), guardrail tests (SQL injection, prompt injection row id 'IGNORE PREVIOUS INSTRUCTIONS' expense memo)
2. Phase 8: README.md (setup/run/demo flow), `scripts/snapshot.py` (+ `make snapshot`/`restore-demo`), `reports/acceptance.md` (checklist in code.md section 17), final `reports/test_summary.md`
3. Phase 9: `scripts/make_share_report.py` -> `reports/share/` (insight miner, 2 storylines drafted by the model, verifier, figures, PDF, zip) and final summary message (section 18.6)
4. Known gaps to revisit if time: page-load perf on cold cache (reconciliation ~7 s), Playwright screenshots for the share report
