"""Replay the rules over history at 6-hourly checkpoints so alerts carry realistic detection timestamps, then close old
episodes with a simulated review history (so the demo queue is not a wall of ancient alerts)."""
from __future__ import annotations

from nsw_sim import clock, db
from nsw_sim.config import yaml_config
from nsw_sim.sim.ids import IdFactory
from nsw_sim.supervision import reviews, rules


def run(conn, until: float, progress=None) -> dict:
    ids = IdFactory(conn)
    last = db.kv_get(conn, "rules_last_eval")
    start = clock.to_epoch(last) if last else clock.sim_start().timestamp()
    cps = rules.checkpoints(start, until)
    new = 0
    for i, cp in enumerate(cps):
        new += rules.evaluate_at(conn, ids, cp)
        if progress and i % 40 == 0:
            progress(f"Rules replay: {clock.fmt_wat(cp, '%d %b')} ({new} alerts so far)")
    closed = finalize_history(conn, until)
    # make sure the final state reflects 'now' (live-style evaluation)
    new += rules.evaluate_at(conn, ids, until, live=True)
    n_alerts = conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
    if progress:
        progress(f"Rules replay done: {n_alerts} alerts ({closed} closed with simulated review history)")
    return {"alerts": n_alerts, "closed": closed}


def finalize_history(conn, now: float) -> int:
    """Alerts whose condition cleared more than ``auto_resolve_after_days`` ago are resolved with a seeded review trail."""
    days = yaml_config("alert_rules")["auto_resolve_after_days"]
    cutoff = clock.iso(now - days * 86400)
    rows = conn.execute("SELECT alert_id, cleared_at, entity_id FROM alerts WHERE status='open' AND cleared_at IS NOT NULL AND cleared_at<? ORDER BY detected_at", (cutoff,)).fetchall()
    n = 0
    conn.execute("BEGIN")
    for aid, cleared, _ent in rows:
        t = clock.to_epoch(cleared)
        for role, action, off, comment in (("Analyst", "acknowledge", 1.5, "Acknowledged; reviewing supporting records."), ("Supervisor", "under_review", 5, "Under review with the agency focal point."),
                                           ("Supervisor", "resolve", 26, "Condition cleared and confirmed in the data. (Simulated history.)")):
            reviews.act(conn, "alert", aid, role, action, comment, ts=clock.iso(t + off * 3600), reviewer=f"{reviews.ROLES[role]['name']}")
        n += 1
    conn.execute("COMMIT")
    return n
