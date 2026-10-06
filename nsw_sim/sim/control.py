"""The live-generation switch, shared by the app, the background service and the terminal command (``scripts/live.py``).

Live generation is OFF by default: with the switch off the database is frozen at its last data time and nothing is simulated or
sent to the language model for generation. The switch is a small JSON file next to the database, so a command run in another
terminal and the Streamlit app see the same state; the service polls it. A heartbeat file, written by the running app, tells the
terminal command whether anything is actually listening."""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from nsw_sim import clock
from nsw_sim.config import db_path

HEARTBEAT_STALE_S = 15.0
_write_lock = threading.Lock()


def switch_path() -> Path:
    return Path(str(db_path()) + ".live.json")


def heartbeat_path() -> Path:
    return Path(str(db_path()) + ".live_status.json")


def _write_atomic(path: Path, obj: dict) -> None:
    with _write_lock:
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(obj), encoding="utf-8")
        os.replace(tmp, path)


def _read_json(path: Path) -> dict:
    try:
        v = json.loads(path.read_text(encoding="utf-8"))
        return v if isinstance(v, dict) else {}
    except (OSError, ValueError):
        return {}


# ------------------------------------------------------------------ the switch
def read() -> dict:
    """``{"enabled": bool, "by": str | None, "at": iso | None}``. A missing or unreadable file means OFF."""
    raw = _read_json(switch_path())
    return {"enabled": raw.get("enabled") is True, "by": raw.get("by"), "at": raw.get("at")}


def is_enabled() -> bool:
    return read()["enabled"]


def set_enabled(flag: bool, by: str) -> dict:
    """Flip the switch (``by``: who asked: 'app', 'cli', 'startup', ...). No-op when it is already in that state."""
    cur = read()
    if cur["enabled"] == bool(flag) and cur["at"]:
        return cur
    new = {"enabled": bool(flag), "by": by, "at": clock.iso(clock.utcnow())}
    _write_atomic(switch_path(), new)
    return new


def reset_for_start(on: bool = False, by: str = "startup") -> dict:
    """Called when the app starts: the switch always starts OFF (or ON when asked, e.g. ``make run-live``). If another app process is
    already running against this database, its switch state is left alone."""
    if heartbeat() is not None:
        return read()
    return set_enabled(on, by)


# ------------------------------------------------------------------ heartbeat (is an app running?)
def beat(mode: str, progress: dict | None = None) -> None:
    _write_atomic(heartbeat_path(), {"pid": os.getpid(), "mode": mode, "progress": progress or {}, "ts": time.time()})


def clear_beat() -> None:
    try:
        heartbeat_path().unlink()
    except OSError:
        pass


def heartbeat() -> dict | None:
    """The running service's last heartbeat, or None when nothing has written one recently."""
    raw = _read_json(heartbeat_path())
    ts = raw.get("ts")
    if not isinstance(ts, (int, float)) or time.time() - ts > HEARTBEAT_STALE_S:
        return None
    return raw
