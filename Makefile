PY ?= .venv/bin/python
PYTEST ?= .venv/bin/python -m pytest
STREAMLIT ?= .venv/bin/streamlit

.PHONY: cost setup probe estimate-cost seed run test test-all eval snapshot restore-demo reset logos lint report variants

setup:
	uv venv --python 3.12 .venv
	uv pip install --python $(PY) -r requirements.txt
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
