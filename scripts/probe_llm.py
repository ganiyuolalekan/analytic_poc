#!/usr/bin/env python
"""Run the LLM capability probe (``make probe``). Prints no secrets."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nsw_sim.llm.probe import run_probe, summarize  # noqa: E402

if __name__ == "__main__":
    report = run_probe()
    print(summarize(report))
    for a in report.get("attempts", []):
        print(f"  tried model={a['model']} via {a['base_url_source']}: chat_ok={a['chat_ok']}"
              + (f" ({a['error'][:90]})" if a.get("error") else ""))
    sys.exit(0 if report.get("resolved") else 1)
