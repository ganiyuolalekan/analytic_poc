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

## Next (in order)
1. Phase 6 leftovers: `app/pages/08_assistant.py`, AppTest + tooltip coverage tests (`tests/test_phase6_ui.py`), replace deprecated `use_container_width`, perf check of page loads
3. Phase 7 assistant: `nsw_sim/assistant/*`, golden questions (60+), `tests/oracle.py`, `scripts/run_assistant_eval.py`, `scripts/gen_question_variants.py`
4. Phase 8: README, snapshot (`scripts/snapshot.py`), acceptance (`reports/acceptance.md`), perf checks
5. Phase 9: `scripts/make_share_report.py` -> `reports/share/`

## Useful commands
- `make probe` · `make seed` (or `scripts/seed.py --rebuild`) · `make test` · `make run` · `make cost`
- DB: `data/nsw.db` (~1.6 GB). Rebuild facts in ~160 s without spending model tokens: `.venv/bin/python scripts/seed.py --rebuild`
