import json
import logging
import re
from pathlib import Path

import pytest

from nsw_sim import config
from nsw_sim.llm import cache
from nsw_sim.llm.client import LLM, STATUS, ChatResult, LLMConfig, StubLLM, extract_json, resolve_llm_settings

ROOT = Path(__file__).resolve().parent.parent


def test_no_anthropic_imports_anywhere():
    """Section 4.5: product code, scripts and tests must not import the anthropic package."""
    pat = re.compile(r"^\s*(import|from)\s+anthropic(\s|\.|$)", re.M)
    offenders = []
    for folder in ("nsw_sim", "app", "scripts", "tests"):
        for f in (ROOT / folder).rglob("*.py"):
            if f.name == Path(__file__).name:
                continue
            if pat.search(f.read_text(encoding="utf-8")):
                offenders.append(str(f))
    assert not offenders, offenders


def test_extract_json_handles_fences_and_prose():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Here you go: {"a": [1, 2]} thanks') == {"a": [1, 2]}
    with pytest.raises(ValueError):
        extract_json("no json here")


def test_cache_roundtrip_and_key_sensitivity(tmp_path):
    k1 = cache.make_key("profile", "m", "sys", "user", "v1")
    k2 = cache.make_key("profile", "m", "sys", "user", "v2")
    assert k1 != k2
    assert cache.get(k1) is None
    cache.put(k1, "profile", "m", '{"x":1}', 10, 20)
    assert cache.get(k1)["response"] == '{"x":1}'


def test_offline_mode_returns_no_result_without_network():
    llm = LLM(LLMConfig(api_key="k", base_url="http://127.0.0.1:9", model_data="m", model_chat="m"), offline=True)
    r = llm.json_call("profile", "s", "u")
    assert not r.ok and r.error == "offline"
    assert not llm.chat([{"role": "user", "content": "hi"}]).ok
    assert STATUS.state == "offline"
    llm.set_offline(False)
    assert STATUS.state == "live"


def test_missing_key_is_offline():
    llm = LLM(LLMConfig(api_key=None, base_url="http://x", model_data="m", model_chat="m"), offline=False)
    assert llm.offline


def test_secret_redaction_in_logs_and_helpers(monkeypatch, caplog):
    monkeypatch.setenv("OPENAI_API_KEY", "ABSKQmVkcm9ja0FQSUtleS1zdXBlci1zZWNyZXQtdmFsdWUtMTIzNDU2Nzg5MA==")
    assert "ABSK" not in config.redact("failed with key ABSKQmVkcm9ja0FQSUtleS1zdXBlci1zZWNyZXQtdmFsdWUtMTIzNDU2Nzg5MA== !")
    log = config.get_logger("nsw.test_redact")
    with caplog.at_level(logging.INFO, logger="nsw.test_redact"):
        log.info("token=%s", "ABSKQmVkcm9ja0FQSUtleS1zdXBlci1zZWNyZXQtdmFsdWUtMTIzNDU2Nzg5MA==")
    assert all("ABSKQmVk" not in r.getMessage() for r in caplog.records)


def test_stub_llm_follows_script():
    stub = StubLLM(handlers={"profile": lambda s, u: json.dumps({"ok": True})},
                   chat_script=[lambda m: ChatResult("hello", [])])
    assert json.loads(stub.json_call("profile", "s", "u").text) == {"ok": True}
    assert stub.chat([{"role": "user", "content": "x"}]).content == "hello"


def test_resolve_prefers_env_overrides(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "k" * 20)
    monkeypatch.setenv("LLM_MODEL_DATA", "custom.data")
    monkeypatch.setenv("LLM_MODEL_CHAT", "custom.chat")
    cfg = resolve_llm_settings()
    assert (cfg.model_data, cfg.model_chat) == ("custom.data", "custom.chat")


def test_default_model_is_gpt55_profile(monkeypatch):
    for v in ("LLM_MODEL", "MODEL", "BEDROCK_MODEL_ID", "LLM_MODEL_DATA", "LLM_MODEL_CHAT", "CHAT_MODEL"):
        monkeypatch.delenv(v, raising=False)
    cfg = resolve_llm_settings()
    assert "gpt-5.5" in cfg.model_data
