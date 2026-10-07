"""A full disk, a broken stream or a bad host must never turn a working AI into "unavailable", and when something is wrong the reason has to reach the logs (a new host cannot be inspected any other way)."""
from __future__ import annotations

import sqlite3
import threading
import types

import pytest

from nsw_sim.llm import cache, selfcheck
from nsw_sim.llm.client import LLM, STATUS, LLMConfig


def _chunk(text):
    return types.SimpleNamespace(usage=None, choices=[types.SimpleNamespace(delta=types.SimpleNamespace(content=text, tool_calls=None))])


def _response(text):
    return types.SimpleNamespace(choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=text, tool_calls=None), finish_reason="stop")],
                                 usage=types.SimpleNamespace(prompt_tokens=3, completion_tokens=2))


class FakeSDK:
    """Stands in for the OpenAI client. ``stream_error`` / ``plain_error`` make that kind of request fail."""

    def __init__(self, stream_error: Exception | None = None, plain_error: Exception | None = None) -> None:
        self.stream_error, self.plain_error, self.calls = stream_error, plain_error, []
        self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self._create))

    def _create(self, **kw):
        kind = "stream" if kw.get("stream") else "plain"
        self.calls.append(kind)
        err = self.stream_error if kind == "stream" else self.plain_error
        if err:
            raise err
        return iter([_chunk("hel"), _chunk("lo")]) if kind == "stream" else _response("hello")


def _reset_status():
    STATUS.state, STATUS.reason, STATUS.consec_fail, STATUS.last_error, STATUS.forced_offline = "live", "", 0, None, False


@pytest.fixture
def llm(conn):
    _reset_status()
    client = LLM(LLMConfig("k" * 20, "http://example.invalid/v1", "m", "m", {}), offline=False, conn=conn)
    client._client = FakeSDK()
    yield client
    _reset_status()


ASK = [{"role": "user", "content": "How are we doing?"}]


def _stream(llm):
    deltas, final = [], None
    for kind, payload in llm.chat_stream(ASK, None, role="chat"):
        if kind == "delta":
            deltas.append(payload)
        else:
            final = payload
    return "".join(deltas), final


def test_a_full_disk_does_not_discard_an_answer(llm, monkeypatch):
    def full():
        raise sqlite3.OperationalError("database or disk is full")
    monkeypatch.setattr(cache, "_get", full)
    monkeypatch.setattr(cache, "_warned", False)
    plain = llm.chat(ASK)
    text, final = _stream(llm)
    assert plain.ok and plain.content == "hello"
    assert final.ok and final.content == "hello" and text == "hello"
    assert STATUS.state == "live"


def test_a_broken_stream_is_retried_as_a_plain_request(llm):
    llm._client = FakeSDK(stream_error=RuntimeError("stream broke"))
    text, final = _stream(llm)
    assert final.ok and final.content == "hello" and text == "hello"
    assert llm._client.calls == ["stream", "plain"]
    assert STATUS.state == "live" and STATUS.consec_fail == 0


def test_when_both_fail_it_is_one_failure_with_a_reason(llm):
    llm._client = FakeSDK(stream_error=RuntimeError("stream broke"), plain_error=RuntimeError("the endpoint is down"))
    _, final = _stream(llm)
    assert not final.ok and "the endpoint is down" in final.error
    assert STATUS.consec_fail == 1


def test_selfcheck_reports_a_healthy_host(llm):
    lines = selfcheck.collect(llm)
    text = "\n".join(lines)
    assert "plain request: ok" in text and "streaming request with tools: ok" in text
    assert "answer cache working" in text and "FAILED" not in text and "NOT" not in text


def test_selfcheck_names_the_reason_and_hides_the_key(llm, monkeypatch):
    secret = "sk-" + "a" * 30
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    llm._client = FakeSDK(stream_error=RuntimeError(f"401 rejected {secret}"), plain_error=RuntimeError(f"401 rejected {secret}"))
    text = "\n".join(selfcheck.collect(llm))
    assert "plain request: FAILED" in text and "streaming request with tools: FAILED" in text and "401 rejected" in text
    assert secret not in text


def test_selfcheck_makes_no_request_when_the_ai_is_off(llm):
    llm.set_offline(True)
    llm._client = FakeSDK(plain_error=AssertionError("must not be called"), stream_error=AssertionError("must not be called"))
    text = "\n".join(selfcheck.collect(llm))
    assert "no request was made" in text and llm._client.calls == []


def test_selfcheck_starts_once_per_process(llm, monkeypatch):
    done, calls = threading.Event(), []
    monkeypatch.setattr(selfcheck, "_started", False)
    monkeypatch.setattr(selfcheck, "report", lambda m: (calls.append(m), done.set()))
    selfcheck.start(llm)
    assert done.wait(5)
    selfcheck.start(llm)
    assert len(calls) == 1
