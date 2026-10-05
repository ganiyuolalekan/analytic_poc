"""Append-only audit log. Every state change (alerts, reviews, sign-offs, injections) writes a row."""
from __future__ import annotations

import json
import sqlite3

from nsw_sim import clock


def log(conn: sqlite3.Connection, actor: str, role: str, action: str, target_type: str, target_id: str, detail: dict | None = None,
        ts: str | None = None) -> None:
    conn.execute("INSERT INTO audit_log(ts,actor,role,action,target_type,target_id,detail_json) VALUES(?,?,?,?,?,?,?)",
                 (ts or clock.iso(clock.utcnow()), actor, role, action, target_type, target_id, json.dumps(detail or {})))


def timeline(conn: sqlite3.Connection, target_type: str | None = None, target_id: str | None = None, limit: int = 200) -> list[dict]:
    q, p = "SELECT ts, actor, role, action, target_type, target_id, detail_json FROM audit_log WHERE 1=1", []
    if target_type:
        q += " AND target_type=?"
        p.append(target_type)
    if target_id:
        q += " AND target_id=?"
        p.append(target_id)
    rows = conn.execute(q + f" ORDER BY audit_id DESC LIMIT {int(limit)}", p).fetchall()
    return [{"ts": r[0], "actor": r[1], "role": r[2], "action": r[3], "target_type": r[4], "target_id": r[5], "detail": json.loads(r[6] or "{}")} for r in rows]
