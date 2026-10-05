"""Remittances to the Federation Account (Section 5.1 rules; simulated) with beat B5 and injected lateness."""
from __future__ import annotations

import json
import math
import random
from datetime import date

from nsw_sim import clock, db
from nsw_sim.sim import ids

DAY = 86400.0


class RemittanceMixin:
    def period_key(self, entity: str, d: str) -> str:
        freq = self.profiles[entity].remittance.frequency
        return d[:7] if freq == "monthly" else f"{d[:4]}-Q{(int(d[5:7]) - 1) // 3 + 1}"

    def h_rem_obligation(self, t: float, ref_: str, p: dict) -> None:
        ent, period = p["entity"], p["period"]
        rule = self.profiles[ent].remittance
        base = self.net_settled.get((ent, period), 0)
        if rule.basis == "surplus":
            base = max(0, base - self.opex_acc.get((ent, period), 0))
        amount = int(round(base * rule.share))
        if amount <= 0:
            return
        # period end month -> due date (due_day of the following month, 17:00 WAT)
        y, m = (int(period[:4]), int(period[5:7])) if "Q" not in period else (int(period[:4]), int(period[-1]) * 3)
        due_d = date(y + (m == 12), (m % 12) + 1, min(rule.due_day, 28))
        due_ts = clock.wat_midnight_utc(due_d).timestamp() + 17 * 3600
        rnd = random.Random(f"{ent}{period}")
        delay = min(2, rnd.choice(rule.typical_delay_days))
        plan = self.ops_plans.get((ent, clock.week_start(due_d)))
        if plan:
            delay += min(1, max(0, plan[0].remittance_delay_days))
        b5 = self.cond._w["B5"]
        if ent == b5[2]["entity"] and b5[0] <= due_ts < b5[1]:
            delay = int(b5[2]["days_late"])                       # beat B5: enforced by the engine, not the model
        for inc in self.cond.incidents:
            if inc.kind == "late_remittance" and inc.entity == ent and inc.start <= due_ts and inc.params.get("period") == period:
                delay = max(delay, int(inc.params.get("days_late", 9)))
        paid_ts = due_ts + delay * DAY + rnd.uniform(-3, 3) * 3600
        days_late = max(0, int(math.ceil((paid_ts - due_ts) / DAY - 0.2))) if paid_ts > due_ts else 0
        end_m = f"{y}{m:02d}"
        self.rem_seq[(ent, end_m)] = self.rem_seq.get((ent, end_m), 0) + 1
        rid = ids.remittance_id(ent, end_m, self.rem_seq[(ent, end_m)])
        self._post_obligation(t, rid, ent, period, due_ts, amount, base, rule.share)
        self.schedule(paid_ts, "REMPAY", f"REMPAY:{rid}", {"rid": rid, "entity": ent, "amount": amount, "due": due_ts, "late": days_late})
        if days_late > 0:
            self.schedule(due_ts + DAY, "REMLATE", f"REMPAY:{rid}", {"rid": rid, "entity": ent, "amount": amount, "due": due_ts, "late": days_late})

    def _post_obligation(self, t: float, rid: str, ent: str, period: str, due_ts: float, amount: int, base: int, share: float) -> None:
        self.b["rem"].append((rid, ent, period, clock.iso(due_ts), None, amount, "due", "Federation Account", None, clock.iso(t), base, share))
        self.ledger.post(ent, t, "remittance", rid, None, f"Remittance obligation {period}", [("3200", amount, 0, "NGN", None, None), ("2300", 0, amount, "NGN", None, None)])
        self.emit("nsw.remittance.due", t, ent, rid, "Remittance obligation recognised", f"{ent} remittance for {period} is due on {clock.wat_day(due_ts)}.",
                  {"amount_ngn_minor": amount, "period": period, "due_date": clock.wat_day(due_ts)})

    def h_rem_pay(self, t: float, ref_: str, p: dict) -> None:
        ent, amt = p["entity"], p["amount"]
        late = p["late"]
        self.b["rem_upd"].append((clock.iso(t), late, p["rid"]))
        self.ledger.post(ent, t, "remittance_paid", p["rid"], None, "Remittance paid", [("2300", amt, 0, "NGN", None, None), ("1100", 0, amt, "NGN", None, None)])
        self.ledger.post("MOF-FA", t, "remittance_received", p["rid"], None, f"Remittance from {ent}", [("1100", amt, 0, "NGN", None, "RCPT"), ("4900", 0, amt, "NGN", None, "RCPT")])
        self.emit("nsw.remittance.paid", t, ent, p["rid"], "Remittance paid", f"{ent} remitted to the Federation Account" + (f", {late} days after the due date." if late else "."),
                  {"amount_ngn_minor": amt, "days_late": late}, severity="medium" if late > 3 else "info")
        self.rup(ent, clock.wat_day(t), "remitted", "ALL", "ALL", amt)

    def h_rem_late(self, t: float, ref_: str, p: dict) -> None:
        self.emit("nsw.remittance.late", t, p["entity"], p["rid"], "Remittance past due", f"{p['entity']} remittance {p['rid']} passed its due date and is awaiting payment.",
                  {"amount_ngn_minor": p["amount"]}, severity="medium")

    def inject_late_remittance(self, inc) -> None:
        """Presenter incident: a supplementary remittance that fell due 5 days ago and is paid 9 days after its due date."""
        ent = inc.entity or "NAFDAC"
        t = self.t
        base = int(sum(v for (e, k), v in self.net_settled.items() if e == ent and "Q" not in k) / max(1, len({k for (e, k) in self.net_settled if e == ent and "Q" not in k})) * 0.25)
        amount = max(int(base * self.profiles[ent].remittance.share), 100_000_00)
        due_ts = t - 5 * DAY
        rid = f"REM-{ent}-ADHOC-{int(t) % 100000:05d}"
        self._post_obligation(t - 6 * DAY, rid, ent, f"SUPP-{clock.wat_day(t)}", due_ts, amount, base, self.profiles[ent].remittance.share)
        paid = due_ts + int(inc.params.get("days_late", 9)) * DAY
        self.schedule(paid, "REMPAY", f"REMPAY:{rid}", {"rid": rid, "entity": ent, "amount": amount, "due": due_ts, "late": int(inc.params.get("days_late", 9))})
        self.schedule(t + 60, "REMLATE", f"REMPAY:{rid}", {"rid": rid, "entity": ent, "amount": amount, "due": due_ts, "late": 9})

    # ---- pseudo-row persistence (batches, remittance payments, refunds, system events)
    def _pseudo_row(self, ref_: str, pend: list) -> tuple:
        pend = sorted(pend)
        return (ref_, pend[0][2], clock.iso(pend[0][0]), json.dumps({"events": [[p[0], p[2], p[4]] for p in pend]}))

    def _restore_pseudo(self, ref_: str, d: dict) -> None:
        pass

    def _restore_money(self) -> None:
        """Rebuild derivable money state (accumulators, unsettled allocations, open batches) after a restart."""
        conn = self.conn
        for ent, m, v in conn.execute("SELECT entity_id, substr(datetime(occurred_at,'+1 hour'),1,7), SUM(amount_ngn_minor-collection_cost_ngn_minor) "
                                      "FROM settlements GROUP BY 1,2"):
            q = f"{m[:4]}-Q{(int(m[5:7]) - 1) // 3 + 1}"
            self.net_settled[(ent, m)] = self.net_settled.get((ent, m), 0) + v
            self.net_settled[(ent, q)] = self.net_settled.get((ent, q), 0) + v
        for ent, m, v in conn.execute("SELECT entity_id, substr(datetime(occurred_at,'+1 hour'),1,7), SUM(amount_ngn_minor) FROM expenses GROUP BY 1,2"):
            q = f"{m[:4]}-Q{(int(m[5:7]) - 1) // 3 + 1}"
            self.opex_acc[(ent, m)] = self.opex_acc.get((ent, m), 0) + v
            self.opex_acc[(ent, q)] = self.opex_acc.get((ent, q), 0) + v
        in_batch = {pid for it in self.heap if it[2] == "BATCHDONE" for pid in it[4]["pids"]}
        rows = conn.execute("SELECT a.payment_id, a.alloc_id, a.assessment_id, a.entity_id, a.process_code, a.fee_code, a.currency, a.amount_ngn_minor, "
                            "a.origin_country, a.mode, a.port, a.commodity_group, p.bank, p.occurred_at FROM payment_allocations a "
                            "JOIN payments p ON p.payment_id=a.payment_id WHERE a.settled_at IS NULL").fetchall()
        for r in rows:
            self.pay_allocs.setdefault(r[0], []).append({"id": r[1], "aid": r[2], "e": r[3], "p": r[4], "fc": r[5], "ccy": r[6], "amt": r[7],
                                                          "origin": r[8], "mode": r[9], "port": r[10], "grp": r[11]})
            self.pay_meta[r[0]] = (r[12], sum(x["amt"] for x in self.pay_allocs[r[0]]), clock.to_epoch(r[13]))
        for pid, (bank, _, _) in self.pay_meta.items():
            if pid not in in_batch and pid not in {x for v in self.open_batch.values() for x in v}:
                self.open_batch.setdefault(bank, []).append(pid)
        for ent, mx in conn.execute("SELECT entity_id, MAX(occurred_at) FROM funding_receipts WHERE source_type IN ('grant','concessional_loan') GROUP BY 1"):
            self.last_partner[ent] = clock.to_epoch(mx)
        for ent, per, n in conn.execute("SELECT entity_id, substr(remittance_id,-10,6), COUNT(*) FROM remittances GROUP BY 1,2"):
            self.rem_seq[(ent, per)] = n

    def _persist_money_state(self) -> None:
        db.kv_set(self.conn, "fee_ema", json.dumps({f"{e}|{c}": v for (e, c), v in self.fee_ema.items()}))
        db.kv_set(self.conn, "dq_pending", json.dumps(self.dq_pending))

    def _restore_runtime_state(self) -> None:
        conn = self.conn
        self.fee_ema = {tuple(k.split("|", 1)): v for k, v in json.loads(db.kv_get(conn, "fee_ema") or "{}").items()}
        self.dq_pending = json.loads(db.kv_get(conn, "dq_pending") or "{}")
        rows = conn.execute("SELECT payment_id,nsw_ref,payer_id,bank,amount_ngn_minor,origin_country,mode,port,commodity_group,occurred_at "
                            "FROM payments WHERE status='confirmed' AND is_duplicate=0 AND nsw_ref NOT LIKE 'UNMATCHED%' "
                            "ORDER BY occurred_at DESC LIMIT 300").fetchall()
        for r in reversed(rows):
            self.recent_payments.append((*r[:9], clock.to_epoch(r[9])))
