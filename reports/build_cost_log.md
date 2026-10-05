# Build cost log

Two separate meters:

1. **Product model usage (GPT-5.5 via the .env endpoint)** — `make cost` (also Admin page + share report). Tokens by role/model/day;
   dollars only if prices are set in `config/settings.yaml` (`llm.llm_prices_per_mtok`). Prices are never guessed.
2. **Claude Code (the builder)** — plan-limit snapshots taken at phase boundaries (the harness exposes these; code cannot).

| When | Phase finished | Claude Code 5-hour window | Weekly (all models) | Context used |
|---|---|---|---|---|
| 2026-10-05 ~17:20 WAT | Phase 0 + Phase 1 (core, LLM layer, DB, ids, logos) | 33% | 20% | 29% |
| 2026-10-05 ~20:00 WAT | All phases (final) | 91% | 30% | 91% (context) |

Product model usage at completion: about 3.6M tokens across ~1,750 calls (mostly the 62-question evaluation); `make cost` for the live ledger.
