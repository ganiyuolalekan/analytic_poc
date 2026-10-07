"""A one-time check of the AI connection and of the host, written to the logs and nowhere else (never to the screen, never a secret).
When the assistant says it is unavailable on a new host such as Streamlit Community Cloud, the reason is in these lines: Manage app > Logs, search for "NSW self-check"."""
from __future__ import annotations

import importlib.metadata as md
import platform
import shutil
import threading
import time
import uuid
from urllib.parse import urlparse

from nsw_sim.config import data_dir, get_logger, redact
from nsw_sim.llm import cache

log = get_logger("nsw.selfcheck")
_started = False
_lock = threading.Lock()


def _version(pkg: str) -> str:
    try:
        return md.version(pkg)
    except md.PackageNotFoundError:
        return "not installed"


def _host_lines() -> list[str]:
    d = data_dir()
    try:
        free = f"{shutil.disk_usage(d).free / 1e9:.1f} GB free"
    except OSError as e:
        free = f"disk unknown ({type(e).__name__})"
    probe = d / f".selfcheck-{uuid.uuid4().hex[:6]}"
    try:
        probe.write_text("ok")
        probe.unlink()
        writable = "writable"
    except OSError as e:
        writable = f"NOT writable ({type(e).__name__}: {e})"
    key = f"selfcheck-{uuid.uuid4().hex}"
    cache.put(key, "selfcheck", "-", "ok", 0, 0)
    saved = cache.get(key) is not None
    cache.discard(key)
    return [f"python {platform.python_version()} · openai {_version('openai')} · streamlit {_version('streamlit')}",
            f"data folder {d} · {free} · {writable} · answer cache {'working' if saved else 'NOT working'}"]


def _try(label: str, fn) -> str:
    t0 = time.monotonic()
    try:
        ok, detail = fn()
    except Exception as e:  # noqa: BLE001 - this is the diagnostic: report whatever went wrong
        ok, detail = False, f"{type(e).__name__}: {e}"
    return f"{label}: {'ok' if ok else 'FAILED'} in {time.monotonic() - t0:.1f}s" + ("" if ok else f" · {redact(detail)[:300]}")


def collect(llm) -> list[str]:
    """The lines of the report. Makes two tiny real requests (plain, then streaming with a tool) unless the AI is switched off."""
    cfg = llm.cfg
    base = urlparse(cfg.base_url or "")
    lines = _host_lines()
    lines.append(f"AI key {'present' if cfg.api_key else 'MISSING'} · endpoint {base.netloc}{base.path} (from {cfg.base_url_source}) · model {cfg.model_chat} · capabilities file {'found' if cfg.caps else 'not found'}")
    if llm.offline:
        lines.append("AI is switched off or has no key: no request was made")
        return lines
    nonce = uuid.uuid4().hex[:6]            # a fresh wording each start, so the answer cache cannot stand in for a real request

    def plain():
        r = llm.chat([{"role": "user", "content": f"Reply with the single word: ready ({nonce})"}], max_tokens=40, refresh=True)
        return r.ok, r.error or ""

    def streaming():
        spec = [{"type": "function", "function": {"name": "ping", "description": "Not needed for this request.", "parameters": {"type": "object", "properties": {}}}}]
        final = None
        for kind, payload in llm.chat_stream([{"role": "user", "content": f"Reply with the single word: ready ({nonce}b)"}], spec, role="chat", max_tokens=40):
            if kind == "final":
                final = payload
        return bool(final and final.ok), (final.error if final else "") or ""

    lines.append(_try("plain request", plain))
    lines.append(_try("streaming request with tools", streaming))
    return lines


def report(llm) -> None:
    lines = collect(llm)
    failed = any("FAILED" in x or "NOT " in x or "MISSING" in x for x in lines)
    (log.warning if failed else log.info)("NSW self-check%s\n  %s", " (something is wrong)" if failed else "", "\n  ".join(lines))


def start(llm) -> None:
    """Once per process, in the background: the page never waits for it."""
    global _started
    with _lock:
        if _started:
            return
        _started = True
    threading.Thread(target=report, args=(llm,), name="nsw-selfcheck", daemon=True).start()
