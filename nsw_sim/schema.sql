-- NSW Intelligence Console schema (SYNTHETIC DATA). Money = integer kobo (*_minor). Timestamps = UTC ISO text
-- 'YYYY-MM-DDTHH:MM:SSZ' (lexicographic order == chronological order).
CREATE TABLE IF NOT EXISTS entities(
  entity_id TEXT PRIMARY KEY, code TEXT UNIQUE, name TEXT, type TEXT, colour TEXT, logo_path TEXT,
  profile_json TEXT, profile_source TEXT, created_at TEXT, mandate TEXT);
CREATE TABLE IF NOT EXISTS entity_profiles(
  entity_id TEXT PRIMARY KEY, profile_json TEXT, source TEXT, model TEXT, version INT, created_at TEXT);
CREATE TABLE IF NOT EXISTS countries(
  iso2 TEXT PRIMARY KEY, name TEXT, region TEXT, currency TEXT, risk_tier INT, ecowas INT, transit_days REAL);
CREATE TABLE IF NOT EXISTS parties(
  party_id TEXT PRIMARY KEY, kind TEXT, name TEXT, tin TEXT, rc_number TEXT, size_tier TEXT);
CREATE TABLE IF NOT EXISTS fx_rates(
  ts_utc TEXT, ccy TEXT, rate_to_ngn REAL, source TEXT, PRIMARY KEY(ts_utc, ccy));

CREATE TABLE IF NOT EXISTS consignments(
  nsw_ref TEXT PRIMARY KEY, mode TEXT, port TEXT, origin_country TEXT, commodity_group TEXT, hs_code TEXT,
  cif_value_minor INT, cif_ccy TEXT, fx_rate REAL, cif_value_ngn_minor INT,
  importer_id TEXT, agent_id TEXT, carrier TEXT, bank TEXT, rotation_no TEXT, form_m TEXT, paar TEXT,
  declaration_no TEXT, bl_no TEXT, container_no TEXT, vessel_imo TEXT, weight_kg REAL, teu REAL, risk_lane TEXT,
  manifested_at TEXT, arrived_at TEXT, declared_at TEXT, released_at TEXT, gate_out_at TEXT, status TEXT, source TEXT,
  dwell_h REAL, clearance_h REAL, exit_h REAL, digital_h REAL, physical_h REAL, handoff_h REAL);
CREATE TABLE IF NOT EXISTS inflight(
  nsw_ref TEXT PRIMARY KEY, next_stage TEXT, next_event_at TEXT, state_json TEXT);
CREATE TABLE IF NOT EXISTS stage_events(
  event_id TEXT PRIMARY KEY, nsw_ref TEXT, stage TEXT, system_type TEXT, owner_entity TEXT,
  ready_at TEXT, started_at TEXT, occurred_at TEXT, wait_h REAL, dur_h REAL, sla_hours REAL, sla_breach INT,
  status TEXT, data_complete INT);

CREATE TABLE IF NOT EXISTS fee_assessments(
  assessment_id TEXT PRIMARY KEY, nsw_ref TEXT, entity_id TEXT, process_code TEXT, fee_code TEXT, basis TEXT,
  base_amount_minor INT, rate REAL, amount_minor INT, currency TEXT, fx_rate REAL, amount_ngn_minor INT,
  origin_country TEXT, expected_amount_ngn_minor INT, occurred_at TEXT,
  mode TEXT, port TEXT, commodity_group TEXT, hs_code TEXT, risk_lane TEXT);
CREATE TABLE IF NOT EXISTS payments(
  payment_id TEXT PRIMARY KEY, payment_ref TEXT UNIQUE, nsw_ref TEXT, payer_id TEXT, channel TEXT, bank TEXT,
  amount_ngn_minor INT, status TEXT, is_duplicate INT, initiated_at TEXT, occurred_at TEXT,
  origin_country TEXT, mode TEXT, port TEXT, commodity_group TEXT);
CREATE TABLE IF NOT EXISTS payment_allocations(
  alloc_id TEXT PRIMARY KEY, payment_id TEXT, assessment_id TEXT, nsw_ref TEXT, entity_id TEXT, process_code TEXT,
  fee_code TEXT, origin_country TEXT, mode TEXT, port TEXT, commodity_group TEXT, currency TEXT,
  amount_ngn_minor INT, paid_at TEXT, settlement_id TEXT, settled_at TEXT,
  collection_cost_ngn_minor INT, settled_ngn_minor INT);
CREATE TABLE IF NOT EXISTS settlement_batches(
  batch_id TEXT PRIMARY KEY, bank TEXT, value_date TEXT, closed_at TEXT, completed_at TEXT,
  total_ngn_minor INT, lag_hours REAL, status TEXT, n_payments INT);
CREATE TABLE IF NOT EXISTS settlements(
  settlement_id TEXT PRIMARY KEY, batch_id TEXT, payment_id TEXT, entity_id TEXT, amount_ngn_minor INT,
  collection_cost_ngn_minor INT, occurred_at TEXT);
CREATE TABLE IF NOT EXISTS remittances(
  remittance_id TEXT PRIMARY KEY, entity_id TEXT, period TEXT, due_date TEXT, paid_at TEXT, amount_ngn_minor INT,
  status TEXT, destination TEXT, days_late INT, occurred_at TEXT, base_ngn_minor INT, share REAL);
CREATE TABLE IF NOT EXISTS funding_receipts(
  receipt_id TEXT PRIMARY KEY, entity_id TEXT, source_type TEXT, source_country TEXT, facility TEXT,
  amount_ngn_minor INT, occurred_at TEXT, source TEXT);
CREATE TABLE IF NOT EXISTS expenses(
  expense_id TEXT PRIMARY KEY, entity_id TEXT, category TEXT, vendor TEXT, amount_ngn_minor INT,
  occurred_at TEXT, memo TEXT, source TEXT);
CREATE TABLE IF NOT EXISTS refunds(
  refund_id TEXT PRIMARY KEY, entity_id TEXT, fee_code TEXT, amount_ngn_minor INT, occurred_at TEXT,
  memo TEXT, source TEXT);

CREATE TABLE IF NOT EXISTS chart_of_accounts(
  entity_id TEXT, account_code TEXT, name TEXT, class TEXT, statement_line TEXT, PRIMARY KEY(entity_id, account_code));
CREATE TABLE IF NOT EXISTS journal_entries(
  entry_id TEXT PRIMARY KEY, entity_id TEXT, occurred_at TEXT, ref_type TEXT, ref_id TEXT, nsw_ref TEXT, memo TEXT);
CREATE TABLE IF NOT EXISTS journal_lines(
  line_id INTEGER PRIMARY KEY, entry_id TEXT, entity_id TEXT, account_code TEXT, debit_minor INT, credit_minor INT,
  currency TEXT, origin_country TEXT, process_code TEXT, occurred_at TEXT);

CREATE TABLE IF NOT EXISTS live_events(
  event_id TEXT PRIMARY KEY, occurred_at TEXT, type TEXT, severity TEXT, entity_id TEXT, subject TEXT,
  headline TEXT, description TEXT, payload_json TEXT, source_kind TEXT, run_id TEXT, speed REAL);
CREATE TABLE IF NOT EXISTS alerts(
  alert_id TEXT PRIMARY KEY, rule_code TEXT, severity TEXT, entity_id TEXT, subject TEXT, detected_at TEXT,
  window_start TEXT, window_end TEXT, metric_value REAL, threshold REAL, details_json TEXT, status TEXT,
  assigned_to TEXT, updated_at TEXT, dedup_key TEXT, cleared_at TEXT);
CREATE TABLE IF NOT EXISTS reviews(
  review_id TEXT PRIMARY KEY, target_type TEXT, target_id TEXT, reviewer TEXT, role TEXT, decision TEXT,
  comment TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS audit_log(
  audit_id INTEGER PRIMARY KEY, ts TEXT, actor TEXT, role TEXT, action TEXT, target_type TEXT, target_id TEXT,
  detail_json TEXT);
CREATE TABLE IF NOT EXISTS saved_reports(
  report_id TEXT PRIMARY KEY, created_at TEXT, creator TEXT, params_json TEXT, as_of TEXT, fingerprint TEXT,
  status TEXT, file_paths_json TEXT);

CREATE TABLE IF NOT EXISTS sim_state(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS generation_runs(
  run_id TEXT PRIMARY KEY, kind TEXT, entity_id TEXT, window_start TEXT, window_end TEXT, status TEXT, source TEXT,
  llm_model TEXT, tokens_in INT, tokens_out INT, latency_ms INT, created_at TEXT,
  UNIQUE(kind, entity_id, window_start, window_end));
CREATE TABLE IF NOT EXISTS weekly_plans(
  kind TEXT, entity_id TEXT, week_start TEXT, plan_json TEXT, source TEXT, model TEXT, created_at TEXT,
  PRIMARY KEY(kind, entity_id, week_start));
CREATE TABLE IF NOT EXISTS llm_calls(
  call_id TEXT PRIMARY KEY, ts TEXT, role TEXT, model TEXT, tokens_in INT, tokens_out INT, latency_ms INT,
  outcome TEXT, cached INT DEFAULT 0);
CREATE TABLE IF NOT EXISTS incidents(
  incident_id TEXT PRIMARY KEY, kind TEXT, entity_id TEXT, port TEXT, bank TEXT, start_at TEXT, end_at TEXT,
  params_json TEXT, source TEXT, label TEXT);
CREATE TABLE IF NOT EXISTS directives(
  directive_id TEXT PRIMARY KEY, ts TEXT, params_json TEXT, source TEXT);

CREATE TABLE IF NOT EXISTS rollup_minute(
  entity_id TEXT, minute TEXT, metric TEXT, value REAL, PRIMARY KEY(entity_id, minute, metric));
CREATE TABLE IF NOT EXISTS rollup_day(
  entity_id TEXT, day TEXT, metric TEXT, dim TEXT, dim_value TEXT, value REAL,
  PRIMARY KEY(entity_id, day, metric, dim, dim_value));
