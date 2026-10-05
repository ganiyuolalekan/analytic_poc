"""Settlement batches: per bank per day (17:00 WAT cut-off); completion after the bank's lag (T+1 by default)."""
from __future__ import annotations

import random

from nsw_sim import clock
from nsw_sim.sim import ids

H = 3600.0


class SettlementMixin:
    def h_batch_close(self, t: float, ref_: str, p: dict) -> None:
        bank = p["bank"]
        pids = [x for x in self.open_batch.get(bank, []) if self.pay_meta[x][2] <= t]
        if not pids:
            return
        self.open_batch[bank] = [x for x in self.open_batch[bank] if x not in set(pids)]
        day = clock.wat_day(t).replace("-", "")
        bid = ids.batch_id(day, bank, 1)
        total = sum(self.pay_meta[x][1] for x in pids)
        rnd = random.Random(int(t) + ord(bank))
        lag = self.cond.bank_lag_h(bank, t) * rnd.uniform(0.92, 1.08)
        self.b["batch"].append((bid, bank, clock.wat_day(t), clock.iso(t), None, total, None, "closed", len(pids)))
        self.emit("nsw.settlement.batch_closed", t, "CBN", bid, "Settlement batch closed", f"Batch {bid}: {len(pids)} payments totalling pending settlement at Bank {bank}.",
                  {"amount_ngn_minor": total, "bank": bank, "n_payments": len(pids), "expected_lag_h": round(lag, 1)})
        self.schedule(t + lag * H, "BATCHDONE", f"BATCH:{bid}", {"bid": bid, "bank": bank, "lag": lag, "close": t, "pids": pids})

    def h_batch_done(self, t: float, ref_: str, p: dict) -> None:
        iso = clock.iso(t)
        day = clock.wat_day(t)
        month, quarter = day[:7], f"{day[:4]}-Q{(int(day[5:7]) - 1) // 3 + 1}"
        cbn_total = 0
        for pid in p["pids"]:
            allocs = self.pay_allocs.pop(pid, None)
            self.pay_meta.pop(pid, None)
            if allocs is None:
                allocs = self._allocs_from_db(pid)
            by_ent: dict[str, list] = {}
            for al in allocs:
                by_ent.setdefault(al["e"], []).append(al)
            sid_base = pid[3:]
            for ent, items in by_ent.items():
                rate = self.profiles[ent].collection_cost_rate
                gross = cost = 0
                sid = f"ST{sid_base}-{ent}"
                for al in items:
                    c = int(round(al["amt"] * rate))
                    gross += al["amt"]
                    cost += c
                    self.b["alloc_upd"].append((sid, iso, c, al["amt"] - c, al["id"]))
                net = gross - cost
                origin = items[0]["origin"]
                self.b["stl"].append((sid, p["bid"], pid, ent, gross, cost, iso))
                self.ledger.post(ent, t, "settlement", sid, None, f"Settlement of {pid} via {p['bid']}",
                                 [("1100", net, 0, "NGN", origin, items[0]["p"]), ("5500", cost, 0, "NGN", origin, items[0]["p"]),
                                  ("1105", 0, gross, "NGN", origin, items[0]["p"])])
                self.net_settled[(ent, month)] = self.net_settled.get((ent, month), 0) + net
                self.net_settled[(ent, quarter)] = self.net_settled.get((ent, quarter), 0) + net
                for al in items:
                    c = int(round(al["amt"] * rate))
                    self.rup(ent, day, "settled", "ALL", "ALL", al["amt"] - c)
                    self.rup(ent, day, "collection_cost", "ALL", "ALL", c)
                    self.rup(ent, day, "settled", "origin_country", al["origin"], al["amt"] - c)
                    self.rup(ent, day, "settled", "process_code", al["p"], al["amt"] - c)
                self.rum(ent, t, "settled", net)
                cbn_total += gross
        if cbn_total:
            self.ledger.post("CBN", t, "settlement_batch", p["bid"], None, f"Batch {p['bid']} settled to agencies",
                             [("2100", cbn_total, 0, "NGN", None, "SETTLE"), ("1100", 0, cbn_total, "NGN", None, "SETTLE")])
        lag_h = (t - p["close"]) / H
        self.b["batch_upd"].append((iso, round(lag_h, 2), p["bid"]))
        self.rup("CBN", day, "batch_lag_h_sum", "bank", p["bank"], lag_h)
        self.rup("CBN", day, "batch_count", "bank", p["bank"], 1)
        self.emit("nsw.settlement.completed", t, "CBN", p["bid"], "Settlement completed", f"Batch {p['bid']} settled to agencies after {lag_h:.0f} hours.",
                  {"amount_ngn_minor": cbn_total, "bank": p["bank"], "lag_hours": round(lag_h, 1)},
                  severity="medium" if lag_h > 48 else "info")

    def _allocs_from_db(self, pid: str) -> list[dict]:
        rows = self.conn.execute("SELECT alloc_id,assessment_id,entity_id,process_code,fee_code,currency,amount_ngn_minor,origin_country,mode,port,"
                                 "commodity_group FROM payment_allocations WHERE payment_id=?", (pid,)).fetchall()
        return [{"id": r[0], "aid": r[1], "e": r[2], "p": r[3], "fc": r[4], "ccy": r[5], "amt": r[6], "origin": r[7], "mode": r[8], "port": r[9],
                 "grp": r[10]} for r in rows]
