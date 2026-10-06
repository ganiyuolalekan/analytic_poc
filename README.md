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
make eval         # live assistant evaluation (65 golden questions, independent oracle) -> reports/assistant_eval.md (a subset run via --ids writes assistant_eval_partial.* instead)
make run          # streamlit run app/main.py  (live generation starts OFF: the data stays frozen at its last data time)
make run-live     # same, but starts with live generation ON (catches up to now, then keeps generating)
make live-on      # switch live generation ON in a running app (from any terminal)
make live-off     # switch it OFF again: no generation, no model calls for it
make live-status  # switch state, whether an app is running, and how old the data is
make cost         # model usage ledger (tokens by role/model/day)
```
Other targets: `make snapshot` / `make restore-demo` (emergency restore of a known-good DB), `make logos` (logo matching report), `make report` (share report), `make lint`, `make reset`.

Offline: `NSW_OFFLINE=1 make run` works end to end (deterministic fallback for every model role). Rebuild facts without re-spending model tokens:
`.venv/bin/python scripts/seed.py --rebuild`.

## Two databases: indexed to share, unindexed to test with
`data/nsw.db` is **indexed** (44 chosen indexes plus planner statistics, about 1.6 GB): reads are fastest, so it is the one the shared link serves. `data/nsw_unindexed.db` is the same data without those indexes
(about 0.9 GB): quicker to copy, back up and write into, and a little slower to read (measured on this data: clearance statistics 1.6 s becomes 7.4 s, Entity Explorer 3.7 s becomes 5.5 s, a Trace search 2.2 s becomes 3.5 s,
the landing page is unchanged).
```
make db-status       # shows whether each file is indexed, its size and whether statistics are present
make db-unindexed    # (re)makes data/nsw_unindexed.db from data/nsw.db: an online backup, so a running app is not disturbed (7 s)
make db-index        # (re)builds the indexes and statistics on data/nsw.db (15 s on the unindexed copy of this data)
make run-local       # your own copy of the app on the unindexed file (port 8599, full controls, its own live-generation switch)
```
Indexes can be rebuilt on any unindexed copy at any time, so nothing is lost by testing on the slim file.

## Deploying on Streamlit Community Cloud
The code is public on GitHub; the data goes in a **private Hugging Face dataset** that the app downloads on first start. `make db-package` and `make db-publish` prepare and upload it; `DEPLOY.md` has the
full steps (tokens, secrets, Streamlit settings, looking after it; `make streamlit-secrets` prints the Secrets box from your `.env`) and `.streamlit/secrets.toml.example` is the template for Streamlit's Secrets box.

## Sharing the app for review (free, from your own machine)
The database (1.6 GB) stays on your machine, so there is nothing to upload or rebuild. `make share` starts the app and an ngrok tunnel together:
```
make share                                         # asks for an access code (hidden), starts the app, then ngrok
NGROK_DOMAIN=your-name.ngrok-free.app make share    # your free static dev domain, so the link never changes
TOKEN_CAP=300000 make share                        # daily AI usage cap for the Assistant (default 1,000,000)
```
Give reviewers the `https://...ngrok-free.app` link and the access code. On the free plan they click ngrok's one-time "Visit Site" page; the plan allows 1 GB and 20,000 requests a month.
Shared mode is deliberately locked down: the app listens on 127.0.0.1 only (reachable only through the tunnel); an **access code** is required on every page, including direct page URLs;
it is **view-only** (no live-generation switch for viewers; you still have it with `make live-on`); the **technical pages** (Architecture & Integration, Admin / Generation Control) and every engine detail are hidden, and a direct address shows only a notice, because the audience is not technical and does not need the "how"; and the AI assistant has a **daily usage cap**.
Reviewers can still use the Assistant (within the cap) and act on alerts (acknowledge, resolve), which writes to the shared demo database. Keep the terminal open and the Mac awake (the script holds it awake while running); Ctrl+C stops the tunnel and the app.
Running `make run` for your own use is unchanged: no code, full controls. Environment switches behind this: `NSW_ACCESS_CODE`, `NSW_VIEW_ONLY=1`, `NSW_TOKEN_BUDGET_DAY`.

## Pages
A reviewer's sidebar shows **Home, Entity Explorer, Trace Workbench and Supervision** only, and the AI chat is on every page (inline on Home, an **Ask AI** button elsewhere). On the presenter's own machine the other pages sit under "More pages (presenter)" (switch them off with *Show all pages*). The sidebar filters are in one collapsed *Filters* panel.

| # | Page | Need | One line |
|---|---|---|---|
| 0 | Home | all | the landing page: a hero with the AI chat (two dropdowns for agency and period, a first question filled in, one-click suggestions), three number cards (coming in, going out, still to come in), a money-in versus money-out chart, who collects what, agency tiles and links to the three sections |
| 1 | Command Center | speed, trust | KPI strip with deltas, live feed, last-hour money by agency, agency tiles, origin map, situation board |
| 2 | Clearance Journey | speed | stage waterfall, digital vs physical, percentiles, target tracker with projection, bottlenecks, cost of delay |
| 3 | Entity Explorer | trust | logo grid; overview; four statements with trace buttons; processes; ledger; funding and expenses; scorecard |
| 4 | Trace Workbench | trust | search any reference; swim-lane timeline; money Sankey; ledger trace; statement line > accounts > entries > documents > consignment |
| 5 | Reconciliation | trust | four-way funnel, exceptions, leakage heatmap, ageing, duplicates, in-transit, variance bridge |
| 6 | Supervision | supervision | alerts with "why was this raised", review queue, roles, scorecards, remittance monitor, audit |
| 7 | Reports | speed, trust | compute any period (hour to year), narrative with verifier, fingerprint, PDF/Excel/CSV/HTML exports, sign-off |
| 8 | Assistant | all | the full-page chat: answers carry an answer-check badge (Checked / Partly checked / Not checked) and "How this was worked out"; the same conversation is behind the **Ask AI** button on every other page |
| 9 | Data Quality & Onboarding | trust | confidence score components, trends, field completeness |
| 10 | Architecture & Integration | trust | presenter only: read-only integration diagram, hosting toggle, phase roadmap |
| 11 | Admin / Generation Control | supervision | presenter only: status, AI vs backup share, AI usage, presenter controls, incident injection, story-beat viewer |
| 12 | Methodology & Glossary | trust | definitions, simulated-ness, benchmarks, searchable glossary |

## 8-minute demo flow (each step tied to a need)
1. **Command Center (speed, trust):** "the NSW channel, live": feed, today's running total vs yesterday, logos.
2. **Clearance Journey (speed):** stage waterfall and digital vs physical: where time is lost, what the NSW controls. Target tracker: median dwell 15 to about 10.5 days.
3. **Inject an incident (supervision):** switch on *Presenter mode*, Admin > *Scanner outage at Apapa*; watch the feed, situation board and (at LIVE_SPEED 5 or 20) the exam-wait alert and dwell react.
4. **Entity Explorer > NCS or NAFDAC (trust):** statements, funding by country, process-by-country; click a statement line to **trace** it down to a consignment.
5. **Reconciliation (trust):** four-way match; open the under-assessment cluster (Electronics from China, early Sep): "this is revenue assurance".
6. **Supervision (supervision):** alert > review > assign > resolve; remittance monitor shows the late remittance (SON, September); scorecards.
7. **Reports (speed, trust):** "Since 1 July", compute in seconds, show the fingerprint, export PDF.
8. **Assistant (all):** ask three questions, open "How I computed this", note the Verified badge. Presenter only: finish on **Architecture** ("read-only, no disruption") if the room is technical; it is not in the review link.

Before the meeting (30 minutes ahead): `make probe` > `make seed` > `make logos` > `make test` > `make run`; header should show "AI assistant ready", "Data frozen at …" (generation is off by default) and logos present. Use the top-bar toggle or `make live-on` if you want the data time advancing live, and switch it off again to keep the numbers still while presenting. Keep `data/demo_snapshot.db` (`make snapshot`) for emergencies.

## Story beats (the engine guarantees these exist and detectors fire)
B1 dwell 15 to about 10.5 days, digital share 38% to 18% · B2 Apapa scanner outage 12-15 Aug (R-PHYS-01) · B3 NAFDAC permit backlog 24-31 Aug (R-SLA-01) · B4 FX +6% over 5 days early Sep (R-FX-01) ·
B5 SON remits 9 days late (R-REM-01) · B6 Electronics from China under-assessed about 12% for 6 days (R-FEE-01) · B7 Bank C T+3 17-19 Sep (R-SET-01, R-REC-01) ·
B8 14 duplicate payments 2 Oct (R-DUP-01) · B9 FAAN timestamps missing 28-29 Sep (R-DQ-01) · B10 Independence Day volume dip. Admin > *Story beats* shows whether each detector fired.

## Layout
`nsw_sim/` backend (llm, sim, analytics, supervision, assistant) · `app/` Streamlit (components, 13 screens) · `config/` settings, scenario, alert rules, entities, reference data, tooltips ·
`scripts/` probe, seed, calibrate, eval, snapshot, report · `tests/` unit/integration tests and the independent oracle · `reports/` evaluation, tests, acceptance, share report.

## Honesty and limits
Not real data, not official, no claims about real agency performance. Published benchmark figures (dwell 18-21 days, target under 7 days by end 2026, peers) are for calibration: verify before quoting.
