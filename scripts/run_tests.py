#!/usr/bin/env python
"""Run the whole test suite (fast + slow) and write reports/test_summary.md."""
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

if __name__ == "__main__":
    t0 = time.time()
    p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--tb=short", "-rA"], cwd=ROOT, capture_output=True, text=True)
    out = p.stdout + p.stderr
    summary = out.strip().splitlines()[-1] if out.strip() else "no output"
    passed = re.findall(r"^PASSED (\S+)", out, re.M)
    failed = re.findall(r"^FAILED (\S+)", out, re.M)
    by_file: dict[str, list[int]] = {}
    for t in passed:
        by_file.setdefault(t.split("::")[0], [0, 0])[0] += 1
    for t in failed:
        by_file.setdefault(t.split("::")[0], [0, 0])[1] += 1
    md = ["# Test summary\n", f"Run at {time.strftime('%Y-%m-%d %H:%M:%S')} · {time.time() - t0:.0f} s · `{summary}`\n", f"**{len(passed)} passed, {len(failed)} failed** (fast and slow tests; slow tests run against `data/nsw.db`).\n",
          "| test file | passed | failed |", "|---|---|---|"]
    md += [f"| {f} | {v[0]} | {v[1]} |" for f, v in sorted(by_file.items())]
    if failed:
        md += ["\n## Failures\n", *[f"- {f}" for f in failed], "\n```\n" + out[-3000:] + "\n```"]
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "reports" / "test_summary.md").write_text("\n".join(md))
    print("\n".join(md[:3]))
    sys.exit(p.returncode)
