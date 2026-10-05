"""SQLite access (WAL). One writer (the service), many readers.

Analytics MUST read through :func:`reader` which binds an ``as_of`` instant to a set of TEMP views
(``v_*``) so every query only sees facts with ``occurred_at <= as_of`` (Section 8.1 of the brief).
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Iterable, Sequence

from nsw_sim import clock
from nsw_sim.config import db_path

SCHEMA_VERSION = 3
_SCHEMA_FILE = Path(__file__).with_name("schema.sql")
_tls = threading.local()

# name -> (table, columns). Created after bulk loads (see drop_bulk_indexes / create_indexes).
INDEXES: dict[str, tuple[str, str]] = {
    "ix_cons_arrived": ("consignments", "arrived_at"),
    "ix_cons_gate": ("consignments", "gate_out_at"),
    "ix_cons_manif": ("consignments", "manifested_at"),
    "ix_cons_origin": ("consignments", "origin_country, port"),
    "ix_stage_ref": ("stage_events", "nsw_ref"),
    "ix_stage_owner": ("stage_events", "owner_entity, occurred_at"),
    "ix_stage_occ": ("stage_events", "stage, occurred_at"),
    "ix_fee_ent": ("fee_assessments", "entity_id, occurred_at"),
    "ix_fee_ref": ("fee_assessments", "nsw_ref"),
    "ix_fee_occ": ("fee_assessments", "occurred_at"),
    "ix_fee_code": ("fee_assessments", "fee_code, occurred_at"),
    "ix_pay_ref": ("payments", "nsw_ref"),
    "ix_pay_occ": ("payments", "occurred_at"),
    "ix_pay_bank": ("payments", "bank, occurred_at"),
    "ix_al_ent": ("payment_allocations", "entity_id, paid_at"),
    "ix_al_pay": ("payment_allocations", "payment_id"),
    "ix_al_ass": ("payment_allocations", "assessment_id"),
    "ix_al_ref": ("payment_allocations", "nsw_ref"),
    "ix_al_paid": ("payment_allocations", "paid_at"),
    "ix_al_settled": ("payment_allocations", "settled_at"),
    "ix_al_settl": ("payment_allocations", "settlement_id"),
    "ix_batch_bank": ("settlement_batches", "bank, closed_at"),
    "ix_stl_batch": ("settlements", "batch_id"),
    "ix_stl_ent": ("settlements", "entity_id, occurred_at"),
    "ix_stl_pay": ("settlements", "payment_id"),
    "ix_rem_ent": ("remittances", "entity_id, occurred_at"),
    "ix_fund_ent": ("funding_receipts", "entity_id, occurred_at"),
    "ix_exp_ent": ("expenses", "entity_id, occurred_at"),
    "ix_ref_ent": ("refunds", "entity_id, occurred_at"),
    "ix_je_ent": ("journal_entries", "entity_id, occurred_at"),
    "ix_je_ref": ("journal_entries", "nsw_ref"),
    "ix_je_refid": ("journal_entries", "ref_type, ref_id"),
    "ix_jl_entry": ("journal_lines", "entry_id"),
    "ix_jl_acct": ("journal_lines", "entity_id, account_code, occurred_at"),
    "ix_jl_occ": ("journal_lines", "occurred_at"),
    "ix_live_occ": ("live_events", "occurred_at"),
    "ix_live_ent": ("live_events", "entity_id, occurred_at"),
    "ix_alert_det": ("alerts", "detected_at"),
    "ix_alert_rule": ("alerts", "rule_code, entity_id, status"),
    "ix_rev_target": ("reviews", "target_type, target_id"),
    "ix_audit_ts": ("audit_log", "ts"),
    "ix_llm_ts": ("llm_calls", "ts, role"),
    "ix_inc_start": ("incidents", "start_at"),
    "ix_rday": ("rollup_day", "metric, day"),
}
# Large tables where per-row index maintenance dominates a backfill.
BULK_INDEXES = [n for n, (t, _) in INDEXES.items() if t in {
    "stage_events", "fee_assessments", "payments", "payment_allocations", "journal_entries", "journal_lines",
    "settlements", "consignments"}]


def connect(path: str | Path | None = None, *, query_only: bool = False) -> sqlite3.Connection:
    p = str(path or db_path())
    conn = sqlite3.connect(p, timeout=60, check_same_thread=False, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA cache_size=-200000")
    if query_only:
        conn.execute("PRAGMA query_only=ON")
    return conn


def init_db(conn: sqlite3.Connection, *, indexes: bool = True) -> None:
    from nsw_sim.migrations import migrate
    conn.executescript(_SCHEMA_FILE.read_text(encoding="utf-8"))
    migrate(conn)
    if indexes:
        create_indexes(conn)
    kv_set(conn, "schema_version", str(SCHEMA_VERSION))


def create_indexes(conn: sqlite3.Connection, names: Iterable[str] | None = None) -> None:
    for n in (names or INDEXES):
        t, cols = INDEXES[n]
        conn.execute(f"CREATE INDEX IF NOT EXISTS {n} ON {t}({cols})")


def drop_indexes(conn: sqlite3.Connection, names: Iterable[str] | None = None) -> None:
    for n in (names or INDEXES):
        conn.execute(f"DROP INDEX IF EXISTS {n}")


def kv_get(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM sim_state WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def kv_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT INTO sim_state(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (key, value))


def insert_many(conn: sqlite3.Connection, table: str, columns: Sequence[str], rows: Iterable[Sequence],
                *, or_replace: bool = False) -> int:
    rows = list(rows)
    if not rows:
        return 0
    verb = "INSERT OR REPLACE" if or_replace else "INSERT"
    sql = f"{verb} INTO {table}({','.join(columns)}) VALUES({','.join('?' * len(columns))})"
    conn.executemany(sql, rows)
    return len(rows)


def get_watermark(conn: sqlite3.Connection) -> str | None:
    return kv_get(conn, "watermark_utc")


# ------------------------------------------------------------------------------- read-only views
ASOF = "(SELECT v FROM as_of_ctx)"


def _views() -> list[tuple[str, str]]:
    a = ASOF
    return [
        ("v_entities", "SELECT entity_id, code, name, type, colour, logo_path, mandate, profile_source FROM main.entities"),
        ("v_countries", "SELECT * FROM main.countries"),
        ("v_parties", "SELECT * FROM main.parties"),
        ("v_consignments", f"""SELECT nsw_ref, mode, port, origin_country, commodity_group, hs_code, cif_value_ngn_minor,
              risk_lane, importer_id, agent_id, carrier, bank, rotation_no, form_m, paar, declaration_no, bl_no,
              container_no, vessel_imo, weight_kg, teu, source, manifested_at,
              CASE WHEN arrived_at <= {a} THEN arrived_at END AS arrived_at,
              CASE WHEN declared_at <= {a} THEN declared_at END AS declared_at,
              CASE WHEN released_at <= {a} THEN released_at END AS released_at,
              CASE WHEN gate_out_at <= {a} THEN gate_out_at END AS gate_out_at,
              CASE WHEN gate_out_at <= {a} THEN dwell_h END AS dwell_h,
              CASE WHEN gate_out_at <= {a} THEN clearance_h END AS clearance_h,
              CASE WHEN gate_out_at <= {a} THEN exit_h END AS exit_h,
              CASE WHEN gate_out_at <= {a} THEN digital_h END AS digital_h,
              CASE WHEN gate_out_at <= {a} THEN physical_h END AS physical_h,
              CASE WHEN gate_out_at <= {a} THEN handoff_h END AS handoff_h
           FROM main.consignments WHERE manifested_at <= {a}"""),
        ("v_stage_durations", f"SELECT * FROM main.stage_events WHERE occurred_at <= {a}"),
        ("v_assessments", f"""SELECT *, date(occurred_at,'+1 hour') AS day FROM main.fee_assessments
                              WHERE occurred_at <= {a}"""),
        ("v_collections", f"""SELECT alloc_id, payment_id, assessment_id, nsw_ref, entity_id, process_code, fee_code,
              origin_country, mode, port, commodity_group, currency, amount_ngn_minor, paid_at,
              date(paid_at,'+1 hour') AS paid_day,
              CASE WHEN settled_at <= {a} THEN settled_at END AS settled_at,
              CASE WHEN settled_at <= {a} THEN settlement_id END AS settlement_id,
              CASE WHEN settled_at <= {a} THEN collection_cost_ngn_minor END AS collection_cost_ngn_minor,
              CASE WHEN settled_at <= {a} THEN settled_ngn_minor END AS settled_ngn_minor
           FROM main.payment_allocations WHERE paid_at <= {a}"""),
        ("v_settled", f"""SELECT alloc_id, payment_id, assessment_id, nsw_ref, entity_id, process_code, fee_code,
              origin_country, mode, port, commodity_group, currency, amount_ngn_minor, paid_at, settlement_id,
              settled_at, date(settled_at,'+1 hour') AS settled_day, collection_cost_ngn_minor, settled_ngn_minor
           FROM main.payment_allocations WHERE settled_at <= {a}"""),
        ("v_assessed_vs_paid", """SELECT f.assessment_id, f.nsw_ref, f.entity_id, f.process_code, f.fee_code, f.currency,
              f.origin_country, f.mode, f.port, f.commodity_group, f.hs_code, f.occurred_at, f.day,
              f.amount_ngn_minor AS assessed_ngn_minor, f.expected_amount_ngn_minor,
              COALESCE(p.paid,0) AS paid_ngn_minor, COALESCE(p.settled,0) AS settled_ngn_minor,
              COALESCE(p.cost,0) AS collection_cost_ngn_minor, p.last_paid_at, p.last_settled_at
           FROM v_assessments f LEFT JOIN (
              SELECT assessment_id, SUM(amount_ngn_minor) paid, SUM(COALESCE(settled_ngn_minor,0)) settled,
                     SUM(COALESCE(collection_cost_ngn_minor,0)) cost, MAX(paid_at) last_paid_at,
                     MAX(settled_at) last_settled_at
              FROM v_collections GROUP BY assessment_id) p ON p.assessment_id = f.assessment_id"""),
        ("v_payments", f"""SELECT * FROM main.payments WHERE occurred_at <= {a}"""),
        ("v_settlement_batches", f"""SELECT batch_id, bank, value_date, closed_at,
              CASE WHEN completed_at <= {a} THEN completed_at END AS completed_at, total_ngn_minor, n_payments,
              CASE WHEN completed_at <= {a} THEN lag_hours END AS lag_hours,
              CASE WHEN completed_at <= {a} THEN 'completed' ELSE 'closed' END AS status
           FROM main.settlement_batches WHERE closed_at <= {a}"""),
        ("v_settlements", f"SELECT * FROM main.settlements WHERE occurred_at <= {a}"),
        ("v_remittances", f"""SELECT remittance_id, entity_id, period, due_date, destination, amount_ngn_minor, base_ngn_minor,
              share, occurred_at,
              CASE WHEN paid_at <= {a} THEN paid_at END AS paid_at,
              CASE WHEN paid_at <= {a} THEN 'paid' WHEN due_date < {a} THEN 'overdue' ELSE 'due' END AS status,
              CASE WHEN paid_at <= {a} THEN days_late END AS days_late
           FROM main.remittances WHERE occurred_at <= {a}"""),
        ("v_funding", f"SELECT * FROM main.funding_receipts WHERE occurred_at <= {a}"),
        ("v_expenses", f"SELECT * FROM main.expenses WHERE occurred_at <= {a}"),
        ("v_refunds", f"SELECT * FROM main.refunds WHERE occurred_at <= {a}"),
        ("v_ledger_lines", f"""SELECT l.line_id, l.entry_id, l.entity_id, l.account_code, c.name AS account_name, c.class,
              c.statement_line, l.debit_minor, l.credit_minor, l.currency, l.origin_country, l.process_code,
              l.occurred_at, date(l.occurred_at,'+1 hour') AS day
           FROM main.journal_lines l JOIN main.chart_of_accounts c
             ON c.entity_id = l.entity_id AND c.account_code = l.account_code
           WHERE l.occurred_at <= {a}"""),
        ("v_journal_entries", f"SELECT * FROM main.journal_entries WHERE occurred_at <= {a}"),
        ("v_entity_pnl", """SELECT entity_id, day, class, account_code, account_name, statement_line,
              SUM(credit_minor) - SUM(debit_minor) AS net_credit_minor
           FROM v_ledger_lines WHERE class IN ('Revenue','Expense') GROUP BY entity_id, day, account_code"""),
        ("v_alerts", f"""SELECT alert_id, rule_code, severity, entity_id, subject, detected_at, window_start, window_end,
              metric_value, threshold, status, assigned_to, updated_at, cleared_at
           FROM main.alerts WHERE detected_at <= {a}"""),
        ("v_live_events", f"SELECT * FROM main.live_events WHERE occurred_at <= {a}"),
        ("v_fx_rates", f"SELECT * FROM main.fx_rates WHERE ts_utc <= {a}"),
        ("v_incidents", f"SELECT * FROM main.incidents WHERE start_at <= {a}"),
    ]


VIEW_NAMES = [n for n, _ in _views()]


_WRITE_ACTIONS = {
    sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE, sqlite3.SQLITE_CREATE_INDEX,
    sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_CREATE_TRIGGER, sqlite3.SQLITE_CREATE_VIEW,
    sqlite3.SQLITE_DROP_INDEX, sqlite3.SQLITE_DROP_TABLE, sqlite3.SQLITE_DROP_TRIGGER, sqlite3.SQLITE_DROP_VIEW,
    sqlite3.SQLITE_ALTER_TABLE, sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH,
}


def _read_only_authorizer(action, arg1, arg2, dbname, source):
    """Deny any write/DDL/ATTACH except on the TEMP schema (needed for the as_of context)."""
    if action in _WRITE_ACTIONS and dbname != "temp":
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_PRAGMA and str(arg2) not in ("", "None") and arg1 not in ("table_info", "table_xinfo"):
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def reader(as_of: str | None = None) -> sqlite3.Connection:
    """Thread-local read connection with ``v_*`` temp views bound to ``as_of`` (default: now)."""
    conn: sqlite3.Connection | None = getattr(_tls, "conn", None)
    path = str(db_path())
    if conn is None or getattr(_tls, "path", None) != path:
        conn = connect(path)
        conn.execute("CREATE TEMP TABLE as_of_ctx(v TEXT)")
        conn.execute("INSERT INTO as_of_ctx VALUES('9999-12-31T00:00:00Z')")
        for name, sql in _views():
            conn.execute(f"CREATE TEMP VIEW {name} AS {sql}")
        conn.set_authorizer(_read_only_authorizer)
        _tls.conn, _tls.path = conn, path
    conn.execute("UPDATE as_of_ctx SET v=?", (clock.iso(as_of) if as_of else clock.iso(clock.utcnow()),))
    return conn


def close_reader() -> None:
    conn = getattr(_tls, "conn", None)
    if conn is not None:
        conn.close()
        _tls.conn = None


def log_llm_call(conn: sqlite3.Connection | None, call_id: str, role: str, model: str, tokens_in: int,
                 tokens_out: int, latency_ms: int, outcome: str, cached: bool = False) -> None:
    try:
        c = conn or connect()
        c.execute("INSERT OR REPLACE INTO llm_calls(call_id,ts,role,model,tokens_in,tokens_out,latency_ms,outcome,cached)"
                  " VALUES(?,?,?,?,?,?,?,?,?)",
                  (call_id, clock.iso(clock.utcnow()), role, model, tokens_in, tokens_out, latency_ms, outcome, int(cached)))
        if conn is None:
            c.close()
    except sqlite3.Error:
        pass  # logging must never break a call
