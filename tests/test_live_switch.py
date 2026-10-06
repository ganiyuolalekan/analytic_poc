"""Live generation is OFF by default and can be flipped from the app and from the terminal; the service idles when off."""
import subprocess
import sys
import time
from pathlib import Path

import pytest
from filelock import FileLock, Timeout

from nsw_sim import clock, db
from nsw_sim.config import db_path
from nsw_sim.llm.client import StubLLM
from nsw_sim.sim import control
from nsw_sim.sim import service as service_mod
from nsw_sim.sim.service import Service

ROOT = Path(__file__).resolve().parent.parent


def wait_until(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


def lock_is_held() -> bool:
    probe = FileLock(str(db_path()) + ".sim.lock")
    try:
        probe.acquire(timeout=0.1)
    except Timeout:
        return True
    probe.release()
    return False


# ------------------------------------------------------------------ the switch file
def test_default_is_off_and_the_switch_round_trips():
    assert control.is_enabled() is False
    assert control.read() == {"enabled": False, "by": None, "at": None}
    r = control.set_enabled(True, "cli")
    assert r["enabled"] and r["by"] == "cli" and control.is_enabled()
    r2 = control.set_enabled(True, "app")                       # already on: untouched (keeps who/when of the real change)
    assert r2["by"] == "cli"
    control.set_enabled(False, "app")
    assert control.is_enabled() is False and control.read()["by"] == "app"


def test_unreadable_switch_file_means_off():
    control.switch_path().write_text("{not json", encoding="utf-8")
    assert control.is_enabled() is False
    control.switch_path().write_text('{"enabled": "yes"}', encoding="utf-8")      # only a real true counts
    assert control.is_enabled() is False


def test_heartbeat_goes_stale_and_start_reset_respects_a_running_app():
    assert control.heartbeat() is None
    control.set_enabled(True, "cli")
    control.reset_for_start()                                   # nothing running: the app starts OFF
    assert control.is_enabled() is False
    control.reset_for_start(on=True)                            # make run-live
    assert control.is_enabled() is True
    control.beat("live")
    assert control.heartbeat()["mode"] == "live"
    control.reset_for_start()                                   # another app is running: its switch is left alone
    assert control.is_enabled() is True
    control.heartbeat_path().write_text('{"pid": 1, "mode": "live", "progress": {}, "ts": 1.0}', encoding="utf-8")     # an ancient beat
    assert control.heartbeat() is None
    control.clear_beat()


# ------------------------------------------------------------------ the service state machine
@pytest.fixture
def fast_service(monkeypatch):
    monkeypatch.setattr(Service, "POLL_S", 0.05)
    monkeypatch.setattr(Service, "BEAT_S", 0.1)
    started = []

    def fake_startup(self, conn):
        started.append(time.time())
        self.mode = "live"
        return True

    def fake_live(self, conn):
        while self._wanted():
            self.stop_flag.wait(0.02)

    monkeypatch.setattr(Service, "_startup", fake_startup)
    monkeypatch.setattr(Service, "_live_loop", fake_live)
    svc = Service(llm=StubLLM(offline=True))
    svc.started = started
    yield svc
    svc.stop()


def test_service_idles_until_switched_on_and_releases_the_writer_lock_when_switched_off(fast_service):
    svc = fast_service
    svc.start()
    assert wait_until(lambda: svc.mode == "off")
    time.sleep(0.2)
    assert svc.started == [] and not lock_is_held()             # off by default: nothing generates, nobody holds the writer lock
    assert wait_until(lambda: (control.heartbeat() or {}).get("mode") == "off")

    control.set_enabled(True, "test")
    assert wait_until(lambda: svc.mode == "live") and len(svc.started) == 1
    assert lock_is_held()
    assert wait_until(lambda: (control.heartbeat() or {}).get("mode") == "live")
    assert svc.status()["enabled"] is True

    control.set_enabled(False, "test")
    assert wait_until(lambda: svc.mode == "off")
    assert wait_until(lambda: not lock_is_held())
    assert svc.status()["enabled"] is False

    control.set_enabled(True, "test")                           # back on: starts again (resume from the database)
    assert wait_until(lambda: len(svc.started) == 2 and svc.mode == "live")


def test_presenter_commands_sent_while_off_are_dropped_not_replayed_later(fast_service):
    svc = fast_service
    svc.start()
    assert wait_until(lambda: svc.mode == "off")
    svc.inject("scanner_outage")
    svc.set_speed(20)
    assert wait_until(svc.cmds.empty)


def test_a_second_process_holding_the_writer_lock_makes_this_one_a_read_only_follower(fast_service):
    svc = fast_service
    other = FileLock(str(db_path()) + ".sim.lock")
    other.acquire(timeout=1)
    try:
        svc.start()
        control.set_enabled(True, "test")
        assert wait_until(lambda: svc.mode == "follower")
        assert svc.started == []
    finally:
        other.release()
    assert wait_until(lambda: svc.mode == "live", timeout=8)    # the writer went away: this one takes over


def test_a_failing_start_switches_generation_off_instead_of_retrying_forever(monkeypatch):
    monkeypatch.setattr(Service, "POLL_S", 0.05)
    monkeypatch.setattr(Service, "BEAT_S", 0.1)
    attempts = []

    def boom(self, conn):
        attempts.append(1)
        raise RuntimeError("model plans unavailable")

    monkeypatch.setattr(Service, "_startup", boom)
    svc = Service(llm=StubLLM(offline=True))
    try:
        svc.start()
        control.set_enabled(True, "test")
        assert wait_until(lambda: svc.mode == "error")
        time.sleep(0.3)
        assert len(attempts) == 1 and control.is_enabled() is False and not lock_is_held()
        assert "model plans unavailable" in svc.status()["errors"][-1]
    finally:
        svc.stop()


class FakeEngine:
    def __init__(self, t):
        self.t, self.live_speed, self.calls, self.on_advance = t, 1.0, 0, None

    def advance(self, to):
        self.t, self.calls = to, self.calls + 1
        if self.on_advance:
            self.on_advance(self.calls)

    def flush(self, to):
        pass


class DummyPrefetcher:
    thread = None
    calls = 0

    def __init__(self, *a, **k):
        pass

    def start(self):
        pass

    def shutdown(self):
        pass


@pytest.fixture
def startup_env(monkeypatch, conn):
    now = clock.utcnow().timestamp()
    eng = FakeEngine(now - 4 * 86400)
    monkeypatch.setattr(service_mod.backfill, "prepare", lambda *a, **k: {})
    monkeypatch.setattr(service_mod.backfill, "make_engine", lambda *a, **k: eng)
    monkeypatch.setattr(service_mod, "Prefetcher", DummyPrefetcher)
    monkeypatch.setattr(Service, "_run_rules", lambda self, *a, **k: None)
    svc = Service(llm=StubLLM(offline=True))
    return svc, eng, conn


def test_startup_catches_up_to_now_then_goes_live(startup_env):
    svc, eng, conn = startup_env
    control.set_enabled(True, "test")
    assert svc._startup(conn) is True
    assert svc.mode == "live" and abs(eng.t - clock.utcnow().timestamp()) < 5 and svc.prefetch is not None


def test_switching_off_during_catch_up_stops_it_at_a_day_boundary_and_never_goes_live(startup_env):
    svc, eng, conn = startup_env
    control.set_enabled(True, "test")
    eng.on_advance = lambda n: control.set_enabled(False, "test") if n == 2 else None
    assert svc._startup(conn) is False
    assert eng.calls == 2 and svc.prefetch is None and svc.mode == "catching_up"
    assert eng.t < clock.utcnow().timestamp() - 86400            # stopped part-way, as asked


# ------------------------------------------------------------------ app: top-bar toggle and the empty-database gate
def test_top_bar_toggle_is_off_by_default_and_drives_the_shared_switch(conn):
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_string("from app.components import header\nheader.clock_strip()", default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.toggle[0].label == "Live generation" and at.toggle[0].value is False
    assert "Data frozen at" in at.markdown[0].value and "live generation OFF" in at.markdown[0].value
    at.toggle[0].set_value(True).run()
    assert control.is_enabled() is True and control.read()["by"] == "app"
    control.set_enabled(False, "cli")                           # flipped from the terminal: the app follows on its next refresh
    at.run()
    assert at.toggle[0].value is False


def test_empty_database_with_generation_off_explains_how_to_build_data_and_offers_a_button(conn):
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(ROOT / "app" / "main.py"), default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    assert any("No simulated data yet" in t.value for t in at.title)
    assert control.is_enabled() is False
    at.button[0].click()
    with pytest.raises(RuntimeError):          # the progress screen then polls until a service reports data; a test has no service
        at.run(timeout=3)
    assert control.is_enabled() is True and control.read()["by"] == "app"


# ------------------------------------------------------------------ the terminal command
def live_cli(*args):
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "live.py"), *args], capture_output=True, text=True, cwd=ROOT, timeout=60)


def test_cli_status_on_off_and_toggle():
    r = live_cli("status")
    assert r.returncode == 0 and "Live generation: OFF (default)" in r.stdout and "App: not running" in r.stdout
    r = live_cli("on", "--no-wait")
    assert r.returncode == 0 and "switched ON" in r.stdout and "No running app was found" in r.stdout
    assert control.is_enabled() and control.read()["by"] == "cli"
    assert "Live generation: ON" in live_cli("status").stdout
    r = live_cli("toggle", "--no-wait")
    assert "switched OFF" in r.stdout and not control.is_enabled()
    control.beat("live")                                        # an app is running: no 'start an app' hint
    r = live_cli("off", "--no-wait")
    assert r.returncode == 0 and "No running app" not in r.stdout
    assert "App: running" in live_cli("status").stdout


def test_cli_reports_the_data_time_of_the_database(conn):
    db.kv_set(conn, "watermark_utc", "2026-10-05T18:09:35Z")
    out = live_cli("status").stdout
    assert "Data time: 05 Oct 2026 19:09:35 WAT" in out and "behind real time" in out
