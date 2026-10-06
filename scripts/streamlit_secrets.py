#!/usr/bin/env python
"""Print the whole Secrets box for Streamlit Community Cloud, ready to paste, from your .env (never written to a file, never sent anywhere).
  make streamlit-secrets                      print it (real values: your terminal only)
  make streamlit-secrets ARGS=--mask          preview the layout with secret values hidden
  make streamlit-secrets ARGS=--copy          put it on the clipboard instead of printing it (macOS)
It reads the AI key and project from the active lines of .env and the sharing and data settings from the commented "FOR STREAMLIT COMMUNITY CLOUD" block at the bottom
(uncomment nothing: fill in the values there). It refuses while a value is missing or still a placeholder, so a half-finished box is never pasted.
Options: --env PATH, --force-refresh (adds NSW_DB_FORCE = "1" for one boot after publishing new data), --allow-placeholders."""
import argparse
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MARKER = "FOR STREAMLIT COMMUNITY CLOUD"
# (key, where its value comes from, secret?) in the order they appear in the box
FIELDS = [("OPENAI_API_KEY", "active", True), ("OPENAI_PROJECT_ID", "active", True), ("OPENAI_BASE_URL", "block", False),
          ("NSW_ACCESS_CODE", "block", True), ("NSW_VIEW_ONLY", "block", False), ("NSW_TOKEN_BUDGET_DAY", "block", False),
          ("NSW_DB_REPO", "block", False), ("HF_TOKEN", "block", True)]
OPTIONAL = {"OPENAI_PROJECT_ID"}
GROUPS = {"OPENAI_API_KEY": "The AI assistant", "NSW_ACCESS_CODE": "How the app is shared", "NSW_DB_REPO": "Where the data comes from (a PRIVATE Hugging Face dataset and a READ-ONLY token)"}
PLACEHOLDER = re.compile(r"your-hf-name|hf_paste|paste-|choose-a-|changeme|xxxx", re.I)


class SecretsError(Exception):
    pass


def parse(text: str) -> tuple[dict[str, str], dict[str, str]]:
    """The active variables of a .env file, and the settings in its commented Streamlit block (``# KEY=value`` lines after the marker)."""
    active, block, in_block = {}, {}, False
    for line in text.splitlines():
        if MARKER in line:
            in_block = True
        if not in_block:
            m = re.match(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$", line)
            if m and not line.lstrip().startswith("#"):
                active[m.group(1)] = m.group(2).strip("'\"")
        else:
            m = re.match(r"#\s*([A-Z][A-Z0-9_]+)=(.*?)\s*$", line)
            if m:
                block[m.group(1)] = m.group(2).strip("'\"")
    return active, block


def collect(text: str, force_refresh: bool = False, allow_placeholders: bool = False) -> list[tuple[str, str, bool]]:
    """The (key, value, secret) rows of the box, or a SecretsError naming (never showing) what is missing or still a placeholder."""
    active, block = parse(text)
    if not block:
        raise SecretsError(f"the \"{MARKER}\" block is missing from the .env file: it holds the sharing and data settings")
    rows, missing, unfinished = [], [], []
    for key, source, secret in FIELDS:
        value = (active if source == "active" else block).get(key, "")
        if not value:
            if key not in OPTIONAL:
                missing.append(key)
            continue
        if PLACEHOLDER.search(value):
            unfinished.append(key)
        rows.append((key, value, secret))
    if force_refresh:
        rows.append(("NSW_DB_FORCE", "1", False))
    problems = []
    if missing:
        problems.append("no value for " + ", ".join(missing))
    if unfinished and not allow_placeholders:
        problems.append("still a placeholder: " + ", ".join(unfinished))
    if problems:
        raise SecretsError("; ".join(problems) + ". Fill them in at the bottom of .env first.")
    return rows


def mask(value: str) -> str:
    return "*" * min(len(value), 8) + f" ({len(value)} characters)"


def render(rows: list[tuple[str, str, bool]], masked: bool = False) -> str:
    out = ["# Paste this into Streamlit Community Cloud: Advanced settings > Secrets (or App settings > Secrets).",
           f"# Made by `make streamlit-secrets` from .env on {date.today().isoformat()}. It holds real secrets: do not save it in the repository or share it.", ""]
    for key, value, secret in rows:
        if key in GROUPS:
            out += ([""] if out[-1] != "" else []) + [f"# {GROUPS[key]}"]
        out.append(f"{key} = {json.dumps(mask(value) if masked and secret else value, ensure_ascii=False)}")
    return "\n".join(out) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="Print the Streamlit Secrets box from .env.")
    ap.add_argument("--env", type=Path, default=ROOT / ".env")
    ap.add_argument("--mask", action="store_true", help="hide secret values (to preview the layout)")
    ap.add_argument("--copy", action="store_true", help="copy to the clipboard (macOS pbcopy) instead of printing")
    ap.add_argument("--force-refresh", action="store_true", help='add NSW_DB_FORCE = "1" (load newly published data for one boot)')
    ap.add_argument("--allow-placeholders", action="store_true", help="print even if a value is still a placeholder")
    a = ap.parse_args()
    try:
        if not a.env.exists():
            raise SecretsError(f"{a.env} does not exist")
        text = render(collect(a.env.read_text(encoding="utf-8"), a.force_refresh, a.allow_placeholders), a.mask)
    except SecretsError as e:
        print(f"Cannot make the Secrets box: {e}", file=sys.stderr)
        return 1
    if a.copy:
        try:
            subprocess.run(["pbcopy"], input=text.encode(), check=True)
        except (OSError, subprocess.CalledProcessError):
            print("Cannot reach the clipboard (pbcopy). Run without --copy and select the text instead.", file=sys.stderr)
            return 1
        print("Copied to the clipboard. Paste it into Streamlit's Secrets box, then clear the clipboard (copy anything else).", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
