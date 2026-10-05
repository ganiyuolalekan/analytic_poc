"""Time helpers. All stored timestamps are UTC, fixed-format ISO strings so that string
comparison equals chronological comparison: ``YYYY-MM-DDTHH:MM:SSZ``. Display is WAT (UTC+1)."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

from nsw_sim.config import settings

UTC = timezone.utc
WAT = ZoneInfo("Africa/Lagos")
WAT_OFFSET = timedelta(hours=1)
ISO_FMT = "%Y-%m-%dT%H:%M:%SZ"
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def utcnow() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def to_dt(x: str | datetime | float | int) -> datetime:
    """Parse ISO string / epoch seconds / datetime into an aware UTC datetime."""
    if isinstance(x, datetime):
        return x.astimezone(UTC) if x.tzinfo else x.replace(tzinfo=UTC)
    if isinstance(x, (int, float)):
        return _EPOCH + timedelta(seconds=float(x))
    s = x.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    return dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)


def to_epoch(x: str | datetime | float | int) -> float:
    if isinstance(x, (int, float)):
        return float(x)
    return (to_dt(x) - _EPOCH).total_seconds()


@lru_cache(maxsize=200_000)
def _iso_sec(sec: int) -> str:
    return (_EPOCH + timedelta(seconds=sec)).strftime(ISO_FMT)


def iso(x: str | datetime | float | int) -> str:
    """Canonical stored timestamp (UTC, seconds precision)."""
    if isinstance(x, (int, float)):
        return _iso_sec(int(x))
    return to_dt(x).strftime(ISO_FMT)


def wat(x: str | datetime | float | int) -> datetime:
    return to_dt(x).astimezone(WAT)


def fmt_wat(x: str | datetime | float | int, fmt: str = "%d %b %Y %H:%M WAT") -> str:
    return wat(x).strftime(fmt)


def wat_day(x: str | datetime | float | int) -> str:
    """WAT calendar day (YYYY-MM-DD) of a timestamp."""
    return wat(x).strftime("%Y-%m-%d")


def wat_midnight_utc(d: date | str) -> datetime:
    """UTC instant of 00:00 WAT on calendar day ``d``."""
    if isinstance(d, str):
        d = date.fromisoformat(d)
    return datetime(d.year, d.month, d.day, tzinfo=WAT).astimezone(UTC)


def day_bounds(d: date | str) -> tuple[str, str]:
    """(start, end) ISO UTC strings for the WAT day ``d`` (end exclusive)."""
    if isinstance(d, str):
        d = date.fromisoformat(d)
    return iso(wat_midnight_utc(d)), iso(wat_midnight_utc(d + timedelta(days=1)))


def sim_start() -> datetime:
    """SIM_START: 00:00 WAT on 1 July of the current year unless configured otherwise."""
    cfg = str(settings().get("sim_start", "auto_july_1"))
    if cfg == "auto_july_1":
        return wat_midnight_utc(date(wat(utcnow()).year, 7, 1))
    return wat_midnight_utc(date.fromisoformat(cfg))


def engine_start() -> datetime:
    """Where the engine actually begins (SIM_START minus the warm-up)."""
    return sim_start() - timedelta(days=int(settings().get("warmup_days", 0)))


def sim_day_offset(x: str | datetime | float | int) -> float:
    """Days since SIM_START (fractional)."""
    return (to_dt(x) - sim_start()).total_seconds() / 86400.0


def week_start(d: date) -> date:
    """Monday of the week containing ``d``."""
    return d - timedelta(days=d.weekday())
