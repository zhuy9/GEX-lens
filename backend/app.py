"""Configuration, provider factories, routes, orchestration."""

import hashlib
import json
import logging
import math
import threading
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

import storage
from analytics import (
    IV_HIGH,
    IV_LOW,
    IV_MAXITER,
    IV_RTOL,
    IV_XTOL,
    MAX_RELATIVE_SPREAD,
    MIN_MID,
    MIN_TIME_VALUE,
    MODEL_BOUNDS_TOLERANCE,
    REPRICE_TOLERANCE,
    analyze_snapshot_v2,
    build_expiry_pricing_context,
)
from instruments import INSTRUMENTS
from market_inputs import (
    canonical_json,
    load_local_reference_inputs,
    resolve_dividend_schedule,
    resolve_market_inputs,
    resolve_rate,
)
from models import (
    ChainRequest,
    ConfigResponse,
    DashboardResponseV2,
    ErrorBody,
    ErrorResponse,
    Instrument,
    OptionQuote,
    ParametersV2,
    ResolvedDividendSchedule,
    ResolvedRate,
    Settings,
    calendar_dte,
)
from provider import DividendDataProvider, OptionsDataProvider, ProviderError, RateDataProvider

MIN_CALENDAR_DTE = 1
MAX_CALENDAR_DTE = 60
MIN_STRIKE_PCT = 0.80
MAX_STRIKE_PCT = 1.20
PRICING_TIME_CONVENTION = "16:00 America/New_York on expiration date"
MODEL_ID = "cash_pv_bsm_v2"
ALGORITHM_VERSION = "2"
# Every verified instrument is enabled, in instruments.py order; the first
# is the default symbol.
SYMBOLS = tuple(INSTRUMENTS)

logger = logging.getLogger(__name__)

# Provider error codes with a status other than the 502 default.
_PROVIDER_ERROR_STATUS = {
    "UNSUPPORTED_SYMBOL": 422,
    "UPSTREAM_RATE_LIMITED": 503,
}


def build_provider(settings: Settings) -> OptionsDataProvider:
    """The only place a concrete chain adapter is selected and imported (PRD 4.3)."""
    if settings.source_mode == "fixture":
        from fixtures import FixtureProvider

        return FixtureProvider()
    if settings.source_mode == "nasdaq":
        from nasdaq import NasdaqProvider

        return NasdaqProvider()
    raise ValueError(f"Unknown source_mode: {settings.source_mode!r}")


def build_rate_provider(rate_source: str) -> RateDataProvider | None:
    """None means resolve_rate(rate_source="manual", ...) makes zero calls."""
    if rate_source == "manual":
        return None
    if rate_source == "fixture":
        from fixtures import FixtureRateProvider

        return FixtureRateProvider()
    if rate_source == "nyfed_sofr":
        from rates import NyFedSofrProvider

        return NyFedSofrProvider()
    raise ValueError(f"Unknown rate_source: {rate_source!r}")


def build_dividend_provider(dividend_source: str) -> DividendDataProvider | None:
    """None means resolve_dividend_schedule(dividend_source="manual_schedule", ...)
    makes zero calls. "nasdaq_dividends" is verified only for AAPL/QQQ (M0;
    see docs/dividend-source-contract.md) -- NasdaqDividendProvider itself
    rejects any other symbol with UNSUPPORTED_SYMBOL, since SPY's dividend
    history is confirmed unavailable from this source, not merely unverified."""
    if dividend_source == "manual_schedule":
        return None
    if dividend_source == "fixture":
        from fixtures import FixtureDividendProvider

        return FixtureDividendProvider()
    if dividend_source == "nasdaq_dividends":
        from nasdaq import NasdaqDividendProvider

        return NasdaqDividendProvider()
    raise ValueError(f"Unknown dividend_source: {dividend_source!r}")


class RefreshCoordinator:
    """Process-wide refresh lock plus a global cooldown deadline (PRD 4.1, 11.3)."""

    def __init__(self, min_interval_seconds: int) -> None:
        self.min_interval_seconds = min_interval_seconds
        self._lock = threading.Lock()
        self._next_allowed_monotonic = 0.0
        self._next_allowed_utc: datetime | None = None

    @property
    def in_progress(self) -> bool:
        return self._lock.locked()

    @property
    def not_before(self) -> datetime | None:
        return self._next_allowed_utc

    def begin(self) -> tuple[bool, int | None]:
        """Returns (acquired, retry_after_seconds). Caller must call end() iff acquired."""
        if not self._lock.acquire(blocking=False):
            return False, None
        remaining = self._next_allowed_monotonic - time.monotonic()
        if remaining > 0:
            self._lock.release()
            return False, math.ceil(remaining)
        self._advance_deadline(self.min_interval_seconds)
        return True, None

    def extend_deadline(self, seconds: int) -> None:
        if time.monotonic() + seconds > self._next_allowed_monotonic:
            self._advance_deadline(seconds)

    def _advance_deadline(self, seconds: int) -> None:
        self._next_allowed_monotonic = time.monotonic() + seconds
        self._next_allowed_utc = datetime.now(UTC) + timedelta(seconds=seconds)

    def end(self) -> None:
        self._lock.release()


def _error_response(
    status_code: int, code: str, message: str, retry_after_seconds: int | None = None
) -> JSONResponse:
    body = ErrorResponse(error=ErrorBody(code=code, message=message, retry_after_seconds=retry_after_seconds))
    headers = {"Retry-After": str(retry_after_seconds)} if retry_after_seconds else None
    return JSONResponse(status_code=status_code, content=body.model_dump(mode="json"), headers=headers)


def _http_error(
    status_code: int, code: str, message: str, retry_after_seconds: int | None = None
) -> HTTPException:
    """The one place a route builds an HTTPException's dict detail. The
    global handler below (_http_exception_handler) is this function's
    exact inverse."""
    detail: dict = {"code": code, "message": message}
    if retry_after_seconds is not None:
        detail["retry_after_seconds"] = retry_after_seconds
    return HTTPException(status_code=status_code, detail=detail)


def _date_or_none(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _calculation_input_hash(
    *,
    contracts: tuple[OptionQuote, ...],
    actual_spot: float,
    valuation_at: datetime,
    resolved_rate: ResolvedRate,
    dividend_schedule: ResolvedDividendSchedule,
) -> str:
    """ADR-0001 Section 11.2: the numerical-reproduction hash. Excludes
    UUIDs, report-generation time, and cache timestamps -- only inputs that
    actually change the computed numbers."""
    contract_rows = sorted(
        (
            {
                "symbol": c.symbol,
                "expiration": c.expiration.isoformat(),
                "strike": str(c.strike),
                "option_type": c.option_type,
                "bid": c.bid,
                "ask": c.ask,
                "last": c.last,
                "volume": c.volume,
                "open_interest": c.open_interest,
                "multiplier": c.multiplier,
                "flags": sorted(c.flags),
            }
            for c in contracts
        ),
        key=lambda row: (row["symbol"], row["expiration"], row["strike"], row["option_type"]),
    )
    dividend_rows = sorted(
        (
            {
                "event_id": e.event_id,
                "ex_date": e.ex_date.isoformat(),
                "payment_date": _date_or_none(e.payment_date),
                "amount": str(e.amount),
                "amount_status": e.amount_status,
            }
            for e in dividend_schedule.events
        ),
        key=lambda row: row["event_id"],
    )
    payload = {
        "actual_spot": actual_spot,
        "contracts": contract_rows,
        "valuation_at": valuation_at.astimezone(UTC).isoformat(),
        "min_calendar_dte": MIN_CALENDAR_DTE,
        "max_calendar_dte": MAX_CALENDAR_DTE,
        "min_strike_pct": MIN_STRIKE_PCT,
        "max_strike_pct": MAX_STRIKE_PCT,
        "rate_cc": resolved_rate.rate_cc,
        "dividend_events": dividend_rows,
        "model_id": MODEL_ID,
        "algorithm_version": ALGORITHM_VERSION,
        # Section 11.2's "scope/quality thresholds": a silent change to any of
        # these without bumping algorithm_version must be caught by hash
        # reproduction, not only the scope bounds already listed above.
        "quality_thresholds": {
            "min_mid": MIN_MID,
            "max_relative_spread": MAX_RELATIVE_SPREAD,
            "model_bounds_tolerance": MODEL_BOUNDS_TOLERANCE,
            "min_time_value": MIN_TIME_VALUE,
            "iv_low": IV_LOW,
            "iv_high": IV_HIGH,
            "iv_xtol": IV_XTOL,
            "iv_rtol": IV_RTOL,
            "iv_maxiter": IV_MAXITER,
            "reprice_tolerance": REPRICE_TOLERANCE,
        },
    }
    return hashlib.sha256(canonical_json(payload).encode()).hexdigest()


def _collect_and_save(
    settings: Settings,
    provider: OptionsDataProvider,
    rate_provider: RateDataProvider | None,
    dividend_providers: dict[str, DividendDataProvider | None],
    symbol: str,
    *,
    force_reference_refresh: bool,
) -> dict:
    request = ChainRequest(
        symbol=symbol, min_calendar_dte=MIN_CALENDAR_DTE, max_calendar_dte=MAX_CALENDAR_DTE
    )
    snapshot = provider.fetch_chain(request)

    if snapshot.symbol != symbol or snapshot.provider_id != settings.source_mode:
        raise ProviderError("SNAPSHOT_IDENTITY_MISMATCH", "Provider returned a mismatched snapshot")

    chain_asof = snapshot.chain_asof
    valuation_at = chain_asof if chain_asof is not None else snapshot.collection_started_at
    # Provider-independent: any provider whose chain_asof is null falls back
    # to collection_started_at here, so the warning belongs at this one
    # orchestration boundary, not duplicated in every provider adapter.
    warnings: list[str] = list(
        snapshot.warnings if chain_asof is not None else (*snapshot.warnings, "VALUATION_TIME_ASSUMED")
    )

    # Reference requests occur after chain collection, at this fixed
    # valuation time (Section 4). Read the local file at most once, only
    # when this attempt actually needs it (Section 5.2/8.2).
    dividend_source = settings.dividend_sources[symbol]
    local_inputs = (
        load_local_reference_inputs(Path(settings.reference_inputs_path))
        if settings.rate_source == "manual" or dividend_source != "fixture"
        else None
    )

    resolved_rate = resolve_rate(
        rate_source=settings.rate_source,
        manual=local_inputs.manual_rate if local_inputs is not None else None,
        provider=rate_provider,
        valuation_at=valuation_at,
        attempt_started_at=valuation_at,
        db_path=settings.db_path,
        force_refresh=force_reference_refresh,
    )

    if dividend_source == "fixture":
        from fixtures import fixture_schedule_review

        review = fixture_schedule_review(symbol, valuation_at)
    else:
        if local_inputs is None or symbol not in local_inputs.schedules:
            raise ProviderError(
                "DIVIDEND_REVIEW_REQUIRED",
                f"No reviewed schedule for {symbol!r} in {settings.reference_inputs_path}",
            )
        review = local_inputs.schedules[symbol]

    latest_in_scope_expiration = max(
        (
            c.expiration
            for c in snapshot.contracts
            if MIN_CALENDAR_DTE <= calendar_dte(c.expiration, valuation_at) <= MAX_CALENDAR_DTE
        ),
        default=None,
    )
    dividend_schedule, dividend_warnings = resolve_dividend_schedule(
        dividend_source=dividend_source,
        review=review,
        provider=dividend_providers.get(symbol),
        valuation_at=valuation_at,
        attempt_started_at=valuation_at,
        latest_in_scope_expiration=latest_in_scope_expiration,
        db_path=settings.db_path,
        force_refresh=force_reference_refresh,
    )
    market_inputs_obj = resolve_market_inputs(
        rate=resolved_rate,
        dividend_schedule=dividend_schedule,
        dividend_warnings=dividend_warnings,
        resolved_at=snapshot.collection_started_at,
    )
    for code in market_inputs_obj.warnings:
        if code not in warnings:
            warnings.append(code)

    # One context per expiry actually present in this chain (M3.2), not one
    # independent dividend calculation per function.
    contexts = {
        expiration: build_expiry_pricing_context(
            expiration,
            valuation_at,
            snapshot.underlying_price,
            resolved_rate.rate_cc,
            dividend_schedule.events,
            chain_asof=snapshot.chain_asof,
            spot_asof=snapshot.spot_asof,
            spot_asof_date=snapshot.spot_asof_date,
        )
        for expiration in {c.expiration for c in snapshot.contracts}
    }
    for context in contexts.values():
        for code in context.warnings:
            if code not in warnings:
                warnings.append(code)

    priced, gex, surface, quality = analyze_snapshot_v2(
        snapshot.contracts,
        actual_spot=snapshot.underlying_price,
        contexts=contexts,
        min_calendar_dte=MIN_CALENDAR_DTE,
        max_calendar_dte=MAX_CALENDAR_DTE,
        min_strike_pct=MIN_STRIKE_PCT,
        max_strike_pct=MAX_STRIKE_PCT,
        source_row_count=snapshot.source_row_count,
    )

    snapshot_id = uuid4()
    dashboard = DashboardResponseV2(
        snapshot_id=snapshot_id,
        symbol=symbol,
        source_mode=settings.source_mode,
        collected_at=snapshot.collected_at,
        valuation_at=valuation_at,
        chain_asof=snapshot.chain_asof,
        spot_asof=snapshot.spot_asof,
        spot_asof_date=snapshot.spot_asof_date,
        oi_asof=snapshot.oi_asof,
        spot=snapshot.underlying_price,
        spot_kind=snapshot.underlying_price_kind,
        spot_origin=snapshot.underlying_price_origin,
        instrument=Instrument(symbol=symbol, instrument_class=INSTRUMENTS[symbol].instrument_class),
        parameters=ParametersV2(
            r=resolved_rate.rate_cc,
            q=0.0,
            multiplier_assumed="MULTIPLIER_ASSUMED" in snapshot.warnings,
            min_calendar_dte=MIN_CALENDAR_DTE,
            max_calendar_dte=MAX_CALENDAR_DTE,
            min_strike_pct=MIN_STRIKE_PCT,
            max_strike_pct=MAX_STRIKE_PCT,
            pricing_time_convention=PRICING_TIME_CONVENTION,
        ),
        market_inputs=market_inputs_obj,
        pricing_contexts=tuple(contexts.values()),
        warnings=tuple(warnings),
        quality=quality,
        gex=gex,
        surface=surface,
        calculation_input_hash=_calculation_input_hash(
            contracts=snapshot.contracts,
            actual_spot=snapshot.underlying_price,
            valuation_at=valuation_at,
            resolved_rate=resolved_rate,
            dividend_schedule=dividend_schedule,
        ),
    )
    dashboard_json = dashboard.model_dump(mode="json")

    storage.save_snapshot(
        settings.db_path,
        source_mode=settings.source_mode,
        symbol=symbol,
        snapshot_id=snapshot_id,
        collected_at=snapshot.collected_at,
        valuation_at=valuation_at,
        raw_payload_json=snapshot.raw_payload_json,
        dashboard_json=dashboard_json,
        priced_quotes=priced,
    )
    return dashboard_json


def create_app(
    settings: Settings,
    provider: OptionsDataProvider | None = None,
    rate_provider: RateDataProvider | None = None,
    dividend_providers: dict[str, DividendDataProvider | None] | None = None,
) -> FastAPI:
    if set(settings.dividend_sources) != set(SYMBOLS):
        raise ValueError(
            f"dividend_sources must have exactly one entry per instrument: {', '.join(SYMBOLS)}"
        )
    storage.init_schema(settings.db_path)
    provider_is_owned = provider is None
    active_provider = provider if provider is not None else build_provider(settings)
    rate_provider_is_owned = rate_provider is None
    active_rate_provider = (
        rate_provider if rate_provider is not None else build_rate_provider(settings.rate_source)
    )
    dividend_providers_are_owned = dividend_providers is None
    active_dividend_providers = (
        dividend_providers
        if dividend_providers is not None
        else {
            symbol: build_dividend_provider(settings.dividend_sources[symbol]) for symbol in SYMBOLS
        }
    )
    gate = RefreshCoordinator(settings.refresh_min_interval_seconds)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        # Only close a provider this factory built itself (e.g. NasdaqProvider's
        # own httpx.Client). An injected provider's client (tests, or a future
        # caller) belongs to whoever constructed it, not to this app.
        if provider_is_owned:
            close = getattr(active_provider, "close", None)
            if close is not None:
                close()
        if rate_provider_is_owned:
            close = getattr(active_rate_provider, "close", None)
            if close is not None:
                close()
        if dividend_providers_are_owned:
            for reference_provider in active_dividend_providers.values():
                close = getattr(reference_provider, "close", None)
                if close is not None:
                    close()

    app = FastAPI(lifespan=lifespan)

    def _require_symbol(symbol: str) -> str:
        upper = symbol.upper()
        if upper not in SYMBOLS:
            raise _http_error(422, "UNSUPPORTED_SYMBOL", f"{symbol!r} is not configured")
        return upper

    @app.exception_handler(HTTPException)
    async def _http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        # FastAPI allows a dict `detail=` at raise sites, but Starlette's own
        # HTTPException.detail attribute is typed `str | None`; cast back.
        detail: dict = (
            cast(dict, exc.detail)
            if isinstance(exc.detail, dict)
            else {"code": "ERROR", "message": str(exc.detail)}
        )
        return _error_response(
            exc.status_code,
            detail.get("code", "ERROR"),
            detail.get("message", ""),
            detail.get("retry_after_seconds"),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        return _error_response(422, "VALIDATION_ERROR", str(exc))

    @app.exception_handler(Exception)
    async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        # The one diagnostic boundary for a genuine bug (not an expected
        # ProviderError/HTTPException, which routes map explicitly below).
        # Never put the traceback or exc internals in the response itself.
        logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
        return _error_response(500, "INTERNAL_ERROR", "Unexpected failure")

    @app.get("/api/health")
    def health() -> dict:
        storage.health_check(settings.db_path)
        return {"status": "ok", "schema_version": 1}

    @app.get("/api/config")
    def get_config() -> dict:
        response = ConfigResponse(
            symbols=SYMBOLS,
            default_symbol=SYMBOLS[0],
            source_mode=settings.source_mode,
            pricing_model=settings.pricing_model,
            rate_source=settings.rate_source,
            dividend_sources=settings.dividend_sources,
            min_calendar_dte=MIN_CALENDAR_DTE,
            max_calendar_dte=MAX_CALENDAR_DTE,
            min_strike_pct=MIN_STRIKE_PCT,
            max_strike_pct=MAX_STRIKE_PCT,
            refresh_min_interval_seconds=settings.refresh_min_interval_seconds,
            refresh_in_progress=gate.in_progress,
            server_time=datetime.now(UTC),
            refresh_not_before=gate.not_before,
        )
        return response.model_dump(mode="json")

    @app.get("/api/dashboard/{symbol}")
    def get_dashboard(symbol: str) -> dict:
        symbol = _require_symbol(symbol)
        dashboard = storage.get_latest_dashboard(settings.db_path, settings.source_mode, symbol)
        if dashboard is None:
            raise _http_error(404, "NO_SNAPSHOT", f"No saved snapshot for {symbol}")
        return dashboard

    @app.post("/api/dashboard/{symbol}/refresh")
    def refresh(symbol: str, force_reference_refresh: bool = False) -> dict:
        symbol = _require_symbol(symbol)
        acquired, retry_after = gate.begin()
        if not acquired:
            if retry_after is None:
                raise _http_error(409, "REFRESH_IN_PROGRESS", "A refresh is already running")
            raise _http_error(429, "REFRESH_COOLDOWN", "Refresh is on cooldown", retry_after)
        # Only ProviderError (the adapter's documented failure contract) is
        # mapped to an HTTP response here. Anything else is a real bug, not
        # an expected failure -- let it propagate to the one unhandled-
        # exception handler above, which logs it and returns the same
        # sanitized 500 envelope this used to build by hand.
        try:
            return _collect_and_save(
                settings,
                active_provider,
                active_rate_provider,
                active_dividend_providers,
                symbol,
                force_reference_refresh=force_reference_refresh,
            )
        except ProviderError as exc:
            if exc.code == "UPSTREAM_RATE_LIMITED":
                retry_after = exc.retry_after_seconds
                # PRD 5.3: use a valid Retry-After value, clamped to a 60s
                # floor; only fall back to 300s when there is no valid value
                # at all. A short-but-valid delay (e.g. 5s) must not be
                # discarded in favor of the 300s default.
                effective = max(retry_after, 60) if retry_after is not None else 300
                gate.extend_deadline(effective)
                raise _http_error(503, exc.code, str(exc), effective) from exc
            status_code = _PROVIDER_ERROR_STATUS.get(exc.code, 502)
            raise _http_error(status_code, exc.code, str(exc)) from exc
        finally:
            gate.end()

    return app


def _load_settings(path: Path | None = None) -> Settings:
    path = path if path is not None else Path(__file__).parent / "settings.json"
    raw = json.loads(path.read_text())
    try:
        return Settings.model_validate(raw)
    except ValidationError as exc:
        if "risk_free_rate" in raw or "dividend_yields" in raw:
            raise ValueError(
                f"{path} uses the pre-ADR-0001 config schema (risk_free_rate/dividend_yields). "
                "Migrate to pricing_model/rate_source/dividend_sources/reference_inputs_path -- "
                "see settings.example.json."
            ) from exc
        if "symbols" in raw:
            raise ValueError(
                f"{path}: 'symbols' was removed; every instrument in instruments.py is now "
                "enabled. Delete the key -- see settings.example.json."
            ) from exc
        raise


def main() -> None:
    import uvicorn

    app = create_app(_load_settings())
    uvicorn.run(app, host="127.0.0.1", port=8000, workers=1)


if __name__ == "__main__":
    main()
