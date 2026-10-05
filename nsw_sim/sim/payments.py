"""Payments (S05): initiation -> confirmation (with failures/retries), allocations, duplicates, orphans, refunds."""
from __future__ import annotations

import random

from nsw_sim import clock
from nsw_sim.sim.consignments import Cn

H = 3600.0


class PaymentMixin:
    def _init_money(self) -> None:
        self.pay_allocs: dict[str, list[dict]] = {}       # unsettled payment_id -> allocation dicts
        self.pay_meta: dict[str, tuple] = {}              # payment_id -> (bank, total, confirmed_t)
        self.open_batch: dict[str, list[str]] = {b: [] for b in self.sim_cfg["banks"]}
        self.net_settled: dict[tuple[str, str], int] = {}
        self.opex_acc: dict[tuple[str, str], int] = {}
        self.fa_open = 0
        self.rem_seq: dict[tuple[str, str], int] = {}

    def pay_allocs_cleanup(self, ref_: str) -> None:
        pass

    # ---- S05
    def h_payi(self, t: float, ref_: str, _p) -> None:
        cn = self.cns[ref_]
        cn.pay_tries += 1
        if cn.pay_tries == 1:
            cn.s05_first_init = t
        rnd = self.rng(cn, 100 + cn.pay_tries)
        fail = cn.pay_tries < 4 and rnd.random() < self.cond.payment_fail_rate(t)
        _, d = self.sample("S05", cn, t, "CBN", rnd)
        total = sum(a["ngn"] for a in cn.pending)
        self.emit("nsw.payment.initiated", t, "CBN", cn.ref, "Payment initiated", f"Payment initiated for {cn.ref}.",
                  {"amount_ngn_minor": total, "bank": cn.bank})
        self.schedule(t + d * H, "PAYD", ref_, {"fail": fail, "init": t})

    def h_payd(self, t: float, ref_: str, p: dict) -> None:
        cn = self.cns[ref_]
        total_assessed = sum(a["ngn"] for a in cn.pending)
        pay_n, pref = self.ids.next_payment()
        pid = f"PAY{pay_n:08d}"
        iso = clock.iso
        if p["fail"]:
            self.b["pay"].append((pid, pref, cn.ref, cn.importer, "bank_transfer", cn.bank, total_assessed, "failed", 0, iso(p["init"]), iso(t),
                                  cn.origin_country, cn.mode, cn.port, cn.commodity_group))
            self.emit("nsw.payment.failed", t, "CBN", cn.ref, "Payment failed", f"Payment attempt for {cn.ref} failed; the trader will retry.",
                      {"amount_ngn_minor": total_assessed, "bank": cn.bank}, severity="low")
            rnd = self.rng(cn, 120 + cn.pay_tries)
            self.schedule(t + rnd.lognormvariate(1.6, 0.5) * H, "PAYI", ref_, {})
            return
        rnd = self.rng(cn, 130)
        channel = rnd.choices(["bank_transfer", "nip", "card"], weights=[0.62, 0.28, 0.10])[0]
        allocs, by_ent = [], {}
        for k, a in enumerate(cn.pending):
            pay = a["ngn"] if a["ccy"] == "NGN" else int(round(a["am"] * self.cond.fx(a["ccy"], t)))
            al = {"id": f"AL{cn.n:07d}-{k + 1}", "aid": a["id"], "e": a["e"], "p": a["p"], "fc": a["fc"], "ccy": a["ccy"], "amt": pay,
                  "assessed": a["ngn"], "origin": cn.origin_country, "mode": cn.mode, "port": cn.port, "grp": cn.commodity_group}
            allocs.append(al)
            by_ent.setdefault(a["e"], []).append(al)
        total = sum(al["amt"] for al in allocs)
        self.b["pay"].append((pid, pref, cn.ref, cn.importer, channel, cn.bank, total, "confirmed", 0, iso(p["init"]), iso(t), cn.origin_country,
                              cn.mode, cn.port, cn.commodity_group))
        day = clock.wat_day(t)
        for al in allocs:
            self.b["alloc"].append((al["id"], pid, al["aid"], cn.ref, al["e"], al["p"], al["fc"], al["origin"], al["mode"], al["port"], al["grp"],
                                    al["ccy"], al["amt"], iso(t)))
            self.rup(al["e"], day, "paid", "ALL", "ALL", al["amt"])
            self.rup(al["e"], day, "paid", "origin_country", al["origin"], al["amt"])
            self.rup(al["e"], day, "paid", "process_code", al["p"], al["amt"])
        for ent, items in by_ent.items():
            paid = sum(i["amt"] for i in items)
            assessed = sum(i["assessed"] for i in items)
            diff = paid - assessed
            lines = [("1105", paid, 0, "NGN", cn.origin_country, items[0]["p"]), ("1200", 0, assessed, "NGN", cn.origin_country, items[0]["p"])]
            if diff > 0:
                lines.append(("4950", 0, diff, "NGN", cn.origin_country, items[0]["p"]))
            elif diff < 0:
                lines.append(("5950", -diff, 0, "NGN", cn.origin_country, items[0]["p"]))
            self.ledger.post(ent, t, "payment", f"{pid}|{ent}", cn.ref, f"Payment {pref} applied", lines)
            self.rum(ent, t, "paid", paid)
        self.ledger.post("CBN", t, "payment", pid, cn.ref, f"Payment {pref} received", [("1100", total, 0, "NGN", cn.origin_country, "PCONF"),
                                                                                         ("2100", 0, total, "NGN", cn.origin_country, "PCONF")])
        # S05 stage row, state
        wait_h = (cn.s05_first_init - cn.s05_ready) / H
        dur_h = (t - cn.s05_first_init) / H
        self.stage_row(cn, "S05", "CBN", cn.s05_ready, cn.s05_first_init, t, wait_h, dur_h)
        cn.digital_h += wait_h + dur_h
        cn.handoff_h += wait_h
        cn.status = "paid"
        cn.pending = []
        self.pay_allocs[pid] = allocs
        self.pay_meta[pid] = (cn.bank, total, t)
        self.open_batch[cn.bank].append(pid)
        self.recent_payments.append((pid, cn.ref, cn.importer, cn.bank, total, cn.origin_country, cn.mode, cn.port, cn.commodity_group, t))
        self.emit("nsw.payment.confirmed", t, "CBN", cn.ref, "Payment confirmed", f"Payment {pref} confirmed for {cn.ref}.",
                  {"amount_ngn_minor": total, "origin_country": cn.origin_country, "payment_ref": pref, "channel": channel, "bank": cn.bank})
        if rnd.random() < self.sim_cfg["duplicate_rate"]:
            self.schedule(t + rnd.uniform(120, 1500), "DUP", ref_, {"pid": pid})
        if cn.lane in ("yellow", "red"):
            self.schedule(t, "Q06", ref_, None)
        else:
            self.to_s07(cn, t)

    # ---- duplicates / orphans (unapplied receipts, refunded later)
    def _unapplied(self, t: float, pref_note: str, nsw_ref: str, payer: str, bank: str, total: int, dup: bool, rec: tuple | None,
                   typ: str, headline: str, desc: str) -> None:
        pay_n, pref = self.ids.next_payment()
        pid = f"PAY{pay_n:08d}"
        origin, mode, port, grp = (rec[5], rec[6], rec[7], rec[8]) if rec else (None, None, None, None)
        self.b["pay"].append((pid, pref, nsw_ref, payer, "bank_transfer", bank, total, "duplicate" if dup else "confirmed", int(dup),
                              clock.iso(t - 60), clock.iso(t), origin, mode, port, grp))
        self.ledger.post("NSW", t, "unapplied_receipt", pid, nsw_ref, pref_note, [("1105", total, 0, "NGN", origin, "TXN"), ("2150", 0, total, "NGN", origin, "TXN")])
        self.ledger.post("CBN", t, "payment", pid, nsw_ref, pref_note, [("1100", total, 0, "NGN", origin, "PCONF"), ("2100", 0, total, "NGN", origin, "PCONF")])
        self.emit(typ, t, "NSW", nsw_ref, headline, desc, {"amount_ngn_minor": total, "payment_ref": pref, "bank": bank}, severity="medium")
        self.rup("NSW", clock.wat_day(t), "unapplied", "kind", "duplicate" if dup else "orphan", total)
        rnd = random.Random(pay_n)
        self.schedule(t + rnd.uniform(5, 9) * 86400, "UNAPPREF", f"DUPREF:{pid}", {"pid": pid, "amount": total, "dup": dup, "ref": nsw_ref})

    def h_dup(self, t: float, ref_: str, p: dict) -> None:
        rec = next((r for r in self.recent_payments if r[0] == p["pid"]), None)
        cn = self.cns.get(ref_)
        if rec is None and cn is None:
            return
        total = rec[4] if rec else 0
        self._unapplied(t, f"Duplicate payment on {ref_}", ref_, rec[2] if rec else "", rec[3] if rec else "A", total, True, rec,
                        "nsw.payment.duplicate", "Duplicate payment detected", f"A second payment with the same payer and amount was received for {ref_}.")

    def h_forcedup(self, t: float, ref_: str, p: dict) -> None:
        for rec in reversed(self.recent_payments):
            if t - rec[9] > 60:
                self._unapplied(t, f"Duplicate payment on {rec[1]}", rec[1], rec[2], rec[3], rec[4], True, rec, "nsw.payment.duplicate",
                                "Duplicate payment detected", f"A second payment with the same payer and amount was received for {rec[1]}.")
                return

    def h_orphan(self, t: float, ref_: str, p: dict) -> None:
        rnd = random.Random(int(t))
        total = int(rnd.uniform(25_000, 450_000) * 100)
        n = self.ids.pay_n + 1
        self._unapplied(t, "Payment without a matching assessment", f"UNMATCHED-{n:06d}", f"TRD{rnd.randrange(1, 400):04d}", rnd.choice("ABCDEF"), total,
                        False, None, "nsw.payment.confirmed", "Unmatched payment received", "A payment was received with a reference that matches no open assessment.")

    def h_unapplied_refund(self, t: float, ref_: str, p: dict) -> None:
        amt = p["amount"]
        self.ledger.post("NSW", t, "unapplied_refund", p["pid"], p["ref"], "Refund of unapplied receipt", [("2150", amt, 0, "NGN", None, "TXN"), ("1105", 0, amt, "NGN", None, "TXN")])
        self.ledger.post("CBN", t, "refund", p["pid"], p["ref"], "Refund of unapplied receipt", [("2100", amt, 0, "NGN", None, "PCONF"), ("1100", 0, amt, "NGN", None, "PCONF")])
        self.b["refund"].append((f"RFU-{p['pid']}", "NSW", "UNAPPLIED", amt, clock.iso(t), "Refund of duplicate or unmatched payment", "engine"))

    def h_refund(self, t: float, ref_: str, p: dict) -> None:
        ent, amt = p["entity"], p["amount"]
        acct = p["acct"]
        self.ledger.post(ent, t, "refund", p["id"], None, p["memo"], [(acct, amt, 0, "NGN", None, p["process"]), ("1100", 0, amt, "NGN", None, p["process"])])
        self.b["refund"].append((p["id"], ent, p["fee_code"], amt, clock.iso(t), p["memo"], p["src"]))
