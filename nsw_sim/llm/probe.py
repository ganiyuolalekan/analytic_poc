"""Capability probe (Section 4.2). Writes ``data/llm_capabilities.json``; never prints secrets.

The probe is authoritative: it tries (base URL, model) candidate pairs and records the first pair that
completes a chat call, then measures JSON modes, tool calling, streaming, reasoning behaviour and a
small parallel burst to pick a safe concurrency.
"""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from nsw_sim import clock
from nsw_sim.config import env_first, get_logger, load_env, redact, settings
from nsw_sim.llm.client import capabilities_path, derive_runtime_base_url, env_base_url, region_from_env

log = get_logger("nsw.probe")

_TOOLS = [{"type": "function", "function": {
    "name": "add", "description": "Add two numbers.",
    "parameters": {"type": "object", "properties": {"x": {"type": "number"}, "y": {"type": "number"}},
                   "required": ["x", "y"]}}}]


def _client(base_url: str, api_key: str, timeout: float = 60):
    from openai import OpenAI
    return OpenAI(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=0)


def _try(fn) -> dict:
    t0 = time.monotonic()
    try:
        out = fn()
        return {"ok": True, "latency_s": round(time.monotonic() - t0, 2), **(out or {})}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "latency_s": round(time.monotonic() - t0, 2), "error": redact(f"{type(e).__name__}: {e}")[:240]}


def _basic(c, model: str) -> dict:
    """Basic chat; also determines which token parameter the route accepts."""
    msgs = [{"role": "user", "content": "Reply with the single word: ready"}]
    last = None
    for tp in ("max_completion_tokens", "max_tokens"):
        r = _try(lambda tp=tp: _chat_once(c, model, msgs, **{tp: 200}))
        if r["ok"]:
            r["token_param"] = tp
            return r
        last = r
    return last or {"ok": False}


def _chat_once(c, model, msgs, **kw) -> dict:
    resp = c.chat.completions.create(model=model, messages=msgs, **kw)
    m = resp.choices[0].message
    return {"text": (m.content or "")[:40], "usage": resp.usage.model_dump(exclude_none=True) if resp.usage else None,
            "separate_reasoning": bool(m.model_extra and any("reason" in k for k in m.model_extra))}


def probe_pair(base_url: str, api_key: str, model: str) -> dict:
    c = _client(base_url, api_key)
    out: dict[str, Any] = {"base_url": base_url, "model": model}
    out["chat"] = _basic(c, model)
    if not out["chat"]["ok"]:
        return out
    tp = out["chat"].get("token_param", "max_completion_tokens")
    tkw = {tp: 400}
    # --- JSON modes
    jmsg = [{"role": "user", "content": 'Return JSON with keys a (integer 1) and b (string "two").'}]
    schema = {"type": "json_schema", "json_schema": {"name": "x", "strict": True, "schema": {
        "type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "string"}},
        "required": ["a", "b"], "additionalProperties": False}}}
    js = _try(lambda: _json_check(c, model, jmsg, schema, tkw))
    jo = _try(lambda: _json_check(c, model, jmsg, {"type": "json_object"}, tkw))
    out["json"] = {"json_schema": js["ok"], "json_object": jo["ok"],
                   "preferred": "json_object" if jo["ok"] else ("json_schema" if js["ok"] else "prompt_only"),
                   "note": "Schemas with free-form maps are not strict-compatible, so json_object + pydantic is preferred."}
    # --- tool calling (with a tool-result round trip)
    out["tools"] = _try(lambda: _tool_check(c, model, tkw))
    # --- streaming
    out["streaming"] = {"basic": _try(lambda: _stream_check(c, model, tkw, tools=None))["ok"],
                        "with_tools": _try(lambda: _stream_check(c, model, tkw, tools=_TOOLS))["ok"]}
    # --- reasoning behaviour
    msgs = [{"role": "user", "content": "What is 17*23? Answer with only the number."}]
    eff = {}
    for e in ("low", "medium"):
        eff[e] = _try(lambda e=e: _chat_once(c, model, msgs, reasoning_effort=e, **tkw))
    reasoning_toks = None
    for e in eff.values():
        if e["ok"] and e.get("usage"):
            reasoning_toks = (e["usage"].get("completion_tokens_details") or {}).get("reasoning_tokens")
    out["reasoning"] = {"effort_param": eff["low"]["ok"] and eff["medium"]["ok"],
                        "separate_content": out["chat"].get("separate_reasoning", False),
                        "reasoning_tokens_seen": reasoning_toks,
                        "policy": "reasoning content is ignored and never displayed; effort=low for data, medium for chat"}
    # --- rate limits: small parallel burst
    def one(_i):
        return _try(lambda: _chat_once(c, model, msgs, **tkw))
    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=6) as ex:
        res = list(ex.map(one, range(6)))
    ok = sum(1 for r in res if r["ok"])
    out["rate_limit"] = {"burst": 6, "ok": ok, "wall_s": round(time.monotonic() - t0, 2),
                         "max_concurrency_recommended": 6 if ok == 6 else max(1, ok),
                         "errors": [r.get("error") for r in res if not r["ok"]][:2]}
    return out


def _json_check(c, model, msgs, fmt, tkw) -> dict:
    r = c.chat.completions.create(model=model, messages=msgs, response_format=fmt, **tkw)
    d = json.loads(r.choices[0].message.content)
    assert isinstance(d, dict) and d.get("a") == 1
    return {}


def _tool_check(c, model, tkw) -> dict:
    msgs = [{"role": "user", "content": "Use the add tool to add 2 and 3."}]
    r = c.chat.completions.create(model=model, messages=msgs, tools=_TOOLS, **tkw)
    m = r.choices[0].message
    if not m.tool_calls:
        raise RuntimeError("no tool_calls returned")
    tc = m.tool_calls[0]
    msgs2 = msgs + [{"role": "assistant", "content": None, "tool_calls": [{
        "id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}]},
        {"role": "tool", "tool_call_id": tc.id, "content": json.dumps({"result": 5})}]
    r2 = c.chat.completions.create(model=model, messages=msgs2, tools=_TOOLS, **tkw)
    return {"tool_call_name": tc.function.name, "round_trip": "5" in (r2.choices[0].message.content or "")}


def _stream_check(c, model, tkw, tools) -> dict:
    msgs = [{"role": "user", "content": "Use the add tool to add 2 and 3." if tools else "Count from 1 to 5."}]
    kw = dict(tkw)
    if tools:
        kw["tools"] = tools
    n = 0
    for ch in c.chat.completions.create(model=model, messages=msgs, stream=True, **kw):
        if ch.choices and (ch.choices[0].delta.content or ch.choices[0].delta.tool_calls):
            n += 1
    if n == 0:
        raise RuntimeError("no streamed chunks")
    return {"chunks": n}


def _models_endpoint(base_url: str, api_key: str) -> dict:
    def go():
        ms = _client(base_url, api_key, 30).models.list()
        ids = sorted(m.id for m in ms.data)
        return {"count": len(ids), "ids": ids}
    return _try(go)


def candidate_pairs() -> list[tuple[str, str, str]]:
    """(base_url, model, why) in the order they are tried."""
    load_env()
    s = settings()["llm"]
    shared, _ = env_first("LLM_MODEL", "MODEL", "BEDROCK_MODEL_ID")
    d, _ = env_first("LLM_MODEL_DATA")
    models = [m for m in dict.fromkeys([d, shared] if (d or shared) else s["model_candidates"]) if m]
    if d or shared:  # explicit env choice first, then the user-preferred candidates as a safety net
        models += [m for m in s["model_candidates"] if m not in models]
    urls: list[tuple[str, str]] = []
    explicit, name = env_first("LLM_BASE_URL")
    if explicit:
        urls.append((explicit, name or "LLM_BASE_URL"))
    eurl, ename = env_base_url()
    if eurl and (eurl, ename) not in urls and eurl not in [u for u, _ in urls]:
        urls.append((eurl, ename or "env"))
    derived = derive_runtime_base_url(region_from_env())
    if derived not in [u for u, _ in urls]:
        urls.append((derived, "derived_from_region"))
    return [(u, m, why) for m in models for u, why in urls]


def run_probe(write: bool = True) -> dict:
    load_env()
    key, key_name = env_first("OPENAI_API_KEY", "AWS_BEARER_TOKEN_BEDROCK")
    report: dict[str, Any] = {"probed_at": clock.iso(clock.utcnow()),
                              "env_vars_detected": [n for n in (
                                  "OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_API_BASE", "OPENAI_PROJECT_ID",
                                  "AWS_BEARER_TOKEN_BEDROCK", "AWS_REGION", "AWS_DEFAULT_REGION", "LLM_MODEL",
                                  "LLM_MODEL_DATA", "LLM_MODEL_CHAT", "MODEL", "BEDROCK_MODEL_ID", "CHAT_MODEL")
                                  if env_first(n)[0]],
                              "key_present": bool(key), "attempts": []}
    if not key:
        report["resolved"] = None
        report["error"] = "no API key in .env: the app will run in offline (fallback) mode"
        return _finish(report, write)
    pairs = candidate_pairs()
    chosen = None
    models_by_url: dict[str, dict] = {}
    for base, model, why in pairs:
        if base not in models_by_url:
            models_by_url[base] = _models_endpoint(base, key)
        full = probe_pair(base, key, model)
        report["attempts"].append({"base_url": base, "base_url_source": why, "model": model,
                                   "chat_ok": full["chat"]["ok"], "error": full["chat"].get("error")})
        if full["chat"]["ok"]:
            chosen = (base, model, why, full)
            break
    if not chosen:
        report["resolved"] = None
        report["error"] = "no candidate (base URL, model) pair completed a chat call: the app will run offline"
        return _finish(report, write)
    base, model, why, full = chosen
    report.update({k: v for k, v in full.items() if k not in ("base_url", "model")})
    report["models_endpoint"] = {"base_url": base, **{k: v for k, v in models_by_url[base].items() if k != "ids"},
                                 "model_ids_sample": (models_by_url[base].get("ids") or [])[:60]}
    report["resolved"] = {"base_url": base, "base_url_source": why, "model_data": model, "model_chat": model,
                          "selection_reason": ("user-preferred GPT-5.5 inference profile" if "gpt-5.5" in model
                                               else "first working candidate")}
    return _finish(report, write)


def _finish(report: dict, write: bool) -> dict:
    if write:
        capabilities_path().write_text(json.dumps(report, indent=2))
    return report


def summarize(report: dict) -> str:
    """Human summary with no secrets: host + model only."""
    from urllib.parse import urlparse
    r = report.get("resolved")
    if not r:
        return f"PROBE FAILED: {report.get('error')}; attempts={len(report.get('attempts', []))}"
    host = urlparse(r["base_url"]).netloc
    lines = [f"LLM key: {'present' if report.get('key_present') else 'absent'} · base URL host: {host} · "
             f"model id: {r['model_data']} ({r['selection_reason']})",
             f"  chat: {report['chat']['latency_s']}s token_param={report['chat'].get('token_param')}",
             f"  json: {report['json']}",
             f"  tools: ok={report['tools']['ok']} round_trip={report['tools'].get('round_trip')}",
             f"  streaming: {report['streaming']}",
             f"  reasoning: effort_param={report['reasoning']['effort_param']} separate={report['reasoning']['separate_content']}",
             f"  burst: {report['rate_limit']['ok']}/{report['rate_limit']['burst']} ok in {report['rate_limit']['wall_s']}s"
             f" -> concurrency {report['rate_limit']['max_concurrency_recommended']}"]
    return "\n".join(lines)
