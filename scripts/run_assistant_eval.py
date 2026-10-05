#!/usr/bin/env python
"""Automated assistant evaluation (Section 13.6): runs the golden questions through the real agent at a fixed as_of, compares with the independent
oracle, and writes reports/assistant_eval.md + .html. Usage: run_assistant_eval.py [--as-of ISO] [--limit N] [--ids Q01,Q02] [--variants] [--workers 3]"""
import argparse
import html
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import logging  # noqa: E402

from nsw_sim.assistant import verifier  # noqa: E402
from nsw_sim.assistant.agent import Agent  # noqa: E402
from nsw_sim.assistant.questions import GOLDEN  # noqa: E402
from nsw_sim.config import REPORTS_DIR, data_dir  # noqa: E402
from nsw_sim.llm.client import LLM  # noqa: E402
from tests import oracle  # noqa: E402

logging.getLogger().setLevel(logging.ERROR)
PERIOD_WORDS = re.compile(r"(all time|all-time|all available|overall|in total|dataset|data time|january|february|march|april|may|june|july|august|september|october|q[1-4]|since|today|yesterday|last |week|month|to date|2026|now|minutes)", re.I)


def expected_ok(claims, expected: dict, tol: float, raw_text: str = "") -> tuple[bool, list]:
    missing = []
    for label, want in expected.items():
        alts = want if isinstance(want, list) else [want]
        hit = False
        for w in alts:
            for c in claims:
                vals = [c.value]
                if c.kind == "money":
                    pass
                for v in vals:
                    t = max(abs(w) * max(tol, 0.005), (0.5 * 10 ** (-c.decimals)) * (1e9 if re.search(r"bn|billion", c.text, re.I) else 1e6 if re.search(r"\dm\b|million", c.text, re.I) else 1.0 if c.kind == "money" else 1.0) + 1e-9)
                    if abs(v - w) <= t:
                        hit = True
                    if c.kind in ("count", "unit") and abs(v - w) <= max(abs(w) * tol, 0.51 if float(w).is_integer() else 0.051):
                        hit = True
            if hit:
                break
        if not hit and raw_text:
            for w in alts:
                if float(w).is_integer() and abs(w) < 10:
                    words = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
                    if re.search(rf"(?<![\d.,]){int(w)}(?![\d.,]|\s?%)", raw_text) or re.search(rf"\b{words[int(w)]}\b", raw_text, re.I):
                        hit = True
        if not hit:
            missing.append((label, want))
    return not missing, missing


def run_one(q, text: str, as_of: str, llm: LLM, trace_ref: str, c) -> dict:
    qt = text.replace("{TRACE_REF}", trace_ref)
    t0 = time.time()
    res = Agent(llm, as_of=as_of).ask(qt)
    lat = time.time() - t0
    called = {t["name"] for t in res.tool_trace}
    expected = oracle.ORACLES[q.oracle](c) if q.oracle != "none" else {}
    claims = verifier.extract_claims(res.text.replace("Illustrative synthetic data.", ""))
    ok_vals, missing = expected_ok(claims, expected, q.tol, res.text.replace('Illustrative synthetic data.', ''))
    ok_tools = all(any(t in called for t in g) for g in q.tools)
    ok_ver = res.status in ("verified", "refused") if not q.negative or q.id == "Q62" else True
    ok_period = bool(PERIOD_WORDS.search(res.text)) if not q.negative else True
    forbidden = [f for f in q.forbid if f in res.text]
    if q.id == "Q40":
        passed = res.status == "refused" and not forbidden
    elif q.negative:
        passed = not forbidden and all(any(alt.strip().lower() in res.text.lower() for alt in m.split('|')) for m in q.must_mention) and (q.id != "Q62" or ok_vals)
    else:
        passed = ok_vals and ok_tools and ok_ver and ok_period and all(any(alt.strip().lower() in res.text.lower() for alt in m.split('|')) for m in q.must_mention)
    return {"id": q.id, "category": q.category, "question": qt, "answer": res.text, "status": res.status, "expected": expected, "missing": missing, "tools_called": sorted(called),
            "tools_expected": [list(g) for g in q.tools], "ok_values": ok_vals, "ok_tools": ok_tools, "ok_verified": ok_ver, "ok_period": ok_period, "latency_s": round(lat, 1), "passed": bool(passed),
            "unmatched": res.unmatched, "trace": res.tool_trace, "forbidden_hits": forbidden}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", default=oracle.AS_OF)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--ids")
    ap.add_argument("--variants", action="store_true")
    ap.add_argument("--workers", type=int, default=3)
    a = ap.parse_args()
    llm = LLM()
    c = oracle.conn()
    trace_ref = oracle.sample_trace_ref(c)
    qs = [q for q in GOLDEN if not a.ids or q.id in a.ids.split(",")][: a.limit]
    jobs = [(q, q.text, "base") for q in qs]
    if a.variants:
        vf = data_dir() / "question_variants.json"
        v = json.loads(vf.read_text()) if vf.exists() else {}
        jobs += [(q, t, f"variant{i + 1}") for q in qs for i, t in enumerate(v.get(q.id, []))]
    results = []

    def work(j):
        q, text, kind = j
        cc = oracle.conn()
        try:
            r = run_one(q, text, a.as_of, llm, trace_ref, cc)
        finally:
            cc.close()
        r["kind"] = kind
        return r
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        for i, r in enumerate(ex.map(work, jobs)):
            results.append(r)
            print(f"{'PASS' if r['passed'] else 'FAIL'} {r['id']} [{r['kind']}] {r['latency_s']}s status={r['status']} tools={r['tools_called']}" + ("" if r["passed"] else f" missing={r['missing']} unmatched={r['unmatched'][:3]}"), flush=True)
    base = [r for r in results if r["kind"] == "base"]
    var = [r for r in results if r["kind"] != "base"]
    rate = sum(r["passed"] for r in base) / max(1, len(base))
    vrate = sum(r["passed"] for r in var) / len(var) if var else None
    unverified_pass = [r["id"] for r in base if r["passed"] and r["status"] not in ("verified", "refused") and r["category"] != "negative"]
    summary = {"as_of": a.as_of, "questions": len(base), "pass_rate": rate, "variant_pass_rate": vrate, "variants": len(var), "median_latency_s": sorted(r["latency_s"] for r in base)[len(base) // 2] if base else None,
               "slow_over_20s": [r["id"] for r in base if r["latency_s"] > 20], "passing_with_unverified_numbers": unverified_pass, "model": llm.cfg.model_chat}
    REPORTS_DIR.mkdir(exist_ok=True)
    md = [f"# Assistant evaluation\n", f"As of `{a.as_of}` · model `{summary['model']}` · {len(base)} base questions" + (f" + {len(var)} paraphrases" if var else "") + "\n",
          f"**Pass rate: {rate:.1%}** (target ≥ 95%)" + (f" · paraphrase robustness: {vrate:.1%}" if vrate is not None else "") + f" · median latency {summary['median_latency_s']} s\n",
          "\n| id | question | expected | tools used (expected) | status | verdict |\n|---|---|---|---|---|---|"]
    for r in results:
        exp = "; ".join(f"{k}={v if not isinstance(v, float) else round(v, 2)}" for k, v in r["expected"].items())[:120]
        md.append(f"| {r['id']}{'' if r['kind'] == 'base' else ' ' + r['kind']} | {r['question'][:90]} | {exp} | {', '.join(r['tools_called'])} ({'/'.join('|'.join(g) for g in r['tools_expected'])}) | {r['status']} | {'PASS' if r['passed'] else 'FAIL'} |")
    fails = [r for r in results if not r["passed"]]
    if fails:
        md.append("\n## Failures\n")
        for r in fails:
            md.append(f"### {r['id']} ({r['kind']})\n- Q: {r['question']}\n- Missing expected: {r['missing']}\n- Unmatched numbers: {r['unmatched']}\n- Tools: {r['tools_called']} (expected one of each group {r['tools_expected']})\n- Answer: {r['answer'][:600]}\n- Trace: {json.dumps([(t['name'], t['arguments']) for t in r['trace']], default=str)[:600]}\n")
    (REPORTS_DIR / "assistant_eval.md").write_text("\n".join(md))
    (REPORTS_DIR / "assistant_eval.json").write_text(json.dumps({"summary": summary, "results": results}, indent=1, default=str))
    rows = "".join(f"<tr class='{'ok' if r['passed'] else 'bad'}'><td>{r['id']}</td><td>{html.escape(r['question'])}</td><td>{html.escape(str(r['expected'])[:160])}</td><td>{', '.join(r['tools_called'])}</td><td>{r['status']}</td><td>{'PASS' if r['passed'] else 'FAIL'}</td></tr>" for r in results)
    (REPORTS_DIR / "assistant_eval.html").write_text(f"<html><body style='font-family:sans-serif'><h1>Assistant evaluation</h1><p>Synthetic data. Pass rate {rate:.1%}</p><table border=1 cellpadding=4>{rows}</table></body></html>")
    print(json.dumps(summary, indent=1))
    return 0 if rate >= 0.95 else 1


if __name__ == "__main__":
    sys.exit(main())
