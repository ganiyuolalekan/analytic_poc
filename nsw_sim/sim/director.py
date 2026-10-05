"""Live event director (Section 7.3 role 4): LLM-written directives + neutral feed items, schema-validated, with a
prefetch buffer so the UI never waits on the model, and an adaptive interval (30-90 s)."""
from __future__ import annotations

import json
import queue
import sqlite3
import threading
import time

from nsw_sim import clock
from nsw_sim.config import get_logger, settings
from nsw_sim.llm import prompts as P
from nsw_sim.llm.schemas import DirectorOutput
from nsw_sim.llm.validate import RoleResult, run_json_role
from nsw_sim.sim import fallback as fb
from nsw_sim.sim import planners
from nsw_sim.sim.reference import ref

log = get_logger("nsw.director")


def build_context(conn: sqlite3.Connection, engine, now: float) -> dict:
    """Compact, engine-computed context (a few hundred tokens): aggregates, never rows."""
    iso_now, iso_60 = clock.iso(now), clock.iso(now - 3600)
    one = lambda sql, *a: conn.execute(sql, a).fetchone()[0]  # noqa: E731
    ctx = {
        "time_wat": clock.fmt_wat(now, "%a %H:%M"),
        "last_60min": {
            "manifests": one("SELECT COUNT(*) FROM stage_events WHERE stage='S01' AND occurred_at>? AND occurred_at<=?", iso_60, iso_now),
            "payments_confirmed": one("SELECT COUNT(*) FROM payments WHERE status='confirmed' AND is_duplicate=0 AND occurred_at>? AND occurred_at<=?", iso_60, iso_now),
            "payments_failed": one("SELECT COUNT(*) FROM payments WHERE status='failed' AND occurred_at>? AND occurred_at<=?", iso_60, iso_now),
            "exams_queued": one("SELECT COUNT(*) FROM stage_events WHERE stage='S06' AND status='queued' AND occurred_at>? AND occurred_at<=?", iso_60, iso_now),
            "gate_outs": one("SELECT COUNT(*) FROM stage_events WHERE stage='S09' AND occurred_at>? AND occurred_at<=?", iso_60, iso_now),
        },
        "active_incidents": [{"kind": i.kind, "port": i.port, "entity": i.entity, "bank": i.bank} for i in engine.cond.active_incidents(now)],
        "scanners_offline": {p: engine.cond.scanners_offline(p, now) for p in ("NGAPP", "NGTIN", "NGLEK", "NGONN", "NGPHC")},
        "open_alerts": {sev: n for sev, n in conn.execute("SELECT severity, COUNT(*) FROM alerts WHERE status IN ('open','acknowledged','under_review') GROUP BY 1")},
        "bank_lag_hours": {b: round(engine.cond.bank_lag_h(b, now)) for b in ref()["banks"]},
        "permit_pressure": {e: round(engine.cond.permit_mult(e, now), 2) for e in ("NAFDAC", "SON", "NAQS", "NESREA")},
    }
    return ctx


def interval_for(ctx: dict, last_inject_age_s: float | None) -> float:
    base = float(settings()["director_interval_seconds"])
    if last_inject_age_s is not None and last_inject_age_s < 600:
        return 30.0
    quiet = not ctx["active_incidents"] and not any(ctx["open_alerts"].get(s, 0) for s in ("high",))
    return 90.0 if quiet else base


def call_director(llm, ctx: dict, interval_s: float, seed_key: str) -> RoleResult:
    ports = {p: v["scanners"] for kind in ("sea", "air") for p, v in ref()["ports"][kind].items()}
    user = P.director_user(ctx, int(interval_s))
    active = [f"{i['kind']}" for i in ctx["active_incidents"]]
    return run_json_role(llm, "director", P.DIRECTOR_SYSTEM.format(n=int(interval_s)), user, DirectorOutput, planners.check_director(ports),
                         lambda: fb.fallback_director(seed_key, active), schema_version=P.PROMPT_VERSION + "-live")


class Prefetcher:
    """Keeps up to two director outputs ready. ``take()`` never blocks on the network."""

    def __init__(self, llm, conn_factory, engine) -> None:
        self.llm, self.conn_factory, self.engine = llm, conn_factory, engine
        self.q: queue.Queue = queue.Queue(maxsize=2)
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None
        self.last_inject_t: float | None = None
        self.interval = float(settings()["director_interval_seconds"])
        self.calls = 0

    def start(self) -> None:
        self.thread = threading.Thread(target=self._run, name="director-prefetch", daemon=True)
        self.thread.start()

    def _run(self) -> None:
        conn = self.conn_factory()
        while not self.stop.is_set():
            if self.q.full():
                self.stop.wait(1.0)
                continue
            t0 = time.time()
            try:
                now = max(self.engine.t, 0)
                ctx = build_context(conn, self.engine, now)
                age = None if self.last_inject_t is None else time.time() - self.last_inject_t
                self.interval = interval_for(ctx, age)
                self.calls += 1
                res = call_director(self.llm, ctx, self.interval, f"{int(now)}")
                self.q.put((res, ctx, now))
            except Exception:  # noqa: BLE001 - the prefetcher must never die
                log.exception("director prefetch failed")
            self.stop.wait(max(1.0, self.interval - (time.time() - t0)))

    def take(self):
        try:
            return self.q.get_nowait()
        except queue.Empty:
            return None

    def shutdown(self) -> None:
        self.stop.set()
