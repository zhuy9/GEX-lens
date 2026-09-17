"""Configuration, provider factory, four routes, orchestration."""

import json
import math
import threading
import time
from datetime import datetime, timedelta, timezone
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
        self._next_allowed_utc = datetime.now(timezone.utc) + timedelta(seconds=seconds)

    def end(self) -> None:
        self._lock.release()


def _error_response(
    status_code: int, code: str, message: str, retry_after_seconds: int | None = None
) -> JSONResponse:
    body = ErrorResponse(error=ErrorBody(code=code, message=message, retry_after_seconds=retry_after_seconds))
    headers = {"Retry-After": str(retry_after_seconds)} if retry_after_seconds else None
    return JSONResponse(status_code=status_code, content=body.model_dump(mode="json"), headers=headers)


def _collect_and_save(settings: Settings, provider: OptionsDataProvider, symbol: str) -> dict:
    request = ChainRequest(
        symbol=symbol, min_calendar_dte=MIN_CALENDAR_DTE, max_calendar_dte=MAX_CALENDAR_DTE
    )
    snapshot = provider.fetch_chain(request)

    if snapshot.symbol != symbol or snapshot.provider_id != settings.source_mode:
        raise ProviderError("SNAPSHOT_IDENTITY_MISMATCH", "Provider returned a mismatched snapshot")

    chain_asof = snapshot.chain_asof
    valuation_at = chain_asof if chain_asof is not None else snapshot.collection_started_at
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
        warnings=snapshot.warnings,
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
            raise HTTPException(
                status_code=422,
                detail={"code": "UNSUPPORTED_SYMBOL", "message": f"{symbol!r} is not configured"},
            )
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
            server_time=datetime.now(timezone.utc),
            refresh_not_before=gate.not_before,
        )
        return response.model_dump(mode="json")

    @app.get("/api/dashboard/{symbol}")
    def get_dashboard(symbol: str) -> dict:
        symbol = _require_symbol(symbol)
        dashboard = storage.get_latest_dashboard(settings.db_path, settings.source_mode, symbol)
        if dashboard is None:
            raise HTTPException(
                status_code=404, detail={"code": "NO_SNAPSHOT", "message": f"No saved snapshot for {symbol}"}
            )
        return dashboard

    @app.post("/api/dashboard/{symbol}/refresh")
    def refresh(symbol: str) -> dict:
        symbol = _require_symbol(symbol)
        acquired, retry_after = gate.begin()
        if not acquired:
            if retry_after is None:
                raise HTTPException(
                    status_code=409,
                    detail={"code": "REFRESH_IN_PROGRESS", "message": "A refresh is already running"},
                )
            raise HTTPException(
                status_code=429,
                detail={
                    "code": "REFRESH_COOLDOWN",
                    "message": "Refresh is on cooldown",
                    "retry_after_seconds": retry_after,
                },
            )
        try:
            return _collect_and_save(settings, active_provider, symbol)
        except ProviderError as exc:
            if exc.code == "UPSTREAM_RATE_LIMITED":
                retry_after = exc.retry_after_seconds
                effective = retry_after if (retry_after is not None and retry_after >= 60) else 300
                gate.extend_deadline(effective)
                raise HTTPException(
                    status_code=503,
                    detail={"code": exc.code, "message": str(exc), "retry_after_seconds": effective},
                ) from exc
            status_code = _PROVIDER_ERROR_STATUS.get(exc.code, 502)
            raise HTTPException(
                status_code=status_code, detail={"code": exc.code, "message": str(exc)}
            ) from exc
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=500, detail={"code": "INTERNAL_ERROR", "message": "Unexpected failure"}
            ) from exc
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
