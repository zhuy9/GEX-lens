"""Canonical typed records, configuration, and API models."""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

ALGORITHM_VERSION = "1"
NY_TZ = ZoneInfo("America/New_York")
SECONDS_PER_YEAR = 31_536_000


def ny_local_date(at: datetime) -> date:
    """Calendar date in America/New_York for a UTC-aware instant."""
    return at.astimezone(NY_TZ).date()


def model_expiry_at_utc(expiration: date) -> datetime:
    """16:00 America/New_York on the expiration date, per PRD 6.1's pricing convention."""
    return datetime.combine(expiration, time(16, 0), tzinfo=NY_TZ).astimezone(UTC)


def dividend_ex_at(ex_date: date) -> datetime:
    """09:30 America/New_York on ex_date, per ADR-0001 Section 7.4's model timing convention."""
    return datetime.combine(ex_date, time(9, 30), tzinfo=NY_TZ).astimezone(UTC)


def dividend_pay_at(payment_date: date) -> datetime:
    """16:00 America/New_York on payment_date, per ADR-0001 Section 7.4."""
    return datetime.combine(payment_date, time(16, 0), tzinfo=NY_TZ).astimezone(UTC)


def calendar_dte(expiration: date, valuation_at: datetime) -> int:
    return (expiration - ny_local_date(valuation_at)).days


def year_fraction(expiration: date, valuation_at: datetime) -> float:
    """Fractional T for BSM pricing; do not round to whole days."""
    return (model_expiry_at_utc(expiration) - valuation_at).total_seconds() / SECONDS_PER_YEAR


def _require_aware(v: datetime | None) -> datetime | None:
    # A canonical snapshot's datetime fields must be safe to subtract from an
    # aware expiry timestamp (year_fraction) without a provider adapter
    # having to remember that rule -- enforced once, here, at construction.
    if v is not None and v.tzinfo is None:
        raise ValueError("datetime fields must be timezone-aware")
    return v


class ChainRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    min_calendar_dte: int
    max_calendar_dte: int


class OptionQuote(BaseModel):
    """A single normalized call or put contract. Provider-agnostic."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    expiration: date
    strike: Decimal
    option_type: Literal["C", "P"]
    bid: float | None
    ask: float | None
    last: float | None
    volume: int | None
    open_interest: int | None
    multiplier: int
    provider_contract_id: str | None
    quote_asof: datetime | None
    flags: tuple[str, ...] = ()

    @field_validator("strike")
    @classmethod
    def _strike_positive(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("strike must be positive")
        return v

    @field_validator("volume", "open_interest")
    @classmethod
    def _count_nonnegative(cls, v: int | None) -> int | None:
        # A provider adapter must produce a valid canonical count once, or
        # flag it invalid and pass null -- not push a negative/fractional
        # count downstream for analytics to repair.
        if v is not None and v < 0:
            raise ValueError("count fields must be null or nonnegative")
        return v

    @field_validator("quote_asof")
    @classmethod
    def _quote_asof_aware(cls, v: datetime | None) -> datetime | None:
        return _require_aware(v)


class ChainSnapshot(BaseModel):
    """A complete, normalized option-chain collection from one provider call."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider_id: str
    symbol: str
    underlying_price: float
    underlying_price_kind: Literal["last_trade"]
    underlying_price_origin: Literal["chain_payload"]
    collection_started_at: datetime
    collected_at: datetime
    chain_asof: datetime | None
    spot_asof: datetime | None
    # Section 7.5: a date-only underlying as-of, populated only when the
    # chain adapter can verify it independently of a precise spot_asof.
    # Never synthesized from spot_asof; old snapshots simply lack it.
    spot_asof_date: date | None = None
    oi_asof: date | None
    contracts: tuple[OptionQuote, ...]
    warnings: tuple[str, ...] = ()
    source_row_count: int
    provider_response_count: int
    raw_payload_json: str

    @field_validator("underlying_price")
    @classmethod
    def _finite_positive(cls, v: float) -> float:
        if not math.isfinite(v) or v <= 0:
            raise ValueError("underlying_price must be finite and positive")
        return v

    @field_validator("collection_started_at", "collected_at", "chain_asof", "spot_asof")
    @classmethod
    def _datetimes_aware(cls, v: datetime | None) -> datetime | None:
        return _require_aware(v)


class PricedQuote(BaseModel):
    """An OptionQuote plus its analytics outputs. Internal to analytics<->storage."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    quote: OptionQuote
    mid: float | None
    iv: float | None
    gamma: float | None
    exclusion_reason: str | None


class RateObservation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    effective_date: date
    percent_rate: float
    rate_type: str
    revision_indicator: str | None

    @field_validator("percent_rate")
    @classmethod
    def _finite(cls, v: float) -> float:
        if isinstance(v, bool) or not math.isfinite(v):
            raise ValueError("percent_rate must be a finite number, not a bool")
        return v


class RateBatch(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    provider_id: str
    fetched_at: datetime
    source_ref: str
    observations: tuple[RateObservation, ...]
    raw_payload_json: str

    @field_validator("fetched_at")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        _require_aware(v)
        return v


class ResolvedRate(BaseModel):
    """Section 5.1's exact MarketInputs.rate contract."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_provider_id: str
    source_ref: str
    effective_date: date
    fetched_at: datetime
    raw_percent_rate: float | None
    rate_cc: float
    quote_convention: Literal["percent_simple_act360", "continuous_act365f"]
    normalization: Literal["constant_daily_sofr_proxy", "manual_already_continuous"]
    revision_indicator: str | None
    manual_reason: str | None

    @field_validator("fetched_at")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        _require_aware(v)
        return v

    @field_validator("rate_cc")
    @classmethod
    def _rate_cc_bounded(cls, v: float) -> float:
        # Same application guardrail as Settings.risk_free_rate (PRD/ADR 6.2):
        # an application input limit, not a statement about possible market rates.
        if not math.isfinite(v) or not (-0.10 <= v <= 0.50):
            raise ValueError("rate_cc must be finite and within [-0.10, 0.50]")
        return v

    @field_validator("raw_percent_rate")
    @classmethod
    def _raw_finite(cls, v: float | None) -> float | None:
        if v is not None and (isinstance(v, bool) or not math.isfinite(v)):
            raise ValueError("raw_percent_rate must be null or a finite number")
        return v


class ManualRateInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    rate_cc: float
    effective_date: date
    entered_at: datetime
    source_ref: str
    reason: str

    @field_validator("entered_at")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        _require_aware(v)
        return v


class DividendRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    provider_record_id: str | None
    symbol: str
    currency: str
    ex_date: date
    payment_date: date | None
    declaration_date: date | None
    amount: Decimal | None
    kind: Literal["ordinary_cash", "other", "unknown"]
    source_ref: str

    @field_validator("amount")
    @classmethod
    def _amount_positive(cls, v: Decimal | None) -> Decimal | None:
        if v is not None and v <= 0:
            raise ValueError("amount must be null or positive")
        return v


class DividendFeedSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    provider_id: str
    symbol: str
    fetched_at: datetime
    source_asof: datetime | None
    records: tuple[DividendRecord, ...]
    raw_payload_json: str
    warnings: tuple[str, ...] = ()

    @field_validator("fetched_at", "source_asof")
    @classmethod
    def _aware(cls, v: datetime | None) -> datetime | None:
        return _require_aware(v)


class ExpectedDividend(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str
    ex_date: date
    payment_date: date | None
    amount: Decimal | None
    amount_status: Literal["declared", "estimated", "pending_source"]
    source_ref: str

    @field_validator("amount")
    @classmethod
    def _amount_positive(cls, v: Decimal | None) -> Decimal | None:
        if v is not None and v <= 0:
            raise ValueError("amount must be null or positive")
        return v

    @model_validator(mode="after")
    def _pending_has_no_amount(self) -> ExpectedDividend:
        if self.amount_status == "pending_source" and self.amount is not None:
            raise ValueError("pending_source events cannot carry an amount")
        if self.amount_status != "pending_source" and self.amount is None:
            raise ValueError("declared/estimated events require an amount")
        return self


class ScheduleReview(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    reviewed_at: datetime
    coverage_start: date
    coverage_end: date
    no_other_events_expected: Literal[True]
    source_refs: tuple[str, ...]
    expected_events: tuple[ExpectedDividend, ...]

    @field_validator("reviewed_at")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        _require_aware(v)
        return v

    @model_validator(mode="after")
    def _coverage_ordered(self) -> ScheduleReview:
        if self.coverage_end < self.coverage_start:
            raise ValueError("coverage_end must not precede coverage_start")
        return self


class ResolvedDividend(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str
    ex_date: date
    payment_date: date | None
    amount: Decimal
    amount_status: Literal["source_reported", "owner_declared", "estimated"]
    source_ref: str
    source_provider_id: str

    @field_validator("amount")
    @classmethod
    def _amount_positive(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("amount must be positive")
        return v

    @model_validator(mode="after")
    def _payment_not_before_ex(self) -> ResolvedDividend:
        if self.payment_date is not None and self.payment_date < self.ex_date:
            raise ValueError("payment_date cannot precede ex_date")
        return self


class ResolvedDividendSchedule(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    events: tuple[ResolvedDividend, ...]
    review: ScheduleReview


class LocalReferenceInputs(BaseModel):
    """Parsed `reference_inputs.json` (ADR-0001 Section 5.2). Local, git-ignored."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    input_schema_version: Literal[1]
    manual_rate: ManualRateInput | None
    schedules: dict[str, ScheduleReview]


class MarketInputs(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    input_schema_version: Literal[1] = 1
    resolved_at: datetime
    rate: ResolvedRate
    dividend_schedule: ResolvedDividendSchedule
    warnings: tuple[str, ...]
    reference_bundle_hash: str

    @field_validator("resolved_at")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        _require_aware(v)
        return v


class ExpiryPricingContext(BaseModel):
    """One per in-scope expiration (Section 9.2): IV, gamma, model bounds,
    forward, and surface-side selection all consume this same context."""

    # protected_namespaces=(): model_spot is the ADR's own field name, not a
    # pydantic ML-model convention pydantic would otherwise warn about.
    model_config = ConfigDict(frozen=True, extra="forbid", protected_namespaces=())

    expiration: date
    valuation_at: datetime
    expiry_at: datetime
    T: float
    actual_spot: float
    model_spot: float
    r_cc: float
    q_continuous: float
    pv_dividends: float
    forward: float
    used_event_ids: tuple[str, ...]
    warnings: tuple[str, ...]
    status: Literal["OK", "INVALID_DIVIDEND_ADJUSTED_SPOT"]

    @field_validator("valuation_at", "expiry_at")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        _require_aware(v)
        return v


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # A plain string, not a closed enum: build_provider() still only knows
    # how to construct "fixture" or "nasdaq" for production, but a test may
    # inject any OptionsDataProvider under any configured identity and
    # exercise the real API/DB path under it. See PRD 4.3's provider Protocol.
    source_mode: str
    db_path: str
    # The first entry is the default symbol -- not a separate field to keep
    # in sync; every observed config already used symbols[0] for this.
    symbols: tuple[str, ...]
    refresh_min_interval_seconds: int
    # ADR-0001 Section 5.2: replaces the old flat risk_free_rate/
    # dividend_yields. pricing_model/rate_source are plain strings for the
    # same reason source_mode is (PRD 4.3): a test may inject any resolver
    # under any configured identity.
    pricing_model: str
    rate_source: str
    dividend_sources: dict[str, str]
    reference_inputs_path: str

    @field_validator("symbols")
    @classmethod
    def _validate_symbols(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        if not (1 <= len(v) <= 3):
            raise ValueError("symbols must contain 1 to 3 entries")
        if len(set(v)) != len(v):
            raise ValueError("symbols must be unique")
        for s in v:
            if not s or not s.isupper() or not s.isalpha():
                raise ValueError(f"symbol {s!r} must be uppercase letters")
        return v

    @field_validator("refresh_min_interval_seconds")
    @classmethod
    def _validate_cooldown(cls, v: int) -> int:
        if v < 60:
            raise ValueError("refresh_min_interval_seconds must be >= 60")
        return v

    @model_validator(mode="after")
    def _validate_cross_fields(self) -> Settings:
        if set(self.dividend_sources.keys()) != set(self.symbols):
            raise ValueError("dividend_sources keys must exactly match symbols")
        return self


class ErrorBody(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str
    message: str
    retry_after_seconds: int | None = None


class ErrorResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    error: ErrorBody


class ConfigResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    config_schema_version: Literal[2] = 2
    symbols: tuple[str, ...]
    default_symbol: str
    source_mode: str
    pricing_model: str
    rate_source: str
    dividend_sources: dict[str, str]
    default_move_unit: Literal["per_1pct"] = "per_1pct"
    min_calendar_dte: int
    max_calendar_dte: int
    min_strike_pct: float
    max_strike_pct: float
    refresh_mode: Literal["manual"] = "manual"
    refresh_min_interval_seconds: int
    refresh_in_progress: bool
    server_time: datetime
    refresh_not_before: datetime | None


class Parameters(BaseModel):
    """Legacy (schema-version-1) pricing parameters."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    r: float
    q: float
    multiplier_assumed: bool
    min_calendar_dte: int
    max_calendar_dte: int
    min_strike_pct: float
    max_strike_pct: float
    pricing_time_convention: str
    algorithm_version: str


class ParametersV2(BaseModel):
    # protected_namespaces=(): model_id is the ADR's own field name.
    model_config = ConfigDict(frozen=True, extra="forbid", protected_namespaces=())

    r: float
    q: float
    multiplier_assumed: bool
    min_calendar_dte: int
    max_calendar_dte: int
    min_strike_pct: float
    max_strike_pct: float
    pricing_time_convention: str
    algorithm_version: Literal["2"] = "2"
    model_id: Literal["cash_pv_bsm_v2"] = "cash_pv_bsm_v2"
    dividend_model: Literal["cash_schedule"] = "cash_schedule"


class Instrument(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    instrument_class: Literal["equity", "etf"]
    currency: Literal["USD"] = "USD"
    exercise_style: Literal["american"] = "american"
    standard_multiplier: int = 100


class QualityCounts(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_rows: int
    normalized_contracts: int
    in_scope_contracts: int
    valid_ivs: int
    known_oi_contracts: int
    complete_gex_cells: int
    exclusion_counts: dict[str, int]


class GexCell(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    call_oi: int | None
    put_oi: int | None
    call_gamma: float | None
    put_gamma: float | None
    call_exposure: float | None
    put_exposure: float | None
    signed_proxy: float | None
    gross_exposure: float | None
    status: Literal["COMPLETE", "INCOMPLETE"]


class GexData(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    # Section 10: shared by v1 and v2 -- the legacy contract's exposure
    # fields are already per-1%-move dollars, so this is the same unit
    # under both schema versions, not a v2-only addition.
    canonical_unit: Literal["usd_delta_notional_per_1pct"] = "usd_delta_notional_per_1pct"
    strikes: tuple[Decimal, ...]
    expirations: tuple[date, ...]
    cells: tuple[tuple[GexCell | None, ...], ...]


class SurfaceObservation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    expiration: date
    strike: Decimal
    k: float
    dte: float
    iv: float


class SurfaceData(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["READY", "INSUFFICIENT_DATA"]
    k: tuple[float, ...]
    expirations: tuple[date, ...]
    dte: tuple[float, ...]
    iv: tuple[tuple[float | None, ...], ...] | None
    observations: tuple[SurfaceObservation, ...]


class DashboardResponseV1(BaseModel):
    """Legacy continuous-yield dashboard. A GET returns a saved v1 row
    unchanged -- never reconstructed, upgraded, or recomputed on read."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    snapshot_id: UUID
    symbol: str
    source_mode: str
    collected_at: datetime
    valuation_at: datetime
    chain_asof: datetime | None
    spot_asof: datetime | None
    oi_asof: date | None
    spot: float
    spot_kind: Literal["last_trade"]
    spot_origin: Literal["chain_payload"]
    parameters: Parameters
    warnings: tuple[str, ...]
    quality: QualityCounts
    gex: GexData
    surface: SurfaceData

    @field_validator("collected_at", "valuation_at", "chain_asof", "spot_asof")
    @classmethod
    def _datetimes_aware(cls, v: datetime | None) -> datetime | None:
        return _require_aware(v)


class DashboardResponseV2(BaseModel):
    """Cash-PV BSM dashboard (ADR-0001 Section 11.1). Every new refresh
    writes this; a v1 row is never upgraded in place."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[2] = 2
    snapshot_id: UUID
    symbol: str
    source_mode: str
    collected_at: datetime
    valuation_at: datetime
    chain_asof: datetime | None
    spot_asof: datetime | None
    spot_asof_date: date | None
    oi_asof: date | None
    spot: float
    spot_kind: Literal["last_trade"]
    spot_origin: Literal["chain_payload"]
    instrument: Instrument
    parameters: ParametersV2
    market_inputs: MarketInputs
    pricing_contexts: tuple[ExpiryPricingContext, ...]
    warnings: tuple[str, ...]
    quality: QualityCounts
    gex: GexData
    surface: SurfaceData
    calculation_input_hash: str

    @field_validator("collected_at", "valuation_at", "chain_asof", "spot_asof")
    @classmethod
    def _datetimes_aware(cls, v: datetime | None) -> datetime | None:
        return _require_aware(v)
