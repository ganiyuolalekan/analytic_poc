# ASSUMPTIONS.md — decisions where `code.md` was silent or where reality differed

Every entry is a choice made without asking, per the brief ("choose the safest reasonable option and record it").

## LLM / environment
1. **Model: OpenAI GPT-5.5** (explicit user instruction) instead of the brief's default `gpt-oss-20b/120b`. Both the data and chat roles use it.
2. **Model ID and endpoint.** The `.env` base URL (`bedrock-mantle…/v1`) lists `openai.gpt-5.5` but rejects it on both Chat Completions and Responses ("isn't supported on this route"); only `gpt-oss` models work there. Bedrock's runtime route accepts GPT-5.5 *only as an inference profile*: `https://bedrock-runtime.<region>.amazonaws.com/openai/v1` with model `us.openai.gpt-5.5` (the same bearer key works). The probe tries (base URL × model) candidates in order and records the winner in `data/llm_capabilities.json`; the region is read from the `.env` host. `.env` itself is never modified.
3. **`.env` variable names detected:** `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `OPENAI_PROJECT_ID` (values never printed). Overrides supported: `LLM_BASE_URL`, `LLM_MODEL`, `LLM_MODEL_DATA`, `LLM_MODEL_CHAT`, `NSW_OFFLINE`.
4. **JSON mode.** `json_schema` strict mode works, but our plan schemas contain free-form maps (e.g. share by country), which strict mode cannot express. The client therefore uses `json_object` + a compact shape in the prompt + pydantic validation.
5. **Reasoning tokens.** GPT-5.x bills hidden reasoning inside the completion budget, so `max_completion_tokens = spec cap × reasoning_token_headroom (3)`. The per-run token budget was raised from the starter 400k to 1.2M (data role) for the same reason; it is still a hard stop that triggers the deterministic fallback.
6. **Project location.** The repository root is the working folder (`data_analytics_poc/`), not a nested `nsw-console/` folder.
7. **Python.** A uv-managed Python 3.12 venv (`.venv`) is used (brief requires ≥3.11).
8. **Git.** A local repository was initialised so each phase can be committed; nothing is pushed anywhere. `.env`, `data/` are git-ignored.

## Simulation and data
9. **Warm-up.** The engine starts 28 days before 1 July (3 June) so the pipeline is in steady state on 1 July (otherwise the first weeks show an artificially low dwell). Warm-up weeks use deterministic plans (no model call); facts before 1 July exist in the database but all default periods start at 1 July.
10. **Arrivals.** Weekday volume is scaled so the weekly mean equals 450 consignments/day despite weekend and holiday dips. Consignment value medians are scaled by `sim.value_scale` (0.38) so monthly collections land in the brief's bands.
11. **Stage model.** Each stage has a hand-off wait and a processing time (lognormal, medians interpolated from start to end state by progress). SLAs measure processing time only. Office-hours alignment applies to permits, assessment and release, and its share falls with progress (automation), which produces the digital-share story (37.8% to 18.0% in calibration). Permit and exam stages also write a *queued* row at entry with the planned wait, so incidents are visible immediately.
12. **Permit SLA** is 36 h (brief starter: 48 h) so the NAFDAC backlog beat (approval times x2) lifts the breach rate above 15% while baseline stays below.
13. **Remittances** use bounded delays (typical -3..+2 days, plan delay 0..1 day) so only beat B5 (one regulator, 9 days late) is more than 3 days late. NCS and NRS remit 95-96% of net settled collections (the rest is the retained cost-of-collection share that funds operations); regulators remit about 30-35%; NPA and FAAN 50% of quarterly surplus; NSW retains.
14. **FX.** USD-denominated fees are assessed at the assessment-day rate and paid at the payment-day rate; the difference posts to FX gains/losses. The B4 move (+6% over 5 days) is an engine overlay on the model's gentle FX path (base clamped to 1450-1560 so the total stays under 1650).
15. **Duplicates and unmatched payments** sit in an unapplied-receipts liability and are refunded 5-9 days later. Beat B8 is a burst (14 within about 75 minutes) so rule R-DUP-01 (5 in an hour) can fire.
16. **Alerts** are replayed over history at 06:00/12:00/18:00/24:00 WAT checkpoints; alerts whose condition cleared more than 10 days ago are closed with a simulated, clearly labelled review history.
17. **Data-confidence completeness** for agencies that own no stage records (NRS, NIMASA) defaults to 100% and is labelled "fee data only".
18. **Model bands.** Profiles may vary fee rates within x0.6-1.5 of the baseline (structure, bases, currencies and revenue accounts are fixed by code); remittance delays and data-quality incident counts are tightly bounded. Facility names stay generic and stable.
19. **Assistant.** The agent receives a compact schema catalogue of the read-only views, rule-code aliases and tool guidance; tool outputs are wrapped as data. The eval harness accepts alternative legitimate tools per question and any-of oracle values where a question is ambiguous (for example "largest NCS payment" as a single allocation or a whole payment). Paraphrase robustness is reported separately.
20. **Token budgets.** `budget_tokens_per_day` was raised to 10M after the 62-question evaluation (multi-round chat with tool outputs) used about 3.6M tokens in a day; the guard is still a hard stop that triggers fallbacks.
21. **Performance measurement** uses Streamlit AppTest (not a browser). Analytics are cached on a 30-second data-time bucket so the live service's moving watermark does not defeat the cache.
22. **Python 3.11 compatibility** is kept in code (the venv is 3.12).
