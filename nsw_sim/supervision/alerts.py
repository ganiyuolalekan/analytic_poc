"""Alert creation with de-duplication per episode, condition-cleared tracking, and the live-feed event."""
from __future__ import annotations

import json
import sqlite3

from nsw_sim import clock
from nsw_sim.sim.ids import IdFactory
from nsw_sim.supervision import audit

OPEN_STATES = ("open", "acknowledged", "under_review")
COOLDOWN_S = 12 * 3600


def raise_alert(conn: sqlite3.Connection, ids: IdFactory, rule: str, severity: str, entity: str, subject: str, t: float, window: tuple[str, str],
                metric: float, threshold: float, details: dict, emit_event: bool = True) -> str | None:
    """Create or refresh the alert for (rule, entity, subject). Returns the alert id if newly created."""
    key = f"{rule}|{entity}|{subject}"
    ts = clock.iso(t)
    row = conn.execute("SELECT alert_id, status, cleared_at FROM alerts WHERE dedup_key=? ORDER BY detected_at DESC LIMIT 1", (key,)).fetchone()
    if row and (row[1] in OPEN_STATES or (row[2] is None) or clock.to_epoch(row[2]) > t - COOLDOWN_S):
        conn.execute("UPDATE alerts SET metric_value=?, details_json=?, updated_at=?, window_end=?, cleared_at=NULL WHERE alert_id=?",
                     (metric, json.dumps(details), ts, window[1], row[0]))
        return None
    aid = ids.alert_id(clock.wat_day(ts).replace("-", ""))
    conn.execute("INSERT INTO alerts(alert_id,rule_code,severity,entity_id,subject,detected_at,window_start,window_end,metric_value,threshold,details_json,"
                 "status,assigned_to,updated_at,dedup_key,cleared_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)",
                 (aid, rule, severity, entity, subject, ts, window[0], window[1], metric, threshold, json.dumps(details), "open", None, ts, key))
    audit.log(conn, "System (rules engine)", "system", "alert_raised", "alert", aid, {"rule": rule, "entity": entity, "metric": metric}, ts=ts)
    if emit_event:
        conn.execute("INSERT INTO live_events(event_id,occurred_at,type,severity,entity_id,subject,headline,description,payload_json,source_kind,run_id,speed) "
                     "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                     (f"evt_alert_{aid}", ts, "supervision.alert.raised", {"info": "info", "low": "low", "medium": "medium", "high": "high"}[severity], entity, aid,
                      f"Alert raised: {rule}", details.get("summary", ""), json.dumps({"entity": entity, "rule": rule, "metric": metric, "threshold": threshold}),
                      "rule", f"rules-{clock.wat_day(ts)}", 1.0))
    return aid


def mark_cleared(conn: sqlite3.Connection, rule: str, firing_keys: set[str], t: float) -> int:
    """Alerts of ``rule`` whose condition no longer holds get ``cleared_at`` (humans still resolve them)."""
    ts = clock.iso(t)
    rows = conn.execute("SELECT alert_id, dedup_key FROM alerts WHERE rule_code=? AND cleared_at IS NULL", (rule,)).fetchall()
    n = 0
    for aid, key in rows:
        if key not in firing_keys:
            conn.execute("UPDATE alerts SET cleared_at=? WHERE alert_id=?", (ts, aid))
            n += 1
    return n


def get_alert(conn: sqlite3.Connection, alert_id: str) -> dict | None:
    r = conn.execute("SELECT alert_id,rule_code,severity,entity_id,subject,detected_at,window_start,window_end,metric_value,threshold,details_json,status,"
                     "assigned_to,updated_at,cleared_at FROM alerts WHERE alert_id=?", (alert_id,)).fetchone()
    if not r:
        return None
    keys = ["alert_id", "rule_code", "severity", "entity_id", "subject", "detected_at", "window_start", "window_end", "metric_value", "threshold", "details", "status",
            "assigned_to", "updated_at", "cleared_at"]
    d = dict(zip(keys, r))
    d["details"] = json.loads(d["details"] or "{}")
    return d
