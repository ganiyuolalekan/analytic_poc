"""Verifier (Section 13.5): extracts numbers from a drafted answer and matches each to values present in the tool outputs
(or produced by ``compute``) within 0.5%. Status: Verified / Partially verified / Unverified."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

TOL = 0.005
MONTHS = "january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec"
_MASKS = [
    re.compile(rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s*(?:-|–|to|and)\s*\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{MONTHS})\b(?:\s+\d{{4}})?", re.I),
    re.compile(rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{MONTHS})\b(?:\s+\d{{4}})?", re.I), re.compile(rf"\b(?:{MONTHS})\s+\d{{1,2}}(?:st|nd|rd|th)?\b(?:,?\s+\d{{4}})?", re.I),
    re.compile(r"\b(?:19|20)\d{2}\b"), re.compile(r"\bQ[1-4]\b", re.I), re.compile(r"\b[A-Za-z]+[-/][A-Za-z0-9\-/]+\b"), re.compile(r"\b[A-Za-z]+\d+[A-Za-z0-9]*\b"),
    re.compile(r"\bC\d{6}/\d{2}\b"), re.compile(r"\b\d{12}\b"), re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b"), re.compile(r"(?m)^\s*\d+[\.\)]\s"),
    re.compile(r"\b(?:last|past|previous)\s+\d+\s+(?:days?|weeks?|months?|hours?|minutes?)\b", re.I), re.compile(r"\b(?:T\+\d|top\s+\d+|top-\d+|\d+-way|four-way|\d+(?:st|nd|rd|th))\b", re.I),
    re.compile(r"\b\d+-(?:day|hour|minute|week)\b", re.I)]
_MONEY = re.compile(r"₦\s?(-?[\d,]+(?:\.\d+)?)\s?(tn|trillion|bn|billion|m|million|k)?\b", re.I)
_PCT = re.compile(r"(-?[\d,]*\.?\d+)\s?(?:%|per\s?cent|percentage points?|pp)", re.I)
_UNIT = re.compile(r"(-?[\d,]*\.?\d+)\s?(days?|hours?|hrs?|h|minutes?|min|teu)\b", re.I)
_NUM = re.compile(r"(?<![\w.])(-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?)(?![\w])")
_SCALE = {"tn": 1e12, "trillion": 1e12, "bn": 1e9, "billion": 1e9, "m": 1e6, "million": 1e6, "k": 1e3, None: 1.0, "": 1.0}


@dataclass
class Claim:
    text: str
    value: float
    kind: str                # money | pct | unit | count
    decimals: int = 0


@dataclass
class Verdict:
    status: str
    claims: list[Claim] = field(default_factory=list)
    unmatched: list[Claim] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.unmatched


def _f(s: str) -> float:
    return float(s.replace(",", ""))


def _dec(s: str) -> int:
    return len(s.split(".")[1]) if "." in s else 0


def extract_claims(text: str) -> list[Claim]:
    t = text
    claims: list[Claim] = []
    for m in _MONEY.finditer(t):
        raw = m.group(1)
        claims.append(Claim(m.group(0), _f(raw) * _SCALE[(m.group(2) or "").lower() or None] if True else 0, "money", _dec(raw)))
    t = _MONEY.sub(" ", t)
    for m in _PCT.finditer(t):
        claims.append(Claim(m.group(0), _f(m.group(1)), "pct", _dec(m.group(1))))
    t = _PCT.sub(" ", t)
    for m in _UNIT.finditer(t):
        claims.append(Claim(m.group(0), _f(m.group(1)), "unit", _dec(m.group(1))))
    t = _UNIT.sub(" ", t)
    for mk in _MASKS:
        t = mk.sub(" ", t)
    for m in _NUM.finditer(t):
        raw = m.group(1)
        v = _f(raw)
        if abs(v) < 10 and "." not in raw and "," not in raw:      # small standalone integers (ranks, 'two', list markers) are not claimed figures
            continue
        claims.append(Claim(m.group(0), v, "count", _dec(raw)))
    return claims


def collect_numbers(obj, out: list[float] | None = None) -> list[float]:
    """Every numeric value in tool outputs, plus numbers embedded in their display strings."""
    out = [] if out is None else out
    if isinstance(obj, bool) or obj is None:
        return out
    if isinstance(obj, (int, float)):
        out.append(float(obj))
    elif isinstance(obj, str):
        for c in extract_claims(obj):
            out.append(c.value)
        for m in _NUM.finditer(obj):
            out.append(_f(m.group(1)))
    elif isinstance(obj, dict):
        for v in obj.values():
            collect_numbers(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            collect_numbers(v, out)
    return out


def _close(v: float, p: float, decimals: int, scale: float = 1.0) -> bool:
    half_unit = 0.5 * 10 ** (-decimals) * scale + 1e-9
    return abs(v - p) <= max(TOL * abs(p), half_unit)


def matches(claim: Claim, pool: list[float]) -> bool:
    v = claim.value
    for p in pool:
        if claim.kind == "money":
            scale = 1e12 if "tn" in claim.text.lower() or "trillion" in claim.text.lower() else 1e9 if re.search(r"bn|billion", claim.text, re.I) else 1e6 if re.search(r"\dm\b|million", claim.text, re.I) else 1e3 if re.search(r"\dk\b", claim.text, re.I) else 1.0
            if _close(v, p, claim.decimals, scale):
                return True
        elif claim.kind == "pct":
            if _close(v, p * 100, claim.decimals) or _close(v, p, claim.decimals):
                return True
        else:
            if _close(v, p, claim.decimals) or (claim.kind == "unit" and (_close(v, p / 24, claim.decimals) or _close(v, p / 3600, claim.decimals))):
                return True
            if claim.kind == "count" and (_close(v, p / 1e9, claim.decimals) or _close(v, p / 1e6, claim.decimals)):
                return True
    return False


def verify(text: str, tool_outputs: list, allow: list[float] | None = None) -> Verdict:
    """Match every claimed figure in ``text`` to the tool outputs. ``allow`` lets callers whitelist context numbers (e.g. the facts JSON)."""
    claims = extract_claims(text)
    pool = collect_numbers(tool_outputs)
    pool += allow or []
    if not claims:
        return Verdict("verified", [], [])
    unmatched = [c for c in claims if not matches(c, pool)]
    if not unmatched:
        return Verdict("verified", claims, [])
    return Verdict("partial" if len(unmatched) < len(claims) else "unverified", claims, unmatched)


def strip_unverified_sentences(text: str, pool_source: list, allow: list[float] | None = None) -> tuple[str, list[str]]:
    """Remove sentences containing numbers that cannot be matched (used for report narratives and digests)."""
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    kept, removed = [], []
    for s in parts:
        if not s.strip():
            continue
        v = verify(s, pool_source, allow)
        (kept if v.ok else removed).append(s.strip())
    return " ".join(kept), removed
