#!/usr/bin/env python
"""Build the simulated world: profiles + weekly plans (LLM, cached) then backfill from SIM_START-warmup to now.

  make seed                         full backfill (uses the model; cached calls are free)
  scripts/seed.py --estimate-only   print expected calls/tokens before any call is made
  scripts/seed.py --plans-only      only generate profiles and weekly plans
  scripts/seed.py --offline         deterministic fallback for every role (no network)
  scripts/seed.py --refresh-llm     bypass the cache; --reseed regenerates entity profiles
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nsw_sim import clock, db  # noqa: E402
from nsw_sim.config import data_dir, db_path, startup_banner  # noqa: E402
from nsw_sim.llm import ledger  # noqa: E402
from nsw_sim.llm.client import LLM  # noqa: E402
from nsw_sim.sim import backfill, planners  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--estimate-only", action="store_true")
    ap.add_argument("--plans-only", action="store_true")
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--refresh-llm", action="store_true")
    ap.add_argument("--reseed", action="store_true")
    ap.add_argument("--until", help="ISO UTC instant to simulate to (default: now)")
    ap.add_argument("--no-rules", action="store_true")
    ap.add_argument("--rebuild", action="store_true", help="drop all simulated facts (keeps profiles, plans, LLM log) and re-simulate")
    a = ap.parse_args()
    print(startup_banner())
    data_dir()
    if a.rebuild:
        backfill.reset_facts()
        print('facts cleared (profiles, plans and the LLM call log were kept)')
    conn = db.connect()
    db.init_db(conn, indexes=False)
    llm = LLM(offline=True if a.offline else None)
    until = clock.to_epoch(a.until) if a.until else clock.utcnow().timestamp()
    start = clock.wat(clock.engine_start()).date()
    from datetime import timedelta
    weeks = planners.weeks_between(start, clock.wat(until).date() + timedelta(days=14))
    est = planners.estimate(conn, weeks, llm)
    print(f"Pending model work: {est['calls']}  ≈ {est['total_calls']} calls, ~{est['total_tokens']:,} tokens "
          f"(cached calls are free). {est['note']}")
    if a.estimate_only:
        return 0
    if a.refresh_llm:
        from nsw_sim.llm import cache
        cache.clear()
    t0 = time.time()
    log = lambda m: print(m, flush=True)  # noqa: E731
    if a.plans_only:
        from nsw_sim.sim.reference import seed_reference
        seed_reference(conn)
        profiles = planners.ensure_profiles(conn, llm, reseed=a.reseed, progress=log)
        planners.ensure_plans(conn, llm, profiles, weeks, ops_llm_from=clock.week_start(clock.wat(clock.sim_start()).date()), progress=log)
    else:
        backfill.run_backfill(conn, llm, until, progress=log, reseed=a.reseed)
        if not a.no_rules:
            try:
                from nsw_sim.supervision import replay
                replay.run(conn, until, progress=log)
            except ImportError:
                print("supervision module not available yet: skipping alert replay")
    print(f"done in {time.time() - t0:.0f}s · db={db_path()}")
    print(ledger.format_summary(ledger.summary(conn)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
