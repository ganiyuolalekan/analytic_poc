#!/usr/bin/env python
"""Turn live generation on or off from the terminal (the running app reacts within a couple of seconds).

  make live-on        switch ON: the app catches the data up to the current time, then keeps generating
  make live-off       switch OFF: data is frozen at its last data time, no generation and no model calls for it
  make live-status    show the switch, whether the app is running and how old the data is

Live generation is OFF whenever the app starts (``make run``); ``make run-live`` starts it ON.
Direct use: ``.venv/bin/python scripts/live.py {on,off,toggle,status} [--no-wait]``."""
import argparse
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nsw_sim import clock  # noqa: E402
from nsw_sim.config import db_path  # noqa: E402
from nsw_sim.sim import control  # noqa: E402

WAIT_ON_S = 25
WAIT_OFF_S = 40


def watermark() -> str | None:
    """The database's data time, read without touching the app's connections."""
    p = db_path()
    if not p.exists():
        return None
    try:
        c = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=5)
        try:
            row = c.execute("SELECT value FROM sim_state WHERE key='watermark_utc'").fetchone()
        finally:
            c.close()
        return row[0] if row else None
    except sqlite3.Error:
        return None


def age_text(seconds: float) -> str:
    h, m = int(seconds // 3600), int(seconds % 3600 // 60)
    return f"{h} h {m:02d} min" if h else f"{m} min"


def describe_data() -> str:
    wm = watermark()
    if not wm:
        return "The database has no data yet (run `make seed`, or switch generation on from the app)."
    behind = clock.utcnow().timestamp() - clock.to_epoch(wm)
    return f"Data time: {clock.fmt_wat(wm, '%d %b %Y %H:%M:%S WAT')} ({age_text(max(0, behind))} behind real time)"


def describe_app() -> str:
    hb = control.heartbeat()
    if hb is None:
        return "App: not running (nothing is listening to the switch; the app starts listening when a browser first opens it)"
    prog = hb.get("progress") or {}
    extra = f" · {prog.get('label')} {prog.get('fraction', 0):.0%}" if hb["mode"] == "catching_up" and prog.get("label") else ""
    return f"App: running (pid {hb['pid']}), service mode: {hb['mode']}{extra}"


def show_status() -> None:
    sw = control.read()
    who = f" (set by {sw['by']} at {clock.fmt_wat(sw['at'], '%d %b %H:%M:%S WAT')})" if sw["at"] else " (default)"
    print(f"Live generation: {'ON' if sw['enabled'] else 'OFF'}{who}")
    print(describe_app())
    print(describe_data())


def wait_for(modes: tuple[str, ...], timeout: float) -> str | None:
    """Wait until the running app reports one of ``modes``; returns that mode, or None on timeout / no app."""
    end = time.time() + timeout
    while time.time() < end:
        hb = control.heartbeat()
        if hb is None:
            return None
        if hb["mode"] in modes:
            return hb["mode"]
        time.sleep(0.5)
    hb = control.heartbeat()
    return hb["mode"] if hb and hb["mode"] in modes else None


def switch(flag: bool, wait: bool) -> int:
    new = control.set_enabled(flag, "cli")
    print(f"Live generation switched {'ON' if new['enabled'] else 'OFF'}.")
    if control.heartbeat() is None:
        print("No running app was found, so nothing has changed yet. Start one with `make run-live` (generation starts ON), or open the app in a browser first "
              "(an app started with `make run` resets the switch to OFF when it is first opened).")
        return 0
    if not wait:
        return 0
    if flag:
        mode = wait_for(("live", "follower", "error"), WAIT_ON_S)
        if mode == "live":
            print("The app is generating live data.")
        elif mode == "follower":
            print("Another process holds the writer lock, so this app stays read-only.")
        elif mode == "error":
            print("The service hit an error and switched itself off; see the app's Admin page.")
        else:
            print(describe_app() + "\nStill catching up to the current time; check `make live-status`.")
    else:
        mode = wait_for(("off", "error"), WAIT_OFF_S)
        print("Generation has stopped." if mode in ("off", "error") else describe_app() + "\nStill stopping; check `make live-status`.")
    print(describe_data())
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Switch live data generation on or off.")
    ap.add_argument("action", choices=["on", "off", "toggle", "status"])
    ap.add_argument("--no-wait", action="store_true", help="return immediately instead of waiting for the app to react")
    a = ap.parse_args()
    if a.action == "status":
        show_status()
        return 0
    flag = (not control.is_enabled()) if a.action == "toggle" else a.action == "on"
    return switch(flag, wait=not a.no_wait)


if __name__ == "__main__":
    sys.exit(main())
