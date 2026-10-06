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
- Run the app: `make run` (http://localhost:8501) or `.venv/bin/streamlit run app/main.py --server.port 8599`. **Live generation is OFF by default** (data frozen at its last data time): flip it from the top-bar toggle or `make live-on` / `make live-off` / `make live-status`; `make run-live` starts it ON (see ASSUMPTIONS 23); `data/nsw.db` is ~1.6 GB (`make seed` / `scripts/seed.py --rebuild` rebuilds it).
- Possible follow-ups: Playwright screenshots for the share report; browser-level URL-filter test; dark-mode check; an Admin button for DB snapshot.

## Alert lookup and episode questions (2026-10-06)
- A question that quotes a figure ("12.1% below expectation") or names an alert is about a specific window, not the UI period. New tools `shortfall_episodes` (WHEN a slice was under-assessed, shortfall over the episode and over the whole period) and `get_alert` (any alert by id); the agent re-prompts once when a figure quoted in the question appears in no tool result and otherwise marks the answer *Partially verified* with a note (ASSUMPTIONS 24).
- Supervision > Alert board: "Find an alert by ID" (all statuses, ignores the filters) and `/supervision?alert=ALT-...` deep links; fee-shortfall alerts show a day-by-day evidence chart and table. Resolved alerts stay hidden on the default board (add them to Status).
- Golden questions Q63-Q65 added (episode, alert, nonexistent figure). Verified live: Q63-Q65 plus a 19-question regression subset passed 22/22 (`reports/assistant_eval_partial.md`); the full 62-question run in `reports/assistant_eval.md` pre-dates them and was not repeated.

## Sharing and R-TGT-01 (2026-10-06)
- `make share` serves the app from this machine through ngrok (access code, view-only, token cap; ASSUMPTIONS 26). The ngrok leg itself has not been started or tested end to end.
- R-TGT-01 no longer fires without a real projection and its metric is the fitted dwell days; the spurious ALT-20261005-00001 was dismissed with an audit entry (ASSUMPTIONS 25). The share report (`make report`, uses the model) still says 16 alerts and B1 detected 02 Jul; regenerate it if those figures must match the corrected history.

## Review round 2 (2026-10-06): landing page, plain language, no naira sign
- New landing page `00_home.py`: agency and period dropdowns, AI chat (default question filled in, six one-click suggestions scoped by the dropdowns), money in vs money out chart (complete weeks only) and three numbers. The old Command Center is unchanged apart from the sign.
- `app/components/qa.py` is the one assistant component (landing page, floating **Ask AI** button on every other page, Assistant page). `fmt.py`: figures without the sign, legend, `plain()` for displayed text.
- Plain language for a non-technical audience: AI wording everywhere, technical pages (Architecture, Admin) and engine detail only on the presenter's machine, nothing technical in a shared link. Tests: `tests/test_landing_and_plain_language.py`.

## Review round 3 (2026-10-06, evening): three sections, full width, livelier home, fuller answers
- Sidebar: Home, Entity Explorer, Trace Workbench, Supervision (others under "More pages (presenter)"); filters in one collapsed panel; Ask AI button on every page except Home, where the chat is the hero.
- Page margin cut from 85 px to about 27 px so content fills the width; Home redesigned (hero chat, three number cards, two charts, agency tiles).
- Answers: structured (answer, what is behind it, what it means, a quiet "Based on" line), more thorough, with small charts from the data; the prompt avoids comparing with periods before the data starts.
- Two databases: indexed `data/nsw.db` (shared) and unindexed `data/nsw_unindexed.db` (local testing, `make run-local`); `scripts/db_index.py` and the `db-*` make targets; one-click homepage answers are pre-computed into the live answer cache.
- Streamlit Community Cloud readiness: private Hugging Face dataset for the data (`scripts/publish_db.py`, `nsw_sim/dbpublish.py`, `nsw_sim/dbfetch.py`), secrets-to-environment (`app/components/cloud.py`), fetch progress screen, `DEPLOY.md`, `.streamlit/secrets.toml.example` (the real secrets file is git-ignored), runtime and dev requirements split.
- `make streamlit-secrets` (scripts/streamlit_secrets.py): prints the Streamlit Secrets box from `.env`; refuses placeholders; `ARGS=--mask` / `ARGS=--copy`.
