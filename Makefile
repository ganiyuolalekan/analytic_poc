PY ?= .venv/bin/python
PYTEST ?= .venv/bin/python -m pytest
STREAMLIT ?= .venv/bin/streamlit

.PHONY: db-package db-publish db-status db-unindexed db-index run-local cost setup probe estimate-cost seed run run-live live-on live-off live-status share unshare test test-all eval snapshot restore-demo reset logos lint report variants

setup:
	uv venv --python 3.12 .venv
	uv pip install --python $(PY) -r requirements-dev.txt
	@test -f .env || (cp .env.example .env && echo "Created .env from .env.example - add your key")

probe:
	$(PY) scripts/probe_llm.py

cost:
	$(PY) scripts/cost_report.py

estimate-cost:
	$(PY) scripts/seed.py --estimate-only

seed:
	$(PY) scripts/seed.py

run:
	$(STREAMLIT) run app/main.py

# Live generation is OFF whenever the app starts; run-live starts it ON. Flip it any time from the top bar or with the targets below.
run-live:
	NSW_LIVE_ON_START=1 $(STREAMLIT) run app/main.py

# Two databases: data/nsw.db is INDEXED (what you share); data/nsw_unindexed.db is about half the size and quick to copy, restore and write into (what you test with).
db-status:
	$(PY) scripts/db_index.py status

db-unindexed:
	$(PY) scripts/db_index.py slim

db-index:
	$(PY) scripts/db_index.py index

# Publish the database to a PRIVATE Hugging Face dataset so a new host can fetch it (see DEPLOY.md). db-package needs no account; db-publish needs a WRITE token and NSW_DB_REPO.
db-package:
	$(PY) scripts/publish_db.py build

db-publish:
	$(PY) scripts/publish_db.py upload

run-local:
	NSW_DB_PATH=data/nsw_unindexed.db $(STREAMLIT) run app/main.py --server.port 8599

live-on:
	@$(PY) scripts/live.py on

live-off:
	@$(PY) scripts/live.py off

live-status:
	@$(PY) scripts/live.py status

# Share this machine's app with reviewers through ngrok: access code, view-only, daily token cap (see scripts/share.sh)
share:
	@bash scripts/share.sh

# Stop sharing: closes the ngrok tunnel and the shared app (your normal `make run` instance is left running)
unshare:
	-@pkill -f "scripts/share.sh"
	-@pkill -f "ngrok http"
	-@pkill -f "streamlit run app/main.py --server.address 127.0.0.1"
	@echo "sharing stopped"

test:
	$(PYTEST) -q -m "not slow"

test-all:
	$(PYTEST) -q

eval:
	$(PY) scripts/run_assistant_eval.py

variants:
	$(PY) scripts/gen_question_variants.py

snapshot:
	$(PY) scripts/snapshot.py

restore-demo:
	$(PY) scripts/snapshot.py --restore

reset:
	@read -p "Delete data/ (database + LLM cache)? [y/N] " a; if [ "$$a" = "y" ]; then rm -rf data; echo "data/ removed"; fi

logos:
	$(PY) scripts/check_logos.py

lint:
	.venv/bin/ruff check nsw_sim app scripts tests
	.venv/bin/ruff format --check nsw_sim app scripts tests

report:
	$(PY) scripts/make_share_report.py
