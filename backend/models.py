"""Canonical typed records, configuration, and API models."""

from __future__ import annotations

import math
from datetime import date, datetime, time, timezone
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
    return datetime.combine(expiration, time(16, 0), tzinfo=NY_TZ).astimezone(timezone.utc)


def calendar_dte(expiration: date, valuation_at: datetime) -> int:
    return (expiration - ny_local_date(valuation_at)).days


def year_fraction(expiration: date, valuation_at: datetime) -> float:
    """Fractional T for BSM pricing; do not round to whole days."""
    return (model_expiry_at_utc(expiration) - valuation_at).total_seconds() / SECONDS_PER_YEAR


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


class PricedQuote(BaseModel):
    """An OptionQuote plus its analytics outputs. Internal to analytics<->storage."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    quote: OptionQuote
    mid: float | None
    iv: float | None
    gamma: float | None
    exclusion_reason: str | None


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_mode: Literal["fixture", "nasdaq"]
    db_path: str
    symbols: tuple[str, ...]
    default_symbol: str
    refresh_min_interval_seconds: int
    risk_free_rate: float
    dividend_yields: dict[str, float]

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

    @field_validator("risk_free_rate")
    @classmethod
    def _validate_r(cls, v: float) -> float:
        if not (-0.10 <= v <= 0.50):
            raise ValueError("risk_free_rate must be within [-0.10, 0.50]")
        return v

    @model_validator(mode="after")
    def _validate_cross_fields(self) -> Settings:
        if self.default_symbol not in self.symbols:
            raise ValueError("default_symbol must be one of symbols")
        if set(self.dividend_yields.keys()) != set(self.symbols):
            raise ValueError("dividend_yields keys must exactly match symbols")
        for sym, q in self.dividend_yields.items():
            if not (0 <= q <= 0.50):
                raise ValueError(f"dividend yield for {sym!r} must be within [0, 0.50]")
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

    symbols: tuple[str, ...]
    default_symbol: str
    source_mode: Literal["fixture", "nasdaq"]
    risk_free_rate: float
    dividend_yields: dict[str, float]
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


class DashboardResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    snapshot_id: UUID
    symbol: str
    source_mode: Literal["fixture", "nasdaq"]
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
