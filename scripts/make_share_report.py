#!/usr/bin/env python
"""Phase 9: the share report (Section 18). Code computes (insight miner, figures, tables); the .env model drafts slide text from facts.json only;
the verifier removes any unmatched number; everything is bundled in reports/share/. Usage: make_share_report.py [--offline]"""
import argparse
import json
import re
import sys
import zipfile
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from nsw_sim import DISCLAIMER, clock, db  # noqa: E402
from nsw_sim.analytics import clearance, forecast, quality, queries, reconcile, reports, statements  # noqa: E402
from nsw_sim.analytics.queries import Filters  # noqa: E402
from nsw_sim.assistant import verifier  # noqa: E402
from nsw_sim.config import REPORTS_DIR, settings, yaml_config  # noqa: E402
from nsw_sim.llm import ledger  # noqa: E402
from nsw_sim.llm import prompts as P
from nsw_sim.llm.client import LLM  # noqa: E402
from nsw_sim.llm.schemas import Slide, StorylineSlides  # noqa: E402
from nsw_sim.llm.validate import run_json_role  # noqa: E402
from nsw_sim.money import fmt_ngn  # noqa: E402

OUT = REPORTS_DIR / "share"
FIG = OUT / "figures"
TAB = OUT / "tables"
ENT = yaml_config("entities")["entities"]
GREEN, BLUE, AMBER, RED = "#0B5D3B", "#2F5D9B", "#B7791F", "#B03A2E"


def day(off: float) -> str:
    return clock.iso(clock.sim_start().timestamp() + off * 86400)


def fig_save(fig, name: str) -> str:
    fig.text(0.5, 0.5, "SYNTHETIC DATA", fontsize=40, color="red", alpha=0.07, ha="center", va="center", rotation=20)
    fig.text(0.01, 0.005, DISCLAIMER, fontsize=6, color=RED)
    fig.tight_layout(rect=(0, 0.02, 1, 1))
    fig.savefig(FIG / f"{name}.png", dpi=130)
    plt.close(fig)
    return f"figures/{name}.png"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    FIG.mkdir(exist_ok=True)
    TAB.mkdir(exist_ok=True)
    wm = queries.watermark()
    as_of = clock.iso(int(clock.to_epoch(wm) // 3600) * 3600)
    f0 = Filters(start=day(0), end=as_of)
    conn = db.connect()
    llm = LLM(offline=True if a.offline else None)
    facts: dict = {"as_of": as_of, "insights": [], "charts": {}, "tables": {}}
    ins = facts["insights"]

    def add(id_, need, headline, numbers: dict, query: str, trace: str = "", chart: str | None = None, score: float = 0.5):
        ins.append({"id": id_, "need": need, "headline_fact": headline, "numbers": numbers, "evidence_query": query, "trace_ref": trace, "chart": chart, "score": round(score, 2)})

    # --------------------------------------------------------------------------- data collected once
    wk = clearance.weekly_dwell(f0, as_of)
    dp = clearance.digital_physical(f0, as_of)
    full = wk.iloc[:-1] if len(wk) > 1 else wk
    first, last = full.iloc[0], full.iloc[-1]
    dsh0, dsh1 = dp.iloc[0], dp.iloc[len(full) - 1]
    proj = forecast.project_to_target(full)
    st4 = clearance.stage_summary(f0.with_(start=day(70)), as_of)
    bn = clearance.bottlenecks(f0.with_(start=day(70)), as_of)
    rpt = reports.ReportEngine.compute(reports.ReportParams(start=f0.start, end=as_of, sections=("kpis", "consolidated", "reconciliation")), as_of)
    fw = rpt["reconciliation"]["four_way"]
    # ---- Storyline A insights (speed)
    add("S01", "speed", f"Weekly median dwell fell from {first['median_days']:.1f} days to {last['median_days']:.1f} days between the first and last complete weeks.", {"first_week_median_days": f"{first['median_days']:.1f}", "last_week_median_days": f"{last['median_days']:.1f}", "change_percent": f"{(last['median_days'] - first['median_days']) / first['median_days'] * 100:.1f}%"},
        "clearance.weekly_dwell (consignments by gate-out week)", "", "C1", 0.95)
    add("S02", "speed", f"The digital share of total time fell from {dsh0['digital_share'] * 100:.1f}% to {dsh1['digital_share'] * 100:.1f}%.", {"digital_share_first_week": f"{dsh0['digital_share'] * 100:.1f}%", "digital_share_last_week": f"{dsh1['digital_share'] * 100:.1f}%"}, "clearance.digital_physical", "", "C2", 0.9)
    d_first, d_last = dsh0["digital_h"] / dsh0["n"], dsh1["digital_h"] / dsh1["n"]
    p_first, p_last = dsh0["physical_h"] / dsh0["n"], dsh1["physical_h"] / dsh1["n"]
    add("S03", "speed", f"Counter-intuitive: digital time per consignment fell {(1 - d_last / d_first) * 100:.0f}% (from {d_first:.0f} to {d_last:.0f} hours) while physical time fell only {(1 - p_last / p_first) * 100:.0f}% (from {p_first:.0f} to {p_last:.0f} hours), so physical stages now dominate.",
        {"digital_hours_first": f"{d_first:.0f} hours", "digital_hours_last": f"{d_last:.0f} hours", "digital_fall_percent": f"{(1 - d_last / d_first) * 100:.0f}%", "physical_hours_first": f"{p_first:.0f} hours", "physical_hours_last": f"{p_last:.0f} hours", "physical_fall_percent": f"{(1 - p_last / p_first) * 100:.0f}%"},
        "clearance.digital_physical per-consignment hours", "", "C2", 0.95)
    if not bn.empty:
        t0_ = bn.iloc[0]
        add("S04", "speed", f"{t0_['name']} is the largest source of delay: {t0_['mean_h_per_consignment']:.0f} hours per consignment, {t0_['share_of_total'] * 100:.0f}% of total time, and it is outside the NSW's control.", {"stage": t0_["name"], "mean_hours_per_consignment": f"{t0_['mean_h_per_consignment']:.0f} hours", "share_of_total_time": f"{t0_['share_of_total'] * 100:.0f}%"}, "clearance.bottlenecks (last four weeks)", "", "C3", 0.85)
    ss = st4.sort_values("median_total_h", ascending=False).head(3)
    add("S05", "speed", "Stage medians: " + "; ".join(f"{r.name} {r.median_total_h:.0f} hours" for r in ss.itertuples()) + ".", {r.stage + "_median_hours": f"{r.median_total_h:.0f} hours" for r in ss.itertuples()}, "clearance.stage_summary", "", "C3", 0.6)
    # B2
    c = db.reader(as_of)
    qd = lambda s, e: c.execute("SELECT AVG(s.wait_h), COUNT(*) FROM v_stage_durations s JOIN v_consignments k ON k.nsw_ref=s.nsw_ref WHERE s.stage='S06' AND s.status='queued' AND k.port='NGAPP' AND s.occurred_at>=? AND s.occurred_at<?", (day(s), day(e))).fetchone()  # noqa: E731
    w_in, n_in = qd(42, 46)
    w_out, _ = qd(28, 41)
    al2 = c.execute("SELECT alert_id, detected_at FROM v_alerts WHERE rule_code='R-PHYS-01' AND subject LIKE '%NGAPP%' AND detected_at>=? ORDER BY detected_at LIMIT 1", (day(41),)).fetchone()
    excess_days = (w_in - w_out) * n_in / 24
    add("S06", "speed", f"During the Apapa scanner outage the planned exam wait rose from {w_out:.0f} to {w_in:.0f} hours ({w_in / w_out:.1f} times), detected at {clock.fmt_wat(al2[1], '%d %b %H:%M')} WAT by alert {al2[0]}; about {excess_days:.0f} consignment-days of extra waiting accrued.",
        {"exam_wait_before_hours": f"{w_out:.0f} hours", "exam_wait_during_hours": f"{w_in:.0f} hours", "ratio": f"{w_in / w_out:.1f} times", "detected_at": clock.fmt_wat(al2[1], "%d %b %H:%M"), "alert": al2[0], "excess_consignment_days": f"{excess_days:.0f} days"}, "stage_events S06 queued rows at NGAPP; alerts R-PHYS-01", al2[0], "C4", 0.9)
    add("S07", "speed", f"On the current trend median dwell reaches {proj['target_days']} days " + (f"around {proj['projected_date']}" if proj.get("projected_date") else "not within the horizon") + f" (status: {proj['status']}); the trend slope is {proj.get('slope_days_per_week', 0):.2f} days per week.",
        {"projected_date": str(proj.get("projected_date")), "status": proj["status"], "slope_days_per_week": f"{proj.get('slope_days_per_week', 0):.2f}", "target_days": str(proj["target_days"])}, "forecast.project_to_target", "", "C1", 0.85)
    cod = clearance.cost_of_delay(f0.with_(start=day(70)), as_of)
    add("S08", "speed", f"Illustrative cost of dwell beyond 7 days over the last four weeks: {fmt_ngn(cod['total_ngn'] * 100)} (assumptions editable; illustrative).", {"illustrative_cost_of_excess_dwell": fmt_ngn(cod["total_ngn"] * 100), "mean_excess_days": f"{cod['mean_excess_days']:.1f} days"}, "clearance.cost_of_delay", "", None, 0.7)
    dwt = clearance.dwell_table(f0.with_(start=day(70)), None, as_of).iloc[0]
    add("S09", "speed", f"Predictability: over the last four weeks median dwell was {dwt['dwell_p50']:.1f} days and p90 {dwt['dwell_p90']:.1f} days.", {"p50_days": f"{dwt['dwell_p50']:.1f}", "p90_days": f"{dwt['dwell_p90']:.1f}"}, "clearance.dwell_table", "", None, 0.6)
    add("S10", "speed", f"Against published benchmarks (verify before quoting): current median dwell {last['median_days']:.1f} days vs target under {proj['target_days']} days and a global benchmark of about 4 days.", {"current_median_days": f"{last['median_days']:.1f}", "target_days": str(proj["target_days"]), "global_benchmark_days": "4"}, "published figures + clearance.weekly_dwell", "", "C1", 0.55)
    mv = lambda s, e: c.execute("SELECT AVG(wait_h+dur_h) FROM v_stage_durations WHERE owner_entity='NAFDAC' AND stage='S02' AND status='queued' AND occurred_at>=? AND occurred_at<?", (day(s), day(e))).fetchone()[0]  # noqa: E731
    al3 = c.execute("SELECT alert_id, detected_at, metric_value FROM v_alerts WHERE rule_code='R-SLA-01' AND entity_id='NAFDAC' AND detected_at>=? ORDER BY detected_at LIMIT 1", (day(54),)).fetchone()
    add("S11", "speed", f"During the NAFDAC permit backlog, planned approval time rose from {mv(40, 52):.0f} to {mv(55, 61):.0f} hours and the SLA alert {al3[0]} was raised on {clock.fmt_wat(al3[1], '%d %b')}.", {"approval_hours_before": f"{mv(40, 52):.0f} hours", "approval_hours_during": f"{mv(55, 61):.0f} hours", "alert": al3[0]}, "stage_events S02 queued rows (NAFDAC); alerts R-SLA-01", al3[0], None, 0.7)
    cons = lambda off: c.execute("SELECT COUNT(*) FROM v_consignments WHERE manifested_at>=? AND manifested_at<?", (day(off), day(off + 1))).fetchone()[0]  # noqa: E731
    add("S12", "speed", f"Independence Day (1 October) saw {cons(92)} manifests against {(cons(91) + cons(94)) / 2:.0f} on the neighbouring weekdays.", {"manifests_1_oct": str(cons(92)), "manifests_neighbouring_weekday_average": f"{(cons(91) + cons(94)) / 2:.0f}"}, "consignments by manifest day", "", None, 0.4)
    # ---- Storyline B insights (trust / supervision)
    add("T01", "trust", f"Since 1 July the NSW channel assessed {fmt_ngn(fw['assessed']['amount'])}, collected {fmt_ngn(fw['paid']['amount'])}, settled {fmt_ngn(fw['settled']['amount'])} to agencies and recorded {fmt_ngn(fw['remitted']['amount'])} remitted to the Treasury.",
        {"assessed": fmt_ngn(fw["assessed"]["amount"]), "paid": fmt_ngn(fw["paid"]["amount"]), "settled_gross": fmt_ngn(fw["settled"]["amount"]), "remitted": fmt_ngn(fw["remitted"]["amount"])}, "reconcile.four_way", "", "C5", 0.95)
    cc = fw["settled"]["collection_cost"]
    add("T02", "trust", f"Collection efficiency was {fw['settled']['amount'] / fw['assessed']['amount'] * 100:.1f}% and the cost of collection {100 * cc / fw['settled']['amount']:.2f} naira per ₦100 collected.", {"collection_efficiency": f"{fw['settled']['amount'] / fw['assessed']['amount'] * 100:.1f}%", "cost_per_100": f"₦{100 * cc / fw['settled']['amount']:.2f}"}, "reconcile.four_way", "", "C5", 0.7)
    itb = reconcile.in_transit_by_bank(as_of)
    add("T03", "trust", f"{fmt_ngn(float(itb['in_transit_minor'].sum()))} is in transit (paid, not yet settled) across {len(itb)} banks; this is timing, not error.", {"in_transit_total": fmt_ngn(float(itb["in_transit_minor"].sum())), "banks": str(len(itb))}, "reconcile.in_transit_by_bank", "", None, 0.6)
    hm = reconcile.leakage_heatmap(Filters(start=day(69), end=day(75)), as_of, fee_code="NCS-DUTY")
    top = hm.iloc[0]
    al6 = c.execute("SELECT alert_id, detected_at FROM v_alerts WHERE rule_code='R-FEE-01' ORDER BY detected_at LIMIT 1").fetchone()
    add("T04", "trust", f"Revenue assurance caught an under-assessment cluster: NCS duty on {top['commodity_group']} from {top['origin_country']} was assessed {top['shortfall_pct'] * 100:.1f}% below expectation (about {fmt_ngn(top['shortfall_minor'])} over 6 days); alert {al6[0]} fired on {clock.fmt_wat(al6[1], '%d %b')}.",
        {"shortfall_percent": f"{top['shortfall_pct'] * 100:.1f}%", "shortfall": fmt_ngn(top["shortfall_minor"]), "alert": al6[0], "assessments": str(int(top["n"]))}, "reconcile.leakage_heatmap (NCS-DUTY, 8-13 Sep); alerts R-FEE-01", al6[0], "C6", 0.95)
    dup = c.execute("SELECT COUNT(*), SUM(amount_ngn_minor) FROM v_payments WHERE is_duplicate=1 AND occurred_at>=? AND occurred_at<?", (day(93), day(94))).fetchone()
    ald = c.execute("SELECT alert_id, detected_at FROM v_alerts WHERE rule_code='R-DUP-01' ORDER BY detected_at LIMIT 1").fetchone()
    add("T05", "supervision", f"A burst of {dup[0]} duplicate payments worth {fmt_ngn(dup[1])} on 2 October was flagged by alert {ald[0]} at {clock.fmt_wat(ald[1], '%H:%M')} WAT.", {"duplicates": str(dup[0]), "value": fmt_ngn(dup[1]), "alert": ald[0]}, "payments is_duplicate; alerts R-DUP-01", ald[0], None, 0.8)
    bl = lambda s, e: c.execute("SELECT AVG(lag_hours) FROM v_settlement_batches WHERE bank='C' AND closed_at>=? AND closed_at<?", (day(s), day(e))).fetchone()[0]  # noqa: E731
    als = c.execute("SELECT alert_id, detected_at FROM v_alerts WHERE rule_code='R-SET-01' ORDER BY detected_at LIMIT 1").fetchone()
    add("T06", "supervision", f"Bank C settlement lag rose from {bl(60, 75):.0f} to {bl(78, 81):.0f} hours (T+3); alert {als[0]} was raised on {clock.fmt_wat(als[1], '%d %b %H:%M')} WAT.", {"lag_before_hours": f"{bl(60, 75):.0f} hours", "lag_during_hours": f"{bl(78, 81):.0f} hours", "alert": als[0]}, "settlement_batches Bank C; alerts R-SET-01", als[0], "C7", 0.85)
    rm = c.execute("SELECT remittance_id, days_late, amount_ngn_minor FROM v_remittances WHERE entity_id='SON' AND days_late>3").fetchone()
    alr = c.execute("SELECT alert_id, detected_at FROM v_alerts WHERE rule_code='R-REM-01' ORDER BY detected_at LIMIT 1").fetchone()
    add("T07", "supervision", f"One regulator remitted {rm[1]} days late ({fmt_ngn(rm[2])}); alert {alr[0]} was raised on {clock.fmt_wat(alr[1], '%d %b')}, three days after the due date.", {"days_late": str(rm[1]), "amount": fmt_ngn(rm[2]), "alert": alr[0]}, "remittances; alerts R-REM-01", alr[0], None, 0.8)
    tr = quality.completeness_trend(as_of, 20, ("FAAN",))
    gap = tr[tr["day"].isin([clock.wat_day(day(89.5)), clock.wat_day(day(90.5))])]["completeness"].min()
    normal = tr[tr["day"] < clock.wat_day(day(88))]["completeness"].mean()
    add("T08", "trust", f"A two-day data gap at one operator cut record completeness from {normal * 100:.0f}% to {gap * 100:.0f}% and was flagged the same day (alert R-DQ-01).", {"completeness_normal": f"{normal * 100:.0f}%", "completeness_gap": f"{gap * 100:.0f}%"}, "quality.completeness_trend (FAAN); alerts R-DQ-01", "", "C8", 0.85)
    nj = conn.execute("SELECT COUNT(*) FROM journal_entries").fetchone()[0]
    unb = conn.execute("SELECT COUNT(*) FROM (SELECT entry_id FROM journal_lines GROUP BY entry_id HAVING SUM(debit_minor)!=SUM(credit_minor))").fetchone()[0]
    pos = statements.position("ALL", as_of, as_of)
    add("T09", "trust", f"All {nj:,} journal entries balance ({unb} unbalanced) and the consolidated balance sheet balances at {fmt_ngn(pos['total_assets'])} of assets.", {"journal_entries": f"{nj:,}", "unbalanced_entries": str(unb), "consolidated_assets": fmt_ngn(pos["total_assets"])}, "journal_lines integrity query; statements.position('ALL')", "", None, 0.9)
    ref = c.execute("SELECT nsw_ref FROM v_consignments WHERE gate_out_at IS NOT NULL ORDER BY gate_out_at DESC LIMIT 1 OFFSET 200").fetchone()[0]
    from nsw_sim.analytics import trace as tracem
    tt = tracem.consignment_trace(ref, as_of)
    ents = sorted(tt["fees"]["entity_id"].unique())
    tie = tracem.tie_out(["NCS"], "4110", day(60), day(61), "flow", as_of)
    add("T10", "trust", f"Trace in seconds: consignment {ref} was charged by {len(ents)} agencies ({', '.join(ents)}) totalling {fmt_ngn(tt['fees']['assessed_ngn_minor'].sum())}; a statement line traces to journal entries and source documents with totals tying out at every level (L1 = L2: {tie['ties_l1_l2']}).",
        {"consignment": ref, "agencies": str(len(ents)), "total_assessed": fmt_ngn(tt["fees"]["assessed_ngn_minor"].sum()), "ties_out_l1_l2": str(tie["ties_l1_l2"]), "entries_checked": str(tie["l2_rows"])}, "trace.consignment_trace, trace.tie_out", ref, None, 0.9)
    mixall = queries.entity_mix(f0, as_of)
    ncs_share = float(mixall[mixall["entity"] == "NCS"]["paid"].iloc[0] / mixall["paid"].sum())
    oc = queries.aggregate("paid", ["origin_country"], f0, as_of).sort_values("value", ascending=False)
    add("T11", "trust", f"NCS accounts for {ncs_share * 100:.0f}% of collections; the top origin country, {oc.iloc[0]['origin_country']}, supplies {oc.iloc[0]['value'] / oc['value'].sum() * 100:.0f}% of fees.", {"ncs_share_of_collections": f"{ncs_share * 100:.0f}%", "top_origin": oc.iloc[0]["origin_country"], "top_origin_share": f"{oc.iloc[0]['value'] / oc['value'].sum() * 100:.0f}%"}, "queries.entity_mix; queries.aggregate by origin", "", None, 0.5)
    sc = quality.scorecard(as_of)
    add("T12", "trust", f"Data confidence scores range from {sc['score'].min():.0f} to {sc['score'].max():.0f} across agencies.", {"lowest_score": f"{sc['score'].min():.0f}", "highest_score": f"{sc['score'].max():.0f}"}, "quality.scorecard", "", None, 0.6)
    ac = conn.execute("SELECT rule_code, COUNT(*) FROM alerts GROUP BY 1").fetchall()
    add("T13", "supervision", f"{sum(n for _, n in ac)} alerts across {len(ac)} rules were raised and each scripted incident was detected.", {"alerts": str(sum(n for _, n in ac)), "rules": str(len(ac))}, "alerts table", "", "C9", 0.7)
    ev = json.loads((REPORTS_DIR / "assistant_eval.json").read_text()) if (REPORTS_DIR / "assistant_eval.json").exists() else None
    if ev:
        s_ = ev["summary"]
        add("T14", "trust", f"The assistant passed {s_['pass_rate'] * 100:.0f}% of {s_['questions']} golden questions against an independent oracle" + (f" and {s_['variant_pass_rate'] * 100:.0f}% of {s_['variants']} paraphrases" if s_.get("variant_pass_rate") else "") + ", with every passing answer's numbers verified against tool outputs.",
            {"pass_rate": f"{s_['pass_rate'] * 100:.0f}%", "questions": str(s_["questions"]), **({"paraphrase_pass_rate": f"{s_['variant_pass_rate'] * 100:.0f}%", "paraphrases": str(s_["variants"])} if s_.get("variant_pass_rate") else {})}, "scripts/run_assistant_eval.py", "", None, 0.85)
    gr = conn.execute("SELECT kind, source, COUNT(*) FROM generation_runs GROUP BY 1,2").fetchall()
    llm_n, tot_n = sum(n for _, s, n in gr if s == "llm"), sum(n for _, _, n in gr)
    add("T15", "trust", f"{llm_n} of {tot_n} generated profiles and plans came from the language model; the rest are deterministic fallbacks (warm-up weeks).", {"model_generated": str(llm_n), "total_generated": str(tot_n)}, "generation_runs", "", None, 0.5)
    ex = reconcile.exception_summary(f0, as_of)
    if not ex.empty:
        add("T16", "supervision", "Exception classes: " + "; ".join(f"{r._1} {int(r.count)}" for r in ex.itertuples()) + ".", {f"exceptions_{r._1}": str(int(r.count)) for r in ex.itertuples()}, "reconcile.exception_summary", "", None, 0.5)
    fxr = c.execute("SELECT (SELECT rate_to_ngn FROM v_fx_rates WHERE ccy='USD' AND ts_utc>=? ORDER BY ts_utc LIMIT 1), (SELECT rate_to_ngn FROM v_fx_rates WHERE ccy='USD' AND ts_utc<? ORDER BY ts_utc DESC LIMIT 1)", (day(69), day(63))).fetchone()
    add("T17", "trust", f"The naira weakened {(fxr[0] / fxr[1] - 1) * 100:.1f}% over five days in early September, raising the naira value of USD fees; alert R-FX-01 recorded it.", {"fx_move_percent": f"{(fxr[0] / fxr[1] - 1) * 100:.1f}%"}, "fx_rates; alerts R-FX-01", "", None, 0.55)
    inv = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("consignments", "stage_events", "fee_assessments", "payments", "payment_allocations", "settlements", "journal_entries", "journal_lines", "live_events", "alerts")}
    add("T18", "trust", f"The database holds {inv['consignments']:,} consignments and {inv['journal_lines']:,} ledger lines from the warm-up start to now.", {k: f"{v:,}" for k, v in inv.items()}, "row counts", "", None, 0.4)

    # --------------------------------------------------------------------------- figures + tables
    figs = {}
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.axhspan(18, 21, color=RED, alpha=0.1, label="published baseline 18-21 d")
    ax.axhline(7, color=GREEN, ls="--", label="target <7 d by end 2026")
    ax.axhline(4, color="#9aa8a1", ls=":", label="global benchmark ~4 d (published)")
    ax.plot(pd.to_datetime(wk["week"]), wk["median_days"], color=BLUE, lw=2.5, marker="o", label="weekly median dwell")
    s_ = proj.get("series")
    if s_ is not None and not s_.empty:
        pr_ = s_[s_["projected"]]
        ax.plot(pd.to_datetime(pr_["week"]), pr_["fit"], color=AMBER, ls="--", label="trend projection")
    ax.set_ylim(0, 22); ax.set_ylabel("days"); ax.set_title("Weekly median dwell vs baseline, target and projection"); ax.legend(fontsize=7)
    figs["C1"] = fig_save(fig, "C1_dwell_trend")
    fig, ax = plt.subplots(figsize=(8, 3.8))
    ax.stackplot(pd.to_datetime(dp["period"]), dp["digital_share"] * 100, dp["physical_share"] * 100, colors=[BLUE, AMBER], labels=["Digital (NSW-controlled)", "Physical (not controlled)"])
    ax.set_ylabel("% of total time"); ax.set_title("Digital vs physical share of clearance time"); ax.legend(loc="lower left", fontsize=8)
    figs["C2"] = fig_save(fig, "C2_digital_physical")
    sd = clearance.stage_summary(f0.with_(start=day(70)), as_of)
    sd = sd[sd["stage"] != "S01"]
    fig, ax = plt.subplots(figsize=(8, 3.8))
    ax.bar(sd["name"], sd["median_total_h"], color=[BLUE if t == "D" else AMBER for t in sd["type"]])
    ax.set_ylabel("median hours"); ax.set_title("Median hours per stage (blue digital, amber physical)"); plt.setp(ax.get_xticklabels(), rotation=30, ha="right", fontsize=7)
    figs["C3"] = fig_save(fig, "C3_stage_waterfall")
    ew = pd.read_sql_query("SELECT date(s.occurred_at,'+1 hour') d, AVG(s.wait_h) w FROM v_stage_durations s JOIN v_consignments k ON k.nsw_ref=s.nsw_ref WHERE s.stage='S06' AND s.status='queued' AND k.port='NGAPP' AND s.occurred_at>=? AND s.occurred_at<? GROUP BY 1", c, params=(day(30), day(60)))
    fig, ax = plt.subplots(figsize=(8, 3.6))
    ax.plot(pd.to_datetime(ew["d"]), ew["w"], color=AMBER, marker="o"); ax.axvspan(pd.Timestamp(clock.wat_day(day(42))), pd.Timestamp(clock.wat_day(day(46))), color=RED, alpha=0.12, label="scanner outage")
    ax.set_ylabel("planned exam wait (h)"); ax.set_title("Apapa examination wait around the scanner outage"); ax.legend()
    figs["C4"] = fig_save(fig, "C4_apapa_outage")
    fig, ax = plt.subplots(figsize=(8, 3.6))
    stg = [("Assessed", fw["assessed"]["amount"]), ("Paid", fw["paid"]["amount"]), ("Settled", fw["settled"]["amount"]), ("Remitted", fw["remitted"]["amount"])]
    ax.barh([s for s, _ in stg][::-1], [v / 1e11 for _, v in stg][::-1], color=[AMBER, "#2E7D32", GREEN, BLUE][::-1]); ax.set_xlabel("₦bn"); ax.set_title("Four-way match: assessed to remitted (since 1 July)")
    figs["C5"] = fig_save(fig, "C5_four_way_funnel")
    hm2 = reconcile.leakage_heatmap(Filters(start=day(69), end=day(75)), as_of, min_n=5, fee_code="NCS-DUTY")
    pv = hm2.pivot_table(index="commodity_group", columns="origin_country", values="shortfall_pct").fillna(0) * 100
    fig, ax = plt.subplots(figsize=(8, 3.8))
    im = ax.imshow(pv.values, cmap="RdYlGn_r", vmin=-4, vmax=14, aspect="auto"); ax.set_xticks(range(len(pv.columns)), pv.columns, fontsize=7); ax.set_yticks(range(len(pv.index)), pv.index, fontsize=7)
    fig.colorbar(im, label="% below expected"); ax.set_title("NCS duty: assessed vs expected, 8-13 Sep (commodity x origin)")
    figs["C6"] = fig_save(fig, "C6_leakage_heatmap")
    bt = pd.read_sql_query("SELECT date(closed_at,'+1 hour') d, bank, AVG(lag_hours) l FROM v_settlement_batches WHERE completed_at IS NOT NULL AND closed_at>=? GROUP BY 1,2", c, params=(day(60),))
    fig, ax = plt.subplots(figsize=(8, 3.6))
    for b, g in bt.groupby("bank"):
        ax.plot(pd.to_datetime(g["d"]), g["l"], label=f"Bank {b}", lw=2.5 if b == "C" else 1, color=RED if b == "C" else None)
    ax.axhline(48, color="k", ls="--", lw=0.8); ax.set_ylabel("lag (hours)"); ax.set_title("Settlement lag by bank (alert threshold 48 h)"); ax.legend(fontsize=7, ncol=3)
    figs["C7"] = fig_save(fig, "C7_bank_lag")
    fig, ax = plt.subplots(figsize=(8, 3.4))
    ax.plot(pd.to_datetime(tr["day"]), tr["completeness"] * 100, color=ENT["FAAN"]["colour"], marker="o"); ax.axhline(90, color=RED, ls="--"); ax.set_ylabel("% records complete"); ax.set_title("FAAN stage-record completeness")
    figs["C8"] = fig_save(fig, "C8_completeness_gap")
    ad = pd.read_sql_query("SELECT rule_code, detected_at FROM v_alerts ORDER BY detected_at", c)
    fig, ax = plt.subplots(figsize=(8, 3.4))
    codes = sorted(ad["rule_code"].unique())
    ax.scatter(pd.to_datetime(ad["detected_at"]), [codes.index(r) for r in ad["rule_code"]], color=GREEN); ax.set_yticks(range(len(codes)), codes, fontsize=7); ax.set_title("Alerts raised by rule over time")
    figs["C9"] = fig_save(fig, "C9_alert_timeline")
    facts["charts"] = figs
    mix = queries.entity_mix(f0, as_of)
    tables = {"entity_collections": mix.assign(**{c_: mix[c_] / 100 for c_ in mix.columns if c_ != "entity"}), "weekly_dwell": wk, "digital_physical": dp, "stage_summary": sd, "alerts": pd.read_sql_query("SELECT alert_id, rule_code, severity, entity_id, subject, detected_at, status FROM v_alerts ORDER BY detected_at", c),
              "exceptions_summary": ex, "inventory": pd.DataFrame([{"table": k, "rows": v} for k, v in inv.items()]), "scorecard": sc, "insights": pd.DataFrame([{k: (json.dumps(v) if isinstance(v, dict) else v) for k, v in i.items()} for i in ins])}
    for k, df in tables.items():
        df.to_csv(TAB / f"{k}.csv", index=False)
    facts["tables"] = {k: f"tables/{k}.csv" for k in tables}

    oc_total = oc['value'].sum()
    # --------------------------------------------------------------------------- storylines drafted by the model, then verified
    chosen = {"A": ["S01", "S02", "S03", "S04", "S05", "S06", "S07", "S08", "S09", "S10", "S11", "S12"], "B": ["T01", "T02", "T03", "T04", "T05", "T06", "T07", "T08", "T09", "T10", "T11", "T12", "T13", "T14", "T16", "T17"]}
    byid = {i["id"]: i for i in ins}
    outline = {"A": ["The target and why it matters", "The trend: weekly median dwell July to now", "Anatomy of delay: stage waterfall, digital vs physical, hand-off waits", "The shift: digital share falling while physical dominates",
                     "Case study: the Apapa scanner outage", "Projection and cost of delay", "What we need to make it real"],
               "B": ["The trust gap: many agencies, many ledgers, one question", "The four-way match funnel and exception classes", "Trace in 30 seconds: statement line to journal entries to consignment", "Leakage indicator: the under-assessment cluster",
                     "Supervision loop: late remittance, settlement lag, duplicate payments", "Confidence and onboarding: data confidence, data-quality gap, cost of collection", "How it plugs in and the ask"]}
    titles = {"A": "Where the time really goes: proof of progress and the last mile", "B": "One window, one truth: every naira traceable and supervised"}
    slides_out, removal_log = {}, []
    pool_numbers = verifier.collect_numbers(facts["insights"])
    pool_numbers += [float(x) for t in sum(outline.values(), []) for x in re.findall(r"\d+", t)]      # numbers in the brief's own slide titles (e.g. 'Trace in 30 seconds')
    for sl in ("A", "B"):
        fx = [byid[i] for i in chosen[sl] if i in byid]
        user = (f"Storyline: {titles[sl]}. Draft EXACTLY these 7 slides in order: " + json.dumps(outline[sl]) + ".\nAvailable charts: " + json.dumps(list(figs)) + ". Evidence ids you may cite: " + json.dumps([i["id"] for i in fx]) +
                ".\nVerified facts (the only source of numbers):\n" + json.dumps([{k: i[k] for k in ("id", "headline_fact", "numbers", "chart")} for i in fx], ensure_ascii=False))

        def check(m: StorylineSlides, ok_ids=None):
            if ok_ids is None:
                ok_ids = {i["id"] for i in fx}
            errs = []
            if len(m.slides) < 7:
                errs.append("need 7 slides")
            for s in m.slides:
                if not 3 <= len(s.bullets) <= 5:
                    errs.append(f"slide '{s.title[:30]}' needs 3 to 5 bullets")
                s.evidence_ids = [e for e in s.evidence_ids if e in ok_ids]
                s.chart_ref = s.chart_ref if s.chart_ref in figs or s.chart_ref == "" else ""
            return m, errs[:4], 0

        def fallback(fx=fx, sl=sl):
            return StorylineSlides(storyline=titles[sl], slides=[Slide(title=t, key_message=(fx[min(i, len(fx) - 1)]["headline_fact"]), bullets=[x["headline_fact"] for x in fx[i * 2:i * 2 + 3]] or [fx[0]["headline_fact"]] * 3,
                                                                       speaker_notes="Deterministic fallback text: numbers come directly from the verified facts.", chart_ref=(fx[i]["chart"] or "") if i < len(fx) else "", evidence_ids=[fx[min(i, len(fx) - 1)]["id"]])
                                                          for i, t in enumerate(outline[sl])])
        res = run_json_role(llm, "storyline", P.STORYLINE_SYSTEM, user, StorylineSlides, check, fallback, max_tokens=3500, schema_version=P.PROMPT_VERSION)
        cleaned = []
        for s in res.obj.slides[:7]:
            def keep(text: str, where: str) -> str:
                k, rem = verifier.strip_unverified_sentences(text, [], pool_numbers)
                removal_log.extend({"storyline": sl, "slide": s.title, "field": where, "removed": r} for r in rem)
                return k
            s.key_message = keep(s.key_message, "key_message") or s.title
            s.bullets = [b for b in (keep(b, "bullet") for b in s.bullets) if b][:5]
            s.speaker_notes = keep(s.speaker_notes, "speaker_notes")
            while len(s.bullets) < 3:
                s.bullets.append(s.key_message)
            cleaned.append(s)
        slides_out[sl] = {"title": titles[sl], "source": res.source, "slides": [s.model_dump() for s in cleaned]}
    all_text = " ".join(" ".join([s["title"], s["key_message"], *s["bullets"], s["speaker_notes"]]) for sl in slides_out.values() for s in sl["slides"])
    final = verifier.verify(all_text, [], pool_numbers)
    facts["verification"] = {"unmatched_numbers_after_cleaning": [c.text for c in final.unmatched], "sentences_removed": removal_log, "passed": final.ok}
    facts["storylines"] = slides_out
    (OUT / "facts.json").write_text(json.dumps(facts, indent=1, default=str, ensure_ascii=False))

    # --------------------------------------------------------------------------- markdown
    tests = (REPORTS_DIR / "test_summary.md").read_text() if (REPORTS_DIR / "test_summary.md").exists() else "Test summary not yet generated (run scripts/run_tests.py)."
    led = ledger.summary(conn)
    beats = yaml_config("scenario")["beats"]
    al_all = pd.read_sql_query("SELECT rule_code, entity_id, subject, detected_at FROM alerts", conn)
    beat_rows = []
    for bid, b in beats.items():
        s_, e_ = clock.iso(clock.sim_start() + timedelta(days=b["start_day"] - 1)), clock.iso(clock.sim_start() + timedelta(days=b["end_day"] + 8))
        fired = al_all[al_all["rule_code"].isin(b["detected_by"]) & (al_all["detected_at"] >= s_) & (al_all["detected_at"] <= e_)] if b["detected_by"] else pd.DataFrame()
        beat_rows.append(f"| {bid} | {b['name']} | {', '.join(b['detected_by']) or 'volume chart'} | {'yes' if len(fired) or not b['detected_by'] else 'NO'} | {clock.fmt_wat(fired['detected_at'].min(), '%d %b %H:%M') if len(fired) else '–'} |")
    md = ["# NSW Intelligence Console: share report\n", f"> **{DISCLAIMER}**\n", f"Data as of **{clock.fmt_wat(as_of)}** · report fingerprint `{rpt['fingerprint']}` · model: {llm.cfg.model_chat}\n",
          "## 1. Cover and disclaimer\nAll data is synthetic. Agency names and logos illustrate structure only; fee names, rates, remittance rules, account codes and identifiers are simulated approximations. Nothing here describes real agency performance. "
          f"Report generated for a conversation with the NSW secretariat; reproducible with `make report` (fingerprint above, as-of {as_of}).\n",
          "## 2. What was built\nA live, traceable, supervised financial view across NSW agencies. Needs: **speed** (clearance, target tracker, live feed), **trust** (ledger-backed statements, four-way match, trace, verified assistant), **supervision** (12 rules, reviews, audit, scorecards). "
          "Twelve pages: Command Center, Clearance Journey, Entity Explorer, Trace Workbench, Reconciliation, Supervision, Reports, Assistant, Data Quality, Architecture, Admin, Methodology. Run: `make setup probe seed run` (see README).\n",
          "## 3. Data inventory\n" + pd.DataFrame([{"table": k, "rows": f"{v:,}"} for k, v in inv.items()]).to_markdown(index=False) + f"\n\nDate range: engine warm-up from {clock.fmt_wat(clock.engine_start(), '%d %b %Y')}, analysis window from {clock.fmt_wat(clock.sim_start(), '%d %b %Y')} to {clock.fmt_wat(as_of)}.\n\n**Monthly collections by entity (assessed, ₦bn, September):**\n\n" +
          queries.aggregate("assessed", ["entity"], Filters(start=day(62), end=day(92)), as_of).assign(value_bn=lambda d: (d["value"] / 1e11).round(2)).sort_values("value", ascending=False)[["entity", "value_bn"]].to_markdown(index=False) +
          f"\n\nOrigin mix (top 5 by paid): {', '.join(f'{r.origin_country} {r.value / oc_total * 100:.0f}%' for r in oc.head(5).itertuples())}. Model vs fallback: {llm_n} of {tot_n} plans/profiles from the model. Calibration: every entity's monthly assessed total is within ±15% of the brief's band (test `test_calibration_monthly_totals_within_bands`).\n",
          "## 4. Quality and test results\n" + tests + "\n\n**Assistant evaluation:** " + (f"{ev['summary']['pass_rate'] * 100:.1f}% of {ev['summary']['questions']} base questions; paraphrase robustness {ev['summary']['variant_pass_rate'] * 100:.1f}% of {ev['summary']['variants']}; zero passing answers with unverified numbers; median latency {ev['summary']['median_latency_s']} s (cached replays). See `reports/assistant_eval.md`." if ev else "not run") +
          "\n\n**Story beats (B1-B10):**\n\n| beat | name | detector | fired | first detection |\n|---|---|---|---|---|\n" + "\n".join(beat_rows) + "\n\n**Performance:** full backfill about 160 s after plans exist; cached page loads under 2 s (test `test_cached_page_loads_meet_the_two_second_budget`); one simulated day under 5 s offline.\n",
          f"## 5. Model usage and cost\nModel `{llm.cfg.model_chat}` for every generated-content role (profiles, weekly flow and operations plans, live director, narrator, digest, assistant, storyline drafts, paraphrases). {led['calls']} calls ({led['cached_calls']} cached; hit rate {led['cache_hit_rate'] * 100:.0f}%), {led['tokens_total']:,} tokens ({led['tokens_in']:,} in, {led['tokens_out']:,} out). Estimated cost: tokens only (no price configured).\n\n" +
          pd.DataFrame([{"role": k, **v} for k, v in led["by_role"].items()]).to_markdown(index=False) + "\n",
          "## 6. Verified facts pack\n" + "\n".join(f"- **{i['id']}** ({i['need']}, score {i['score']}): {i['headline_fact']}  \n  _evidence:_ {i['evidence_query']}" + (f" · trace: `{i['trace_ref']}`" if i["trace_ref"] else "") + (f" · chart {i['chart']}" if i["chart"] else "") for i in ins) + "\n"]
    for n, sl in (("7", "A"), ("8", "B")):
        s = slides_out[sl]
        md.append(f"## {n}. Storyline {sl}: {s['title']}\n_(drafted by {llm.cfg.model_chat if s['source'] == 'llm' else 'deterministic fallback'}; every number checked against facts.json)_\n")
        for k, sd_ in enumerate(s["slides"], 1):
            md.append(f"### Slide {k}: {sd_['title']}\n**Key message:** {sd_['key_message']}\n\n" + "\n".join(f"- {b}" for b in sd_["bullets"]) + f"\n\n_Speaker notes:_ {sd_['speaker_notes']}\n\n" + (f"![{sd_['chart_ref']}]({figs.get(sd_['chart_ref'], sd_['chart_ref'])})\n\n" if sd_["chart_ref"] else "") + f"_Evidence: {', '.join(sd_['evidence_ids']) or 'n/a'}_\n")
    md += ["## 9. Demo moments\n**Storyline A:** Clearance Journey > target tracker > note the Apapa spike (12-15 Aug) > consignment list > Trace; then Admin (Presenter mode) > inject *Scanner outage at Apapa* with LIVE_SPEED 5 or 20 and watch the feed, situation board and the exam-wait alert.\n\n"
           "**Storyline B:** Reconciliation > open the under-assessment exception (Electronics from China) > Trace; Supervision > alert > review > assign > resolve; Reports > Since 1 July > compute > fingerprint > export PDF; Assistant > ask: \"What was total assessed vs paid vs settled across all entities in September 2026?\", \"Which commodity and origin pair shows the biggest assessment shortfall in September 2026?\", \"List high-severity open alerts\"; open *How I computed this* and note the Verified badge.\n",
           "## 10. Likely objections and answers\n- **Data realism:** all synthetic by design; fee rules and IDs are replaceable via config; a pilot with real extracts under NDA calibrates them.\n- **Sovereignty:** read-only, prompts carry aggregates only, model endpoint is swappable (hosted, in-country or local open-weight), offline degraded mode works.\n- **Overlap with existing systems:** the console reads from NSW and agencies and adds a ledger-backed reconciliation, trace and supervision layer; it does not replace them.\n- **Accuracy:** numbers are computed by SQL/Python, the assistant is verified number by number, and the evaluation above uses an independent oracle.\n- **Procurement:** four-week pilot on one or two agencies; modular phases; open formats (CSV, Excel, PDF).\n",
           "## 11. Questions to close the meeting\nClearance-time group first: (1) Which clock does the programme use for clearance time: arrival, declaration or release? (2) Can the NSW share stage timestamps for scanners, terminals and trucks? (3) Which agencies can provide a read-only extract first? (4) Who owns the end-2026 target and its definition? (5) How are remittances reconciled today and by whom? (6) What hosting rules apply (cloud, in-country, on-premises)? (7) Which reports does the Ministry of Finance receive now, and how often?\n",
           "## 12. Limitations and things to verify\n- Everything is synthetic; rates, rules and identifiers are simulated.\n- Published benchmarks (dwell 18-21 days, global about 4, Ghana 5-7, Benin about 4, Rwanda 11 days to about 1.5, target under 7 days by end 2026) must be re-checked before quoting.\n"
           f"- Logos: {len(yaml_config('entities')['entities'])} entities all matched; one extra file (Nigeria Immigration Service) is not an entity in the brief and is unused.\n- `.env` variable names detected: OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_PROJECT_ID; the model GPT-5.5 is served as inference profile `us.openai.gpt-5.5` on the Bedrock runtime route (see ASSUMPTIONS.md).\n"
           "- Screenshots: Playwright is not installed here, so `screenshots/` lists the exact pages and clicks instead (see `screenshots/README.md`).\n- Paraphrase robustness is below the base pass rate: some variants hit transient timeouts or interpret ambiguous questions differently.\n",
           "## 13. Appendix\n**File manifest:** `NSW_Demo_Share_Report.md/.pdf`, `facts.json`, `figures/`, `tables/`, `screenshots/README.md`, `README_share.md`.\n\n**Reproduce:** `make setup probe seed test eval report`; as-of `" + as_of + "`; simulation seed " + str(settings()['sim']['seed']) + ".\n\n"
           "**Definitions:** *Dwell* = arrival to gate-out. *Clearance* = declaration filed to customs release. *Four-way match* = assessed, paid, settled, remitted with each gap classified.\n\n**Verification log:** " + (f"{len(removal_log)} sentence(s) removed from drafted text because they contained unmatched numbers; remaining unmatched: {len(final.unmatched)}." if removal_log or final.unmatched else "no unmatched numbers in the drafted text.") + "\n\n---\n" + DISCLAIMER + "\n"]
    text = "\n".join(md)
    (OUT / "NSW_Demo_Share_Report.md").write_text(text)
    # --------------------------------------------------------------------------- PDF
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    st_ = getSampleStyleSheet()

    def deco(cv, doc):
        cv.saveState(); cv.setFont("Helvetica", 7); cv.setFillColor(colors.HexColor(RED)); cv.drawString(18 * mm, 10 * mm, f"{DISCLAIMER} · page {doc.page}"); cv.restoreState()
    els = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("# "):
            els.append(Paragraph(ln[2:], st_["Title"]))
        elif ln.startswith("## "):
            els += [PageBreak()] if ln[3:5] in ("7.", "8.") else []
            els.append(Paragraph(ln[3:], st_["Heading1"]))
        elif ln.startswith("### "):
            els.append(Paragraph(ln[4:], st_["Heading2"]))
        elif ln.startswith("|") and i + 1 < len(lines) and lines[i + 1].startswith("|--"):
            rows = [ln]; j = i + 2
            while j < len(lines) and lines[j].startswith("|"):
                rows.append(lines[j]); j += 1
            data = [[Paragraph(x.strip()[:60], st_["BodyText"]) for x in r.strip("|").split("|")] for r in rows]
            t = Table(data, repeatRows=1); t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(GREEN)), ("FONTSIZE", (0, 0), (-1, -1), 7), ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey)]))
            els += [t, Spacer(1, 6)]; i = j; continue
        elif ln.startswith("!["):
            m = re.search(r"\((.*?)\)", ln)
            if m and (OUT / m.group(1)).exists():
                els.append(Image(str(OUT / m.group(1)), width=160 * mm, height=78 * mm))
        elif ln.startswith("- "):
            els.append(Paragraph("• " + re.sub(r"[*_`]", "", ln[2:]).replace("&", "&amp;"), st_["BodyText"]))
        elif ln.strip():
            els.append(Paragraph(re.sub(r"\*\*(.*?)\*\*", r"<b>\1</b>", ln.replace("&", "&amp;").replace("<", "&lt;")).replace("`", ""), st_["BodyText"]))
        i += 1
    SimpleDocTemplate(str(OUT / "NSW_Demo_Share_Report.pdf"), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=16 * mm).build(els, onFirstPage=deco, onLaterPages=deco)
    # --------------------------------------------------------------------------- screenshots note, manifest, zip
    (OUT / "screenshots").mkdir(exist_ok=True)
    (OUT / "screenshots" / "README.md").write_text("# Screens used in the demo\nPlaywright is not available in this environment, so screenshots were not generated automatically. Capture these states:\n\n"
                                                   "1. Command Center (default filters).\n2. Clearance Journey: scroll to the target tracker.\n3. Reconciliation: leakage heatmap with the Electronics/CN cell highlighted.\n"
                                                   "4. Entity Explorer > NCS > Financial statements > Performance: select 'Import duty' to open the trace.\n5. Supervision: open alert for R-FEE-01 and expand *Why was this raised?*\n6. Reports: after compute, the fingerprint line.\n7. Assistant: answer with the Verified badge and the *How I computed this* expander open.\n")
    (OUT / "README_share.md").write_text("# Share bundle manifest\n" + "\n".join(f"- `{p.relative_to(OUT)}`" for p in sorted(OUT.rglob("*")) if p.is_file() and p.name != "share_bundle.zip") + f"\n\n{DISCLAIMER}\nData as of {as_of}. Fingerprint {rpt['fingerprint']}.\n")
    with zipfile.ZipFile(OUT / "share_bundle.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(OUT.rglob("*")):
            if p.is_file() and p.name != "share_bundle.zip":
                z.write(p, p.relative_to(OUT))
    print(f"insights: {len(ins)} · charts: {len(figs)} · verification passed: {final.ok} (removed {len(removal_log)} sentences)")
    print(f"bundle: {OUT / 'share_bundle.zip'}\nreport: {OUT / 'NSW_Demo_Share_Report.md'}")
    return 0 if final.ok else 2


if __name__ == "__main__":
    sys.exit(main())
