"""Configuration, provider factory, four routes, orchestration."""

import json
import logging
import math
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

import storage
from analytics import analyze_snapshot
from models import (
    ALGORITHM_VERSION,
    ChainRequest,
    ConfigResponse,
    DashboardResponse,
    ErrorBody,
    ErrorResponse,
    Parameters,
    Settings,
)
from provider import OptionsDataProvider, ProviderError

MIN_CALENDAR_DTE = 1
MAX_CALENDAR_DTE = 60
MIN_STRIKE_PCT = 0.80
MAX_STRIKE_PCT = 1.20
PRICING_TIME_CONVENTION = "16:00 America/New_York on expiration date"

logger = logging.getLogger(__name__)

# Provider error codes with a status other than the 502 default.
_PROVIDER_ERROR_STATUS = {
    "UNSUPPORTED_SYMBOL": 422,
    "UPSTREAM_RATE_LIMITED": 503,
}


def build_provider(settings: Settings) -> OptionsDataProvider:
    """The only place a concrete adapter is selected and imported (PRD 4.3)."""
    if settings.source_mode == "fixture":
        from fixtures import FixtureProvider

        return FixtureProvider()
    if settings.source_mode == "nasdaq":
        from nasdaq import NasdaqProvider

        return NasdaqProvider()
    raise ValueError(f"Unknown source_mode: {settings.source_mode!r}")


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


def _collect_and_save(settings: Settings, provider: OptionsDataProvider, symbol: str) -> dict:
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
    warnings = snapshot.warnings if chain_asof is not None else (*snapshot.warnings, "VALUATION_TIME_ASSUMED")
    q = settings.dividend_yields[symbol]

    priced, gex, surface, quality = analyze_snapshot(
        snapshot.contracts,
        spot=snapshot.underlying_price,
        r=settings.risk_free_rate,
        q=q,
        valuation_at=valuation_at,
        min_calendar_dte=MIN_CALENDAR_DTE,
        max_calendar_dte=MAX_CALENDAR_DTE,
        source_row_count=snapshot.source_row_count,
    )

    snapshot_id = uuid4()
    dashboard = DashboardResponse(
        snapshot_id=snapshot_id,
        symbol=symbol,
        source_mode=settings.source_mode,
        collected_at=snapshot.collected_at,
        valuation_at=valuation_at,
        chain_asof=snapshot.chain_asof,
        spot_asof=snapshot.spot_asof,
        oi_asof=snapshot.oi_asof,
        spot=snapshot.underlying_price,
        spot_kind=snapshot.underlying_price_kind,
        spot_origin=snapshot.underlying_price_origin,
        parameters=Parameters(
            r=settings.risk_free_rate,
            q=q,
            multiplier_assumed="MULTIPLIER_ASSUMED" in snapshot.warnings,
            min_calendar_dte=MIN_CALENDAR_DTE,
            max_calendar_dte=MAX_CALENDAR_DTE,
            min_strike_pct=MIN_STRIKE_PCT,
            max_strike_pct=MAX_STRIKE_PCT,
            pricing_time_convention=PRICING_TIME_CONVENTION,
            algorithm_version=ALGORITHM_VERSION,
        ),
        warnings=warnings,
        quality=quality,
        gex=gex,
        surface=surface,
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


def create_app(settings: Settings, provider: OptionsDataProvider | None = None) -> FastAPI:
    storage.init_schema(settings.db_path)
    active_provider = provider if provider is not None else build_provider(settings)
    gate = RefreshCoordinator(settings.refresh_min_interval_seconds)

    app = FastAPI()

    def _require_symbol(symbol: str) -> str:
        upper = symbol.upper()
        if upper not in settings.symbols:
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
        storage.get_latest_dashboard(settings.db_path, settings.source_mode, settings.default_symbol)
        return {"status": "ok", "schema_version": 1}

    @app.get("/api/config")
    def get_config() -> dict:
        response = ConfigResponse(
            symbols=settings.symbols,
            default_symbol=settings.default_symbol,
            source_mode=settings.source_mode,
            risk_free_rate=settings.risk_free_rate,
            dividend_yields=settings.dividend_yields,
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
    def refresh(symbol: str) -> dict:
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
            return _collect_and_save(settings, active_provider, symbol)
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


def _load_settings() -> Settings:
    path = Path(__file__).parent / "settings.json"
    return Settings.model_validate(json.loads(path.read_text()))


def main() -> None:
    import uvicorn

    app = create_app(_load_settings())
    uvicorn.run(app, host="127.0.0.1", port=8000, workers=1)


if __name__ == "__main__":
    main()
