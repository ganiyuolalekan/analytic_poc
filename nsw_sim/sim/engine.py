"""Discrete-event engine (Section 7). Deterministic given (seed, plans, profiles); the LLM supplies plans, never rows.

A consignment is created at manifest acceptance, arrives later, then runs stages S02-S09 sampled lazily from the
conditions in force when each stage becomes ready. Only facts with ``occurred_at <= watermark`` reach the database;
pending events live in ``inflight`` (one row per consignment, plus pseudo rows for batches/remittances)."""
from __future__ import annotations

import heapq
import json
import math
import random
import sqlite3
import zlib
from collections import deque
from datetime import date, datetime, timedelta

from nsw_sim import clock, db
from nsw_sim.config import settings, yaml_config
from nsw_sim.llm.schemas import EntityProfile, FlowPlan, OpsPlan
from nsw_sim.sim import fallback as fb
from nsw_sim.sim import fees
from nsw_sim.sim.conditions import Conditions, Incident, next_working_time
from nsw_sim.sim.consignments import Cn, PlanContext, Sampler
from nsw_sim.sim.expenses import OpsMixin
from nsw_sim.sim.ids import IdFactory
from nsw_sim.sim.ledger import Ledger
from nsw_sim.sim.payments import PaymentMixin
from nsw_sim.sim.reference import entity_cards, ref
from nsw_sim.sim.remittance import RemittanceMixin
from nsw_sim.sim.settlement import SettlementMixin

H = 3600.0
PSEUDO = ("BATCH:", "REMPAY:", "DUPREF:", "SYS:")
STAGE_OWNER = {"S01": "NCS", "S03": "NSW", "S04": "NCS", "S05": "CBN", "S06": "NCS", "S07": "NCS"}
SLA_KEY = {"S02": "S02_permit", "S04": "S04_assessment", "S05": "S05_payment", "S06": "S06_exam", "S07": "S07_release",
           "S09": "S09_evacuation"}
# stage events kept in the live feed when backfilling (sampled, to bound volume); notable ones are always kept
FEED_SAMPLE = {"nsw.manifest.accepted": 0.04, "nsw.arrival.recorded": 0.04, "nsw.permit.approved": 0.04, "nsw.declaration.filed": 0.04,
               "nsw.declaration.assessed": 0.04, "nsw.payment.initiated": 0.0, "nsw.payment.confirmed": 0.05, "nsw.exam.queued": 0.03,
               "nsw.exam.started": 0.0, "nsw.exam.completed": 0.04, "nsw.release.granted": 0.05, "nsw.gate.out": 0.04,
               "nsw.permit.queried": 0.3}


def lerp(pair, p: float) -> float:
    return pair[0] + (pair[1] - pair[0]) * p


def stable_u(*parts) -> float:
    return (zlib.crc32("|".join(map(str, parts)).encode()) % 1_000_000) / 1_000_000.0


class Engine(PaymentMixin, SettlementMixin, RemittanceMixin, OpsMixin):
    def __init__(self, conn: sqlite3.Connection, profiles: dict[str, EntityProfile], *, live_speed: float = 1.0,
                 feed_full_from: float | None = None, rollup_minute_from: float | None = None) -> None:
        self.conn = conn
        self.cfg = settings()
        self.sim_cfg = self.cfg["sim"]
        self.profiles = profiles
        self.types = {c: v["type"] for c, v in entity_cards().items()}
        self.ids = IdFactory(conn)
        self.ledger = Ledger(self.ids)
        self.cond = Conditions(self._fx_base)
        self.t = clock.to_epoch(db.kv_get(conn, "watermark_utc") or clock.iso(clock.engine_start()))
        self.heap: list = []
        self.seq = 0
        self.pending: dict[str, list] = {}
        self.cns: dict[str, Cn] = {}
        self.flow_plans: dict[date, FlowPlan] = {}
        self.ops_plans: dict[tuple[str, date], tuple[OpsPlan, str]] = {}
        self.flow_source: dict[date, str] = {}
        self._ctx: dict[date, PlanContext] = {}
        self.party_rows = [tuple(r) for r in conn.execute("SELECT * FROM parties")]
        self.sampler = Sampler(profiles, self.ids, self.party_rows)
        self.feed_full_from = feed_full_from if feed_full_from is not None else 0.0
        self.rollup_minute_from = rollup_minute_from if rollup_minute_from is not None else 0.0
        self.live_speed = live_speed
        self.jobs_day: str | None = db.kv_get(conn, "jobs_day")
        self.minute_cursor = float(db.kv_get(conn, "minute_cursor") or (math.floor(self.t / 60) * 60))
        self.recent_payments: deque = deque(maxlen=300)
        self.dq_pending: dict[str, int] = {}
        self.last_partner: dict[str, float] = {}
        self.fee_ema: dict[tuple[str, str], float] = {}
        self._init_buffers()
        self._init_money()
        self._load_state()
        wf = self.sim_cfg["weekend_arrival_factor"]
        self.base_per_day = self.cfg["consignments_per_day"] * 7.0 / (5.0 + 2.0 * wf)   # keeps the weekly mean at the configured rate
        self.sla = {e: p.sla_hours for e, p in profiles.items()}
        self.target_mid = {e: fb.target_mid_ngn(e) for e in profiles}

    # ------------------------------------------------------------------ plumbing
    def _init_buffers(self) -> None:
        self.dirty: set[str] = set()
        self.gone: set[str] = set()
        self.b: dict[str, list] = {k: [] for k in (
            "stage", "fees", "pay", "alloc", "alloc_upd", "batch", "batch_upd", "stl", "rem", "rem_upd", "fund", "exp",
            "refund", "live", "fx", "incident")}
        self.ru_day: dict[tuple, float] = {}
        self.ru_min: dict[tuple, float] = {}

    def schedule(self, t: float, kind: str, ref_: str, payload=None) -> None:
        self.seq += 1
        item = (t, self.seq, kind, ref_, payload)
        heapq.heappush(self.heap, item)
        self.pending.setdefault(ref_, []).append(item)
        self.dirty.add(ref_)

    def _fx_base(self, d: date) -> float:
        ws = clock.week_start(d)
        plan = self.flow_plans.get(ws)
        if plan is None:
            plan = fb.fallback_flow_plan(ws)
        return plan.fx_path[d.weekday()]

    def set_plans(self, flow: dict[date, tuple[FlowPlan, str]], ops: dict[tuple[str, date], tuple[OpsPlan, str]]) -> None:
        for w, (p, src) in flow.items():
            self.flow_plans[w] = p
            self.flow_source[w] = src
        self.ops_plans.update(ops)

    def ctx_for(self, t: float) -> tuple[PlanContext, str]:
        ws = clock.week_start(clock.wat(t).date())
        if ws not in self._ctx:
            plan = self.flow_plans.get(ws) or fb.fallback_flow_plan(ws)
            self._ctx[ws] = PlanContext(plan)
        return self._ctx[ws], self.flow_source.get(ws, "fallback")

    def _load_state(self) -> None:
        """Resume: rebuild the heap and in-flight consignments from the ``inflight`` table."""
        for ref_, nxt, nat, sj in self.conn.execute("SELECT nsw_ref,next_stage,next_event_at,state_json FROM inflight"):
            d = json.loads(sj)
            events = d.pop("events", [])
            if not ref_.startswith(PSEUDO):
                cn = Cn(**{k: v for k, v in d.items() if k != "events"})
                self.cns[ref_] = cn
            else:
                self._restore_pseudo(ref_, d)
            for t, kind, payload in events:
                self.seq += 1
                item = (t, self.seq, kind, ref_, payload)
                heapq.heappush(self.heap, item)
                self.pending.setdefault(ref_, []).append(item)
        self._restore_money()
        self._restore_runtime_state()
        if not any(it[2] == "MIN" for it in self.heap):
            self.schedule(self.minute_cursor + 60, "MIN", "SYS:MIN")

    # ------------------------------------------------------------------ stage sampling
    def _owner_mods(self, owner: str, cn: Cn) -> tuple[float, float]:
        prof = self.profiles.get(owner)
        if not prof:
            return 1.0, 1.0
        sens = prof.country_sensitivity.get(cn.origin_country)
        return (sens.dwell_modifier if sens else 1.0), prof.processing_speed_factor

    def sample(self, stage: str, cn: Cn, t_ready: float, owner: str, rnd: random.Random, wait_mult: float = 1.0,
               dur_mult: float = 1.0) -> tuple[float, float]:
        st = self.sim_cfg["stages"][stage]
        p = self.cond.progress(t_ready)
        dwell_mod, speed = self._owner_mods(owner, cn)
        pf = 1.0
        if st["type"] == "P":
            pf = self.sim_cfg["port_factor"].get(cn.port, 1.0) * (self.sim_cfg["air_physical_factor"] if cn.mode == "air" else 1.0)
        wmed = max(0.05, lerp(st["wait"], p) * dwell_mod * pf * wait_mult)
        dmed = max(0.05, lerp(st["dur"], p) * dwell_mod * speed * pf * dur_mult)
        return rnd.lognormvariate(math.log(wmed), st["sw"]), rnd.lognormvariate(math.log(dmed), st["sd"])

    def rng(self, cn: Cn, tag: int) -> random.Random:
        return random.Random((cn.seed << 6) ^ (tag * 2654435761 % (1 << 30)))

    def start_time(self, stage: str, cn: Cn, t_ready: float, wait_h: float) -> float:
        t = t_ready + wait_h * H
        if self.sim_cfg["stages"][stage].get("office"):
            # automation (B1): the share of steps that still wait for office hours falls as the platform matures
            if stable_u(cn.ref, stage, "office") < 1.0 - 0.85 * self.cond.progress(t_ready):
                t = next_working_time(t, jitter_s=(cn.seed % 7200))
        return t

    @staticmethod
    def permit_no(agency: str, cn: Cn) -> str | None:
        y = clock.wat(cn.manifested_at).strftime("%Y")
        pats = {"NAFDAC": f"NAFDAC/IP/{y}/{cn.n:06d}", "SON": f"SONCAP/CoC/{y}/{cn.n:07d}", "NAQS": f"NAQS/PHY/{y}/{cn.n:06d}",
                "NESREA": f"NESREA/EP/{y}/{cn.n:05d}", "NPA": f"NPA/PD/{cn.port}/{y}/{cn.n:06d}", "FAAN": f"FAAN/CH/{cn.port}/{y}/{cn.n:06d}"}
        return pats.get(agency)

    # ------------------------------------------------------------------ rows / events
    def stage_row(self, cn: Cn, stage: str, owner: str, ready: float, started: float, done: float, wait_h: float, dur_h: float,
                  status: str = "done", idx: str = "", doc_no: str | None = None) -> None:
        sla = None
        key = SLA_KEY.get(stage)
        if key:
            sla = float(self.sla.get(owner, {}).get(key, self.cfg["sla_hours"].get(key, 0)) or self.cfg["sla_hours"].get(key, 0)) or None
        breach = int(sla is not None and dur_h > sla)
        complete = 1
        miss = self.cond.dq_missing_share(owner, done)
        if self.dq_pending.get(owner, 0) > 0:
            self.dq_pending[owner] -= 1
            complete = 0
        elif miss and stable_u(cn.ref, stage, idx, "dq") < miss:
            complete = 0
        elif owner in self.profiles and stable_u(cn.ref, stage, idx, "inc") > self.profiles[owner].onboarding.completeness:
            complete = 0
        if status == "done" and owner in self.profiles and stage not in ("S01",):
            p = self.cond.progress(done)
            ob = self.profiles[owner].onboarding
            if stable_u(cn.ref, stage, idx, "man") > ob.digital_share_start + (ob.digital_share_end - ob.digital_share_start) * p:
                status = "manual"
        eid = f"SE{cn.n:07d}-{stage}{idx}"
        if status == "queued":      # queue-entry fact: the planned wait/duration are known, the start time is not (yet)
            row = (eid, cn.ref, stage, self.sim_cfg["stages"].get(stage, {"type": "D"})["type"], owner, clock.iso(ready),
                   None, clock.iso(done), round(wait_h, 3), round(dur_h, 3), sla, breach, status, 1, None)
            self.b["stage"].append(row)
            return
        if complete:
            row = (eid, cn.ref, stage, self.sim_cfg["stages"].get(stage, {"type": "D"})["type"], owner, clock.iso(ready),
                   clock.iso(started), clock.iso(done), round(wait_h, 3), round(dur_h, 3), sla, breach, status, 1, doc_no)
        else:
            row = (eid, cn.ref, stage, self.sim_cfg["stages"].get(stage, {"type": "D"})["type"], owner, clock.iso(ready),
                   None, clock.iso(done), None, None, sla, breach if status == "queued" else None, status, 0, doc_no)
        self.b["stage"].append(row)
        day = clock.wat_day(done)
        self.rup(owner, day, "dq_total", "field", "stage_events.started_at", 1)
        if complete:
            self.rup(owner, day, "dq_present", "field", "stage_events.started_at", 1)
        if status in ("done", "manual"):
            self.rup(owner, day, "stage_done", "stage", stage, 1)
            self.rup(owner, day, "digital_native", "all", "ALL", 1 if status == "done" else 0)

    def rup(self, entity: str, day: str, metric: str, dim: str, dim_value: str, v: float) -> None:
        k = (entity, day, metric, dim, dim_value)
        self.ru_day[k] = self.ru_day.get(k, 0.0) + v

    def rum(self, entity: str, t: float, metric: str, v: float) -> None:
        if t >= self.rollup_minute_from:
            k = (entity, clock.iso(t)[:16] + "Z", metric)
            self.ru_min[k] = self.ru_min.get(k, 0.0) + v

    def emit(self, typ: str, t: float, entity: str, subject: str, headline: str, desc: str, data: dict | None = None,
             severity: str = "info", kind: str = "engine") -> None:
        p = FEED_SAMPLE.get(typ)
        if p is not None and t < self.feed_full_from and stable_u(subject, typ) >= p:
            return
        payload = {"entity": entity, **(data or {}), "currency": "NGN"}
        self.b["live"].append((self.ids_evt(), clock.iso(t), typ, severity, entity, subject, headline, desc, json.dumps(payload),
                               kind, f"run-{clock.wat_day(t)}", self.live_speed))

    _evt_n = 0

    def ids_evt(self) -> str:
        Engine._evt_n += 1
        return f"evt_{int(self.t):010x}{Engine._evt_n:012x}"

    # ------------------------------------------------------------------ main loop
    def advance(self, t1: float) -> None:
        """Process everything up to ``t1`` (daily jobs fire when time first enters a new WAT day)."""
        while self.t < t1:
            day = clock.wat_day(self.t)
            if self.jobs_day != day:
                self.daily_jobs(day)
                self.jobs_day = day
            nxt = clock.wat_midnight_utc(date.fromisoformat(day) + timedelta(days=1)).timestamp()
            step_end = min(t1, nxt)
            self._process(step_end)
            self.t = step_end

    def _process(self, t_end: float) -> None:
        heap = self.heap
        handlers = self._handlers()
        while heap and heap[0][0] <= t_end:
            item = heapq.heappop(heap)
            t, _, kind, ref_, payload = item
            lst = self.pending.get(ref_)
            if lst is not None:
                try:
                    lst.remove(item)
                except ValueError:
                    pass
                if not lst:
                    self.pending.pop(ref_, None)
            self.dirty.add(ref_)
            handlers[kind](t, ref_, payload)

    def _handlers(self):
        if not hasattr(self, "_h"):
            self._h = {
                "MIN": self.h_minute, "ARR": self.h_arrive, "PERMIT": self.h_permit, "FEEDEV": self.h_feedev, "S03": self.h_s03,
                "S04": self.h_s04, "PAYI": self.h_payi, "PAYD": self.h_payd, "Q06": self.h_q06, "ST06": self.h_st06,
                "S06": self.h_s06, "S07": self.h_s07, "S08": self.h_s08, "S09": self.h_s09,
                "BATCHCLOSE": self.h_batch_close, "BATCHDONE": self.h_batch_done, "REMOB": self.h_rem_obligation,
                "REMPAY": self.h_rem_pay, "REMLATE": self.h_rem_late, "EXP": self.h_expense, "FUND": self.h_fund, "DEPR": self.h_depr,
                "REFUND": self.h_refund, "DUP": self.h_dup, "FORCEDUP": self.h_forcedup, "UNAPPREF": self.h_unapplied_refund,
                "ORPHAN": self.h_orphan, "SCANEV": self.h_scan_event, "FXROW": self.h_fx_row}
        return self._h

    # ------------------------------------------------------------------ arrivals (per minute)
    def h_minute(self, t: float, _ref: str, _p) -> None:
        m0 = t - 60
        ctx, src = self.ctx_for(m0)
        plan = ctx.plan
        w = clock.wat(m0)
        per_day = self.base_per_day * plan.daily_multiplier[w.weekday()] * self.cond.arrival_mult(m0)
        season = 1.0
        lam = per_day * (ctx.hourly[w.hour] / ctx.hourly_sum) / 60.0 * season
        rnd = random.Random(int(m0) * 1000003 + self.sim_cfg["seed"])
        n, pr, u = 0, math.exp(-lam), rnd.random()
        cum = pr
        while u > cum and n < 60:
            n += 1
            pr *= lam / n
            cum += pr
        for k in range(n):
            t0 = m0 + rnd.random() * 60
            cn = self.sampler.make(t0, int(m0) * 131 + k, ctx, self.cond, src)
            self.manifest(cn, t0)
        self.minute_cursor = t
        self.schedule(t + 60, "MIN", "SYS:MIN")

    def manifest(self, cn: Cn, t0: float) -> None:
        self.cns[cn.ref] = cn
        self.dirty.add(cn.ref)
        rnd = self.rng(cn, 1)
        lead = rnd.uniform(0.3, 2.5) * H
        self.stage_row(cn, "S01", "NCS", t0 - lead - 600, t0 - lead, t0, 0.17, lead / H, idx="")
        self.rup("NCS", clock.wat_day(t0), "consignments", "all", "ALL", 1)
        self.rup("NCS", clock.wat_day(t0), "consignments", "origin_country", cn.origin_country, 1)
        self.emit("nsw.manifest.accepted", t0, "NCS", cn.ref, "Manifest accepted",
                  f"{cn.ref}: {cn.mode} cargo from {cn.origin_country} to {cn.port} ({cn.commodity_group}).",
                  {"origin_country": cn.origin_country, "port": cn.port, "mode": cn.mode})
        self.assess_stage(cn, "S01", t0, rnd)
        self.schedule(cn.eta, "ARR", cn.ref)

    # ------------------------------------------------------------------ fee assessment + posting
    def assess_stage(self, cn: Cn, stage: str, t: float, rnd: random.Random, entities: list[str] | None = None) -> None:
        rows = fees.assess_fees(cn, stage, self.profiles, self.cond, t, rnd, set(cn.permits), entities,
                                noise=self.sim_cfg["assessment_noise"])
        if not rows:
            return
        by_ent: dict[str, list] = {}
        ts = clock.iso(t)
        for a in rows:
            cn.fa_k += 1
            aid = f"FA{cn.n:07d}-{cn.fa_k}"
            self.b["fees"].append((aid, cn.ref, a.entity, a.process, a.fee_code, a.basis, a.base_minor, a.rate, a.amount_minor,
                                   a.currency, a.fx, a.amount_ngn, cn.origin_country, a.expected_ngn, ts, cn.mode, cn.port,
                                   cn.commodity_group, cn.hs_code, cn.lane))
            cn.pending.append({"id": aid, "e": a.entity, "fc": a.fee_code, "p": a.process, "ngn": a.amount_ngn, "ccy": a.currency,
                               "am": a.amount_minor, "acct": a.account, "fx": a.fx})
            by_ent.setdefault(a.entity, []).append(a)
            day = clock.wat_day(t)
            self.rup(a.entity, day, "assessed", "ALL", "ALL", a.amount_ngn)
            self.rup(a.entity, day, "assessed", "origin_country", cn.origin_country, a.amount_ngn)
            self.rup(a.entity, day, "assessed", "process_code", a.process, a.amount_ngn)
            self.rum(a.entity, t, "assessed", a.amount_ngn)
            k = (a.entity, a.fee_code)
            self.fee_ema[k] = self.fee_ema.get(k, a.amount_ngn) * 0.995 + a.amount_ngn * 0.005
        for ent, items in by_ent.items():
            lines = [("1200", sum(i.amount_ngn for i in items), 0, "NGN", cn.origin_country, items[0].process)]
            for i in items:
                lines.append((i.account, 0, i.amount_ngn, "NGN", cn.origin_country, i.process))
            self.ledger.post(ent, t, "assessment", f"{cn.ref}|{stage}", cn.ref, f"Fees assessed {cn.ref}", lines)

    # ------------------------------------------------------------------ lifecycle handlers
    def h_arrive(self, t: float, ref_: str, _p) -> None:
        cn = self.cns[ref_]
        cn.arrived_at, cn.status = t, "arrived"
        rnd = self.rng(cn, 2)
        self.emit("nsw.arrival.recorded", t, "NPA" if cn.mode == "sea" else "FAAN", cn.ref, "Arrival recorded",
                  f"{cn.ref} {'berthed at' if cn.mode == 'sea' else 'landed at'} {cn.port}.", {"port": cn.port})
        if not cn.permits:
            cn.s02_left = 0
            self.schedule(t, "S03", ref_, {"ready": t})
            return
        cn.s02_left = len(cn.permits)
        cn.s02_first_ready = t
        for i, agency in enumerate(cn.permits):
            r2 = self.rng(cn, 10 + i)
            wmult = self.cond.queue_pressure(agency, t)
            w, d = self.sample("S02", cn, t, agency, r2, wait_mult=wmult, dur_mult=self.cond.permit_mult(agency, t))
            sens = self.profiles[agency].country_sensitivity.get(cn.origin_country)
            if sens and r2.random() < sens.doc_issue_rate * 0.5:           # a query adds review time
                d *= 1 + r2.uniform(0.4, 1.0)
                self.schedule(t + (w + d / 2) * H, "FEEDEV", ref_, ["nsw.permit.queried", agency, "Permit queried",
                              f"{agency} raised a documentation query on {cn.ref}."])
            started = self.start_time("S02", cn, t, w)
            done = started + d * H
            self.stage_row(cn, "S02", agency, t, started, t, (started - t) / H, d, status="queued", idx=f"Q{i}")
            self.emit("nsw.permit.applied", t, agency, cn.ref, "Permit applied", f"{agency} permit requested for {cn.ref}.", {})
            self.schedule(done, "PERMIT", ref_, {"a": agency, "i": i, "ready": t, "start": started, "w": (started - t) / H, "d": d})

    def h_permit(self, t: float, ref_: str, p: dict) -> None:
        cn = self.cns[ref_]
        agency = p["a"]
        self.stage_row(cn, "S02", agency, p["ready"], p["start"], t, p["w"], p["d"], idx=f"A{p['i']}", doc_no=self.permit_no(agency, cn))
        self.emit("nsw.permit.approved", t, agency, cn.ref, "Permit approved", f"{agency} approved the permit for {cn.ref}.", {})
        self.assess_stage(cn, "S02", t, self.rng(cn, 30 + p["i"]), entities=[agency])
        cn.s02_left -= 1
        if cn.s02_left == 0:
            cn.digital_h += (t - cn.arrived_at) / H
            cn.handoff_h += 0.0
            self.schedule(t, "S03", ref_, {"ready": t})

    def h_feedev(self, t: float, ref_: str, p: list) -> None:
        self.emit(p[0], t, p[1], ref_, p[2], p[3], {}, severity="low")

    def h_s03(self, t: float, ref_: str, p: dict) -> None:
        cn = self.cns[ref_]
        rnd = self.rng(cn, 3)
        w, d = self.sample("S03", cn, t, "NSW", rnd)
        started = t + w * H
        done = started + d * H
        self.schedule(done, "S04", ref_, {"ready": t, "start": started, "w": w, "d": d, "stage": "S03"})

    def h_s04(self, t: float, ref_: str, p: dict) -> None:
        cn = self.cns[ref_]
        if p.get("stage") == "S03":           # declaration filed
            self.stage_row(cn, "S03", "NSW", p["ready"], p["start"], t, p["w"], p["d"])
            cn.declared_at, cn.status = t, "declared"
            cn.declaration_no = f"C{cn.n % 1_000_000:06d}/{clock.wat(t).strftime('%y')}"
            cn.digital_h += p["w"] + p["d"]
            cn.handoff_h += p["w"]
            self.emit("nsw.declaration.filed", t, "NSW", cn.ref, "Declaration filed", f"{cn.declaration_no} filed for {cn.ref}.", {})
            self.assess_stage(cn, "S03", t, self.rng(cn, 31))
            rnd = self.rng(cn, 4)
            w, d = self.sample("S04", cn, t, "NCS", rnd)
            started = self.start_time("S04", cn, t, w)
            self.schedule(started + d * H, "S04", ref_, {"ready": t, "start": started, "w": (started - t) / H, "d": d, "stage": "S04"})
            return
        self.stage_row(cn, "S04", "NCS", p["ready"], p["start"], t, p["w"], p["d"])
        cn.digital_h += p["w"] + p["d"]
        cn.handoff_h += p["w"]
        cn.status = "assessed"
        self.assess_stage(cn, "S04", t, self.rng(cn, 32))
        self.emit("nsw.declaration.assessed", t, "NCS", cn.ref, "Declaration assessed",
                  f"{cn.ref} assessed: {fb_money(sum(a['ngn'] for a in cn.pending))}.", {"amount_ngn_minor": sum(a["ngn"] for a in cn.pending)})
        cn.s05_ready = t
        rnd = self.rng(cn, 5)
        w, _ = self.sample("S05", cn, t, "CBN", rnd)
        cn.pay_tries = 0
        self.schedule(t + w * H, "PAYI", ref_, {})

    def h_q06(self, t: float, ref_: str, p) -> None:
        cn = self.cns[ref_]
        rnd = self.rng(cn, 6)
        lf = self.sim_cfg["lane_exam_factor"][cn.lane]
        w, d = self.sample("S06", cn, t, "NCS", rnd, wait_mult=self.cond.scanner_mult(cn.port, t) * (lf if lf else 1.0),
                           dur_mult=max(0.3, lf))
        self.stage_row(cn, "S06", "NCS", t, t + w * H, t, w, d, status="queued", idx="Q")
        self.emit("nsw.exam.queued", t, "NCS", cn.ref, "Examination queued",
                  f"{cn.ref} queued for {cn.lane}-lane examination at {cn.port}.", {"port": cn.port, "lane": cn.lane})
        cn.status = "exam"
        start = t + w * H
        self.schedule(start, "ST06", ref_, {"ready": t, "w": w, "d": d})

    def h_st06(self, t: float, ref_: str, p: dict) -> None:
        cn = self.cns[ref_]
        self.emit("nsw.exam.started", t, "NCS", cn.ref, "Examination started", f"{cn.ref} examination started at {cn.port}.", {})
        self.schedule(t + p["d"] * H, "S06", ref_, p)

    def h_s06(self, t: float, ref_: str, p: dict) -> None:
        cn = self.cns[ref_]
        self.stage_row(cn, "S06", "NCS", p["ready"], p["ready"] + p["w"] * H, t, p["w"], p["d"])
        cn.physical_h += p["w"] + p["d"]
        cn.handoff_h += p["w"]
        self.emit("nsw.exam.completed", t, "NCS", cn.ref, "Examination completed", f"{cn.ref} examination completed.", {})
        self.to_s07(cn, t)

    def to_s07(self, cn: Cn, t: float) -> None:
        rnd = self.rng(cn, 7)
        w, d = self.sample("S07", cn, t, "NCS", rnd)
        started = self.start_time("S07", cn, t, w)
        self.schedule(started + d * H, "S07", cn.ref, {"ready": t, "start": started, "w": (started - t) / H, "d": d})

    def h_s07(self, t: float, ref_: str, p: dict) -> None:
        cn = self.cns[ref_]
        self.stage_row(cn, "S07", "NCS", p["ready"], p["start"], t, p["w"], p["d"])
        cn.digital_h += p["w"] + p["d"]
        cn.handoff_h += p["w"]
        cn.released_at, cn.status = t, "released"
        self.emit("nsw.release.granted", t, "NCS", cn.ref, "Release granted", f"{cn.ref} released by Customs.", {})
        owner = cn.owner
        rnd = self.rng(cn, 8)
        w, d = self.sample("S08", cn, t, owner, rnd)
        self.schedule(t + (w + d) * H, "S08", ref_, {"ready": t, "start": t + w * H, "w": w, "d": d})

    def h_s08(self, t: float, ref_: str, p: dict) -> None:
        cn = self.cns[ref_]
        self.stage_row(cn, "S08", cn.owner, p["ready"], p["start"], t, p["w"], p["d"], doc_no=self.permit_no(cn.owner, cn))
        cn.physical_h += p["w"] + p["d"]
        cn.handoff_h += p["w"]
        rnd = self.rng(cn, 9)
        w, d = self.sample("S09", cn, t, cn.owner, rnd)
        self.schedule(t + (w + d) * H, "S09", ref_, {"ready": t, "start": t + w * H, "w": w, "d": d})

    def h_s09(self, t: float, ref_: str, p: dict) -> None:
        cn = self.cns[ref_]
        self.stage_row(cn, "S09", cn.owner, p["ready"], p["start"], t, p["w"], p["d"])
        cn.physical_h += p["w"] + p["d"]
        cn.handoff_h += p["w"]
        cn.gate_out_at, cn.status = t, "completed"
        self.emit("nsw.gate.out", t, cn.owner, cn.ref, "Gate-out", f"{cn.ref} left {cn.port} (dwell {(t - cn.arrived_at) / 86400:.1f} days).",
                  {"dwell_days": round((t - cn.arrived_at) / 86400, 2)})
        self.gone.add(ref_)

    # ------------------------------------------------------------------ scenario / injection helpers
    def inject(self, inc: Incident) -> None:
        """Real input to the engine: dashboards, alerts and chat react coherently."""
        self.cond.add_incident(inc)
        self.cond.clear_fx_cache()
        self.b["incident"].append((inc.incident_id, inc.kind, inc.entity, inc.port, inc.bank, clock.iso(inc.start), clock.iso(inc.end),
                                   json.dumps(inc.params), inc.source, inc.label))
        etype = {"scanner_outage": "ops.scanner.offline", "permit_backlog": "ops.congestion.alert",
                 "bank_delay": "ops.congestion.alert", "fx_shock": "ops.congestion.alert",
                 "duplicate_burst": "nsw.payment.duplicate", "late_remittance": "nsw.remittance.late"}.get(inc.kind, "ops.congestion.alert")
        self.emit(etype, max(inc.start, self.t), inc.entity or "NSW", inc.incident_id, inc.label or f"Incident: {inc.kind}",
                  f"Presenter-injected incident ({inc.kind}); engine inputs have been adjusted.", dict(inc.params), severity="high", kind="rule")
        if inc.kind == "scanner_outage":
            self.schedule(inc.end, "SCANEV", inc.incident_id, {"port": inc.port})
        if inc.kind == "duplicate_burst":
            n = int(inc.params.get("count", 14))
            for i in range(n):
                self.schedule(self.t + (i + 1) * 90, "FORCEDUP", inc.incident_id, {"i": i})
        if inc.kind == "late_remittance":
            self.inject_late_remittance(inc)

    def h_scan_event(self, t: float, ref_: str, p: dict) -> None:
        self.emit("ops.scanner.restored", t, "NCS", ref_, "Scanners restored", f"Scanner capacity at {p['port']} has been restored.", {"port": p["port"]})

    def h_fx_row(self, t: float, ref_: str, p) -> None:
        pass

    # ------------------------------------------------------------------ flush
    def flush(self, t_end: float) -> dict:
        """Write buffered facts atomically and advance the watermark to ``t_end``."""
        conn = self.conn
        b = self.b
        stats = {k: len(v) for k, v in b.items()}
        conn.execute("BEGIN")
        try:
            self._flush_tables(t_end)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        self.b = {k: [] for k in b}
        self.dirty.clear()
        self.gone.clear()
        self.ru_day.clear()
        self.ru_min.clear()
        return stats

    def _flush_tables(self, t_end: float) -> None:
        conn, b = self.conn, self.b
        e, l_ = self.ledger.take()
        Ledger.flush(conn, e, l_)
        conn.executemany("INSERT OR REPLACE INTO stage_events(event_id,nsw_ref,stage,system_type,owner_entity,ready_at,started_at,occurred_at,"
                         "wait_h,dur_h,sla_hours,sla_breach,status,data_complete,doc_no) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", b["stage"])
        conn.executemany("INSERT INTO fee_assessments(assessment_id,nsw_ref,entity_id,process_code,fee_code,basis,base_amount_minor,rate,"
                         "amount_minor,currency,fx_rate,amount_ngn_minor,origin_country,expected_amount_ngn_minor,occurred_at,mode,port,"
                         "commodity_group,hs_code,risk_lane) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", b["fees"])
        conn.executemany("INSERT OR REPLACE INTO payments(payment_id,payment_ref,nsw_ref,payer_id,channel,bank,amount_ngn_minor,status,is_duplicate,"
                         "initiated_at,occurred_at,origin_country,mode,port,commodity_group) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", b["pay"])
        conn.executemany("INSERT INTO payment_allocations(alloc_id,payment_id,assessment_id,nsw_ref,entity_id,process_code,fee_code,origin_country,"
                         "mode,port,commodity_group,currency,amount_ngn_minor,paid_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", b["alloc"])
        conn.executemany("UPDATE payment_allocations SET settlement_id=?, settled_at=?, collection_cost_ngn_minor=?, settled_ngn_minor=? "
                         "WHERE alloc_id=?", b["alloc_upd"])
        conn.executemany("INSERT OR REPLACE INTO settlement_batches(batch_id,bank,value_date,closed_at,completed_at,total_ngn_minor,lag_hours,"
                         "status,n_payments) VALUES(?,?,?,?,?,?,?,?,?)", b["batch"])
        conn.executemany("UPDATE settlement_batches SET completed_at=?, lag_hours=?, status='completed' WHERE batch_id=?", b["batch_upd"])
        conn.executemany("INSERT INTO settlements(settlement_id,batch_id,payment_id,entity_id,amount_ngn_minor,collection_cost_ngn_minor,occurred_at) "
                         "VALUES(?,?,?,?,?,?,?)", b["stl"])
        conn.executemany("INSERT OR REPLACE INTO remittances(remittance_id,entity_id,period,due_date,paid_at,amount_ngn_minor,status,destination,"
                         "days_late,occurred_at,base_ngn_minor,share) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", b["rem"])
        conn.executemany("UPDATE remittances SET paid_at=?, days_late=?, status='paid' WHERE remittance_id=?", b["rem_upd"])
        conn.executemany("INSERT INTO funding_receipts(receipt_id,entity_id,source_type,source_country,facility,amount_ngn_minor,occurred_at,source) "
                         "VALUES(?,?,?,?,?,?,?,?)", b["fund"])
        conn.executemany("INSERT INTO expenses(expense_id,entity_id,category,vendor,amount_ngn_minor,occurred_at,memo,source) VALUES(?,?,?,?,?,?,?,?)", b["exp"])
        conn.executemany("INSERT INTO refunds(refund_id,entity_id,fee_code,amount_ngn_minor,occurred_at,memo,source) VALUES(?,?,?,?,?,?,?)", b["refund"])
        conn.executemany("INSERT INTO live_events(event_id,occurred_at,type,severity,entity_id,subject,headline,description,payload_json,source_kind,run_id,speed) "
                         "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", b["live"])
        conn.executemany("INSERT OR REPLACE INTO fx_rates(ts_utc,ccy,rate_to_ngn,source) VALUES(?,?,?,?)", b["fx"])
        conn.executemany("INSERT OR REPLACE INTO incidents(incident_id,kind,entity_id,port,bank,start_at,end_at,params_json,source,label) "
                         "VALUES(?,?,?,?,?,?,?,?,?,?)", b["incident"])
        # consignments + inflight (dirty only)
        rows, infl, dele = [], [], []
        for ref_ in self.dirty:
            if ref_.startswith(PSEUDO):
                pend = self.pending.get(ref_)
                if pend:
                    infl.append(self._pseudo_row(ref_, pend))
                else:
                    dele.append((ref_,))
                continue
            cn = self.cns.get(ref_)
            if cn is None:
                continue
            rows.append(self._cn_row(cn))
            if ref_ in self.gone and not self.pending.get(ref_):
                dele.append((ref_,))
            else:
                pend = sorted(self.pending.get(ref_, []))
                cn.events = [[p[0], p[2], p[4]] for p in pend]
                infl.append((ref_, pend[0][2] if pend else "", clock.iso(pend[0][0]) if pend else None, cn.to_state()))
        conn.executemany("INSERT OR REPLACE INTO consignments(nsw_ref,mode,port,origin_country,commodity_group,hs_code,cif_value_minor,cif_ccy,fx_rate,"
                         "cif_value_ngn_minor,importer_id,agent_id,carrier,bank,rotation_no,form_m,paar,declaration_no,bl_no,container_no,vessel_imo,"
                         "weight_kg,teu,risk_lane,manifested_at,arrived_at,declared_at,released_at,gate_out_at,status,source,dwell_h,clearance_h,"
                         "exit_h,digital_h,physical_h,handoff_h) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        conn.executemany("INSERT OR REPLACE INTO inflight(nsw_ref,next_stage,next_event_at,state_json) VALUES(?,?,?,?)", infl)
        conn.executemany("DELETE FROM inflight WHERE nsw_ref=?", dele)
        for ref_ in list(self.gone):
            if not self.pending.get(ref_):
                self.cns.pop(ref_, None)
                self.pay_allocs_cleanup(ref_)
        # rollups
        conn.executemany("INSERT INTO rollup_day(entity_id,day,metric,dim,dim_value,value) VALUES(?,?,?,?,?,?) "
                         "ON CONFLICT(entity_id,day,metric,dim,dim_value) DO UPDATE SET value=value+excluded.value",
                         [(*k, v) for k, v in self.ru_day.items()])
        conn.executemany("INSERT INTO rollup_minute(entity_id,minute,metric,value) VALUES(?,?,?,?) "
                         "ON CONFLICT(entity_id,minute,metric) DO UPDATE SET value=value+excluded.value",
                         [(*k, v) for k, v in self.ru_min.items()])
        self.ids.persist(conn)
        db.kv_set(conn, "watermark_utc", clock.iso(t_end))
        db.kv_set(conn, "jobs_day", self.jobs_day or "")
        db.kv_set(conn, "minute_cursor", str(self.minute_cursor))
        self._persist_money_state()

    def _cn_row(self, cn: Cn) -> tuple:
        iso = clock.iso
        done = cn.gate_out_at is not None
        dwell = (cn.gate_out_at - cn.arrived_at) / H if done else None
        clearance = (cn.released_at - cn.declared_at) / H if cn.released_at and cn.declared_at else None
        exit_h = (cn.gate_out_at - cn.released_at) / H if done and cn.released_at else None
        return (cn.ref, cn.mode, cn.port, cn.origin_country, cn.commodity_group, cn.hs_code, cn.cif_minor, cn.cif_ccy, cn.fx, cn.cif_ngn_minor,
                cn.importer, cn.agent, cn.carrier, cn.bank, cn.rotation_no, cn.form_m, cn.paar, cn.declaration_no, cn.bl_no, cn.container_no,
                cn.vessel_imo, cn.weight_kg, cn.teu, cn.lane, iso(cn.manifested_at), iso(cn.arrived_at) if cn.arrived_at else None,
                iso(cn.declared_at) if cn.declared_at else None, iso(cn.released_at) if cn.released_at else None,
                iso(cn.gate_out_at) if done else None, cn.status, cn.source, dwell, clearance, exit_h,
                cn.digital_h if done else None, cn.physical_h if done else None, cn.handoff_h if done else None)


def fb_money(minor: int) -> str:
    from nsw_sim.money import fmt_ngn
    return fmt_ngn(minor)
