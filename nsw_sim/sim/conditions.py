"""World conditions at a point in time: scenario beats (B1-B10), injected incidents, live directives, FX path,
Nigerian calendar (holidays, office hours). The ENGINE enforces beat magnitudes; the LLM only colours wording."""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from functools import lru_cache

import holidays

from nsw_sim import clock
from nsw_sim.config import settings, yaml_config
from nsw_sim.llm.schemas import Directives
from nsw_sim.sim.reference import ref

CROSS = {"USD": 1.0, "EUR": 1.085, "GBP": 1.27, "CNY": 0.1385, "AED": 0.2723, "INR": 0.0119}
DAY = 86400.0


@dataclass
class Incident:
    incident_id: str
    kind: str                     # scanner_outage | permit_backlog | bank_delay | fx_shock | late_remittance | duplicate_burst
    start: float                  # epoch seconds
    end: float
    entity: str | None = None
    port: str | None = None
    bank: str | None = None
    params: dict = field(default_factory=dict)
    source: str = "injected"
    label: str = ""

    def active(self, t: float) -> bool:
        return self.start <= t < self.end


@lru_cache(maxsize=8)
def _ng_holidays(year: int):
    return holidays.country_holidays("NG", years=[year - 1, year, year + 1])


def is_holiday(d: date) -> bool:
    return d in _ng_holidays(d.year)


def wat_dt(t: float) -> datetime:
    return clock.wat(t)


def is_working_time(t: float) -> bool:
    w = wat_dt(t)
    return w.weekday() < 5 and 8 <= w.hour < 17 and not is_holiday(w.date())


def next_working_time(t: float, jitter_s: float = 0.0) -> float:
    """Next instant inside Mon-Fri 08:00-17:00 WAT (not a public holiday); ``jitter_s`` spreads same-time arrivals."""
    if is_working_time(t):
        return t
    w = wat_dt(t)
    d = w.date()
    if w.weekday() < 5 and w.hour < 8 and not is_holiday(d):
        pass
    else:
        d += timedelta(days=1)
    while d.weekday() >= 5 or is_holiday(d):
        d += timedelta(days=1)
    return clock.wat_midnight_utc(d).timestamp() + 8 * 3600 + jitter_s


class Conditions:
    """Thread-safe view of everything that modulates the engine at time ``t`` (epoch seconds)."""

    def __init__(self, flow_fx_lookup=None) -> None:
        s = settings()
        self.cfg = s
        self.sim0 = clock.sim_start().timestamp()
        self.beats = yaml_config("scenario")["beats"]
        self.incidents: list[Incident] = []
        self.directives = Directives()
        self.lock = threading.RLock()
        self.fx_lookup = flow_fx_lookup          # callable(date) -> USD/NGN base from the weekly flow plans
        self._fx_cache: dict[str, float] = {}
        self.progress_days = float(s["sim"]["progress_days"])
        self.banks_base = {b: float(h) for b, h in s["sim"]["banks"].items()}
        self.scanners = {p: v["scanners"] for kind in ("sea", "air") for p, v in ref()["ports"][kind].items()}
        # absolute beat windows
        self._w = {bid: (self.sim0 + b["start_day"] * DAY, self.sim0 + b["end_day"] * DAY, b.get("params", {}))
                   for bid, b in self.beats.items()}

    # ---- incidents / directives
    def add_incident(self, inc: Incident) -> None:
        with self.lock:
            self.incidents.append(inc)

    def set_directives(self, d: Directives) -> None:
        with self.lock:
            self.directives = d

    def active_incidents(self, t: float, kind: str | None = None) -> list[Incident]:
        return [i for i in self.incidents if i.active(t) and (kind is None or i.kind == kind)]

    def _in(self, bid: str, t: float) -> dict | None:
        s, e, p = self._w[bid]
        return p if s <= t < e else None

    # ---- B1: improvement progress
    def progress(self, t: float) -> float:
        return min(1.0, max(0.0, (t - self.sim0) / (self.progress_days * DAY)))

    # ---- B2 / injected scanner outage / directive
    def scanner_mult(self, port: str, t: float) -> float:
        m = 1.0
        b = self._in("B2", t)
        if b and port == b["port"]:
            m *= b["s06_wait_mult"]
        for inc in self.active_incidents(t, "scanner_outage"):
            if inc.port == port:
                total = self.scanners.get(port, 2)
                off = min(total - 1, int(inc.params.get("scanners_offline", total // 2)))
                m *= total / max(1, total - off) * float(inc.params.get("extra_mult", 1.0)) if off else 1.0
        off = self.directives.scanner_offline.get(port, 0)
        if off:
            total = self.scanners.get(port, 2)
            m *= total / max(1, total - min(off, total - 1))
        return m

    def scanners_offline(self, port: str, t: float) -> int:
        """Number of scanners currently offline at ``port`` (for the situation board)."""
        off = 0
        b = self._in("B2", t)
        if b and port == b["port"]:
            off = max(off, int(b["scanners_offline"]))
        for inc in self.active_incidents(t, "scanner_outage"):
            if inc.port == port:
                off = max(off, int(inc.params.get("scanners_offline", self.scanners.get(port, 2) // 2)))
        return max(off, self.directives.scanner_offline.get(port, 0))

    # ---- B3 / injected permit backlog / directive
    def permit_mult(self, entity: str, t: float) -> float:
        m = 1.0
        b = self._in("B3", t)
        if b and entity == b["entity"]:
            m *= b["approval_time_mult"]
        for inc in self.active_incidents(t, "permit_backlog"):
            if inc.entity == entity:
                m *= float(inc.params.get("approval_time_mult", 2.0))
        m *= self.directives.permit_queue_pressure.get(entity, 1.0)
        return m

    def queue_pressure(self, entity: str, t: float) -> float:
        b = self._in("B3", t)
        base = float(b["queue_pressure"]) if b and entity == b["entity"] else 1.0
        return base * self.permit_mult(entity, t) / (b["approval_time_mult"] if b and entity == b["entity"] else 1.0)

    # ---- B7 / injected bank delay / directive
    def bank_lag_h(self, bank: str, t: float) -> float:
        lag = self.banks_base.get(bank, 24.0)
        b = self._in("B7", t)
        if b and bank == b["bank"]:
            lag = float(b["lag_hours"])
        for inc in self.active_incidents(t, "bank_delay"):
            if inc.bank == bank:
                lag = max(lag, float(inc.params.get("lag_hours", 72)))
        return max(2.0, lag + self.directives.settlement_lag_change_h.get(bank, 0.0))

    # ---- arrivals: holidays, weekends, B10, directive
    def arrival_mult(self, t: float) -> float:
        w = wat_dt(t)
        sim = self.cfg["sim"]
        m = 1.0
        if is_holiday(w.date()):
            m *= sim["holiday_arrival_factor"]
        elif w.weekday() >= 5:
            m *= sim["weekend_arrival_factor"]
        b = self._in("B10", t)
        if b:
            m = min(m, float(b["arrival_factor"]))
        return m * self.directives.arrival_multiplier

    def payment_fail_rate(self, t: float) -> float:
        return max(0.0, self.directives.payment_failure_rate if self.directives else self.cfg["sim"]["payment_failure_rate"])

    # ---- B9 / injected DQ
    def dq_missing_share(self, entity: str, t: float) -> float:
        b = self._in("B9", t)
        return float(b["missing_share"]) if b and entity == b["entity"] else 0.0

    # ---- B6
    def underassessment(self, entity: str, fee_code: str, group: str, origin: str, t: float) -> float:
        b = self._in("B6", t)
        if b and entity == b["entity"] and fee_code == b["fee_code"] and group == b["commodity_group"] and origin == b["origin_country"]:
            return float(b["shortfall"])
        return 0.0

    # ---- FX (B4 + injected fx_shock)
    def fx_usd(self, t: float) -> float:
        d = clock.wat_day(t)
        key = d + (str(len(self.incidents)) if self.incidents else "")
        v = self._fx_cache.get(key)
        if v is None:
            day = date.fromisoformat(d)
            base = self.fx_lookup(day) if self.fx_lookup else self.cfg["fx_usd_ngn"]["start"]
            cum = self._b4_cum(t)
            for inc in self.incidents:
                if inc.kind == "fx_shock" and t >= inc.start:
                    n = max(1, int(inc.params.get("days", 3)))
                    frac = min(1.0, (t - inc.start) / (n * DAY) + 1.0 / n)
                    cum += float(inc.params.get("move", 0.04)) * frac
            hi = self.cfg["fx_usd_ngn"]["max"]
            v = min(base * (1 + cum), hi)
            self._fx_cache[key] = v
        return v

    def _b4_cum(self, t: float) -> float:
        s, e, p = self._w["B4"]
        if t < s:
            return 0.0
        if t >= e:
            return float(p["total_move"])
        idx = int((t - s) // DAY)
        return float(p["total_move"]) * sum(p["daily_split"][:idx + 1])

    def fx(self, ccy: str, t: float) -> float:
        return self.fx_usd(t) * CROSS.get(ccy, 1.0)

    def clear_fx_cache(self) -> None:
        self._fx_cache.clear()
