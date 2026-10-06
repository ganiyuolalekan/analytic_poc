"""Package the database for a private Hugging Face dataset, and upload it. The package is the UNINDEXED database compressed with zstd (about 140 MB: the indexes are rebuilt in about
15 seconds on the new host, see ``dbfetch``), the AI answer cache and the AI capabilities file, with a manifest of checksums. Nothing here ever prints a token."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from nsw_sim import db, dbtools

FORMAT = 1
DB_FILE = "nsw_unindexed.db.zst"
EXTRAS = ("llm_cache.sqlite", "llm_capabilities.json")          # copied beside the database on the new host when it has none of its own
CARD = """---
license: other
pretty_name: NSW Intelligence Console demo data (synthetic)
---
Synthetic demo data for the NSW Intelligence Console, packaged for first start on a new host. Private: read with a token. The database is stored unindexed and compressed with zstd;
the app unpacks it and builds its indexes on first start. All data is synthetic and illustrative.
"""


class PublishError(Exception):
    pass


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def _snapshot(src: Path, out: Path) -> None:
    """A consistent copy of a SQLite file (online backup), so a running app's WAL is folded in."""
    out.unlink(missing_ok=True)
    s = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=60)
    d = sqlite3.connect(out)
    with d:
        s.backup(d)
    s.close()
    d.execute("PRAGMA journal_mode=DELETE")
    d.close()


def build(src_db: Path, out_dir: Path, data_dir: Path, level: int = 15) -> dict:
    """Write the upload folder (``out_dir``): compressed unindexed database, extras, ``manifest.json`` and the dataset card. Returns the manifest."""
    import zstandard
    info = dbtools.status(src_db)
    if info["kind"] != "unindexed":
        raise PublishError(f"{src_db} is {info['kind']}: publish the unindexed copy (make db-unindexed) so the upload stays small")
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.iterdir():
        old.unlink()
    snap = out_dir / "_snapshot.db"
    _snapshot(src_db, snap)
    c = sqlite3.connect(f"file:{snap}?mode=ro", uri=True)
    schema, watermark = db.kv_get(c, "schema_version"), db.kv_get(c, "watermark_utc")
    c.close()
    comp = out_dir / DB_FILE
    with open(snap, "rb") as fin, open(comp, "wb") as fout:
        zstandard.ZstdCompressor(level=level, threads=-1).copy_stream(fin, fout, read_size=1 << 22, write_size=1 << 22)
    db_bytes = snap.stat().st_size
    snap.unlink()
    extras = []
    for name in EXTRAS:
        p = data_dir / name
        if not p.exists():
            continue
        dest = out_dir / name
        if name.endswith(".sqlite"):
            _snapshot(p, dest)
        else:
            dest.write_bytes(p.read_bytes())
        extras.append({"file": name, "sha256": sha256_file(dest), "bytes": dest.stat().st_size})
    manifest = {"format": FORMAT, "db_file": DB_FILE, "db_sha256": sha256_file(comp), "db_bytes_compressed": comp.stat().st_size, "db_bytes": db_bytes, "schema_version": schema,
                "data_time": watermark, "extras": extras, "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (out_dir / "README.md").write_text(CARD, encoding="utf-8")
    return manifest


def upload(out_dir: Path, repo_id: str, token: str | None = None) -> str:
    """Upload the folder to a PRIVATE dataset (created if needed). Refuses if the dataset turns out to be public. The token comes from ``token``, the HF_TOKEN variable or a prior login."""
    from huggingface_hub import HfApi
    if not (out_dir / "manifest.json").exists():
        raise PublishError(f"{out_dir} has no manifest.json: run the build step first")
    api = HfApi(token=token or os.environ.get("HF_TOKEN") or None)
    api.create_repo(repo_id, repo_type="dataset", private=True, exist_ok=True)
    if not api.dataset_info(repo_id).private:
        raise PublishError(f"{repo_id} is a PUBLIC dataset: make it private in its Hugging Face settings first. Nothing was uploaded.")
    api.upload_folder(repo_id=repo_id, repo_type="dataset", folder_path=str(out_dir), commit_message="NSW demo data package")
    return f"https://huggingface.co/datasets/{repo_id}"
