"""Pydantic schemas for every JSON role (all carry ``schema_version``; mismatches are rejected)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SCHEMA_VERSION = 1


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore")
    schema_version: int = SCHEMA_VERSION

    @field_validator("schema_version")
    @classmethod
    def _ver(cls, v: int) -> int:
        if v != SCHEMA_VERSION:
            raise ValueError(f"schema_version must be {SCHEMA_VERSION}")
        return v


# ----------------------------------------------------------------------------- entity profile
class FeeRule(BaseModel):
    model_config = ConfigDict(extra="ignore")
    fee_code: str
    name: str
    process_code: str | None = None
    basis: str = "ad_valorem"            # ad_valorem | flat | per_tonne | per_kg | per_teu
    base: str = "cif"                    # cif | cif_plus_duty (for ad_valorem)
    rate: float = 0.0                    # fraction for ad_valorem
    rate_by_group: dict[str, float] | None = None
    amount: float = 0.0                  # per unit, in `currency`
    currency: str = "NGN"
    applies_if: dict = Field(default_factory=dict)
    p: float = 1.0                       # probability the rule applies when applies_if matches
    p_by_group: dict[str, float] | None = None
    min_amount: float | None = None
    max_amount: float | None = None
    stage: str = "S04"                   # S01 | S02 | S03 | S04
    country_sensitive: bool = False
    permit: bool = False                 # primary permit rule of a permitting agency
    requires_permit_entity: bool = False  # only if this agency's permit applied to the consignment
    revenue_account: str | None = None   # assigned by the engine (4110, 4120, ...)


class CountrySens(BaseModel):
    model_config = ConfigDict(extra="ignore")
    fee_multiplier: float = 1.0
    inspection_rate_modifier: float = 1.0
    doc_issue_rate: float = 0.05
    dwell_modifier: float = 1.0


class PartnerFunding(BaseModel):
    model_config = ConfigDict(extra="ignore")
    facility_name: str
    partner_country: str
    instrument: Literal["grant", "concessional_loan"] = "grant"
    amount_ngn_per_quarter: float
    purpose: str = ""


class ExpenseCategory(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str
    share: float
    monthly_growth: float = 0.0


class ExpenseStructure(BaseModel):
    model_config = ConfigDict(extra="ignore")
    opex_ratio: float                    # monthly opex as a fraction of NSW-channel monthly collections
    categories: list[ExpenseCategory]
    appropriation_coverage: float = 0.0  # share of opex funded by government appropriation releases


class RemittanceRule(BaseModel):
    model_config = ConfigDict(extra="ignore")
    basis: Literal["net_settled", "surplus", "none"] = "net_settled"
    share: float = 0.3
    frequency: Literal["monthly", "quarterly", "none"] = "monthly"
    due_day: int = 10
    typical_delay_days: list[int] = Field(default_factory=lambda: [-1, 0, 0, 1, 2])
    destination: str = "Federation Account"


class Onboarding(BaseModel):
    model_config = ConfigDict(extra="ignore")
    digital_share_start: float = 0.9
    digital_share_end: float = 0.96
    completeness: float = 0.985          # share of stage records with all fields


class EntityProfile(_Base):
    entity: str
    processes: dict[str, str] = Field(default_factory=dict)
    fee_rules: list[FeeRule] = Field(default_factory=list)
    country_sensitivity: dict[str, CountrySens] = Field(default_factory=dict)
    partner_funding: list[PartnerFunding] = Field(default_factory=list)
    expense_structure: ExpenseStructure | None = None
    collection_cost_rate: float = 0.007
    remittance: RemittanceRule = Field(default_factory=RemittanceRule)
    sla_hours: dict[str, float] = Field(default_factory=dict)
    processing_speed_factor: float = 1.0
    onboarding: Onboarding = Field(default_factory=Onboarding)
    seasonality: dict[str, float] = Field(default_factory=dict)   # "1".."12" -> volume/yield index
    quirks: list[str] = Field(default_factory=list)


# ----------------------------------------------------------------------------- weekly flow plan
class FlowIncident(BaseModel):
    model_config = ConfigDict(extra="ignore")
    day: int = 0
    kind: str = "note"
    port: str | None = None
    note: str = ""


class FlowPlan(_Base):
    week_start: str
    daily_multiplier: list[float]
    port_share: dict[str, float]
    mode_share: dict[str, float]
    origin_share: dict[str, float]
    commodity_share: dict[str, float]
    hourly_shape: list[float]
    fx_path: list[float]
    value_multiplier: float = 1.0
    expected_incidents: list[FlowIncident] = Field(default_factory=list)
    narrative: str = ""


# ----------------------------------------------------------------------------- weekly entity ops plan
class ApprRelease(BaseModel):
    model_config = ConfigDict(extra="ignore")
    day: int
    amount_factor: float = 1.0


class PartnerReceipt(BaseModel):
    model_config = ConfigDict(extra="ignore")
    day: int
    facility: str
    partner_country: str
    amount_factor: float = 1.0


class RefundItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    day: int
    fee_code: str
    amount_factor: float = 1.0
    memo: str = ""


class DQIncident(BaseModel):
    model_config = ConfigDict(extra="ignore")
    day: int
    kind: Literal["orphan_payment", "missing_field"] = "orphan_payment"
    count: int = 1


class OpsPlan(_Base):
    entity: str
    week_start: str
    appropriation_releases: list[ApprRelease] = Field(default_factory=list)
    partner_receipts: list[PartnerReceipt] = Field(default_factory=list)
    expense_multipliers: dict[str, float] = Field(default_factory=dict)
    expense_day_weights: list[float] = Field(default_factory=lambda: [1, 1, 1, 1, 1, 0, 0])
    refunds: list[RefundItem] = Field(default_factory=list)
    remittance_delay_days: int = 0
    dq_incidents: list[DQIncident] = Field(default_factory=list)
    note: str = ""


# ----------------------------------------------------------------------------- live director
class Directives(BaseModel):
    model_config = ConfigDict(extra="ignore")
    arrival_multiplier: float = 1.0
    scanner_offline: dict[str, int] = Field(default_factory=dict)
    permit_queue_pressure: dict[str, float] = Field(default_factory=dict)
    settlement_lag_change_h: dict[str, float] = Field(default_factory=dict)
    payment_failure_rate: float = 0.02


class FeedItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    headline: str
    description: str = ""
    severity: Literal["info", "low", "medium", "high"] = "info"
    entity: str = "NSW"
    type: str = "ops.congestion.alert"


class DirectorOutput(_Base):
    directives: Directives = Field(default_factory=Directives)
    feed_items: list[FeedItem] = Field(default_factory=list)


class FeedItems(_Base):
    feed_items: list[FeedItem] = Field(default_factory=list)


# ----------------------------------------------------------------------------- narrative / storyline / paraphrase
class NarrativeFacts(_Base):
    summary: str
    bullets: list[str] = Field(default_factory=list)


class Slide(BaseModel):
    model_config = ConfigDict(extra="ignore")
    title: str
    key_message: str
    bullets: list[str]
    speaker_notes: str = ""
    chart_ref: str = ""
    evidence_ids: list[str] = Field(default_factory=list)


class StorylineSlides(_Base):
    storyline: str = ""
    slides: list[Slide]


class QuestionVariants(_Base):
    variants: list[str]


class DigestOut(_Base):
    headline: str
    bullets: list[str] = Field(default_factory=list)
