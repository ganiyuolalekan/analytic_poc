"""Deterministic trend projection for the dwell target tracker (no model, no randomness)."""
from __future__ import annotations

from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from nsw_sim.config import settings


def project_to_target(weekly: pd.DataFrame, target_days: float | None = None, target_date: str | None = None, window: int = 8,
                      horizon_weeks: int = 26) -> dict:
    """OLS on the last ``window`` weekly medians. Returns fitted/projected series, the date the line crosses the target
    (with a 95% band from the residual standard error) or ``None`` if the trend does not reach it."""
    b = settings()["benchmarks"]
    target_days = target_days or b["target_days"]
    target_date = target_date or b["target_date"]
    out = {"method": f"Ordinary least squares on the last {window} complete weekly medians (by gate-out week); linear extrapolation. "
                     "It assumes the recent pace continues and ignores seasonality and incidents.", "target_days": target_days,
           "target_date": target_date, "reaches_target": False}
    w = weekly.dropna(subset=["median_days"]).tail(window).reset_index(drop=True)
    if len(w) < 4:
        return {**out, "status": "insufficient data", "projected_date": None, "series": pd.DataFrame()}
    x = np.arange(len(w), dtype=float)
    y = w["median_days"].to_numpy(float)
    slope, intercept = np.polyfit(x, y, 1)
    resid = y - (slope * x + intercept)
    se = float(np.sqrt((resid ** 2).sum() / max(1, len(w) - 2)))
    last_week = date.fromisoformat(w["week"].iloc[-1])
    rows = []
    for k in range(-len(w) + 1, horizon_weeks + 1):
        xi = len(w) - 1 + k
        fit = slope * xi + intercept
        rows.append({"week": (last_week + timedelta(weeks=k)).isoformat(), "fit": fit, "lo": fit - 1.96 * se, "hi": fit + 1.96 * se, "projected": k > 0})
    series = pd.DataFrame(rows)
    out.update({"slope_days_per_week": float(slope), "current_fit_days": float(slope * (len(w) - 1) + intercept), "residual_se": se, "series": series})
    if slope >= -0.02:
        return {**out, "status": "not on current trend", "projected_date": None}

    def cross(offset: float) -> date | None:
        weeks = (target_days - (intercept + offset) - slope * (len(w) - 1)) / slope
        if weeks < 0:
            weeks = 0
        return last_week + timedelta(weeks=float(weeks)) if weeks < 520 else None
    mid, early, late = cross(0.0), cross(-1.96 * se), cross(1.96 * se)
    td = date.fromisoformat(target_date)
    out.update({"projected_date": mid.isoformat() if mid else None, "projected_early": early.isoformat() if early else None,
                "projected_late": late.isoformat() if late else None, "reaches_target": bool(mid and mid <= td),
                "status": "on track" if mid and mid <= td else "behind trend"})
    return out
