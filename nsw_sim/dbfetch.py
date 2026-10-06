"""First start on a host that has no database (Streamlit Community Cloud): fetch the compressed, unindexed database from a PRIVATE Hugging Face dataset, check its checksum, unpack it,
build the indexes and run a final check. It runs once per process in a background thread, so the first visitor sees progress instead of a blank page. Needs NSW_DB_REPO (for example
``your-name/nsw-demo-db``) and, for a private dataset, a READ token in HF_TOKEN. With no NSW_DB_REPO set (every local run) none of this does anything."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import threading
from collections.abc import Callable
from pathlib import Path

from nsw_sim import db, dbtools
from nsw_sim.config import data_dir, db_path, get_logger

log = get_logger("nsw.dbfetch")
Download = Callable[[str, str, "str | None", Path], Path]
LABELS = {"download": "Downloading the data", "check": "Checking the download", "unpack": "Unpacking the data", "index": "Building the indexes so pages load quickly",
          "verify": "Final checks", "ready": "Ready", "error": "Something went wrong"}


class FetchError(Exception):
    """A problem a person can act on, worded for them (no tokens, no paths)."""


def repo() -> str | None:
    return (os.environ.get("NSW_DB_REPO") or "").strip() or None


def hf_download(repo_id: str, filename: str, revision: str | None, dest: Path) -> Path:
    from huggingface_hub import hf_hub_download
    return Path(hf_hub_download(repo_id=repo_id, filename=filename, repo_type="dataset", revision=revision, token=os.environ.get("HF_TOKEN") or None, local_dir=str(dest)))


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


class Job:
    """One fetch. ``index_only`` repairs a database that is present but not fully indexed (an earlier start was cut off)."""

    def __init__(self, download: Download = hf_download, index_only: bool = False) -> None:
        self.download, self.index_only = download, index_only
        self.state, self.fraction, self.message = "idle", 0.0, ""
        self.thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def _set(self, state: str, fraction: float, message: str = "") -> None:
        with self._lock:
            self.state, self.fraction, self.message = state, fraction, message

    def snapshot(self) -> dict:
        with self._lock:
            return {"state": self.state, "fraction": self.fraction, "label": LABELS.get(self.state, self.state), "message": self.message,
                    "running": self.state not in ("idle", "ready", "error")}

    def start(self) -> None:
        if self.thread is None:
            self.thread = threading.Thread(target=self._run, daemon=True, name="nsw-dbfetch")
            self.thread.start()

    def _run(self) -> None:
        work = data_dir() / "_fetch"
        part = Path(str(db_path()) + ".part")
        try:
            if self.index_only:
                self._set("index", 0.62)
                dbtools.index(db_path())
            else:
                self._fetch(work, part)
            self._verify()
            self._set("ready", 1.0)
            log.info("database ready: %s", db_path())
        except FetchError as e:
            self._set("error", self.fraction, str(e))
        except Exception as e:  # noqa: BLE001 - never show a stack trace or a token to a visitor
            log.exception("database fetch failed")
            self._set("error", self.fraction, "The data could not be prepared. Check that the dataset name and read token are right, then try again." + (f" ({type(e).__name__})" if os.environ.get("NSW_DEBUG") else ""))
        finally:
            part.unlink(missing_ok=True)
            shutil.rmtree(work, ignore_errors=True)

    def _fetch(self, work: Path, part: Path) -> None:
        import zstandard
        rid, rev = repo(), os.environ.get("NSW_DB_REVISION") or None
        work.mkdir(parents=True, exist_ok=True)
        self._set("download", 0.02)
        manifest = json.loads(Path(self.download(rid, "manifest.json", rev, work)).read_text(encoding="utf-8"))
        if manifest.get("format") != 1:
            raise FetchError("This data package was made by a newer version of the app. Update the app and try again.")
        comp = Path(self.download(rid, manifest["db_file"], rev, work))
        extras = [(e, Path(self.download(rid, e["file"], rev, work))) for e in manifest.get("extras", [])]
        self._set("check", 0.35)
        if _sha256(comp) != manifest["db_sha256"] or any(_sha256(p) != e["sha256"] for e, p in extras):
            raise FetchError("The downloaded data did not match its checksum, so it was not used. Try again.")
        self._set("unpack", 0.40)
        total, done = max(1, int(manifest.get("db_bytes") or 1)), 0
        part.unlink(missing_ok=True)
        with open(comp, "rb") as fin, open(part, "wb") as fout:
            for chunk in iter(lambda r=zstandard.ZstdDecompressor().stream_reader(fin): r.read(1 << 23), b""):
                fout.write(chunk)
                done += len(chunk)
                self._set("unpack", 0.40 + 0.22 * min(1.0, done / total))
        db_path().parent.mkdir(parents=True, exist_ok=True)
        for ext in ("-wal", "-shm"):                              # leftovers of a database being replaced must not be applied to the new file
            Path(str(db_path()) + ext).unlink(missing_ok=True)
        os.replace(part, db_path())
        for e, p in extras:                                       # the AI answer cache and capabilities: only where this host has none of its own
            target = data_dir() / e["file"]
            if not target.exists():
                shutil.copyfile(p, target)
        self._set("index", 0.62)
        dbtools.index(db_path())

    def _verify(self) -> None:
        self._set("verify", 0.95)
        c = sqlite3.connect(f"file:{db_path()}?mode=ro", uri=True, timeout=60)
        try:
            schema, watermark = db.kv_get(c, "schema_version"), db.kv_get(c, "watermark_utc")
        finally:
            c.close()
        if schema != str(db.SCHEMA_VERSION):
            db_path().unlink(missing_ok=True)
            raise FetchError("The data was built for a different version of the app. Publish it again from this version and try again.")
        if not watermark:
            db_path().unlink(missing_ok=True)
            raise FetchError("The data package is empty. Publish it again and try again.")


_job: Job | None = None
_lock = threading.Lock()
_checked = False


def start(download: Download = hf_download) -> Job | None:
    """Begin the fetch if this host needs one (idempotent; safe to call on every page run). Returns the job, or None when there is nothing to do."""
    global _job, _checked
    if not repo():
        return None
    with _lock:
        if _job is None:
            if not db_path().exists() or os.environ.get("NSW_DB_FORCE") == "1":          # NSW_DB_FORCE=1 for one boot loads newly published data over an older copy
                _job = Job(download)
            elif not _checked:
                _checked = True
                if dbtools.status(db_path())["kind"] != "indexed":
                    _job = Job(download, index_only=True)
            if _job is not None:
                _job.start()
        return _job


def needed() -> bool:
    """True while the app cannot be shown yet: no database yet, or the fetch is still running."""
    if not repo():
        return False
    if _job is not None:
        return _job.snapshot()["state"] != "ready"
    return not db_path().exists()


def status() -> dict:
    return _job.snapshot() if _job is not None else {"state": "idle", "fraction": 0.0, "label": "Starting", "message": "", "running": False}


def retry(download: Download = hf_download) -> None:
    """After an error: forget the failed job and start a new one."""
    global _job
    with _lock:
        if _job is not None and _job.snapshot()["state"] == "error":
            _job = None
    start(download)


def reset_for_tests() -> None:
    global _job, _checked
    with _lock:
        _job, _checked = None, False
