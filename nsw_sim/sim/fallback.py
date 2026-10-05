"""Deterministic fallbacks (Appendix C): complete profiles, flow plans, ops plans, directives and feed items.

The app MUST work offline, so everything the LLM roles produce has a deterministic equivalent here.
All fee names, rates, IDs and rules are SIMULATED approximations."""
from __future__ import annotations

import random
from datetime import date, timedelta
from functools import cache

from nsw_sim.config import settings
from nsw_sim.llm.schemas import (
    CountrySens,
    Directives,
    DirectorOutput,
    EntityProfile,
    ExpenseCategory,
    ExpenseStructure,
    FeedItem,
    FeeRule,
    FlowPlan,
    Onboarding,
    OpsPlan,
    PartnerFunding,
    RemittanceRule,
)
from nsw_sim.sim.reference import entity_cards, ref

GROUPS = ["Food & agro", "Pharma & cosmetics", "Electronics", "Machinery & parts", "Vehicles & parts", "Chemicals",
          "Textiles", "Building materials", "Consumer goods", "Petroleum & lubricants"]
DUTY_RATES = {"Food & agro": 0.10, "Electronics": 0.05, "Vehicles & parts": 0.20, "Chemicals": 0.07, "Textiles": 0.20,
              "Machinery & parts": 0.05, "Building materials": 0.10, "Consumer goods": 0.15, "Pharma & cosmetics": 0.05,
              "Petroleum & lubricants": 0.05}
IMP = {"direction": "import"}


def _r(code, name, proc, basis="flat", **kw) -> dict:
    return {"fee_code": code, "name": name + " (simulated)", "process_code": proc, "basis": basis, **kw}


def _permit_p(agency: str) -> dict[str, float]:
    return {g: v["permits"][agency] for g, v in ref()["commodity_groups"].items() if agency in v.get("permits", {})}


def _rules(code: str) -> list[dict]:
    """Baseline fee rules per entity. Rates are tuned by scripts/calibrate.py against the monthly bands."""
    return {
        "NCS": [
            _r("NCS-DUTY", "Import duty", "VAL", "ad_valorem", rate=0.0, rate_by_group=DUTY_RATES, applies_if=IMP,
               country_sensitive=True, stage="S04"),
            _r("NCS-LEVY", "Levies and surcharges", "DECL", "ad_valorem", rate=0.060, applies_if=IMP,
               country_sensitive=True, stage="S04"),
            _r("NCS-EXAM", "Examination fee", "EXAM", "flat", amount=95000, applies_if={"risk_lane": ["yellow", "red"]},
               stage="S04"),
            _r("NCS-PENALTY", "Penalty and fine", "ENF", "ad_valorem", rate=0.02, p=0.015, applies_if=IMP, stage="S04"),
        ],
        "NRS": [
            _r("NRS-VAT", "VAT on imports", "VAT", "ad_valorem", base="cif_plus_duty", rate=0.075, applies_if=IMP,
               stage="S04"),
            _r("NRS-WHT", "Withholding tax on trade services", "WHT", "ad_valorem", rate=0.002, applies_if=IMP,
               stage="S04"),
            _r("NRS-TCC", "Trader tax-compliance clearance fee", "TCC", "flat", amount=28000, p=0.35, applies_if=IMP,
               stage="S03"),
        ],
        "NPA": [
            _r("NPA-WHARF", "Wharfage", "WHARF", "per_tonne", amount=3000, applies_if={"mode": "sea"}, stage="S04"),
            _r("NPA-PORTDUES", "Port dues", "BERTH", "per_teu", amount=250, currency="USD", applies_if={"mode": "sea"},
               country_sensitive=True, stage="S04"),
            _r("NPA-PILOT", "Pilotage", "PILOT", "flat", amount=55000, applies_if={"mode": "sea"}, stage="S04"),
            _r("NPA-GATE", "Gate and truck call-up", "GATE", "flat", amount=40000, applies_if={"mode": "sea"}, stage="S04"),
            _r("NPA-CONC", "Terminal concession charge", "CONC", "ad_valorem", rate=0.001, applies_if={"mode": "sea"},
               stage="S04"),
        ],
        "NIMASA": [
            _r("NIMASA-LEVY", "Cabotage and vessel levy", "CAB", "flat", amount=80, currency="USD",
               applies_if={"mode": "sea"}, stage="S01"),
            _r("NIMASA-CERT", "Certification fee", "REG", "flat", amount=55000, p=0.15, applies_if={"mode": "sea"},
               stage="S01"),
        ],
        "SON": [
            _r("SON-COC", "Conformity certificate", "CoC", "ad_valorem", rate=0.0058, min_amount=55000, max_amount=800000,
               p_by_group=_permit_p("SON"), permit=True, country_sensitive=True, applies_if=IMP, stage="S02"),
            _r("SON-INSP", "Inspection fee", "INSP", "flat", amount=50000, p=0.45, requires_permit_entity=True, stage="S02"),
            _r("SON-LAB", "Laboratory testing fee", "LAB", "flat", amount=85000, p=0.15, requires_permit_entity=True,
               stage="S02"),
        ],
        "NAFDAC": [
            _r("NAFDAC-PERMIT", "Import permit fee", "PERMIT", "flat", amount=235000, p_by_group=_permit_p("NAFDAC"),
               permit=True, applies_if=IMP, stage="S02"),
            _r("NAFDAC-INSP", "Border inspection fee", "INSP", "flat", amount=210000, p=0.5, requires_permit_entity=True,
               stage="S02"),
            _r("NAFDAC-REG", "Product registration fee", "REGP", "flat", amount=400000, p=0.08, requires_permit_entity=True,
               stage="S02"),
        ],
        "NAQS": [
            _r("NAQS-PHYTO", "Phytosanitary inspection fee", "PHYTO", "flat", amount=300000, p_by_group=_permit_p("NAQS"),
               permit=True, applies_if=IMP, stage="S02"),
            _r("NAQS-QPERMIT", "Quarantine permit fee", "QPERMIT", "flat", amount=230000, p=0.8, requires_permit_entity=True,
               stage="S02"),
        ],
        "NESREA": [
            _r("NESREA-ENVP", "Environmental compliance permit", "ENVP", "flat", amount=85000,
               p_by_group=_permit_p("NESREA"), permit=True, applies_if=IMP, stage="S02"),
            _r("NESREA-HAZ", "Hazardous goods screening fee", "HAZ", "flat", amount=55000, p=0.3,
               requires_permit_entity=True, stage="S02"),
        ],
        "FAAN": [
            _r("FAAN-ACH", "Air cargo handling", "ACH", "per_kg", amount=235, applies_if={"mode": "air"}, stage="S04"),
            _r("FAAN-LAND", "Landing and parking allocation", "LAND", "flat", amount=160000, applies_if={"mode": "air"},
               stage="S04"),
            _r("FAAN-CTC", "Cargo terminal concession charge", "CTC", "ad_valorem", rate=0.005, applies_if={"mode": "air"},
               stage="S04"),
        ],
        "NSW": [
            _r("NSW-TXN", "Platform transaction fee", "TXN", "flat", amount=38000, applies_if=IMP, stage="S03"),
            _r("NSW-PERMITFEE", "Platform permit-processing fee", "TXN", "flat", amount=11000, p=0.55, applies_if=IMP,
               stage="S03"),
        ],
        "CBN": [], "MOF-FA": [],
    }[code]


# (opex_ratio, appropriation_coverage, categories shares P/O/I/M, collection_cost_rate, remittance, mid-target ngn bn, opening cash months)
_ECON = {
    "NCS": (0.050, 0.00, (0.52, 0.20, 0.13, 0.15), 0.0060, RemittanceRule(basis="net_settled", share=0.95), 46.5),
    "NRS": (0.046, 0.00, (0.55, 0.12, 0.20, 0.13), 0.0055, RemittanceRule(basis="net_settled", share=0.96), 25.0),
    "NPA": (0.600, 0.00, (0.30, 0.30, 0.10, 0.30), 0.0080, RemittanceRule(basis="surplus", share=0.50, frequency="quarterly"), 7.5),
    "NIMASA": (0.560, 0.20, (0.50, 0.25, 0.15, 0.10), 0.0080, RemittanceRule(basis="net_settled", share=0.30), 1.4),
    "SON": (0.580, 0.20, (0.45, 0.20, 0.15, 0.20), 0.0080, RemittanceRule(basis="net_settled", share=0.30), 1.1),
    "NAFDAC": (0.600, 0.20, (0.42, 0.25, 0.13, 0.20), 0.0080, RemittanceRule(basis="net_settled", share=0.30), 1.25),
    "NAQS": (0.620, 0.25, (0.45, 0.30, 0.10, 0.15), 0.0085, RemittanceRule(basis="net_settled", share=0.35), 0.45),
    "NESREA": (0.640, 0.30, (0.50, 0.30, 0.08, 0.12), 0.0085, RemittanceRule(basis="net_settled", share=0.30), 0.2),
    "FAAN": (0.620, 0.10, (0.35, 0.30, 0.10, 0.25), 0.0080, RemittanceRule(basis="surplus", share=0.50, frequency="quarterly"), 1.7),
    "NSW": (0.700, 0.30, (0.28, 0.12, 0.45, 0.15), 0.0050, RemittanceRule(basis="none", share=0.0, frequency="none"), 0.6),
    "CBN": (0.0, 0.0, (0.5, 0.2, 0.2, 0.1), 0.0, RemittanceRule(basis="none", share=0.0, frequency="none"), 0.0),
    "MOF-FA": (0.0, 0.0, (0.5, 0.2, 0.2, 0.1), 0.0, RemittanceRule(basis="none", share=0.0, frequency="none"), 0.0),
}
_PARTNERS = {
    "NCS": ("Scanner and Risk Management Programme (Simulated)", "GB", "grant", 0.80e9, "Scanner capacity and risk profiling"),
    "NRS": ("Tax Administration Modernisation Facility (Simulated)", "NL", "grant", 0.20e9, "Digital assessment tools"),
    "NPA": ("Port Modernisation Facility (Simulated)", "JP", "concessional_loan", 1.20e9, "Terminal equipment and gate systems"),
    "NIMASA": ("Maritime Safety Programme (Simulated)", "DE", "grant", 0.20e9, "Safety inspection capacity"),
    "SON": ("Standards Capacity Programme (Simulated)", "NL", "grant", 0.20e9, "Laboratory accreditation"),
    "NAFDAC": ("Laboratory Capacity Grant (Simulated)", "DE", "grant", 0.25e9, "Border laboratory equipment"),
    "NAQS": ("Plant Health Facilitation Grant (Simulated)", "FR", "grant", 0.10e9, "Phytosanitary tools"),
    "NESREA": ("Environmental Screening Support (Simulated)", "GB", "grant", 0.08e9, "Hazard screening kits"),
    "FAAN": ("Airport Cargo Facilitation Programme (Simulated)", "KR", "concessional_loan", 0.60e9, "Cargo terminal systems"),
    "NSW": ("Digital Trade Facilitation Grant (Simulated)", "US", "grant", 0.30e9, "Platform integration and onboarding"),
}
_QUIRKS = {
    "NCS": ["Examination wait is sensitive to scanner availability", "Valuation checks concentrate on higher-risk origins",
            "Release is processed in office hours on most days"],
    "NRS": ["VAT assessment follows customs valuation with a short lag", "Trader clearance checks are cached per trader"],
    "NPA": ["Gate call-up queues lengthen when berth occupancy is high", "Port dues follow vessel call patterns by origin"],
    "NIMASA": ["Levy assessment is triggered when the manifest is accepted", "Certification requests arrive in batches"],
    "SON": ["Conformity checks cluster around electronics and building materials", "Laboratory tests extend processing times"],
    "NAFDAC": ["Permit approvals slow when queries are raised on registration data", "Inspection intensity varies by origin"],
    "NAQS": ["Phytosanitary inspections depend on field officer availability", "Agro consignments are inspected more often"],
    "NESREA": ["Permit demand is concentrated in chemicals and petroleum", "Screening kits are re-supplied quarterly"],
    "FAAN": ["Air cargo handling peaks in the early evening", "Stage timestamps depend on terminal integrations"],
    "NSW": ["Platform fees are charged once per declaration and per permit", "Onboarding progress differs by agency"],
}


def _seeded(*parts) -> random.Random:
    return random.Random("|".join(str(p) for p in parts))


def fallback_country_sensitivity(code: str) -> dict[str, CountrySens]:
    out = {}
    for iso2, c in ref()["countries"].items():
        r = _seeded("cs", code, iso2)
        tier = c["risk_tier"]
        jitter = lambda s: 1 + r.uniform(-s, s)  # noqa: E731
        fee = {"NCS": 1 + 0.035 * (tier - 1), "NPA": 1 + (0.03 if c["region"] == "Asia" else 0.0), "SON": 1.0 + 0.02 * tier}.get(code, 1.0)
        insp = {1: 0.8, 2: 1.1, 3: 1.5}[tier] * (1.15 if code in ("NAFDAC", "NAQS") and c["region"] in ("Asia", "Africa") else 1)
        out[iso2] = CountrySens(
            fee_multiplier=round(fee * jitter(0.02), 4), inspection_rate_modifier=round(insp * jitter(0.08), 3),
            doc_issue_rate=round(c["doc_issue_rate"] * jitter(0.12), 4),
            dwell_modifier=round((1 + 0.05 * (tier - 1) + (-0.1 if c["ecowas"] else 0.0)) * jitter(0.04), 3))
    return out


@cache
def fallback_profile(code: str) -> EntityProfile:
    card = entity_cards()[code]
    s = settings()
    opex_ratio, cover, shares, cc, remit, mid = _ECON[code]
    cats = [ExpenseCategory(name=n, share=sh, monthly_growth=0.002) for n, sh in zip(
        ("Personnel", "Operations", "ICT", "Maintenance"), shares)]
    pf = []
    if code in _PARTNERS:
        f, pc, ins, amt, purp = _PARTNERS[code]
        pf = [PartnerFunding(facility_name=f, partner_country=pc, instrument=ins, amount_ngn_per_quarter=amt, purpose=purp)]
    lo, hi = s["sim"]["onboarding_digital_share"].get(code, [0.95, 0.98])
    sla = {"S02_permit": s["sla_hours"]["S02_permit"], "S04_assessment": s["sla_hours"]["S04_assessment"],
           "S05_payment": s["sla_hours"]["S05_payment"], "S06_exam": s["sla_hours"]["S06_exam"],
           "S07_release": s["sla_hours"]["S07_release"], "S09_evacuation": s["sla_hours"]["S09_evacuation"]}
    r = _seeded("profile", code)
    return EntityProfile(
        entity=code, processes=card.get("processes", {}),
        fee_rules=[FeeRule(**x) for x in _rules(code)],
        country_sensitivity=fallback_country_sensitivity(code), partner_funding=pf,
        expense_structure=ExpenseStructure(opex_ratio=opex_ratio, categories=cats, appropriation_coverage=cover),
        collection_cost_rate=cc, remittance=remit, sla_hours=sla,
        processing_speed_factor=round(0.95 + r.random() * 0.15, 3),
        onboarding=Onboarding(digital_share_start=lo, digital_share_end=hi, completeness=0.975 + r.random() * 0.02),
        seasonality={str(m): round(1 + 0.04 * (1 if m in (9, 10, 11, 12) else -0.5), 3) for m in range(1, 13)},
        quirks=_QUIRKS.get(code, []))


def target_mid_ngn(code: str) -> float:
    """Monthly NSW-channel collections target midpoint (₦) used to scale opex and funding."""
    t = settings()["monthly_targets_ngn_bn"].get(code)
    return (t[0] + t[1]) / 2 * 1e9 if t else 0.0


# ----------------------------------------------------------------------------- flow plan
def fallback_fx_path(week_start: date, prev: float | None = None) -> list[float]:
    """Gentle deterministic daily USD/NGN random walk inside [1470, 1545] (beat B4 is added by the engine)."""
    fx = settings()["fx_usd_ngn"]
    base = prev or (fx["start"] + 0.35 * (week_start - date(week_start.year, 6, 1)).days)
    out = []
    for d in range(7):
        r = _seeded("fx", week_start + timedelta(days=d))
        base = base * (1 + r.uniform(-1, 1) * fx["daily_vol"] * 0.6)
        base = min(max(base, 1470.0), 1545.0)
        out.append(round(base, 2))
    return out


def fallback_flow_plan(week_start: date) -> FlowPlan:
    cfg = ref()
    r = _seeded("flow", week_start)
    mult = [round(max(0.5, min(1.5, 1 + r.uniform(-0.12, 0.12))), 3) for _ in range(7)]
    origin = {k: v["weight"] for k, v in cfg["countries"].items()}
    tot = sum(origin.values())
    commodity = {g: v["share"] for g, v in cfg["commodity_groups"].items()}
    ports = {**{k: v["share"] for k, v in cfg["ports"]["sea"].items()}, **{k: v["share"] for k, v in cfg["ports"]["air"].items()}}
    # port_share is per-mode in the engine; stored combined and renormalised per mode
    sea = {k: v["share"] for k, v in cfg["ports"]["sea"].items()}
    air = {k: v["share"] for k, v in cfg["ports"]["air"].items()}
    del ports
    return FlowPlan(week_start=week_start.isoformat(), daily_multiplier=mult,
                    port_share={**{k: v / sum(sea.values()) for k, v in sea.items()},
                                **{k: v / sum(air.values()) for k, v in air.items()}},
                    mode_share={"sea": settings()["sea_share"], "air": 1 - settings()["sea_share"]},
                    origin_share={k: v / tot for k, v in origin.items()}, commodity_share=commodity,
                    hourly_shape=list(settings()["sim"]["hourly_shape"]), fx_path=fallback_fx_path(week_start),
                    value_multiplier=round(1 + r.uniform(-0.05, 0.05), 3), expected_incidents=[],
                    narrative="Baseline weekly flow (deterministic fallback).")


# ----------------------------------------------------------------------------- ops plan
def fallback_ops_plan(code: str, week_start: date) -> OpsPlan:
    prof = fallback_profile(code)
    r = _seeded("ops", code, week_start)
    cats = [c.name for c in (prof.expense_structure.categories if prof.expense_structure else [])]
    mult = {c: round(1 + r.uniform(-0.12, 0.15), 3) for c in cats}
    appr = [{"day": 1, "amount_factor": 1.0}] if (prof.expense_structure and prof.expense_structure.appropriation_coverage > 0) else []
    receipts = []
    if prof.partner_funding and week_start.day <= 7 and week_start.month in (1, 4, 7, 10):
        pf = prof.partner_funding[0]
        receipts = [{"day": 2, "facility": pf.facility_name, "partner_country": pf.partner_country, "amount_factor": 1.0}]
    return OpsPlan.model_validate({
        "entity": code, "week_start": week_start.isoformat(), "appropriation_releases": appr, "partner_receipts": receipts,
        "expense_multipliers": mult, "expense_day_weights": [1, 1, 1, 1, 1, 0.2, 0.0],
        "refunds": ([{"day": r.randrange(0, 5), "fee_code": prof.fee_rules[0].fee_code, "amount_factor": round(r.uniform(0.3, 1.0), 2),
                      "memo": "Assessment adjustment after trader query"}] if prof.fee_rules and r.random() < 0.35 else []),
        "remittance_delay_days": 0,
        "dq_incidents": ([{"day": r.randrange(0, 5), "kind": "orphan_payment", "count": 1}] if r.random() < 0.4 else []),
        "note": "Baseline weekly operations (deterministic fallback)."})


# ----------------------------------------------------------------------------- live director
_FEED_TEMPLATES = [
    ("nsw.payment.confirmed", "info", "NCS", "Collections steady through the Single Window",
     "Payment confirmations are flowing at the expected pace across participating banks."),
    ("ops.congestion.alert", "low", "NPA", "Gate call-up queue lengthening at Apapa",
     "Truck evacuation waits are above the hourly average; terminal teams are re-sequencing call-ups."),
    ("nsw.permit.approved", "info", "SON", "Conformity approvals clearing within service levels",
     "Permit approvals are being issued inside the standard processing window."),
    ("nsw.exam.completed", "info", "NCS", "Examination throughput on plan",
     "Scanner and physical examination queues are moving at the expected rate."),
    ("nsw.settlement.batch_closed", "info", "CBN", "Settlement batch closed on schedule",
     "The daily settlement batch has closed and is awaiting bank completion."),
    ("nsw.release.granted", "info", "NCS", "Releases granted steadily",
     "Customs releases continue at the expected rate after assessment and payment."),
]


def fallback_director(seed_key: str, active_incidents: list[str] | None = None) -> DirectorOutput:
    r = _seeded("dir", seed_key)
    items = [FeedItem(headline=h, description=d, severity=sev, entity=e, type=t)
             for t, sev, e, h, d in r.sample(_FEED_TEMPLATES, k=r.randint(3, 5))]
    for inc in (active_incidents or [])[:2]:
        items.append(FeedItem(headline=f"Incident in progress: {inc}", description="Operational teams are monitoring the effect "
                              "on queues and settlement.", severity="medium", entity="NSW", type="ops.congestion.alert"))
    return DirectorOutput(directives=Directives(arrival_multiplier=round(1 + r.uniform(-0.08, 0.08), 3)), feed_items=items)
