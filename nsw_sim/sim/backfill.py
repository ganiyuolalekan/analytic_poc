"""Backfill / catch-up (Section 7.5): from the watermark to ``until`` in WAT-day chunks, idempotent and resumable."""
from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from datetime import date, timedelta

from nsw_sim import clock, db
from nsw_sim.config import db_path, get_logger
from nsw_sim.sim import planners
from nsw_sim.sim.engine import Engine
from nsw_sim.sim.ledger import build_coa
from nsw_sim.sim.reference import entity_cards, seed_reference

log = get_logger("nsw.backfill")


def prepare(conn: sqlite3.Connection, llm, until: float, *, reseed: bool = False, progress: Callable[[str], None] | None = None) -> dict:
    """Reference data, profiles, COA and weekly plans (LLM or fallback; cached) for the window up to ``until`` + 2 weeks."""
    seed_reference(conn)
    profiles = planners.ensure_profiles(conn, llm, reseed=reseed, progress=progress)
    build_coa(conn, profiles, {c: v["type"] for c, v in entity_cards().items()})
    start = clock.wat(clock.engine_start()).date()
    end = clock.wat(until).date() + timedelta(days=14)
    weeks = planners.weeks_between(start, end)
    ops_from = clock.week_start(clock.wat(clock.sim_start()).date())
    planners.ensure_plans(conn, llm, profiles, weeks, ops_llm_from=ops_from, progress=progress)
    return profiles


def load_plans(conn: sqlite3.Connection, engine: Engine, weeks: list[date]) -> None:
    flow, ops = {}, {}
    for w in weeks:
        p, s = planners.load_plan(conn, "flow", "ALL", w)
        if p:
            flow[w] = (p, s)
        for code in engine.profiles:
            op, os_ = planners.load_plan(conn, "ops", code, w)
            if op:
                ops[(code, w)] = (op, os_)
    engine.set_plans(flow, ops)


def make_engine(conn: sqlite3.Connection, profiles: dict, until: float, *, live_speed: float = 1.0) -> Engine:
    eng = Engine(conn, profiles, live_speed=live_speed, feed_full_from=until - 2 * 86400, rollup_minute_from=until - 3 * 86400)
    start = clock.wat(clock.engine_start()).date()
    load_plans(conn, eng, planners.weeks_between(start, clock.wat(until).date() + timedelta(days=14)))
    return eng


def run_backfill(conn: sqlite3.Connection, llm, until: float | None = None, *, progress: Callable[[str], None] | None = None,
                 reseed: bool = False, stop_after_days: int | None = None) -> Engine:
    until = until if until is not None else clock.utcnow().timestamp()
    t_start = time.time()
    profiles = prepare(conn, llm, until, reseed=reseed, progress=progress)
    wm = db.kv_get(conn, "watermark_utc")
    fresh = wm is None or clock.to_epoch(wm) <= clock.engine_start().timestamp() + 3 * 86400
    if fresh:
        db.drop_indexes(conn, db.BULK_INDEXES)
    eng = make_engine(conn, profiles, until)
    days_done = 0
    while eng.t < until:
        day = clock.wat_day(eng.t)
        day_end = min(until, clock.wat_midnight_utc(date.fromisoformat(day) + timedelta(days=1)).timestamp())
        eng.advance(day_end)
        eng.flush(day_end)
        days_done += 1
        if progress and (days_done % 5 == 0 or day_end >= until):
            progress(f"Catching up: {day} → {clock.fmt_wat(day_end, '%d %b %H:%M')} ({time.time() - t_start:.0f}s)")
        if stop_after_days and days_done >= stop_after_days:
            break
    if fresh or True:
        db.create_indexes(conn)
        conn.execute("ANALYZE")
    return eng


def catch_up_window(conn: sqlite3.Connection, engine: Engine, until: float, progress=None) -> None:
    """Catch an already-built engine up to ``until`` (used by the live service after a pause)."""
    while engine.t < until:
        day = clock.wat_day(engine.t)
        day_end = min(until, clock.wat_midnight_utc(date.fromisoformat(day) + timedelta(days=1)).timestamp())
        engine.advance(day_end)
        engine.flush(day_end)
        if progress:
            progress(f"Catching up: {clock.fmt_wat(day_end, '%d %b %H:%M')}")


KEEP_TABLES = ["entity_profiles", "weekly_plans", "llm_calls", "generation_runs", "directives"]


def reset_facts() -> None:
    """Recreate the database keeping only profiles, weekly plans and the LLM call log (so no model tokens are re-spent)."""
    import os
    path = db_path()
    kept: dict[str, tuple[list, list]] = {}
    if path.exists():
        old = db.connect(path)
        for t in KEEP_TABLES:
            try:
                cur = old.execute(f"SELECT * FROM {t}")
                kept[t] = ([d[0] for d in cur.description], cur.fetchall())
            except sqlite3.Error:
                pass
        old.close()
        for ext in ("", "-wal", "-shm"):
            if os.path.exists(str(path) + ext):
                os.remove(str(path) + ext)
    conn = db.connect(path)
    db.init_db(conn, indexes=False)
    for t, (cols, rows) in kept.items():
        db.insert_many(conn, t, cols, rows, or_replace=True)
    conn.close()
