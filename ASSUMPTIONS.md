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
