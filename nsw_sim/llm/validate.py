"""Validation pipeline for every JSON role (Section 4.4):
parse -> pydantic -> plausibility clamp -> one repair call -> deterministic fallback.
Never returns unvalidated LLM output; every result says whether it came from ``llm`` or ``fallback``."""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from nsw_sim.config import get_logger
from nsw_sim.llm.client import extract_json

log = get_logger("nsw.validate")
M = TypeVar("M", bound=BaseModel)


@dataclass
class RoleResult:
    obj: BaseModel
    source: str                      # 'llm' | 'fallback'
    outcome: str                     # ok | repaired | fallback
    clamped: int = 0
    errors: list[str] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    model: str = ""
    from_cache: bool = False


class Clamp:
    """Counts clamping events while bounding numbers into plausible ranges."""

    def __init__(self) -> None:
        self.n = 0

    def val(self, x: float, lo: float, hi: float) -> float:
        try:
            x = float(x)
        except (TypeError, ValueError):
            self.n += 1
            return (lo + hi) / 2
        y = min(max(x, lo), hi)
        if y != x:
            self.n += 1
        return y


def normalise_shares(d: dict[str, float], allowed: set[str], tol: float, name: str, errors: list[str]) -> dict[str, float]:
    """Shares must sum to 1 ± tol (else an error to repair); then they are renormalised exactly to 1."""
    clean = {k: float(v) for k, v in d.items() if k in allowed and isinstance(v, (int, float)) and v >= 0}
    s = sum(clean.values())
    if not clean or abs(s - 1.0) > tol:
        errors.append(f"{name}: shares must sum to 1 (±{tol}) over known keys; got {s:.3f}")
        return clean
    return {k: v / s for k, v in clean.items()}


def run_json_role(llm, role: str, system: str, user: str, model_cls: type[M],
                  check: Callable[[M], tuple[M, list[str], int]], fallback: Callable[[], M], *,
                  max_tokens: int | None = None, refresh: bool = False, schema_version: str = "v1") -> RoleResult:
    """Run one JSON role end to end.

    ``check(model)`` returns ``(clean_model, hard_errors, n_clamped)``; hard errors trigger the repair call.
    """
    def fb(reason: str, errors: list[str] | None = None, raw=None) -> RoleResult:
        if raw is not None and raw.call_id:
            llm.mark(raw.call_id, "fallback")
        (log.debug if reason == "offline" else log.info)("role %s -> fallback (%s)", role, reason)
        return RoleResult(fallback(), "fallback", "fallback", errors=errors or [reason])

    raw = llm.json_call(role, system, user, max_tokens=max_tokens, refresh=refresh, schema_version=schema_version)
    if not raw.ok or raw.text is None:
        return fb(raw.error or "llm unavailable", raw=None)
    tokens_in, tokens_out = raw.tokens_in, raw.tokens_out
    errors: list[str] = []
    for attempt in (0, 1):
        try:
            obj = model_cls.model_validate(extract_json(raw.text))
            clean, errors, nclamp = check(obj)
            if not errors:
                outcome = "ok" if attempt == 0 else "repaired"
                if attempt == 1:
                    llm.mark(raw.call_id, "repaired")
                return RoleResult(clean, "llm", outcome, nclamp, [], tokens_in, tokens_out, raw.latency_ms, raw.model,
                                  raw.from_cache)
        except (ValueError, ValidationError) as e:
            errors = [f"{type(e).__name__}: {str(e)[:400]}"]
        if attempt == 1:
            break
        repair_user = (user + "\n\nYour previous JSON failed validation:\n- " + "\n- ".join(errors[:8]) +
                       "\n\nPrevious output (truncated):\n" + (raw.text or "")[:1500] +
                       "\n\nReturn the corrected JSON only, fixing every problem above.")
        raw = llm.json_call(role, system, repair_user, max_tokens=max_tokens, refresh=refresh,
                            schema_version=schema_version + "-repair")
        if not raw.ok or raw.text is None:
            return fb(raw.error or "repair call failed", errors)
        tokens_in += raw.tokens_in
        tokens_out += raw.tokens_out
    return fb("validation failed after repair: " + "; ".join(errors[:3]), errors, raw)


def compact(obj) -> str:
    """Compact JSON for prompts (token-cheap)."""
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
