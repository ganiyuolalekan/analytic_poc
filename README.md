# NSW Intelligence Console (concept demo)

> **SYNTHETIC DATA · UNOFFICIAL CONCEPT DEMO · Not endorsed by any agency.**
> Everything in this app is invented. Agency names and logos only illustrate structure; fee names, rates, IDs and rules are simulated approximations.

A live, traceable, supervised financial view across Nigeria's National Single Window (NSW) agencies, built for a conversation with the NSW secretariat.
It demonstrates three needs: **speed** (clearance journey, target tracker, live feed), **trust** (ledger-backed statements, four-way reconciliation,
trace to a single consignment, verified assistant answers) and **supervision** (rules-based alerts, reviews and sign-off, audit log, scorecards).

**Prime directives:** the language model never does arithmetic that ends up on screen (SQL/Python compute every figure); every model output is schema-validated,
clamped, repaired once, then replaced by a deterministic fallback (each row records `llm` vs `fallback`); the API key is read from `.env` and never printed.

## Model used
OpenAI **GPT-5.5**, reached through AWS Bedrock's OpenAI-compatible endpoint. On this account GPT-5.5 is only served as an *inference profile*
(`us.openai.gpt-5.5`) on `https://bedrock-runtime.<region>.amazonaws.com/openai/v1`; the host in `.env` serves only `gpt-oss` models, so `make probe`
tries (base URL x model) candidates and records the working pair in `data/llm_capabilities.json`. See `ASSUMPTIONS.md`.

## Quick start
```bash
make setup        # venv (uv, Python 3.12) + dependencies; creates .env from .env.example if missing (add your key)
make probe        # capability probe: chooses base URL + model, tests JSON mode, tools, streaming, concurrency
make estimate-cost# expected model calls/tokens for pending work (cached calls are free)
make seed         # profiles + weekly plans (model) then ~125 simulated days of history (~3 min after plans exist); builds data/nsw.db (~1.6 GB)
make test         # fast tests (stub LLM, no network)       | make test-all  # + slow tests against the seeded database
make eval         # live assistant evaluation (62 golden questions, independent oracle) -> reports/assistant_eval.md
make run          # streamlit run app/main.py  (starts the background simulation service; catches up to now, then live)
make cost         # model usage ledger (tokens by role/model/day)
```
Other targets: `make snapshot` / `make restore-demo` (emergency restore of a known-good DB), `make logos` (logo matching report), `make report` (share report), `make lint`, `make reset`.

Offline: `NSW_OFFLINE=1 make run` works end to end (deterministic fallback for every model role). Rebuild facts without re-spending model tokens:
`.venv/bin/python scripts/seed.py --rebuild`.

## Pages
| # | Page | Need | One line |
|---|---|---|---|
| 1 | Command Center | speed, trust | KPI strip with deltas, live feed, last-hour money by agency, agency tiles, origin map, situation board |
| 2 | Clearance Journey | speed | stage waterfall, digital vs physical, percentiles, target tracker with projection, bottlenecks, cost of delay |
| 3 | Entity Explorer | trust | logo grid; overview; four statements with trace buttons; processes; ledger; funding and expenses; scorecard |
| 4 | Trace Workbench | trust | search any reference; swim-lane timeline; money Sankey; ledger trace; statement line > accounts > entries > documents > consignment |
| 5 | Reconciliation | trust | four-way funnel, exceptions, leakage heatmap, ageing, duplicates, in-transit, variance bridge |
| 6 | Supervision | supervision | alerts with "why was this raised", review queue, roles, scorecards, remittance monitor, audit |
| 7 | Reports | speed, trust | compute any period (hour to year), narrative with verifier, fingerprint, PDF/Excel/CSV/HTML exports, sign-off |
| 8 | Assistant | all | tool-using chat with a Verified badge and "How I computed this" (also a drawer on every page) |
| 9 | Data Quality & Onboarding | trust | confidence score components, trends, field completeness |
| 10 | Architecture & Integration | trust | read-only integration diagram, hosting toggle, phase roadmap |
| 11 | Admin / Generation Control | supervision | status, model vs fallback share, cost ledger, presenter controls, incident injection, story-beat viewer |
| 12 | Methodology & Glossary | trust | definitions, simulated-ness, benchmarks, searchable glossary |

## 8-minute demo flow (each step tied to a need)
1. **Command Center (speed, trust):** "the NSW channel, live": feed, today's running total vs yesterday, logos.
2. **Clearance Journey (speed):** stage waterfall and digital vs physical: where time is lost, what the NSW controls. Target tracker: median dwell 15 to about 10.5 days.
3. **Inject an incident (supervision):** switch on *Presenter mode*, Admin > *Scanner outage at Apapa*; watch the feed, situation board and (at LIVE_SPEED 5 or 20) the exam-wait alert and dwell react.
4. **Entity Explorer > NCS or NAFDAC (trust):** statements, funding by country, process-by-country; click a statement line to **trace** it down to a consignment.
5. **Reconciliation (trust):** four-way match; open the under-assessment cluster (Electronics from China, early Sep): "this is revenue assurance".
6. **Supervision (supervision):** alert > review > assign > resolve; remittance monitor shows the late remittance (SON, September); scorecards.
7. **Reports (speed, trust):** "Since 1 July", compute in seconds, show the fingerprint, export PDF.
8. **Assistant (all):** ask three questions, open "How I computed this", note the Verified badge. Finish on **Architecture**: "read-only, no disruption".

Before the meeting (30 minutes ahead): `make probe` > `make seed` > `make logos` > `make test` > `make run`; header should show LLM `live`, the watermark advancing and logos present. Keep `data/demo_snapshot.db` (`make snapshot`) for emergencies.

## Story beats (the engine guarantees these exist and detectors fire)
B1 dwell 15 to about 10.5 days, digital share 38% to 18% · B2 Apapa scanner outage 12-15 Aug (R-PHYS-01) · B3 NAFDAC permit backlog 24-31 Aug (R-SLA-01) · B4 FX +6% over 5 days early Sep (R-FX-01) ·
B5 SON remits 9 days late (R-REM-01) · B6 Electronics from China under-assessed about 12% for 6 days (R-FEE-01) · B7 Bank C T+3 17-19 Sep (R-SET-01, R-REC-01) ·
B8 14 duplicate payments 2 Oct (R-DUP-01) · B9 FAAN timestamps missing 28-29 Sep (R-DQ-01) · B10 Independence Day volume dip. Admin > *Story beats* shows whether each detector fired.

## Layout
`nsw_sim/` backend (llm, sim, analytics, supervision, assistant) · `app/` Streamlit (components, 12 pages) · `config/` settings, scenario, alert rules, entities, reference data, tooltips ·
`scripts/` probe, seed, calibrate, eval, snapshot, report · `tests/` unit/integration tests and the independent oracle · `reports/` evaluation, tests, acceptance, share report.

## Honesty and limits
Not real data, not official, no claims about real agency performance. Published benchmark figures (dwell 18-21 days, target under 7 days by end 2026, peers) are for calibration: verify before quoting.
