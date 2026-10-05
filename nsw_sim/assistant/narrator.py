"""Narrator role: report executive summary and the 'Since your last session' digest. The model receives computed facts only; the verifier removes
any sentence with a number it cannot match; a deterministic template is the fallback (every number in it comes straight from the facts)."""
from __future__ import annotations

import json
from datetime import timedelta

from nsw_sim import clock, db
from nsw_sim.analytics import clearance, queries
from nsw_sim.analytics.queries import Filters
from nsw_sim.assistant import verifier
from nsw_sim.config import get_logger
from nsw_sim.llm import prompts as P
from nsw_sim.llm.client import LLM
from nsw_sim.llm.schemas import DigestOut, NarrativeFacts
from nsw_sim.llm.validate import run_json_role
from nsw_sim.money import fmt_ngn

log = get_logger("nsw.narrator")


def report_facts(rep: dict) -> dict:
    f = {"disclaimer": "Synthetic data, unofficial concept demo.", "period_start_wat": clock.fmt_wat(rep["period"][0], "%d %b %Y"), "period_end_wat": clock.fmt_wat(rep["period"][1], "%d %b %Y %H:%M"),
         "fingerprint_prefix": rep["fingerprint"][:12]}
    if "kpis" in rep:
        c, p = rep["kpis"]["current"], rep["kpis"]["previous"] or {}
        f["kpis"] = {k: (fmt_ngn(v) if k in ("assessed", "paid", "settled", "remitted", "outstanding", "in_transit") else v) for k, v in c.items() if v is not None and k in
                     ("assessed", "paid", "settled", "remitted", "outstanding", "in_transit", "median_dwell_days", "consignments", "open_high_alerts")}
        if p:
            f["change_vs_previous_period_percent"] = {k: round((c[k] - p[k]) / abs(p[k]) * 100, 1) for k in ("assessed", "paid", "settled") if p.get(k)}
    if "reconciliation" in rep:
        fw = rep["reconciliation"]["four_way"]
        f["reconciliation"] = {"assessed": fmt_ngn(fw["assessed"]["amount"]), "paid": fmt_ngn(fw["paid"]["amount"]), "settled": fmt_ngn(fw["settled"]["amount"]), "in_transit": fmt_ngn(fw["in_transit"]["amount"]),
                               "exception_classes": [f"{r._1}: {int(r.count)} items" for r in rep["reconciliation"]["summary"].head(4).itertuples()] if not rep["reconciliation"]["summary"].empty else []}
    if "clearance" in rep:
        c = rep["clearance"]
        dw = c["dwell"]
        f["clearance"] = {"median_dwell_days": round(float(dw["dwell_p50"].iloc[0]), 1) if not dw.empty else None, "digital_share_percent": round(c["digital_share"] * 100, 1) if c["digital_share"] else None,
                          "largest_delay_stage": c["bottlenecks"].iloc[0]["name"] if not c["bottlenecks"].empty else None}
    if "alerts" in rep:
        f["alerts_raised"] = int(len(rep["alerts"]))
    if "origin" in rep and not rep["origin"].empty:
        f["top_origin"] = [f"{r.origin_country}: {fmt_ngn(r.value)}" for r in rep["origin"].head(3).itertuples()]
    return f


def _fallback_summary(f: dict) -> NarrativeFacts:
    k = f.get("kpis", {})
    s = f"For {f['period_start_wat']} to {f['period_end_wat']} (synthetic data), the NSW channel assessed {k.get('assessed', '–')}, with {k.get('paid', '–')} paid and {k.get('settled', '–')} settled to agencies."
    bullets = []
    if "clearance" in f and f["clearance"].get("median_dwell_days"):
        bullets.append(f"Median dwell time was {f['clearance']['median_dwell_days']} days.")
    if "alerts_raised" in f:
        bullets.append(f"{f['alerts_raised']} supervision alerts were raised in the period.")
    return NarrativeFacts(summary=s, bullets=bullets)


def report_narrative(rep: dict, llm: LLM | None = None) -> dict:
    llm = llm or LLM()
    facts = report_facts(rep)
    user = "Facts JSON (use only these numbers; quote them exactly):\n" + json.dumps(facts, ensure_ascii=False)
    res = run_json_role(llm, "narrator", P.NARRATOR_SYSTEM, user, NarrativeFacts, lambda m: (m, [] if m.summary.strip() else ["empty summary"], 0), lambda: _fallback_summary(facts),
                        schema_version=P.PROMPT_VERSION)
    text = res.obj.summary + ("\n" + "\n".join(f"- {b}" for b in res.obj.bullets) if res.obj.bullets else "")
    kept, removed = verifier.strip_unverified_sentences(text, [facts])
    status = "verified" if not removed else f"partially verified ({len(removed)} sentence(s) removed)"
    if not kept:
        fb = _fallback_summary(facts)
        kept, removed2 = verifier.strip_unverified_sentences(fb.summary + " " + " ".join(fb.bullets), [facts])
        status, res.source = "deterministic summary", "fallback"
    return {"text": kept, "status": status, "removed": removed, "source": res.source, "facts": facts}


def digest(llm: LLM | None = None) -> dict | None:
    """'Since your last session' card from database facts."""
    conn = db.connect()
    try:
        now = queries.watermark() or queries.now_iso()
        last = db.kv_get(conn, "last_session_at")
        start = last if last and last < now else clock.iso(clock.to_dt(now) - timedelta(hours=24))
        db.kv_set(conn, "last_session_at", clock.iso(clock.utcnow()))
    finally:
        conn.close()
    f = Filters(start=start, end=now)
    ev_n = queries.total("consignments", f, now)
    paid = queries.total("paid", f, now)
    al = queries.alerts(f.with_(start=start), now)
    fprev = Filters(start=clock.iso(clock.to_dt(now) - timedelta(days=14)), end=clock.iso(clock.to_dt(now) - timedelta(days=7)))
    flast = Filters(start=clock.iso(clock.to_dt(now) - timedelta(days=7)), end=now)
    d1, d0 = clearance.dwell_table(flast, None, now), clearance.dwell_table(fprev, None, now)
    facts = {"since_wat": clock.fmt_wat(start, "%d %b %H:%M"), "until_wat": clock.fmt_wat(now, "%d %b %H:%M"), "new_consignments": int(ev_n), "collections_paid": fmt_ngn(paid),
             "new_alerts": int(len(al)), "high_severity_alerts": int((al["severity"] == "high").sum()) if not al.empty else 0,
             "median_dwell_days_last_7d": round(float(d1["dwell_p50"].iloc[0]), 1) if not d1.empty and d1["dwell_p50"].iloc[0] == d1["dwell_p50"].iloc[0] else None,
             "median_dwell_days_prior_7d": round(float(d0["dwell_p50"].iloc[0]), 1) if not d0.empty and d0["dwell_p50"].iloc[0] == d0["dwell_p50"].iloc[0] else None}
    llm = llm or LLM()
    fallback = DigestOut(headline=f"Between {facts['since_wat']} and {facts['until_wat']} WAT, {facts['new_consignments']} consignments were manifested and {facts['collections_paid']} was paid.",
                         bullets=[f"{facts['new_alerts']} alerts were raised ({facts['high_severity_alerts']} high severity).",
                                  *([f"Median dwell over the last 7 days was {facts['median_dwell_days_last_7d']} days, against {facts['median_dwell_days_prior_7d']} days the week before."] if facts["median_dwell_days_last_7d"] and facts["median_dwell_days_prior_7d"] else [])])
    res = run_json_role(llm, "digest", P.DIGEST_SYSTEM, "Facts JSON:\n" + json.dumps(facts, ensure_ascii=False), DigestOut, lambda m: (m, [] if m.headline.strip() else ["empty"], 0), lambda: fallback, schema_version=P.PROMPT_VERSION)
    text = res.obj.headline + " " + " ".join(res.obj.bullets)
    kept, removed = verifier.strip_unverified_sentences(text, [facts])
    if removed or not kept:
        res.obj, res.source = fallback, "fallback"
    return {"headline": res.obj.headline, "bullets": res.obj.bullets[:5], "source": "model-written, number-verified" if res.source == "llm" else "deterministic summary", "facts": facts}
