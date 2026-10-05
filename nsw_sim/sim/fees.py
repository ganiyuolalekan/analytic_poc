"""Fee rule matching and assessment. ``applies_if`` is a structured condition evaluated by a safe matcher (no eval)."""
from __future__ import annotations

import random
from dataclasses import dataclass

from nsw_sim.llm.schemas import EntityProfile, FeeRule


def matches(applies_if: dict, cn) -> bool:
    """Structured condition: every key must match; list values mean 'any of'; ``min_cif_ngn`` is a floor."""
    for k, want in applies_if.items():
        if k == "min_cif_ngn":
            if cn.cif_ngn_major < float(want):
                return False
            continue
        if k == "direction":
            have = "import"
        else:
            have = getattr(cn, k, None)
        if isinstance(want, (list, tuple, set)):
            if have not in want:
                return False
        elif have != want:
            return False
    return True


@dataclass
class Assessment:
    entity: str
    fee_code: str
    process: str
    basis: str
    base_minor: int
    rate: float
    amount_minor: int           # in `currency` minor units
    currency: str
    fx: float
    amount_ngn: int
    expected_ngn: int
    account: str
    stage: str


def raw_amount_major(rule: FeeRule, cn, duty_ngn_major: float) -> tuple[float, float, float]:
    """(amount in fee currency major units, base amount (ngn/units), effective rate)."""
    b = rule.basis
    if b == "ad_valorem":
        base = cn.cif_ngn_major + (duty_ngn_major if rule.base == "cif_plus_duty" else 0.0)
        rate = (rule.rate_by_group or {}).get(cn.commodity_group, rule.rate) if rule.rate_by_group else rule.rate
        amt = base * rate
        return amt, base, rate
    if b == "per_tonne":
        units = cn.weight_kg / 1000.0
    elif b == "per_kg":
        units = cn.weight_kg
    elif b == "per_teu":
        units = max(cn.teu, 1.0)
    else:
        units = 1.0
    return rule.amount * units, units, rule.amount


def applicable(rule: FeeRule, cn, rnd: random.Random, permit_entities: set[str], entity: str, insp_mod: float) -> bool:
    """Deterministic given the consignment's RNG stream."""
    if rule.requires_permit_entity and entity not in permit_entities:
        return False
    if rule.permit:
        return entity in permit_entities
    if not matches(rule.applies_if, cn):
        return False
    p = rule.p
    if rule.p_by_group is not None:
        p = rule.p_by_group.get(cn.commodity_group, 0.0)
    if "INSP" in rule.fee_code:
        p = min(1.0, p * insp_mod)
    return p >= 1.0 or rnd.random() < p


def assess_fees(cn, stage: str, profiles: dict[str, EntityProfile], cond, t: float, rnd: random.Random,
                permit_entities: set[str], entities: list[str] | None = None, duty_ngn_major: float = 0.0,
                noise: dict | None = None) -> list[Assessment]:
    """All fee assessments of ``stage`` for consignment ``cn`` at time ``t`` (NCS first so VAT sees the duty)."""
    out: list[Assessment] = []
    noise = noise or {"prob": 0.015, "sd": 0.03}
    for ent in (entities or sorted(profiles, key=lambda e: (e != "NCS", e))):
        prof = profiles[ent]
        sens = prof.country_sensitivity.get(cn.origin_country)
        for rule in prof.fee_rules:
            if rule.stage != stage:
                continue
            insp_mod = sens.inspection_rate_modifier if sens else 1.0
            if not applicable(rule, cn, rnd, permit_entities, ent, insp_mod):
                continue
            amt, base, rate = raw_amount_major(rule, cn, duty_ngn_major)
            if rule.country_sensitive and sens:
                amt *= sens.fee_multiplier
            if rule.min_amount is not None:
                amt = max(amt, rule.min_amount)
            if rule.max_amount is not None:
                amt = min(amt, rule.max_amount)
            fx = 1.0 if rule.currency == "NGN" else cond.fx(rule.currency, t)
            expected_ngn = int(round(amt * fx * 100))
            dev = 0.0
            if rnd.random() < noise["prob"]:
                dev = rnd.gauss(0.0, noise["sd"])
            dev -= cond.underassessment(ent, rule.fee_code, cn.commodity_group, cn.origin_country, t) * (1 + rnd.uniform(-0.1, 0.1))
            amt_assessed = max(0.0, amt * (1 + dev))
            amount_minor = int(round(amt_assessed * 100))
            amount_ngn = int(round(amount_minor * fx))
            if amount_ngn <= 0:
                continue
            out.append(Assessment(ent, rule.fee_code, rule.process_code or "", rule.basis, int(round(base * 100)) if rule.basis == "ad_valorem" else int(round(base * 100)),
                                  rate, amount_minor, rule.currency, fx, amount_ngn, expected_ngn, rule.revenue_account or "4100", stage))
            if rule.fee_code == "NCS-DUTY" or rule.fee_code == "NCS-LEVY":
                duty_ngn_major += amount_ngn / 100.0
    return out
