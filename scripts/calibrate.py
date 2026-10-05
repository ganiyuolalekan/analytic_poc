#!/usr/bin/env python
"""Offline calibration run (deterministic fallback profiles): dwell, digital share, SLA breach and monthly collections vs bands.
Usage: calibrate.py [--days 55] [--p1]   (--p1 forces the end-state improvement progress)"""
import argparse
import os
import statistics as st
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["NSW_OFFLINE"] = "1"
d = tempfile.mkdtemp()
os.environ.update(NSW_DB_PATH=d + "/cal.db", NSW_LLM_CACHE=d + "/c.sqlite", NSW_DATA_DIR=d)

from nsw_sim import clock, db  # noqa: E402
from nsw_sim.config import settings  # noqa: E402
from nsw_sim.llm.client import LLM  # noqa: E402
from nsw_sim.sim import backfill, conditions  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--days", type=int, default=55)
ap.add_argument("--p1", action="store_true")
ap.add_argument("--p0", action="store_true")
a = ap.parse_args()
if a.p1:
    conditions.Conditions.progress = lambda self, t: 1.0
if a.p0:
    conditions.Conditions.progress = lambda self, t: 0.0
conn = db.connect()
db.init_db(conn)
llm = LLM()
llm.concurrency = 2
t0 = time.time()
until = clock.engine_start().timestamp() + a.days * 86400
backfill.run_backfill(conn, llm, until)
print(f"ran {a.days} days in {time.time() - t0:.0f}s")
q = lambda s: conn.execute(s).fetchall()  # noqa: E731
cut = clock.iso(clock.engine_start().timestamp() + 36 * 86400)
dw = [r[0] / 24 for r in q(f"select dwell_h from consignments where gate_out_at>='{cut}'")]
print(f"dwell (gate-outs after day 36, n={len(dw)}): median {st.median(dw):.2f} d, p90 {sorted(dw)[int(.9 * len(dw))]:.1f} d")
r = q(f"select sum(digital_h), sum(physical_h) from consignments where gate_out_at>='{cut}'")[0]
print(f"digital share {r[0] / (r[0] + r[1]):.3f}   (mean digital {r[0] / len(dw):.0f}h, physical {r[1] / len(dw):.0f}h)")
print("stage (done rows): n, mean wait h, mean dur h, SLA breach")
for s in q("select stage, count(*), round(avg(wait_h),1), round(avg(dur_h),1), round(avg(sla_breach),3) from stage_events where status in ('done','manual') and wait_h is not null group by 1 order by 1"):
    print("  ", s)
w0, w1 = clock.iso(clock.engine_start().timestamp() + 15 * 86400), clock.iso(clock.engine_start().timestamp() + (a.days - 2) * 86400)
span = (clock.to_epoch(w1) - clock.to_epoch(w0)) / 86400
tg = settings()["monthly_targets_ngn_bn"]
print(f"monthly projection from assessments {w0[:10]}..{w1[:10]} ({span:.0f} d):  entity  proj_bn  band  x_to_mid")
for ent, v in q(f"select entity_id, sum(amount_ngn_minor)/1e11 from fee_assessments where occurred_at>='{w0}' and occurred_at<'{w1}' group by 1 order by 2 desc"):
    m = v * 30.4 / span
    b = tg.get(ent)
    print(f"   {ent:7s} {m:7.2f}  {b}  x{(sum(b) / 2) / m:.2f}" if b else f"   {ent:7s} {m:7.2f}")
