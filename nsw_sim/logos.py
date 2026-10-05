"""Logo discovery and matching (Section 11.2). Pure Python (no Streamlit); the UI layer renders the result.

Files in ``organization_logos/`` (also ``/organization_logos`` if it exists) are matched to entity codes by
normalised filename: exact code token, full-name containment, then aliases. Never crashes on missing files.
"""
from __future__ import annotations

import base64
import io
import re
from functools import lru_cache
from pathlib import Path

from nsw_sim.config import LOGO_DIR, get_logger, yaml_config

log = get_logger("nsw.logos")
EXTS = {".png", ".jpg", ".jpeg", ".svg", ".webp"}
_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".svg": "image/svg+xml", ".webp": "image/webp"}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def logo_dirs() -> list[Path]:
    return [d for d in (LOGO_DIR, Path("/organization_logos")) if d.is_dir()]


def list_logo_files() -> list[Path]:
    out: list[Path] = []
    for d in logo_dirs():
        out += [p for p in sorted(d.iterdir()) if p.suffix.lower() in EXTS and not p.name.startswith(".")]
    return out


def _entity_keys() -> dict[str, dict]:
    cards = yaml_config("entities")["entities"]
    keys = {}
    for code, c in cards.items():
        keys[code] = {"code": _norm(code), "name": _norm(c["name"]), "aliases": [_norm(a) for a in c.get("aliases", [])]}
    return keys


def _score(path: Path, k: dict) -> int:
    stem = path.stem
    tokens = {_norm(t) for t in re.split(r"[^A-Za-z0-9]+", stem) if t}
    flat = _norm(stem)
    if flat == k["code"] or k["code"] in tokens:
        return 100
    if k["name"] and k["name"] in flat:
        return 80
    for a in k["aliases"]:
        if a and (flat == a or a in tokens):
            return 60
    for a in k["aliases"]:
        if len(a) >= 5 and a in flat:
            return 40
    return 0


@lru_cache(maxsize=1)
def match_logos() -> dict:
    """Return {'matched': {code: Path}, 'unmatched_files': [Path], 'missing_entities': [code]}."""
    files = list_logo_files()
    keys = _entity_keys()
    best: dict[str, tuple[int, Path]] = {}
    used: set[Path] = set()
    for code, k in keys.items():
        for f in files:
            sc = _score(f, k)
            if sc and sc > best.get(code, (0, None))[0]:
                best[code] = (sc, f)
    matched = {c: p for c, (_, p) in best.items()}
    used = set(matched.values())
    unmatched = [f for f in files if f not in used]
    missing = [c for c in keys if c not in matched]
    for c in missing:
        log.warning("no logo found for %s: a generated badge will be used", c)
    return {"matched": matched, "unmatched_files": unmatched, "missing_entities": missing}


def refresh() -> None:
    match_logos.cache_clear()
    logo_data_uri.cache_clear()


def logo_path(code: str) -> Path | None:
    return match_logos()["matched"].get(code)


def initials(code: str) -> str:
    yaml_config("entities")["entities"].get(code, {}).get("name", code)
    if code in ("MOF-FA",):
        return "MF"
    return code[:3] if len(code) <= 3 else code[:2]


def badge_svg(code: str, size: int = 48) -> str:
    colour = yaml_config("entities")["entities"].get(code, {}).get("colour", "#555555")
    txt = initials(code)
    fs = int(size * (0.34 if len(txt) > 2 else 0.4))
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 {size} {size}">'
            f'<circle cx="{size / 2}" cy="{size / 2}" r="{size / 2}" fill="{colour}"/>'
            f'<text x="50%" y="50%" dy=".35em" text-anchor="middle" font-family="Arial,Helvetica,sans-serif" '
            f'font-weight="700" font-size="{fs}" fill="#ffffff">{txt}</text></svg>')


@lru_cache(maxsize=256)
def logo_data_uri(code: str, px: int = 64) -> str:
    """Inline base64 data URI for a crisp logo (thumbnailed to 2x ``px``); falls back to a generated badge."""
    p = logo_path(code)
    if p is not None:
        try:
            if p.suffix.lower() == ".svg":
                return f"data:image/svg+xml;base64,{base64.b64encode(p.read_bytes()).decode()}"
            from PIL import Image
            with Image.open(p) as im:
                im = im.convert("RGBA")
                im.thumbnail((px * 2, px * 2))
                bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
                bg.alpha_composite(im)
                buf = io.BytesIO()
                bg.convert("RGB").save(buf, format="PNG", optimize=True)
            return f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode()}"
        except Exception as e:  # noqa: BLE001 - a bad image must never crash the UI
            log.warning("could not render logo %s: %s", p.name, e)
    return f"data:image/svg+xml;base64,{base64.b64encode(badge_svg(code, px * 2).encode()).decode()}"


def logo_img_html(code: str, px: int = 28, title: str | None = None) -> str:
    return (f'<img src="{logo_data_uri(code, px)}" width="{px}" height="{px}" alt="{code}" title="{title or code}" '
            f'style="object-fit:contain;border-radius:{px // 6}px;vertical-align:middle;background:#fff"/>')
