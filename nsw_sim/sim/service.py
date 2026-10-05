"""Background simulation service (Section 7.6): catch-up to now, then live ticks. One writer guarded by a file lock.

The UI reads ``Service.status()`` and the database; it never touches the engine. Presenter controls (inject incident,
LIVE_SPEED, offline toggle) are queued as commands and applied on the service thread."""
from __future__ import annotations

import json
import queue
import sqlite3
import threading
import time
from datetime import date, timedelta

from filelock import FileLock, Timeout

from nsw_sim import clock, db
from nsw_sim.config import data_dir, db_path, get_logger, settings
from nsw_sim.llm.client import LLM, STATUS
from nsw_sim.sim import backfill, planners
from nsw_sim.sim.conditions import Incident
from nsw_sim.sim.director import Prefetcher
from nsw_sim.sim.engine import Engine

log = get_logger("nsw.service")

INCIDENT_DEFAULTS = {
    "scanner_outage": {"label": "Scanner outage at Apapa", "port": "NGAPP", "params": {"scanners_offline": 2}, "hours": 8},
    "permit_backlog": {"label": "NAFDAC permit backlog", "entity": "NAFDAC", "params": {"approval_time_mult": 2.2}, "hours": 12},
    "bank_delay": {"label": "Bank C settlement delay", "bank": "C", "params": {"lag_hours": 72}, "hours": 12},
    "fx_shock": {"label": "FX shock (NGN weakens)", "params": {"move": 0.04, "days": 3}, "hours": 72},
    "late_remittance": {"label": "Late remittance", "entity": "NAFDAC", "params": {"days_late": 9}, "hours": 1},
    "duplicate_burst": {"label": "Duplicate payments burst", "params": {"count": 14}, "hours": 1},
}


class Service:
    def __init__(self, llm: LLM | None = None) -> None:
        self.llm = llm or LLM()
        self.thread: threading.Thread | None = None
        self.lock: FileLock | None = None
        self.stop_flag = threading.Event()
        self.cmds: queue.Queue = queue.Queue()
        self.mode = "stopped"
        self.progress = {"label": "", "fraction": 0.0}
        self.engine: Engine | None = None
        self.speed = float(settings()["live_speed"])
        self.last_tick_at: float | None = None
        self.event_rate: list[tuple[float, int]] = []
        self.errors: list[str] = []
        self.rules = None
        self.prefetch: Prefetcher | None = None
        self.last_directive: dict = {}
        STATUS.on_change.append(self._on_llm_status)
        self._status_events: list[tuple[str, str, str]] = []

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> str:
        """Start once. Returns 'writer' if this process owns the simulation, else 'follower' (read-only)."""
        if self.thread and self.thread.is_alive():
            return self.mode
        self.lock = FileLock(str(db_path()) + ".sim.lock")      # one lock per database file: exactly one writer per DB, whatever the data dir
        try:
            self.lock.acquire(timeout=0.2)
        except Timeout:
            self.mode = "follower"
            return "follower"
        self.stop_flag.clear()
        self.thread = threading.Thread(target=self._run, name="nsw-sim-service", daemon=True)
        self.mode = "starting"
        self.thread.start()
        return "writer"

    def stop(self) -> None:
        self.stop_flag.set()
        if self.prefetch:
            self.prefetch.shutdown()
        if self.thread:
            self.thread.join(timeout=10)
        if self.lock and self.lock.is_locked:
            self.lock.release()
        self.mode = "stopped"

    # ------------------------------------------------------------------ commands (presenter controls)
    def inject(self, kind: str, **overrides) -> None:
        self.cmds.put(("inject", kind, overrides))

    def set_speed(self, speed: float) -> None:
        self.cmds.put(("speed", speed))

    def set_offline(self, flag: bool) -> None:
        self.cmds.put(("offline", flag))

    def rerun_catchup(self) -> None:
        self.cmds.put(("catchup",))

    def status(self) -> dict:
        wm = None
        try:
            c = db.reader()
            wm = db.kv_get(c, "watermark_utc")
        except Exception:  # noqa: BLE001
            pass
        recent = [n for t, n in self.event_rate if time.time() - t < 60]
        return {"mode": self.mode, "watermark": wm, "last_tick_at": self.last_tick_at, "progress": dict(self.progress),
                "events_per_min": sum(recent), "speed": self.speed, "llm": STATUS.snapshot(), "errors": self.errors[-3:],
                "offline": self.llm.offline, "model_data": self.llm.cfg.model_data, "director_calls": getattr(self.prefetch, "calls", 0),
                "directive": self.last_directive}

    # ------------------------------------------------------------------ internals
    def _on_llm_status(self, old: str, new: str, reason: str) -> None:
        self._status_events.append((old, new, reason))

    def _run(self) -> None:
        try:
            conn = db.connect()
            db.init_db(conn)
            self._startup(conn)
            self._live_loop(conn)
        except Exception as e:  # noqa: BLE001
            log.exception("service crashed")
            self.errors.append(f"{type(e).__name__}: {e}")
            self.mode = "error"

    def _startup(self, conn: sqlite3.Connection) -> None:
        self.mode = "catching_up"
        now = clock.utcnow().timestamp()
        self.progress = {"label": "Preparing profiles and plans", "fraction": 0.0}
        profiles = backfill.prepare(conn, self.llm, now, progress=lambda m: self.progress.update(label=m))
        wm = clock.to_epoch(db.kv_get(conn, "watermark_utc") or clock.iso(clock.engine_start()))
        fresh = wm <= clock.engine_start().timestamp() + 3 * 86400
        if fresh:
            db.drop_indexes(conn, db.BULK_INDEXES)
        self.engine = backfill.make_engine(conn, profiles, now, live_speed=self.speed)
        total = max(1.0, now - self.engine.t)
        start_t = self.engine.t
        while self.engine.t < now and not self.stop_flag.is_set():
            day = clock.wat_day(self.engine.t)
            day_end = min(now, clock.wat_midnight_utc(date.fromisoformat(day) + timedelta(days=1)).timestamp())
            self.engine.advance(day_end)
            self.engine.flush(day_end)
            self.progress = {"label": f"Catching up: {clock.fmt_wat(self.engine.t, '%d %b')} → now", "fraction": (self.engine.t - start_t) / total}
            self._run_rules(conn, replay_to=self.engine.t)
        if fresh:
            db.create_indexes(conn)
        self.progress = {"label": "Live", "fraction": 1.0}
        self.prefetch = Prefetcher(self.llm, db.connect, self.engine)
        self.prefetch.start()
        self.mode = "live"

    def _run_rules(self, conn, replay_to: float | None = None) -> None:
        try:
            from nsw_sim.supervision import rules
            rules.evaluate_incremental(conn, self.engine, replay_to or self.engine.t)
        except ImportError:
            pass
        except Exception as e:  # noqa: BLE001
            self.errors.append(f"rules: {e}")
            log.exception("rules failed")

    def _live_loop(self, conn: sqlite3.Connection) -> None:
        eng = self.engine
        tick = float(settings()["tick_seconds"])
        max_ahead = float(settings()["live_max_ahead_hours"]) * 3600
        last_wall = time.time()
        n = 0
        while not self.stop_flag.is_set():
            time.sleep(tick)
            wall = time.time()
            self._drain_commands(conn)
            dt = wall - last_wall
            last_wall = wall
            target = eng.t + dt * self.speed
            cap = wall + max_ahead
            if target > cap:
                target = max(eng.t, cap)
            try:
                before = len(eng.b["live"])
                eng.advance(target)
                self._apply_director(conn, eng)
                self._status_feed(eng, target)
                added = len(eng.b["live"]) - before
                eng.flush(target)
                self.last_tick_at = wall
                self.event_rate.append((wall, max(0, added)))
                self.event_rate = self.event_rate[-120:]
                n += 1
                if n % max(1, int(30 / tick)) == 0:
                    self._run_rules(conn)
                    self._prefetch_plans(conn)
            except Exception as e:  # noqa: BLE001
                log.exception("tick failed")
                self.errors.append(f"tick: {type(e).__name__}: {e}")
                time.sleep(2)

    def _drain_commands(self, conn) -> None:
        while True:
            try:
                cmd = self.cmds.get_nowait()
            except queue.Empty:
                return
            if cmd[0] == "speed":
                self.speed = float(cmd[1])
                self.engine.live_speed = self.speed
            elif cmd[0] == "offline":
                self.llm.set_offline(bool(cmd[1]))
            elif cmd[0] == "inject":
                self._do_inject(cmd[1], cmd[2])
            elif cmd[0] == "catchup":
                backfill.catch_up_window(conn, self.engine, clock.utcnow().timestamp())

    def _do_inject(self, kind: str, over: dict) -> None:
        d = INCIDENT_DEFAULTS[kind]
        eng = self.engine
        t = eng.t
        hours = float(over.pop("hours", d["hours"]))
        params = {**d["params"], **over.pop("params", {})}
        inc = Incident(incident_id=f"INC-{clock.wat_day(t).replace('-', '')}-{int(t) % 100000:05d}", kind=kind, start=t, end=t + hours * 3600,
                       entity=over.get("entity", d.get("entity")), port=over.get("port", d.get("port")), bank=over.get("bank", d.get("bank")),
                       params=params, source="injected", label=over.get("label", d["label"]))
        eng.inject(inc)
        if self.prefetch:
            self.prefetch.last_inject_t = time.time()

    def _apply_director(self, conn, eng) -> None:
        got = self.prefetch.take() if self.prefetch else None
        if not got:
            return
        res, ctx, ctx_t = got
        eng.cond.set_directives(res.obj.directives)
        self.last_directive = res.obj.directives.model_dump()
        conn.execute("INSERT OR REPLACE INTO directives(directive_id,ts,params_json,source) VALUES(?,?,?,?)",
                     (f"DIR-{int(eng.t)}", clock.iso(eng.t), json.dumps(self.last_directive), res.source))
        n = len(res.obj.feed_items)
        for i, it in enumerate(res.obj.feed_items):        # spread backwards from now: never future-dated
            eng.emit(it.type, eng.t - (n - 1 - i) * 2.0, it.entity, "director", it.headline, it.description, {}, severity=it.severity,
                     kind="llm" if res.source == "llm" else "engine")

    def _status_feed(self, eng, t: float) -> None:
        while self._status_events:
            old, new, reason = self._status_events.pop(0)
            typ = "system.llm.degraded" if new in ("degraded", "offline") else "system.llm.restored"
            eng.emit(typ, t, "NSW", "llm", f"Language model {new}", f"The assistant and live director are in {new} mode ({reason[:120]}); "
                     "the deterministic fallback keeps the feed running." if new != "live" else "Language model service restored.", {},
                     severity="medium" if new != "live" else "info", kind="rule")

    def _prefetch_plans(self, conn) -> None:
        """Make sure plans exist for the next two weeks (cheap when cached)."""
        try:
            now = self.engine.t
            weeks = planners.weeks_between(clock.wat(now).date(), clock.wat(now).date() + timedelta(days=14))
            missing = [w for w in weeks if planners.load_plan(conn, "flow", "ALL", w)[0] is None]
            if missing and not getattr(self, "_planning", False):
                self._planning = True

                def work():
                    c = db.connect()
                    try:
                        profiles = self.engine.profiles
                        planners.ensure_plans(c, self.llm, profiles, weeks, ops_llm_from=weeks[0])
                        backfill.load_plans(c, self.engine, weeks)
                    finally:
                        self._planning = False
                        c.close()
                threading.Thread(target=work, daemon=True).start()
        except Exception:  # noqa: BLE001
            self._planning = False


_SERVICE: Service | None = None


def get_service() -> Service:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = Service()
    return _SERVICE
