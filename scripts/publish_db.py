#!/usr/bin/env python
"""Publish the database to a PRIVATE Hugging Face dataset, so a new host (Streamlit Community Cloud) can fetch it on first start. Two steps:
  make db-package                                 compress data/nsw_unindexed.db (+ the AI answer cache and capabilities file) into data/hf_upload/ with a checksum manifest
  NSW_DB_REPO=you/nsw-demo-db make db-publish     upload that folder to the private dataset (needs a WRITE token: HF_TOKEN=... or a prior `huggingface-cli login`)
The upload refuses to go to a public dataset. Never put the write token in the repository or in Streamlit's secrets (the app only needs a read token)."""
import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nsw_sim import dbpublish  # noqa: E402
from nsw_sim.config import data_dir  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Package and publish the database.")
    ap.add_argument("action", choices=["build", "upload"])
    ap.add_argument("--src", type=Path, default=data_dir() / "nsw_unindexed.db", help="the unindexed database to package")
    ap.add_argument("--out", type=Path, default=data_dir() / "hf_upload", help="the folder that is uploaded")
    ap.add_argument("--repo", default=os.environ.get("NSW_DB_REPO"), help="upload: the dataset, for example your-name/nsw-demo-db (or set NSW_DB_REPO)")
    ap.add_argument("--level", type=int, default=15, help="build: zstd level (higher is smaller and slower)")
    a = ap.parse_args()
    t0 = time.time()
    try:
        if a.action == "build":
            if not a.src.exists():
                sys.exit(f"{a.src} does not exist: run `make db-unindexed` first")
            m = dbpublish.build(a.src, a.out, data_dir(), a.level)
            print(f"packaged {a.out}: database {m['db_bytes'] / 1e6:,.0f} MB -> {m['db_bytes_compressed'] / 1e6:,.0f} MB compressed, data as of {m['data_time']}, "
                  f"{len(m['extras'])} extra file(s), checksum {m['db_sha256'][:12]}…  ({time.time() - t0:.0f} s)")
        else:
            if not a.repo:
                sys.exit("say which dataset: --repo your-name/nsw-demo-db or NSW_DB_REPO=...")
            print("uploaded to", dbpublish.upload(a.out, a.repo), f"({time.time() - t0:.0f} s)")
    except dbpublish.PublishError as e:
        sys.exit(str(e))
    return 0


if __name__ == "__main__":
    sys.exit(main())
