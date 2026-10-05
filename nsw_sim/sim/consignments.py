"""Consignment attributes and the per-minute arrival sampler."""
from __future__ import annotations

import bisect
import itertools
import json
import math
import random
from dataclasses import dataclass, field

from nsw_sim.config import settings
from nsw_sim.llm.schemas import FlowPlan
from nsw_sim.sim import ids
from nsw_sim.sim.reference import ref


@dataclass
class Cn:
    ref: str
    n: int
    mode: str
    port: str
    origin_country: str
    commodity_group: str
    hs_code: str
    cif_ccy: str
    cif_minor: int
    fx: float
    cif_ngn_minor: int
    importer: str
    agent: str
    carrier: str
    bank: str
    rotation_no: str
    form_m: str
    paar: str
    bl_no: str
    container_no: str | None
    vessel_imo: str | None
    weight_kg: float
    teu: float
    lane: str
    manifested_at: float
    eta: float
    seed: int
    permits: list = field(default_factory=list)
    owner: str = "NPA"
    declaration_no: str | None = None
    arrived_at: float | None = None
    declared_at: float | None = None
    released_at: float | None = None
    gate_out_at: float | None = None
    status: str = "manifested"
    source: str = "fallback"
    pending: list = field(default_factory=list)       # assessments awaiting payment (dicts)
    ready: float = 0.0
    digital_h: float = 0.0
    physical_h: float = 0.0
    handoff_h: float = 0.0
    s02_left: int = 0
    s02_first_ready: float = 0.0
    s02_wait: float = 0.0
    s02_dur: float = 0.0
    pay_init: float = 0.0
    pay_tries: int = 0
    s05_ready: float = 0.0
    s05_first_init: float = 0.0
    permit_rows: list = field(default_factory=list)
    fa_k: int = 0
    n_stage: int = 0
    events: list = field(default_factory=list)

    @property
    def cif_ngn_major(self) -> float:
        return self.cif_ngn_minor / 100.0

    @property
    def yyyymm(self) -> str:
        return self.ref.split("-")[1]

    def to_state(self) -> str:
        d = {k: v for k, v in self.__dict__.items()}
        return json.dumps(d, separators=(",", ":"))

    @staticmethod
    def from_state(s: str) -> Cn:
        return Cn(**json.loads(s))


class PlanContext:
    """Cumulative-weight samplers derived from one weekly FlowPlan."""

    def __init__(self, plan: FlowPlan) -> None:
        cfg = ref()
        self.plan = plan
        self.origins = list(plan.origin_share)
        self.groups = list(cfg["commodity_groups"])
        aff = cfg["country_commodity_affinity"]
        self.comm_by_origin: dict[str, tuple[list[str], list[float]]] = {}
        for o in self.origins:
            w = [plan.commodity_share.get(g, 0.0) * aff.get(o, {}).get(g, 1.0) for g in self.groups]
            self.comm_by_origin[o] = (self.groups, list(itertools.accumulate(w)))
        self.origin_cum = list(itertools.accumulate(plan.origin_share[o] for o in self.origins))
        base_air = sum(cfg["commodity_groups"][g]["share"] * cfg["commodity_groups"][g]["air_share"] for g in self.groups)
        self.air_scale = plan.mode_share.get("air", 0.2) / max(base_air, 1e-9)
        sea = [(p, plan.port_share.get(p, 0.0)) for p in cfg["ports"]["sea"]]
        air = [(p, plan.port_share.get(p, 0.0)) for p in cfg["ports"]["air"]]
        self.sea_ports = ([p for p, _ in sea], list(itertools.accumulate(w for _, w in sea)))
        self.air_ports = ([p for p, _ in air], list(itertools.accumulate(w for _, w in air)))
        self.hourly = plan.hourly_shape
        self.hourly_sum = sum(plan.hourly_shape)
        self.value_mult = plan.value_multiplier


def _pick(rnd: random.Random, items: list, cum: list[float]):
    return items[min(bisect.bisect_left(cum, rnd.random() * cum[-1]), len(items) - 1)]


class Sampler:
    """Builds consignments deterministically from (seed, plan, profiles)."""

    def __init__(self, profiles: dict, id_factory: ids.IdFactory, party_rows: list[tuple]) -> None:
        cfg = ref()
        self.cfg = cfg
        self.profiles = profiles
        self.ids = id_factory
        traders = [r for r in party_rows if r[1] == "trader"]
        w = {"large": 5.0, "medium": 2.0, "small": 1.0}
        self.traders = [r[0] for r in traders]
        self.trader_cum = list(itertools.accumulate(w[r[5]] for r in traders))
        self.agents = [r[0] for r in party_rows if r[1] == "agent"]
        self.carriers_sea = [f"SHP-{s['code']}" for s in cfg["shipping_lines"]]
        self.scac = {f"SHP-{s['code']}": s["code"] for s in cfg["shipping_lines"]}
        self.carriers_air = [f"AIR-{a['code']}" for a in cfg["airlines"]]
        self.banks = list(cfg["banks"])
        self.bank_cum = list(itertools.accumulate(v["share"] for v in cfg["banks"].values()))
        self.vessels = [ids.make_imo(900000 + i * 137) for i in range(80)]
        s = settings()
        self.value_scale = float(s["sim"].get("value_scale", 1.0))
        self.lane_p = s["sim"]["lane_probs"]
        self.lead = s["sim"]["manifest_lead_hours"]
        self.risk_uplift = {"Vehicles & parts": 1.2, "Chemicals": 1.2, "Electronics": 1.15, "Petroleum & lubricants": 1.3,
                            "Pharma & cosmetics": 1.1}
        self.permit_rules = [(ent, r) for ent, p in profiles.items() for r in p.fee_rules if r.permit]

    def make(self, t: float, seed: int, ctx: PlanContext, cond, source: str) -> Cn:
        rnd = random.Random(seed)
        cfg = self.cfg
        origin = _pick(rnd, ctx.origins, ctx.origin_cum)
        groups, cum = ctx.comm_by_origin[origin]
        group = _pick(rnd, groups, cum)
        gcfg = cfg["commodity_groups"][group]
        mode = "air" if rnd.random() < min(0.9, gcfg["air_share"] * ctx.air_scale) else "sea"
        ports, pcum = (ctx.air_ports if mode == "air" else ctx.sea_ports)
        port = _pick(rnd, ports, pcum)
        ccy = cfg["countries"][origin]["currency"]
        usd = rnd.lognormvariate(math.log(gcfg["cif_usd_median"] * self.value_scale * ctx.value_mult), gcfg["cif_sigma"])
        fx = cond.fx(ccy, t)
        usd_ngn = cond.fx("USD", t)
        cif_ngn_minor = int(round(usd * usd_ngn * 100))
        cif_minor = int(round(usd / (fx / usd_ngn) * 100))       # in the invoice currency
        weight = max(50.0, rnd.lognormvariate(math.log(gcfg["weight_kg_median"] * (0.12 if mode == "air" else 1.0)), 0.6))
        teu = 0.0 if mode == "air" else float(max(1, round(weight / 16000)))
        n, nsw_ref = self.ids.next_consignment(clock_yyyymm(t), port)
        year = clock_year(t)
        # risk lane: NCS country sensitivity + commodity uplift
        sens = self.profiles["NCS"].country_sensitivity.get(origin)
        mod = (sens.inspection_rate_modifier if sens else 1.0) * self.risk_uplift.get(group, 1.0)
        lp = self.lane_p
        p_red = min(0.6, lp["red"] * mod)
        p_yel = min(0.4, lp["yellow"] * mod ** 0.5)
        p_blue = lp["blue"]
        u = rnd.random()
        lane = "red" if u < p_red else "yellow" if u < p_red + p_yel else "blue" if u < p_red + p_yel + p_blue else "green"
        # permits
        permits = []
        for ent, rule in self.permit_rules:
            if rnd.random() < (rule.p_by_group or {}).get(group, 0.0):
                permits.append(ent)
        importer = _pick(rnd, self.traders, self.trader_cum)
        idx = int(importer[3:]) if importer[3:].isdigit() else 0
        agent = self.agents[(idx * 7 + (rnd.randrange(3) if rnd.random() < 0.3 else 0)) % len(self.agents)]
        bank = _pick(rnd, self.banks, self.bank_cum)
        if mode == "sea":
            carrier = rnd.choice(self.carriers_sea)
            scac = self.scac[carrier]
            bl = f"{scac}{n:010d}"
            container = ids.make_container(scac, n % 1_000_000)
            imo = rnd.choice(self.vessels)
        else:
            carrier = rnd.choice(self.carriers_air)
            bl = f"{carrier[4:]}-{n:08d}"
            container, imo = None, None
        lo, hi = self.lead["air" if mode == "air" else "sea"]
        eta = t + rnd.uniform(lo, hi) * 3600
        return Cn(ref=nsw_ref, n=n, mode=mode, port=port, origin_country=origin, commodity_group=group,
                  hs_code=rnd.choice(gcfg["hs"]), cif_ccy=ccy, cif_minor=cif_minor, fx=fx, cif_ngn_minor=cif_ngn_minor,
                  importer=importer, agent=agent, carrier=carrier, bank=bank, rotation_no=f"ROT/{year}/{n:06d}",
                  form_m=f"MF{year}{n:07d}", paar=f"PAAR{year}{n:08d}", bl_no=bl, container_no=container, vessel_imo=imo,
                  weight_kg=round(weight, 1), teu=teu, lane=lane, manifested_at=t, eta=eta, seed=seed, permits=permits,
                  owner="FAAN" if mode == "air" else "NPA", source=source)


def clock_yyyymm(t: float) -> str:
    from nsw_sim import clock
    return clock.wat(t).strftime("%Y%m")


def clock_year(t: float) -> str:
    from nsw_sim import clock
    return clock.wat(t).strftime("%Y")
