"""OpenAI-compatible LLM client (Section 4.3).

* Base URL / model come from ``.env`` (+ the capability probe, which is authoritative).
* tenacity retries (429/5xx/timeouts), global concurrency semaphore, calls-per-minute limiter.
* Cache keyed by (role, model, system, user, schema version); ``refresh=True`` bypasses it.
* Every call is logged to ``llm_calls`` (no prompts, no secrets).
* ``STATUS`` (live | degraded | offline) is exposed to the UI header.
"""
from __future__ import annotations

import json
import re
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator
from urllib.parse import urlparse

from nsw_sim import clock, db
from nsw_sim.config import data_dir, env_first, get_logger, load_env, offline_forced, redact, settings
from nsw_sim.llm import cache

log = get_logger("nsw.llm")

DATA_ROLES = {"profile", "flow_plan", "ops_plan", "director", "feed_items", "digest", "paraphrase"}
CHAT_ROLES = {"chat", "narrator", "storyline", "eval_judge"}


# --------------------------------------------------------------------------- configuration
@dataclass
class LLMConfig:
    api_key: str | None
    base_url: str | None
    model_data: str
    model_chat: str
    caps: dict = field(default_factory=dict)
    base_url_source: str = ""
    model_source: str = ""


def capabilities_path() -> Path:
    return data_dir() / "llm_capabilities.json"


def load_capabilities() -> dict:
    p = capabilities_path()
    if p.exists():
        try:
            return json.loads(p.read_text())
        except (OSError, ValueError):
            return {}
    return {}


def derive_runtime_base_url(region: str | None = None) -> str:
    s = settings()["llm"]
    return s["runtime_base_url_template"].format(region=region or region_from_env() or s["default_region"])


def region_from_env() -> str | None:
    v, _ = env_first("AWS_REGION", "AWS_DEFAULT_REGION")
    if v:
        return v
    base, _ = env_first("OPENAI_BASE_URL", "OPENAI_API_BASE", "LLM_BASE_URL")
    if base:
        m = re.search(r"\.([a-z]{2}-[a-z]+-\d)\.", urlparse(base).netloc)
        if m:
            return m.group(1)
    return None


def env_base_url() -> tuple[str | None, str | None]:
    return env_first("LLM_BASE_URL", "OPENAI_BASE_URL", "OPENAI_API_BASE")


def resolve_llm_settings() -> LLMConfig:
    """Resolve key, base URL and models. Env overrides > probe result > settings defaults."""
    load_env()
    s = settings()["llm"]
    key, _ = env_first("OPENAI_API_KEY", "AWS_BEARER_TOKEN_BEDROCK")
    caps = load_capabilities()
    resolved = caps.get("resolved", {}) if caps else {}

    explicit_url, url_name = env_first("LLM_BASE_URL")
    if explicit_url:
        base, base_src = explicit_url, url_name
    elif resolved.get("base_url"):
        base, base_src = resolved["base_url"], "probe"
    else:
        eurl, ename = env_base_url()
        base, base_src = (eurl, ename) if eurl else (derive_runtime_base_url(), "derived_from_region")

    shared, shared_name = env_first("LLM_MODEL", "MODEL", "BEDROCK_MODEL_ID")
    data_m, data_name = env_first("LLM_MODEL_DATA")
    chat_m, chat_name = env_first("LLM_MODEL_CHAT", "CHAT_MODEL")
    default = resolved.get("model_data") or s["model_candidates"][0]
    default_chat = resolved.get("model_chat") or default
    model_data = data_m or shared or default
    model_chat = chat_m or shared or default_chat
    src = data_name or shared_name or ("probe" if resolved else "settings.preferred_model")
    return LLMConfig(api_key=key, base_url=base, model_data=model_data, model_chat=model_chat, caps=caps,
                     base_url_source=base_src or "", model_source=src)


# --------------------------------------------------------------------------- status
class LLMStatus:
    """live | degraded | offline, with latency stats and a change callback (for feed events)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.state = "live"
        self.reason = ""
        self.consec_fail = 0
        self.last_ok_at: str | None = None
        self.last_error: str | None = None
        self.latencies: deque[int] = deque(maxlen=50)
        self.on_change: list[Callable[[str, str, str], None]] = []
        self.forced_offline = False

    def set(self, state: str, reason: str = "") -> None:
        with self._lock:
            old = self.state
            self.state, self.reason = state, reason
        if old != state:
            for cb in list(self.on_change):
                try:
                    cb(old, state, reason)
                except Exception:  # pragma: no cover
                    log.exception("status callback failed")

    def ok(self, latency_ms: int) -> None:
        self.consec_fail = 0
        self.last_ok_at = clock.iso(clock.utcnow())
        self.latencies.append(latency_ms)
        if self.state == "degraded":
            self.set("live", "recovered")

    def fail(self, err: str) -> None:
        self.consec_fail += 1
        self.last_error = redact(err)[:300]
        if self.consec_fail >= 2 and self.state == "live":
            self.set("degraded", self.last_error or "repeated failures")

    @property
    def avg_latency_ms(self) -> int:
        return int(sum(self.latencies) / len(self.latencies)) if self.latencies else 0

    def snapshot(self) -> dict:
        return {"state": self.state, "reason": self.reason, "last_ok_at": self.last_ok_at,
                "last_error": self.last_error, "avg_latency_ms": self.avg_latency_ms,
                "consec_fail": self.consec_fail}


STATUS = LLMStatus()


# --------------------------------------------------------------------------- helpers
@dataclass
class RawResult:
    text: str | None
    ok: bool
    error: str | None = None
    from_cache: bool = False
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    call_id: str = ""
    model: str = ""


@dataclass
class ChatResult:
    content: str
    tool_calls: list[dict]            # [{"id","name","arguments"(str JSON)}]
    ok: bool = True
    error: str | None = None
    from_cache: bool = False
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    call_id: str = ""

    def as_message(self) -> dict:
        m: dict[str, Any] = {"role": "assistant", "content": self.content or None}
        if self.tool_calls:
            m["tool_calls"] = [{"id": t["id"], "type": "function",
                                "function": {"name": t["name"], "arguments": t["arguments"]}} for t in self.tool_calls]
        return m


_FENCE = re.compile(r"^```[a-zA-Z0-9_-]*\s*|\s*```$", re.M)


def extract_json(text: str) -> Any:
    """Parse JSON from model text: strips code fences / prose around the outermost object or array."""
    if text is None:
        raise ValueError("no text")
    t = _FENCE.sub("", text.strip()).strip()
    try:
        return json.loads(t)
    except ValueError:
        pass
    starts = [i for i in (t.find("{"), t.find("[")) if i >= 0]
    if not starts:
        raise ValueError("no JSON found")
    i = min(starts)
    close = "}" if t[i] == "{" else "]"
    j = t.rfind(close)
    if j <= i:
        raise ValueError("unterminated JSON")
    return json.loads(t[i:j + 1])


class _RateLimiter:
    def __init__(self, per_minute: int) -> None:
        self.per_minute = max(1, per_minute)
        self._ts: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                while self._ts and now - self._ts[0] > 60:
                    self._ts.popleft()
                if len(self._ts) < self.per_minute:
                    self._ts.append(now)
                    return
                wait = 60 - (now - self._ts[0])
            time.sleep(max(0.05, wait))


# --------------------------------------------------------------------------- the client
class LLM:
    """Wraps the OpenAI SDK pointed at ``base_url``. Safe to share across threads."""

    def __init__(self, cfg: LLMConfig | None = None, *, offline: bool | None = None, conn=None) -> None:
        self.cfg = cfg or resolve_llm_settings()
        s = settings()["llm"]
        caps = self.cfg.caps or {}
        self.concurrency = int(caps.get("rate_limit", {}).get("max_concurrency_recommended") or s["concurrency"])
        self._sem = threading.BoundedSemaphore(max(1, min(self.concurrency, int(s["concurrency"]))))
        self._rl = _RateLimiter(int(s["max_calls_per_minute"]))
        self._client = None
        self._client_lock = threading.Lock()
        self._disabled_params: set[str] = set()
        self._run_started = clock.iso(clock.utcnow())
        self._offline = offline_forced() if offline is None else offline
        self._conn = conn
        if self._offline or not self.cfg.api_key:
            STATUS.forced_offline = True
            STATUS.set("offline", "forced offline" if self._offline else "no API key")

    # -- state
    @property
    def offline(self) -> bool:
        return self._offline or not self.cfg.api_key

    def set_offline(self, flag: bool) -> None:
        self._offline = flag
        STATUS.forced_offline = flag
        STATUS.set("offline" if flag or not self.cfg.api_key else "live", "toggled" if flag else "back online")

    def model_for(self, role: str) -> str:
        return self.cfg.model_chat if role in CHAT_ROLES else self.cfg.model_data

    def _sdk(self):
        with self._client_lock:
            if self._client is None:
                from openai import OpenAI
                self._client = OpenAI(api_key=self.cfg.api_key, base_url=self.cfg.base_url, max_retries=0)
            return self._client

    # -- budget
    def tokens_used(self, role_kind: str, since: str) -> int:
        try:
            conn = self._conn or db.connect()
            roles = DATA_ROLES if role_kind == "data" else CHAT_ROLES
            q = ",".join("?" * len(roles))
            row = conn.execute(f"SELECT COALESCE(SUM(tokens_in+tokens_out),0) FROM llm_calls WHERE cached=0 AND ts>=? "
                               f"AND role IN ({q})", (since, *roles)).fetchone()
            if self._conn is None:
                conn.close()
            return int(row[0])
        except Exception:
            return 0

    def budget_exceeded(self, role: str) -> bool:
        s = settings()["llm"]
        if role in CHAT_ROLES:
            return self.tokens_used("chat", clock.iso(clock.utcnow())[:10] + "T00:00:00Z") > s["budget_tokens_per_day"]
        run = self.tokens_used("data", self._run_started)
        day = self.tokens_used("data", clock.iso(clock.utcnow())[:10] + "T00:00:00Z")
        return run > s["budget_tokens_per_run"] or day > s["budget_tokens_per_day"] - s["chat_reserved_tokens"]

    # -- low-level request
    def _kwargs(self, model: str, messages: list[dict], max_tokens: int, effort: str | None, json_mode: bool,
                tools: list[dict] | None = None, stream: bool = False) -> dict:
        caps = self.cfg.caps or {}
        kw: dict[str, Any] = {"model": model, "messages": messages}
        tparam = "max_tokens" if "token_param" in self._disabled_params else \
            caps.get("chat", {}).get("token_param", "max_completion_tokens")
        headroom = float(settings()["llm"]["reasoning_token_headroom"])
        kw[tparam] = int(max_tokens * headroom)
        if effort and "reasoning_effort" not in self._disabled_params and caps.get("reasoning", {}).get("effort_param", True):
            kw["reasoning_effort"] = effort
        if json_mode and "response_format" not in self._disabled_params and \
                caps.get("json", {}).get("json_object", True):
            kw["response_format"] = {"type": "json_object"}
        if tools:
            kw["tools"] = tools
        if stream:
            kw["stream"] = True
            kw["stream_options"] = {"include_usage": True}
        return kw

    def _create(self, kw: dict, timeout: float):
        from openai import APIConnectionError, APITimeoutError, BadRequestError, InternalServerError, RateLimitError
        from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

        retry_max = int(settings()["llm"]["retry_max"])

        @retry(retry=retry_if_exception_type((RateLimitError, APIConnectionError, APITimeoutError, InternalServerError)),
               wait=wait_exponential_jitter(initial=1.0, max=20.0), stop=stop_after_attempt(retry_max), reraise=True)
        def _do():
            self._rl.acquire()
            return self._sdk().chat.completions.create(timeout=timeout, **kw)

        try:
            return _do()
        except BadRequestError as e:  # adapt once to unsupported parameters
            msg = str(e).lower()
            changed = False
            for param, tag in (("reasoning_effort", "reasoning_effort"), ("response_format", "response_format"),
                               ("stream_options", "stream_options")):
                if param in msg and param in kw:
                    kw.pop(param, None)
                    self._disabled_params.add(tag)
                    changed = True
            if "max_completion_tokens" in msg and "max_completion_tokens" in kw:
                kw["max_tokens"] = kw.pop("max_completion_tokens")
                self._disabled_params.add("token_param")
                changed = True
            elif "max_tokens" in msg and "max_tokens" in kw:
                kw["max_completion_tokens"] = kw.pop("max_tokens")
                changed = True
            if changed:
                return _do()
            raise

    def _log(self, call_id: str, role: str, model: str, tin: int, tout: int, ms: int, outcome: str, cached: bool) -> None:
        db.log_llm_call(self._conn, call_id, role, model, tin, tout, ms, outcome, cached)

    def mark(self, call_id: str, outcome: str) -> None:
        """Update the outcome of a logged call (ok|repaired|fallback|error) after validation."""
        try:
            conn = self._conn or db.connect()
            conn.execute("UPDATE llm_calls SET outcome=? WHERE call_id=?", (outcome, call_id))
            if self._conn is None:
                conn.close()
        except Exception:
            pass

    # -- JSON role
    def json_call(self, role: str, system: str, user: str, *, max_tokens: int | None = None,
                  schema_version: str = "v1", refresh: bool = False, effort: str | None = None,
                  timeout: float | None = None) -> RawResult:
        s = settings()["llm"]
        model = self.model_for(role)
        max_tokens = max_tokens or s["max_tokens"].get(role, 1000)
        effort = effort or (s["reasoning_effort_chat"] if role in CHAT_ROLES else s["reasoning_effort_data"])
        call_id = uuid.uuid4().hex[:16]
        if self.offline:
            return RawResult(None, False, "offline", call_id=call_id, model=model)
        if self.budget_exceeded(role):
            STATUS.set("degraded", "LLM budget reached: degraded mode") if role in DATA_ROLES else None
            return RawResult(None, False, "budget", call_id=call_id, model=model)
        key = cache.make_key(role, model, system, user, schema_version, {"mt": max_tokens, "e": effort})
        if not refresh:
            hit = cache.get(key)
            if hit:
                self._log(call_id, role, model, 0, 0, 0, "ok", True)
                return RawResult(hit["response"], True, from_cache=True, call_id=call_id, model=model,
                                 tokens_in=hit["tokens_in"], tokens_out=hit["tokens_out"])
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        timeout = timeout or (s["timeout_chat_s"] if role in CHAT_ROLES else s["timeout_data_s"])
        t0 = time.monotonic()
        try:
            with self._sem:
                resp = self._create(self._kwargs(model, messages, max_tokens, effort, True), timeout)
            ms = int((time.monotonic() - t0) * 1000)
            text = resp.choices[0].message.content or ""
            u = resp.usage
            tin, tout = (u.prompt_tokens, u.completion_tokens) if u else (0, 0)
            if resp.choices[0].finish_reason == "length" and not text.strip():
                raise RuntimeError("completion truncated before any output (raise max_tokens)")
            STATUS.ok(ms)
            self._log(call_id, role, model, tin, tout, ms, "ok", False)
            cache.put(key, role, model, text, tin, tout)
            return RawResult(text, True, tokens_in=tin, tokens_out=tout, latency_ms=ms, call_id=call_id, model=model)
        except Exception as e:  # noqa: BLE001 - network/SDK errors are expected here
            ms = int((time.monotonic() - t0) * 1000)
            err = redact(f"{type(e).__name__}: {e}")[:300]
            STATUS.fail(err)
            self._log(call_id, role, model, 0, 0, ms, "error", False)
            log.warning("LLM json_call(%s) failed: %s", role, err)
            return RawResult(None, False, err, latency_ms=ms, call_id=call_id, model=model)

    # -- chat (assistant)
    def chat(self, messages: list[dict], tools: list[dict] | None = None, *, role: str = "chat",
             max_tokens: int | None = None, refresh: bool = False, effort: str | None = None) -> ChatResult:
        s = settings()["llm"]
        model = self.model_for(role)
        max_tokens = max_tokens or s["max_tokens"].get(role, 700)
        effort = effort or s["reasoning_effort_chat"]
        call_id = uuid.uuid4().hex[:16]
        if self.offline:
            return ChatResult("", [], ok=False, error="offline", call_id=call_id)
        if self.budget_exceeded(role):
            return ChatResult("", [], ok=False, error="budget", call_id=call_id)
        key = cache.make_key(role, model, json.dumps(messages, sort_keys=True, default=str),
                             json.dumps(tools, sort_keys=True), "chat-v1", {"mt": max_tokens, "e": effort})
        if not refresh:
            hit = cache.get(key)
            if hit:
                d = json.loads(hit["response"])
                self._log(call_id, role, model, 0, 0, 0, "ok", True)
                return ChatResult(d["content"], d["tool_calls"], from_cache=True, call_id=call_id,
                                  tokens_in=hit["tokens_in"], tokens_out=hit["tokens_out"])
        t0 = time.monotonic()
        try:
            with self._sem:
                resp = self._create(self._kwargs(model, messages, max_tokens, effort, False, tools), s["timeout_chat_s"])
            ms = int((time.monotonic() - t0) * 1000)
            msg = resp.choices[0].message
            tcs = [{"id": t.id, "name": t.function.name, "arguments": t.function.arguments or "{}"}
                   for t in (msg.tool_calls or [])]
            u = resp.usage
            tin, tout = (u.prompt_tokens, u.completion_tokens) if u else (0, 0)
            STATUS.ok(ms)
            self._log(call_id, role, model, tin, tout, ms, "ok", False)
            cache.put(key, role, model, json.dumps({"content": msg.content or "", "tool_calls": tcs}), tin, tout)
            return ChatResult(msg.content or "", tcs, tokens_in=tin, tokens_out=tout, latency_ms=ms, call_id=call_id)
        except Exception as e:  # noqa: BLE001
            ms = int((time.monotonic() - t0) * 1000)
            err = redact(f"{type(e).__name__}: {e}")[:300]
            STATUS.fail(err)
            self._log(call_id, role, model, 0, 0, ms, "error", False)
            return ChatResult("", [], ok=False, error=err, latency_ms=ms, call_id=call_id)

    def chat_stream(self, messages: list[dict], tools: list[dict] | None = None, *, role: str = "chat",
                    max_tokens: int | None = None) -> Iterator[tuple[str, Any]]:
        """Yield ('delta', text) chunks then ('final', ChatResult). Falls back to non-streaming if unsupported."""
        caps = self.cfg.caps or {}
        if self.offline or not caps.get("streaming", {}).get("basic", True):
            res = self.chat(messages, tools, role=role, max_tokens=max_tokens)
            if res.content:
                yield ("delta", res.content)
            yield ("final", res)
            return
        s = settings()["llm"]
        model = self.model_for(role)
        max_tokens = max_tokens or s["max_tokens"].get(role, 700)
        effort = s["reasoning_effort_chat"]
        key = cache.make_key(role, model, json.dumps(messages, sort_keys=True, default=str),
                             json.dumps(tools, sort_keys=True), "chat-v1", {"mt": max_tokens, "e": effort})
        hit = cache.get(key)
        if hit:
            res = self.chat(messages, tools, role=role, max_tokens=max_tokens)
            text = res.content
            for i in range(0, len(text), 40):
                yield ("delta", text[i:i + 40])
            yield ("final", res)
            return
        call_id = uuid.uuid4().hex[:16]
        t0 = time.monotonic()
        content, tcs, tin, tout = "", {}, 0, 0
        try:
            self._rl.acquire()
            with self._sem:
                stream = self._sdk().chat.completions.create(timeout=s["timeout_chat_s"], **self._kwargs(
                    model, messages, max_tokens, effort, False, tools, stream=True))
                for chunk in stream:
                    if getattr(chunk, "usage", None):
                        tin, tout = chunk.usage.prompt_tokens, chunk.usage.completion_tokens
                    if not chunk.choices:
                        continue
                    d = chunk.choices[0].delta
                    if d.content:
                        content += d.content
                        yield ("delta", d.content)
                    for tc in (d.tool_calls or []):
                        slot = tcs.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                        if tc.id:
                            slot["id"] = tc.id
                        if tc.function and tc.function.name:
                            slot["name"] = tc.function.name
                        if tc.function and tc.function.arguments:
                            slot["arguments"] += tc.function.arguments
            ms = int((time.monotonic() - t0) * 1000)
            calls = [tcs[i] for i in sorted(tcs)]
            STATUS.ok(ms)
            self._log(call_id, role, model, tin, tout, ms, "ok", False)
            cache.put(key, role, model, json.dumps({"content": content, "tool_calls": calls}), tin, tout)
            yield ("final", ChatResult(content, calls, tokens_in=tin, tokens_out=tout, latency_ms=ms, call_id=call_id))
        except Exception as e:  # noqa: BLE001
            err = redact(f"{type(e).__name__}: {e}")[:300]
            STATUS.fail(err)
            self._log(call_id, role, model, 0, 0, int((time.monotonic() - t0) * 1000), "error", False)
            yield ("final", ChatResult(content, [], ok=False, error=err, call_id=call_id))


# --------------------------------------------------------------------------- test stub
class StubLLM:
    """Deterministic stand-in used by tests: no network, scripted responses.

    ``handlers`` maps a role to ``fn(system, user) -> str`` (JSON text). ``chat_script`` is a list of
    ChatResult-producing callables consumed in order by :meth:`chat`.
    """

    def __init__(self, handlers: dict[str, Callable[[str, str], str]] | None = None,
                 chat_script: list[Callable[[list[dict]], ChatResult]] | None = None, offline: bool = False) -> None:
        self.handlers = handlers or {}
        self.chat_script = chat_script or []
        self.calls: list[tuple[str, str]] = []
        self.offline = offline
        self.cfg = LLMConfig("stub", "http://stub", "stub-model", "stub-model", {})

    def model_for(self, role: str) -> str:
        return "stub-model"

    def set_offline(self, flag: bool) -> None:
        self.offline = flag

    def mark(self, call_id: str, outcome: str) -> None:
        pass

    def json_call(self, role: str, system: str, user: str, **kw) -> RawResult:
        self.calls.append((role, user))
        if self.offline or role not in self.handlers:
            return RawResult(None, False, "offline" if self.offline else "no-handler")
        try:
            return RawResult(self.handlers[role](system, user), True, tokens_in=50, tokens_out=50, model="stub-model",
                             call_id=uuid.uuid4().hex[:8])
        except Exception as e:  # noqa: BLE001
            return RawResult(None, False, str(e))

    def chat(self, messages: list[dict], tools: list[dict] | None = None, **kw) -> ChatResult:
        if self.offline or not self.chat_script:
            return ChatResult("", [], ok=False, error="offline")
        return self.chat_script.pop(0)(messages)

    def chat_stream(self, messages, tools=None, **kw):
        res = self.chat(messages, tools, **kw)
        if res.content:
            yield ("delta", res.content)
        yield ("final", res)

    def budget_exceeded(self, role: str) -> bool:
        return False
