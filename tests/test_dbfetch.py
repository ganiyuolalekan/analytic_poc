"""Publishing the database to a private Hugging Face dataset and fetching it on a new host (nsw_sim/dbpublish.py, nsw_sim/dbfetch.py), with a fake download standing in for the network."""
import json
import os
import shutil
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from nsw_sim import db, dbfetch, dbpublish, dbtools
from nsw_sim.config import data_dir, db_path

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _fresh_fetcher(monkeypatch):
    dbfetch.reset_for_tests()
    for k in ("NSW_DB_REPO", "NSW_DB_FORCE", "NSW_DB_REVISION", "HF_TOKEN", "NSW_DEBUG"):
        monkeypatch.delenv(k, raising=False)
    yield
    dbfetch.reset_for_tests()


@pytest.fixture
def package(tmp_path):
    """A real (tiny) published package: unindexed database compressed with zstd, extras and manifest; plus a fake download that serves it."""
    src = tmp_path / "published"
    src.mkdir()
    full = src / "full.db"
    c = db.connect(full)
    db.init_db(c)
    db.kv_set(c, "watermark_utc", "2026-10-05T19:09:35Z")
    c.close()
    unindexed = src / "unindexed.db"
    dbtools.slim(full, unindexed)
    extras = src / "extras"
    extras.mkdir()
    (extras / "llm_capabilities.json").write_text('{"resolved": {"model_chat": "m"}}')
    sqlite3.connect(extras / "llm_cache.sqlite").execute("CREATE TABLE llm_cache(key TEXT PRIMARY KEY, role TEXT, model TEXT, response TEXT, tokens_in INT, tokens_out INT, created_at TEXT)").connection.close()
    out = tmp_path / "upload"
    manifest = dbpublish.build(unindexed, out, extras)

    def download(repo_id, filename, revision, dest):
        assert repo_id == "someone/nsw-demo-db"
        shutil.copyfile(out / filename, dest / filename)
        return dest / filename
    return SimpleNamespace(out=out, manifest=manifest, download=download, unindexed=unindexed)


def test_the_package_is_the_compressed_unindexed_database_with_checksums_and_extras(package):
    m = package.manifest
    assert m["format"] == 1 and m["db_file"] == "nsw_unindexed.db.zst" and m["data_time"] == "2026-10-05T19:09:35Z" and m["schema_version"] == str(db.SCHEMA_VERSION)
    assert m["db_sha256"] == dbpublish.sha256_file(package.out / m["db_file"]) and m["db_bytes_compressed"] < m["db_bytes"]
    assert {e["file"] for e in m["extras"]} == {"llm_cache.sqlite", "llm_capabilities.json"}
    assert json.loads((package.out / "manifest.json").read_text()) == m and (package.out / "README.md").exists()
    assert not (package.out / "_snapshot.db").exists()                                  # the working copy is not left in the upload folder


def test_an_indexed_database_is_refused_so_the_upload_stays_small(tmp_path):
    full = tmp_path / "full.db"
    c = db.connect(full)
    db.init_db(c)
    c.close()
    with pytest.raises(dbpublish.PublishError, match="unindexed"):
        dbpublish.build(full, tmp_path / "upload", tmp_path)


def test_a_new_host_gets_an_unpacked_indexed_checked_database_and_the_extras(package, monkeypatch):
    monkeypatch.setenv("NSW_DB_REPO", "someone/nsw-demo-db")
    assert not db_path().exists() and dbfetch.needed() is True
    job = dbfetch.Job(package.download)
    job._run()
    s = job.snapshot()
    assert s["state"] == "ready" and s["fraction"] == 1.0 and not s["running"], s
    assert dbtools.status(db_path())["kind"] == "indexed" and (data_dir() / "llm_capabilities.json").exists() and (data_dir() / "llm_cache.sqlite").exists()
    assert not (data_dir() / "_fetch").exists() and not Path(str(db_path()) + ".part").exists()                       # downloads and half-written files are cleaned up


def test_extras_never_overwrite_a_host_s_own_files(package, monkeypatch):
    monkeypatch.setenv("NSW_DB_REPO", "someone/nsw-demo-db")
    (data_dir() / "llm_capabilities.json").write_text('{"mine": true}')
    dbfetch.Job(package.download)._run()
    assert json.loads((data_dir() / "llm_capabilities.json").read_text()) == {"mine": True}


def test_a_corrupted_download_is_rejected_and_leaves_no_database(package, monkeypatch):
    monkeypatch.setenv("NSW_DB_REPO", "someone/nsw-demo-db")

    def corrupt(repo_id, filename, revision, dest):
        p = package.download(repo_id, filename, revision, dest)
        if filename.endswith(".zst"):
            b = bytearray(p.read_bytes())
            b[len(b) // 2] ^= 0xFF
            p.write_bytes(bytes(b))
        return p
    job = dbfetch.Job(corrupt)
    job._run()
    s = job.snapshot()
    assert s["state"] == "error" and "checksum" in s["message"] and not db_path().exists() and not Path(str(db_path()) + ".part").exists()


def test_data_built_for_another_version_of_the_app_is_rejected(package, monkeypatch):
    monkeypatch.setenv("NSW_DB_REPO", "someone/nsw-demo-db")
    monkeypatch.setattr(db, "SCHEMA_VERSION", db.SCHEMA_VERSION + 1)                    # this app is newer than the published data
    job = dbfetch.Job(package.download)
    job._run()
    s = job.snapshot()
    assert s["state"] == "error" and "different version" in s["message"] and not db_path().exists()


def test_a_failure_never_shows_the_token_or_a_stack_trace(package, monkeypatch):
    monkeypatch.setenv("NSW_DB_REPO", "someone/nsw-demo-db")
    monkeypatch.setenv("HF_TOKEN", "hf_SECRET_TOKEN_VALUE")

    def boom(*a, **k):
        raise RuntimeError("401 Unauthorized with token hf_SECRET_TOKEN_VALUE at /Users/me/secret/path")
    job = dbfetch.Job(boom)
    job._run()
    s = job.snapshot()
    assert s["state"] == "error" and "hf_SECRET" not in s["message"] and "/Users" not in s["message"] and "Traceback" not in s["message"]


def test_nothing_happens_without_a_dataset_name_so_local_runs_are_unaffected():
    assert dbfetch.start() is None and dbfetch.needed() is False and not db_path().exists()


def test_the_app_waits_while_the_fetch_runs_and_not_afterwards(package, monkeypatch):
    monkeypatch.setenv("NSW_DB_REPO", "someone/nsw-demo-db")
    job = dbfetch.start(package.download)
    job.thread.join(120)
    assert job.snapshot()["state"] == "ready" and dbfetch.needed() is False and dbfetch.start(package.download) is job            # idempotent: one job per process


def test_an_existing_complete_database_is_left_alone(package, monkeypatch):
    monkeypatch.setenv("NSW_DB_REPO", "someone/nsw-demo-db")
    shutil.copyfile(package.unindexed, db_path())
    dbtools.index(db_path())
    before = db_path().stat().st_mtime_ns
    assert dbfetch.start(package.download) is None and dbfetch.needed() is False and db_path().stat().st_mtime_ns == before


def test_a_database_left_half_indexed_is_repaired_without_downloading(package, monkeypatch):
    monkeypatch.setenv("NSW_DB_REPO", "someone/nsw-demo-db")
    shutil.copyfile(package.unindexed, db_path())                                      # present but unindexed: an earlier start was cut off
    calls = []
    job = dbfetch.start(lambda *a: calls.append(a))
    job.thread.join(120)
    assert job.snapshot()["state"] == "ready" and not calls and dbtools.status(db_path())["kind"] == "indexed"


def test_force_loads_newly_published_data_over_an_older_copy(package, monkeypatch):
    monkeypatch.setenv("NSW_DB_REPO", "someone/nsw-demo-db")
    c = db.connect(db_path())
    db.init_db(c)
    db.kv_set(c, "watermark_utc", "2020-01-01T00:00:00Z")
    c.close()
    Path(str(db_path()) + "-wal").write_bytes(b"stale")                              # leftovers of the old file must not be applied to the new one
    monkeypatch.setenv("NSW_DB_FORCE", "1")
    job = dbfetch.start(package.download)
    job.thread.join(120)
    c = sqlite3.connect(db_path())
    wal = Path(str(db_path()) + "-wal")
    assert job.snapshot()["state"] == "ready" and db.kv_get(c, "watermark_utc") == "2026-10-05T19:09:35Z"
    assert not wal.exists() or b"stale" not in wal.read_bytes()                                                       # a fresh write-ahead file may exist; the planted stale one must not
    c.close()


def test_the_fetch_screen_offers_a_retry_after_an_error(conn, monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv("NSW_DB_REPO", "someone/nsw-demo-db")
    job = dbfetch.Job(lambda *a: None)
    job.thread = object()                                                             # as if it had run
    job._set("error", 0.3, "The data could not be prepared. Check the dataset name and token.")
    dbfetch._job = job
    db_path().unlink()                                                                # a host with no database
    at = AppTest.from_file(str(ROOT / "app" / "main.py"), default_timeout=60).run()
    assert not at.exception, [e.value[:200] for e in at.exception]
    assert any("Getting the data ready" in t.value for t in at.title) and any("could not be prepared" in e.value for e in at.error)
    assert any(b.label == "Try again" for b in at.button)


class _FakeApi:
    private = True
    calls: list = []

    def __init__(self, token=None):
        _FakeApi.token = token

    def create_repo(self, repo_id, repo_type, private, exist_ok):
        _FakeApi.calls.append(("create_repo", repo_id, repo_type, private))

    def dataset_info(self, repo_id):
        return SimpleNamespace(private=_FakeApi.private)

    def upload_folder(self, repo_id, repo_type, folder_path, commit_message):
        _FakeApi.calls.append(("upload_folder", repo_id, repo_type, folder_path))


def test_the_upload_goes_only_to_a_private_dataset_and_refuses_a_public_one(package, monkeypatch):
    import huggingface_hub
    monkeypatch.setattr(huggingface_hub, "HfApi", _FakeApi)
    _FakeApi.calls.clear()
    _FakeApi.private = False
    with pytest.raises(dbpublish.PublishError, match="PUBLIC"):
        dbpublish.upload(package.out, "someone/nsw-demo-db", token="hf_x")
    assert [c[0] for c in _FakeApi.calls] == ["create_repo"] and _FakeApi.calls[0][3] is True                        # asked for private; nothing uploaded
    _FakeApi.calls.clear()
    _FakeApi.private = True
    assert dbpublish.upload(package.out, "someone/nsw-demo-db", token="hf_x").endswith("/datasets/someone/nsw-demo-db")
    assert _FakeApi.calls[-1] == ("upload_folder", "someone/nsw-demo-db", "dataset", str(package.out)) and _FakeApi.token == "hf_x"


def test_the_upload_needs_a_built_package(tmp_path):
    with pytest.raises(dbpublish.PublishError, match="manifest"):
        dbpublish.upload(tmp_path, "someone/nsw-demo-db")


def test_secrets_become_environment_variables_without_overriding_existing_ones(monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv("NSW_TEST_KEPT", "from-environment")
    monkeypatch.delenv("NSW_TEST_NEW", raising=False)
    at = AppTest.from_string("from app.components import cloud\ncloud.secrets_to_env()", default_timeout=30)
    at.secrets["NSW_TEST_NEW"], at.secrets["NSW_TEST_KEPT"], at.secrets["NSW_TEST_FLAG"], at.secrets["nested"] = "from-secrets", "from-secrets", 1, {"inner": "x"}
    try:
        at.run()
        assert not at.exception and os.environ["NSW_TEST_NEW"] == "from-secrets" and os.environ["NSW_TEST_KEPT"] == "from-environment" and os.environ["NSW_TEST_FLAG"] == "1"
        assert "nested" not in os.environ and "inner" not in os.environ                                                  # sections are not flattened into variables
    finally:
        for k in ("NSW_TEST_NEW", "NSW_TEST_FLAG"):
            os.environ.pop(k, None)


def test_with_no_secrets_a_local_run_is_unaffected():
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_string("from app.components import cloud\ncloud.secrets_to_env()", default_timeout=30).run()
    assert not at.exception


def test_the_secrets_file_is_git_ignored_and_the_template_has_no_real_values():
    ignored = (ROOT / ".gitignore").read_text().splitlines()
    assert ".streamlit/secrets.toml" in ignored and ".env" in ignored and "data/" in ignored          # the real secrets, the AI key file and every data file stay out of a public repo
    t = (ROOT / ".streamlit" / "secrets.toml.example").read_text()
    assert "paste-your-bedrock-api-key" in t and "hf_paste-the-read-only-token" in t and "choose-a-long-random-code" in t
