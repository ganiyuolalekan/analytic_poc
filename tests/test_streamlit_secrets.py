"""``make streamlit-secrets`` (scripts/streamlit_secrets.py): the Streamlit Secrets box built from .env, valid TOML, never half finished, secrets maskable. Uses fake .env content only."""
import importlib.util
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
ENV = """export OPENAI_API_KEY='sk-fake-key-123'
export OPENAI_BASE_URL='https://mantle.example/v1'
export OPENAI_PROJECT_ID='proj_fake'

# =====================================================================================================================
# FOR STREAMLIT COMMUNITY CLOUD (added 2026-10-06; the steps are in DEPLOY.md)
# These are the settings to paste into Streamlit's Secrets box. They are commented out ON PURPOSE.
# In the Secrets box write every value as a quoted string,
# for example  NSW_VIEW_ONLY = "1".  Do not put a write token anywhere in this file.
#
# The AI (copy the two values from the active lines at the top of this file):
#   OPENAI_API_KEY      same key as above
#   OPENAI_PROJECT_ID   same value as above
# OPENAI_BASE_URL=https://bedrock-runtime.us-east-1.amazonaws.com/openai/v1
#   ^ use THIS address on Streamlit
#
# NSW_ACCESS_CODE=abc123-code
# NSW_VIEW_ONLY=1
# NSW_TOKEN_BUDGET_DAY=1000000
#
# NSW_DB_REPO=someone/nsw-demo-db
# HF_TOKEN=hf_realreadtoken123
#
# NSW_DB_FORCE=1
# =====================================================================================================================
"""
SECRET_VALUES = ("sk-fake-key-123", "proj_fake", "abc123-code", "hf_realreadtoken123")


@pytest.fixture
def tool():
    spec = importlib.util.spec_from_file_location("streamlit_secrets", ROOT / "scripts" / "streamlit_secrets.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_box_is_valid_toml_with_the_ai_values_from_the_active_lines_and_the_rest_from_the_block(tool):
    box = tomllib.loads(tool.render(tool.collect(ENV)))
    assert box == {"OPENAI_API_KEY": "sk-fake-key-123", "OPENAI_PROJECT_ID": "proj_fake", "OPENAI_BASE_URL": "https://bedrock-runtime.us-east-1.amazonaws.com/openai/v1",
                   "NSW_ACCESS_CODE": "abc123-code", "NSW_VIEW_ONLY": "1", "NSW_TOKEN_BUDGET_DAY": "1000000", "NSW_DB_REPO": "someone/nsw-demo-db", "HF_TOKEN": "hf_realreadtoken123"}
    assert "mantle" not in tool.render(tool.collect(ENV))                          # the local endpoint that returns "model not found" is not the one pasted


def test_the_one_boot_reload_switch_is_only_added_when_asked_for(tool):
    assert "NSW_DB_FORCE" not in tomllib.loads(tool.render(tool.collect(ENV)))
    assert tomllib.loads(tool.render(tool.collect(ENV, force_refresh=True)))["NSW_DB_FORCE"] == "1"


def test_awkward_characters_survive_the_round_trip(tool):
    text = ENV.replace("abc123-code", 'a"b\\c d#e')
    assert tomllib.loads(tool.render(tool.collect(text)))["NSW_ACCESS_CODE"] == 'a"b\\c d#e'


def test_an_unfinished_box_is_refused_naming_what_is_missing_but_never_showing_values(tool):
    for bad, expect in ((ENV.replace("someone/nsw-demo-db", "your-hf-name/nsw-demo-db"), "NSW_DB_REPO"), (ENV.replace("hf_realreadtoken123", "hf_paste-the-read-only-token"), "HF_TOKEN"),
                        (ENV.replace("# NSW_ACCESS_CODE=abc123-code", "# NSW_ACCESS_CODE=choose-a-long-random-code"), "NSW_ACCESS_CODE")):
        with pytest.raises(tool.SecretsError) as e:
            tool.collect(bad)
        assert expect in str(e.value) and not any(v in str(e.value) for v in SECRET_VALUES) and "Fill them in" in str(e.value)
    assert tool.collect(ENV.replace("hf_realreadtoken123", "hf_paste-the-read-only-token"), allow_placeholders=True)          # an explicit override


def test_a_missing_block_is_explained(tool):
    with pytest.raises(tool.SecretsError, match="FOR STREAMLIT COMMUNITY CLOUD"):
        tool.collect("export OPENAI_API_KEY='x'\n")


def test_the_project_id_is_optional(tool):
    box = tomllib.loads(tool.render(tool.collect(ENV.replace("export OPENAI_PROJECT_ID='proj_fake'\n", ""))))
    assert "OPENAI_PROJECT_ID" not in box and box["OPENAI_API_KEY"] == "sk-fake-key-123"


def test_the_masked_preview_hides_every_secret_but_shows_the_rest(tool):
    shown = tool.render(tool.collect(ENV), masked=True)
    assert not any(v in shown for v in SECRET_VALUES) and "characters)" in shown
    assert 'NSW_VIEW_ONLY = "1"' in shown and 'NSW_DB_REPO = "someone/nsw-demo-db"' in shown and "https://bedrock-runtime" in shown


def test_the_command_prints_to_the_terminal_leaves_the_env_file_alone_and_errors_show_no_values(tool, tmp_path, capsys, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(ENV)
    before = env.read_bytes()
    monkeypatch.setattr("sys.argv", ["streamlit_secrets.py", "--env", str(env)])
    assert tool.main() == 0
    out = capsys.readouterr()
    assert tomllib.loads(out.out)["HF_TOKEN"] == "hf_realreadtoken123" and out.err == "" and env.read_bytes() == before
    env.write_text(ENV.replace("hf_realreadtoken123", "hf_paste-the-read-only-token"))
    assert tool.main() == 1
    out = capsys.readouterr()
    assert out.out == "" and "HF_TOKEN" in out.err and not any(v in out.err for v in SECRET_VALUES)
    monkeypatch.setattr("sys.argv", ["streamlit_secrets.py", "--env", str(tmp_path / "missing.env")])
    assert tool.main() == 1 and "does not exist" in capsys.readouterr().err


def test_copy_goes_to_the_clipboard_and_prints_no_secrets(tool, tmp_path, capsys, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(ENV)
    sent = {}
    monkeypatch.setattr(tool.subprocess, "run", lambda cmd, input=None, check=False: sent.update(cmd=cmd, input=input))
    monkeypatch.setattr("sys.argv", ["streamlit_secrets.py", "--env", str(env), "--copy"])
    assert tool.main() == 0
    out = capsys.readouterr()
    assert sent["cmd"] == ["pbcopy"] and b"sk-fake-key-123" in sent["input"] and out.out == "" and not any(v in out.err for v in SECRET_VALUES)


def test_the_make_target_and_the_docs_mention_it():
    assert "streamlit-secrets:" in (ROOT / "Makefile").read_text() and "make streamlit-secrets" in (ROOT / "DEPLOY.md").read_text()


def test_the_access_code_is_optional_and_without_it_the_box_has_none(tool):
    for text in (ENV.replace("# NSW_ACCESS_CODE=abc123-code\n", ""), ENV.replace("# NSW_ACCESS_CODE=abc123-code", "# NSW_ACCESS_CODE=")):
        rendered = tool.render(tool.collect(text))
        box = tomllib.loads(rendered)
        assert "NSW_ACCESS_CODE" not in box and box["NSW_VIEW_ONLY"] == "1" and box["NSW_DB_REPO"] == "someone/nsw-demo-db"
        assert "abc123-code" not in rendered and "How the app is shared" in rendered
    assert tomllib.loads(tool.render(tool.collect(ENV)))["NSW_ACCESS_CODE"] == "abc123-code"          # still passed on when there is one
