"""Review workflow with demo roles (no real auth): Analyst, Supervisor, Director. Every action writes ``reviews`` + ``audit_log``."""
from __future__ import annotations

import sqlite3
import uuid

from nsw_sim import clock
from nsw_sim.supervision import audit

ROLES = {
    "Analyst": {"name": "Amaka Obi (Analyst)", "can": {"acknowledge", "comment", "raise_review"}},
    "Supervisor": {"name": "Tunde Bello (Supervisor)", "can": {"acknowledge", "comment", "raise_review", "assign", "under_review", "resolve", "dismiss", "review_period"}},
    "Director": {"name": "Ngozi Adeyemi (Director)", "can": {"acknowledge", "comment", "raise_review", "assign", "under_review", "resolve", "dismiss", "review_period", "sign_off"}},
}
STATES = ["open", "acknowledged", "under_review", "resolved", "dismissed"]
ACTION_TO_STATE = {"acknowledge": "acknowledged", "under_review": "under_review", "resolve": "resolved", "dismiss": "dismissed"}
ALLOWED_FROM = {"acknowledged": {"open"}, "under_review": {"open", "acknowledged"}, "resolved": {"open", "acknowledged", "under_review"},
                "dismissed": {"open", "acknowledged", "under_review"}}


def can(role: str, action: str) -> bool:
    return action in ROLES.get(role, {}).get("can", set())


def act(conn: sqlite3.Connection, target_type: str, target_id: str, role: str, action: str, comment: str = "", assign_to: str | None = None,
        ts: str | None = None, reviewer: str | None = None) -> str:
    """Perform a review action. Raises PermissionError if the role may not do it, ValueError for illegal transitions."""
    if not can(role, action):
        raise PermissionError(f"The {role} role cannot perform '{action}'.")
    reviewer = reviewer or ROLES[role]["name"]
    ts = ts or clock.iso(clock.utcnow())
    new_state = None
    if target_type == "alert":
        row = conn.execute("SELECT status FROM alerts WHERE alert_id=?", (target_id,)).fetchone()
        if not row:
            raise ValueError("unknown alert")
        cur = row[0]
        if action in ACTION_TO_STATE:
            new_state = ACTION_TO_STATE[action]
            if cur not in ALLOWED_FROM[new_state]:
                raise ValueError(f"Cannot move an alert from '{cur}' to '{new_state}'.")
            conn.execute("UPDATE alerts SET status=?, updated_at=? WHERE alert_id=?", (new_state, ts, target_id))
        if action == "assign":
            conn.execute("UPDATE alerts SET assigned_to=?, updated_at=? WHERE alert_id=?", (assign_to or reviewer, ts, target_id))
    conn.execute("INSERT INTO reviews(review_id,target_type,target_id,reviewer,role,decision,comment,created_at) VALUES(?,?,?,?,?,?,?,?)",
                 (uuid.uuid4().hex[:12], target_type, target_id, reviewer, role, action if not new_state else new_state, comment, ts))
    audit.log(conn, reviewer, role, action, target_type, target_id, {"comment": comment, "assign_to": assign_to, "new_state": new_state}, ts=ts)
    return new_state or action


def comments(conn: sqlite3.Connection, target_type: str, target_id: str) -> list[dict]:
    rows = conn.execute("SELECT reviewer, role, decision, comment, created_at FROM reviews WHERE target_type=? AND target_id=? ORDER BY created_at", (target_type, target_id)).fetchall()
    return [dict(zip(("reviewer", "role", "decision", "comment", "created_at"), r)) for r in rows]


def queue(conn: sqlite3.Connection, now_ts: str, status: tuple[str, ...] = ("open", "acknowledged", "under_review"), severities: tuple = (),
          entities: tuple = (), limit: int = 500) -> list[dict]:
    """Open work items (alerts + raised exceptions + unsigned reports) with SLA age in hours."""
    q = ("SELECT alert_id, rule_code, severity, entity_id, subject, detected_at, status, assigned_to, metric_value, threshold FROM alerts WHERE status IN ("
         + ",".join("?" * len(status)) + ")")
    p: list = list(status)
    if severities:
        q += f" AND severity IN ({','.join('?' * len(severities))})"
        p += list(severities)
    if entities:
        q += f" AND entity_id IN ({','.join('?' * len(entities))})"
        p += list(entities)
    now = clock.to_epoch(now_ts)
    out = []
    for r in conn.execute(q + f" ORDER BY detected_at DESC LIMIT {int(limit)}", p):
        out.append({"type": "alert", "id": r[0], "rule": r[1], "severity": r[2], "entity": r[3], "subject": r[4], "detected_at": r[5], "status": r[6],
                    "assigned_to": r[7], "age_h": round((now - clock.to_epoch(r[5])) / 3600, 1), "metric": r[8], "threshold": r[9]})
    for r in conn.execute("SELECT target_id, MAX(created_at), reviewer, decision FROM reviews WHERE target_type='exception' GROUP BY target_id"):
        if r[3] not in ("resolve", "resolved", "dismiss", "dismissed"):
            out.append({"type": "exception", "id": r[0], "rule": "reconciliation", "severity": "medium", "entity": "", "subject": r[0], "detected_at": r[1], "status": "raised",
                        "assigned_to": r[2], "age_h": round((now - clock.to_epoch(r[1])) / 3600, 1), "metric": None, "threshold": None})
    for r in conn.execute("SELECT report_id, created_at, creator FROM saved_reports WHERE status!='signed_off'"):
        out.append({"type": "report", "id": r[0], "rule": "period report", "severity": "info", "entity": "", "subject": r[0], "detected_at": r[1], "status": "awaiting sign-off",
                    "assigned_to": None, "age_h": round((now - clock.to_epoch(r[1])) / 3600, 1), "metric": None, "threshold": None})
    return out


def sign_off_report(conn: sqlite3.Connection, report_id: str, role: str, comment: str = "", ts: str | None = None) -> None:
    if not can(role, "sign_off"):
        raise PermissionError(f"The {role} role cannot sign off a period report; only the Director can.")
    conn.execute("UPDATE saved_reports SET status='signed_off' WHERE report_id=?", (report_id,))
    act(conn, "report", report_id, role, "sign_off", comment, ts=ts)
