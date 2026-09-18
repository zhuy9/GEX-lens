import json
import math
import threading
import time
import uuid
from datetime import UTC, date, datetime

import pytest
from conftest import StubProvider, make_settings, make_snapshot
from fastapi.testclient import TestClient

import storage
from app import create_app
from models import DividendFeedSnapshot, RateBatch, RateObservation
from provider import ProviderError


def test_health_check(tmp_path):
    settings = make_settings(str(tmp_path / "t.duckdb"))
    client = TestClient(create_app(settings, provider=StubProvider()))
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "schema_version": 1}


def test_config_shape(tmp_path):
    settings = make_settings(str(tmp_path / "t.duckdb"))
    client = TestClient(create_app(settings, provider=StubProvider()))
    body = client.get("/api/config").json()
    assert body["symbols"] == ["SPY", "QQQ", "AAPL"]
    assert body["default_symbol"] == "SPY"
    assert body["refresh_mode"] == "manual"
    assert body["refresh_in_progress"] is False
    assert body["refresh_not_before"] is None
    assert "server_time" in body


def test_unsupported_symbol_rejected(tmp_path):
    settings = make_settings(str(tmp_path / "t.duckdb"))
    client = TestClient(create_app(settings, provider=StubProvider()))
    response = client.get("/api/dashboard/MSFT")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "UNSUPPORTED_SYMBOL"


def test_missing_snapshot_returns_404(tmp_path):
    settings = make_settings(str(tmp_path / "t.duckdb"))
    client = TestClient(create_app(settings, provider=StubProvider()))
    response = client.get("/api/dashboard/SPY")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NO_SNAPSHOT"


def test_refresh_then_get_returns_same_snapshot(tmp_path):
    settings = make_settings(str(tmp_path / "t.duckdb"))
    stub = StubProvider(snapshot=make_snapshot(provider_id="fixture"))
    client = TestClient(create_app(settings, provider=stub))
    posted = client.post("/api/dashboard/SPY/refresh").json()
    fetched = client.get("/api/dashboard/SPY").json()
    assert posted["snapshot_id"] == fetched["snapshot_id"]


def test_cooldown_returns_429_without_second_provider_call(tmp_path):
    # M3.2, M3.7
    settings = make_settings(str(tmp_path / "t.duckdb"))
    stub = StubProvider(snapshot=make_snapshot(provider_id="fixture"))
    client = TestClient(create_app(settings, provider=stub))

    assert client.post("/api/dashboard/SPY/refresh").status_code == 200
    second = client.post("/api/dashboard/SPY/refresh")

    assert second.status_code == 429
    body = second.json()
    assert body["error"]["code"] == "REFRESH_COOLDOWN"
    assert body["error"]["retry_after_seconds"] > 0
    assert stub.calls == 1


def test_concurrent_refresh_returns_409(tmp_path):
    # M3.2
    settings = make_settings(str(tmp_path / "t.duckdb"))
    release = threading.Event()
    entered = threading.Event()  # R14: signaled, not guessed at via sleep

    class SlowProvider:
        def fetch_chain(self, request):
            entered.set()
            release.wait(timeout=5)
            return make_snapshot(provider_id="fixture")

    client = TestClient(create_app(settings, provider=SlowProvider()))
    results: list[int] = []

    def run() -> None:
        results.append(client.post("/api/dashboard/SPY/refresh").status_code)

    thread = threading.Thread(target=run)
    thread.start()
    assert entered.wait(timeout=5)  # the first request has acquired the refresh lock

    second = client.post("/api/dashboard/SPY/refresh")
    release.set()
    thread.join(timeout=5)

    assert second.status_code == 409
    assert second.json()["error"]["code"] == "REFRESH_IN_PROGRESS"
    assert results == [200]


def _assert_all_finite(value) -> None:
    if isinstance(value, float):
        assert math.isfinite(value), f"non-finite float found: {value}"
    elif isinstance(value, dict):
        for v in value.values():
            _assert_all_finite(v)
    elif isinstance(value, list):
        for v in value:
            _assert_all_finite(v)


def test_dashboard_response_never_contains_nan_or_infinity(tmp_path):
    # M3.1
    from fixtures import FixtureProvider

    settings = make_settings(str(tmp_path / "t.duckdb"))
    client = TestClient(create_app(settings, provider=FixtureProvider()))

    response = client.post("/api/dashboard/SPY/refresh")
    assert response.status_code == 200
    assert "NaN" not in response.text
    assert "Infinity" not in response.text
    _assert_all_finite(response.json())


def test_dashboard_spot_provenance_and_internal_consistency(tmp_path):
    # M3.4
    settings = make_settings(str(tmp_path / "t.duckdb"))
    stub = StubProvider(snapshot=make_snapshot(provider_id="fixture"))
    client = TestClient(create_app(settings, provider=stub))

    dashboard = client.post("/api/dashboard/SPY/refresh").json()
    assert dashboard["spot_kind"] == "last_trade"
    assert dashboard["spot_origin"] == "chain_payload"
    assert dashboard["spot"] == 100.0

    fetched = client.get("/api/dashboard/SPY").json()
    assert fetched["snapshot_id"] == dashboard["snapshot_id"]
    assert fetched["valuation_at"] == dashboard["valuation_at"]
    assert fetched["parameters"] == dashboard["parameters"]


def test_missing_chain_asof_adds_valuation_time_assumed_at_the_orchestration_boundary(tmp_path):
    # R06: any provider whose chain_asof is null gets this warning from
    # app.py, not from remembering to add it in the adapter itself.
    settings = make_settings(str(tmp_path / "t.duckdb"))
    snapshot = make_snapshot(provider_id="fixture").model_copy(update={"chain_asof": None})
    stub = StubProvider(snapshot=snapshot)
    client = TestClient(create_app(settings, provider=stub))

    dashboard = client.post("/api/dashboard/SPY/refresh").json()
    assert "VALUATION_TIME_ASSUMED" in dashboard["warnings"]
    # falls back to collection_started_at, which make_snapshot sets equal to
    # collected_at -- collection_started_at itself isn't in the public API
    assert dashboard["valuation_at"] == dashboard["collected_at"]
    assert dashboard["chain_asof"] is None


def test_get_endpoints_never_call_provider(tmp_path):
    # M3.6
    settings = make_settings(str(tmp_path / "t.duckdb"))
    stub = StubProvider(snapshot=make_snapshot(provider_id="fixture"))
    client = TestClient(create_app(settings, provider=stub))

    client.get("/api/health")
    client.get("/api/config")
    client.get("/api/dashboard/SPY")  # 404, still zero calls
    client.get("/api/dashboard/MSFT")  # 422, still zero calls

    assert stub.calls == 0


def test_rejected_refresh_requests_make_zero_provider_calls(tmp_path):
    # M3.6
    settings = make_settings(str(tmp_path / "t.duckdb"))
    stub = StubProvider(snapshot=make_snapshot(provider_id="fixture"))
    client = TestClient(create_app(settings, provider=stub))

    client.post("/api/dashboard/MSFT/refresh")  # unsupported symbol
    assert stub.calls == 0

    client.post("/api/dashboard/SPY/refresh")  # succeeds, consumes cooldown
    assert stub.calls == 1

    client.post("/api/dashboard/SPY/refresh")  # cooldown active -> 429
    assert stub.calls == 1  # unchanged


def test_failed_refresh_still_consumes_cooldown(tmp_path):
    # M3.7: "A failed permitted attempt still consumes cooldown"
    settings = make_settings(str(tmp_path / "t.duckdb"))
    stub = StubProvider(error=ProviderError("UPSTREAM_UNAVAILABLE", "boom"))
    client = TestClient(create_app(settings, provider=stub))

    first = client.post("/api/dashboard/SPY/refresh")
    assert first.status_code == 502
    assert stub.calls == 1

    second = client.post("/api/dashboard/SPY/refresh")
    assert second.status_code == 429  # cooldown from the failed attempt still applies
    assert stub.calls == 1  # no second provider call


def test_unexpected_exception_is_logged_sanitized_and_does_not_leak_internals(tmp_path, caplog):
    # R09: a genuine bug (not a ProviderError) must still emit a diagnostic
    # log, release the refresh lock, preserve the previous snapshot, and
    # never expose the exception's own message to the client.
    settings = make_settings(str(tmp_path / "t.duckdb"))
    good_stub = StubProvider(snapshot=make_snapshot(provider_id="fixture"))
    first = TestClient(create_app(settings, provider=good_stub)).post("/api/dashboard/SPY/refresh").json()

    failing_stub = StubProvider(error=RuntimeError("boom: leaked secret token abc123"))
    # raise_server_exceptions=False: a real deployment (uvicorn) returns the
    # sanitized JSONResponse our Exception handler builds; TestClient's
    # default instead re-raises the original exception into the test
    # process for easier debugging. Disable that to observe the actual HTTP
    # response this path produces.
    client = TestClient(create_app(settings, provider=failing_stub), raise_server_exceptions=False)

    with caplog.at_level("ERROR"):
        response = client.post("/api/dashboard/SPY/refresh")

    assert response.status_code == 500
    body = response.json()
    assert body["error"]["code"] == "INTERNAL_ERROR"
    assert "boom" not in response.text
    assert "secret token" not in response.text

    assert any("boom" in record.message or "boom" in (record.exc_text or "") for record in caplog.records)

    # Lock released: the next attempt hits cooldown (429), not "still running" (409).
    second = client.post("/api/dashboard/SPY/refresh")
    assert second.status_code == 429

    unchanged = client.get("/api/dashboard/SPY").json()
    assert unchanged["snapshot_id"] == first["snapshot_id"]


def test_repeated_fixture_collections_are_ordered_by_recency_not_uuid(tmp_path):
    # R11: every fixture snapshot used to share one frozen collected_at, so
    # "latest" (storage.py: ORDER BY collected_at DESC, snapshot_id DESC)
    # fell back to comparing random UUIDs -- a later collection could sort
    # as "older" than an earlier one. Calls the orchestration function
    # directly since the refresh route's cooldown (irrelevant to this
    # storage-ordering question) would otherwise block the second call.
    from app import _collect_and_save, build_dividend_provider, build_rate_provider
    from fixtures import FixtureProvider

    settings = make_settings(str(tmp_path / "t.duckdb"))
    storage.init_schema(settings.db_path)
    provider = FixtureProvider()
    rate_provider = build_rate_provider(settings.rate_source)
    dividend_providers = {
        symbol: build_dividend_provider(settings.dividend_sources[symbol]) for symbol in settings.symbols
    }

    first = _collect_and_save(
        settings, provider, rate_provider, dividend_providers, "SPY", force_reference_refresh=False
    )
    time.sleep(0.01)
    second = _collect_and_save(
        settings, provider, rate_provider, dividend_providers, "SPY", force_reference_refresh=False
    )
    assert first["snapshot_id"] != second["snapshot_id"]

    latest = storage.get_latest_dashboard(settings.db_path, settings.source_mode, "SPY")
    assert latest["snapshot_id"] == second["snapshot_id"]


def test_shutdown_closes_a_self_built_providers_http_client_only(tmp_path, monkeypatch):
    # R15: NasdaqProvider owns an httpx.Client when build_provider() builds
    # it (no client was injected). The app must close that client at
    # shutdown, and must never touch a client an injected/test provider owns.
    from nasdaq import NasdaqProvider

    close_calls: list[NasdaqProvider] = []
    original_close = NasdaqProvider.close

    def tracked_close(self: NasdaqProvider) -> None:
        close_calls.append(self)
        original_close(self)

    monkeypatch.setattr(NasdaqProvider, "close", tracked_close)

    settings_nasdaq = make_settings(str(tmp_path / "a.duckdb"), source_mode="nasdaq")
    with TestClient(create_app(settings_nasdaq)):
        pass  # __exit__ runs the lifespan shutdown phase

    assert len(close_calls) == 1  # build_provider()'s own NasdaqProvider was closed
    assert close_calls[0]._client.is_closed is True

    class TrackedProvider:
        def __init__(self) -> None:
            self.closed = False

        def fetch_chain(self, request):
            raise AssertionError("not used in this test")

        def close(self) -> None:
            self.closed = True

    injected = TrackedProvider()
    settings_fixture = make_settings(str(tmp_path / "b.duckdb"))
    with TestClient(create_app(settings_fixture, provider=injected)):
        pass
    assert injected.closed is False  # app.py must never close an injected provider's client


def test_arbitrary_reference_providers_injected_via_create_app_flow_end_to_end(tmp_path):
    # ADR-0001 4.2: "Inject reference providers through keyword arguments to
    # create_app, just as the chain provider is injectable. A stub with an
    # arbitrary provider ID must pass through resolution, analytics,
    # persistence, and response generation."
    reference_inputs_path = tmp_path / "reference_inputs.json"
    reference_inputs_path.write_text(
        json.dumps(
            {
                "input_schema_version": 1,
                "manual_rate": None,
                "schedules": {
                    "SPY": {
                        "symbol": "SPY",
                        "reviewed_at": "2026-01-02T21:00:00Z",
                        "coverage_start": "2026-01-02",
                        "coverage_end": "2026-04-02",
                        "no_other_events_expected": True,
                        "source_refs": ["synthetic"],
                        "expected_events": [],
                    }
                },
            }
        )
    )

    class CustomRateProvider:
        def __init__(self) -> None:
            self.calls = 0
            self.closed = False

        def fetch_rates(self) -> RateBatch:
            self.calls += 1
            return RateBatch(
                provider_id="totally-custom-rate-stub",
                fetched_at=datetime(2026, 1, 2, 21, 0, tzinfo=UTC),
                source_ref="test",
                observations=(
                    RateObservation(
                        effective_date=date(2026, 1, 2),
                        percent_rate=4.0,
                        rate_type="SOFR",
                        revision_indicator=None,
                    ),
                ),
                raw_payload_json="{}",
            )

        def close(self) -> None:
            self.closed = True

    class CustomDividendProvider:
        def __init__(self) -> None:
            self.calls = 0
            self.closed = False

        def fetch_dividends(self, symbol: str) -> DividendFeedSnapshot:
            self.calls += 1
            return DividendFeedSnapshot(
                provider_id="totally-custom-dividend-stub",
                symbol=symbol,
                fetched_at=datetime(2026, 1, 2, 21, 0, tzinfo=UTC),
                source_asof=None,
                records=(),
                raw_payload_json="{}",
                warnings=(),
            )

        def close(self) -> None:
            self.closed = True

    settings = make_settings(str(tmp_path / "t.duckdb")).model_copy(
        update={
            "rate_source": "totally-custom-rate-stub",
            "dividend_sources": {"SPY": "totally-custom-dividend-stub", "QQQ": "fixture", "AAPL": "fixture"},
            "reference_inputs_path": str(reference_inputs_path),
        }
    )
    rate_provider = CustomRateProvider()
    dividend_provider = CustomDividendProvider()

    with TestClient(
        create_app(
            settings,
            provider=StubProvider(make_snapshot("fixture")),
            rate_provider=rate_provider,
            dividend_providers={"SPY": dividend_provider},
        )
    ) as client:
        response = client.post("/api/dashboard/SPY/refresh")
        assert response.status_code == 200
        body = response.json()
        assert body["market_inputs"]["rate"]["source_provider_id"] == "totally-custom-rate-stub"
        assert rate_provider.calls == 1
        assert dividend_provider.calls == 1

        saved = client.get("/api/dashboard/SPY").json()
        assert saved["market_inputs"]["rate"]["source_provider_id"] == "totally-custom-rate-stub"

    # Injected reference providers are never closed by the app, matching the
    # chain provider's own ownership rule (see the shutdown test above).
    assert rate_provider.closed is False
    assert dividend_provider.closed is False


def test_provider_rate_limit_extends_cooldown_and_returns_503(tmp_path):
    # M3.7: "a provider 429 extends it according to Section 5.3"
    settings = make_settings(str(tmp_path / "t.duckdb"))
    stub = StubProvider(error=ProviderError("UPSTREAM_RATE_LIMITED", "rate limited", retry_after_seconds=120))
    client = TestClient(create_app(settings, provider=stub))

    response = client.post("/api/dashboard/SPY/refresh")
    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "UPSTREAM_RATE_LIMITED"
    assert body["error"]["retry_after_seconds"] == 120

    config = client.get("/api/config").json()
    not_before = datetime.fromisoformat(config["refresh_not_before"])
    server_time = datetime.fromisoformat(config["server_time"])
    # the 120s provider retry_after should win over the 60s baseline cooldown
    assert (not_before - server_time).total_seconds() > 60


def test_short_valid_retry_after_is_clamped_to_60_not_discarded_to_300(tmp_path):
    # R03/PRD 5.3: "a valid Retry-After value, subject to a minimum of 60
    # seconds" means clamp a short valid value, not treat it as absent.
    settings = make_settings(str(tmp_path / "t.duckdb"))
    stub = StubProvider(error=ProviderError("UPSTREAM_RATE_LIMITED", "rate limited", retry_after_seconds=5))
    client = TestClient(create_app(settings, provider=stub))

    response = client.post("/api/dashboard/SPY/refresh")
    assert response.status_code == 503
    assert response.json()["error"]["retry_after_seconds"] == 60


def test_missing_retry_after_falls_back_to_300(tmp_path):
    settings = make_settings(str(tmp_path / "t.duckdb"))
    stub = StubProvider(
        error=ProviderError("UPSTREAM_RATE_LIMITED", "rate limited", retry_after_seconds=None)
    )
    client = TestClient(create_app(settings, provider=stub))

    response = client.post("/api/dashboard/SPY/refresh")
    assert response.status_code == 503
    assert response.json()["error"]["retry_after_seconds"] == 300


# --- ADR-0001 M4: schema-version-2 dashboards ------------------------------


def test_v2_dashboard_carries_provenance_hashes_and_model_identifiers(tmp_path):
    # M4.1
    settings = make_settings(str(tmp_path / "t.duckdb"))
    stub = StubProvider(snapshot=make_snapshot(provider_id="fixture"))
    client = TestClient(create_app(settings, provider=stub))

    dashboard = client.post("/api/dashboard/SPY/refresh").json()
    assert dashboard["schema_version"] == 2
    assert dashboard["instrument"] == {
        "symbol": "SPY",
        "instrument_class": "etf",
        "currency": "USD",
        "exercise_style": "american",
        "standard_multiplier": 100,
    }
    assert dashboard["parameters"]["model_id"] == "cash_pv_bsm_v2"
    assert dashboard["parameters"]["algorithm_version"] == "2"
    assert dashboard["parameters"]["dividend_model"] == "cash_schedule"
    assert dashboard["parameters"]["q"] == 0.0
    assert dashboard["market_inputs"]["rate"]["source_provider_id"] == "fixture"
    assert dashboard["market_inputs"]["dividend_schedule"]["review"]["symbol"] == "SPY"
    assert len(dashboard["market_inputs"]["reference_bundle_hash"]) == 64
    assert len(dashboard["calculation_input_hash"]) == 64
    assert dashboard["pricing_contexts"]  # at least one in-scope expiry
    assert dashboard["gex"]["canonical_unit"] == "usd_delta_notional_per_1pct"


def test_v2_dashboard_never_exposes_a_raw_reference_payload(tmp_path):
    # M4.7
    settings = make_settings(str(tmp_path / "t.duckdb"))
    stub = StubProvider(snapshot=make_snapshot(provider_id="fixture"))
    client = TestClient(create_app(settings, provider=stub))

    dashboard = client.post("/api/dashboard/SPY/refresh").json()
    assert "raw_payload_json" not in json.dumps(dashboard)


def test_v1_saved_row_is_returned_unchanged_never_upgraded(tmp_path):
    # M4.2: a v1 database opens without destructive migration; GET preserves
    # its saved values verbatim, and a new refresh still writes v2 alongside it.
    db_path = str(tmp_path / "t.duckdb")
    storage.init_schema(db_path)
    v1_dashboard = {
        "schema_version": 1,
        "snapshot_id": str(uuid.uuid4()),
        "symbol": "SPY",
        "source_mode": "fixture",
        "collected_at": "2025-01-01T00:00:00Z",
        "valuation_at": "2025-01-01T00:00:00Z",
        "chain_asof": "2025-01-01T00:00:00Z",
        "spot_asof": None,
        "oi_asof": None,
        "spot": 100.0,
        "spot_kind": "last_trade",
        "spot_origin": "chain_payload",
        "parameters": {
            "r": 0.04,
            "q": 0.0,
            "multiplier_assumed": False,
            "min_calendar_dte": 1,
            "max_calendar_dte": 60,
            "min_strike_pct": 0.8,
            "max_strike_pct": 1.2,
            "pricing_time_convention": "16:00 America/New_York on expiration date",
            "algorithm_version": "1",
        },
        "warnings": [],
        "quality": {
            "source_rows": 0,
            "normalized_contracts": 0,
            "in_scope_contracts": 0,
            "valid_ivs": 0,
            "known_oi_contracts": 0,
            "complete_gex_cells": 0,
            "exclusion_counts": {},
        },
        "gex": {"strikes": [], "expirations": [], "cells": []},
        "surface": {
            "status": "INSUFFICIENT_DATA",
            "k": [],
            "expirations": [],
            "dte": [],
            "iv": None,
            "observations": [],
        },
    }
    storage.save_snapshot(
        db_path,
        source_mode="fixture",
        symbol="SPY",
        snapshot_id=uuid.UUID(v1_dashboard["snapshot_id"]),
        collected_at=datetime(2025, 1, 1, tzinfo=UTC),
        valuation_at=datetime(2025, 1, 1, tzinfo=UTC),
        raw_payload_json="{}",
        dashboard_json=v1_dashboard,
        priced_quotes=(),
    )

    settings = make_settings(db_path)
    client = TestClient(create_app(settings, provider=StubProvider()))
    fetched = client.get("/api/dashboard/SPY").json()
    assert fetched == v1_dashboard  # byte-for-byte: never reconstructed or upgraded on read


def test_force_reference_refresh_param_does_not_bypass_the_cooldown_gate(tmp_path):
    # M2.4/M4.6: the flag bypasses reference-input TTL only, never the
    # global refresh lock/cooldown.
    settings = make_settings(str(tmp_path / "t.duckdb"))
    stub = StubProvider(snapshot=make_snapshot(provider_id="fixture"))
    client = TestClient(create_app(settings, provider=stub))

    first = client.post("/api/dashboard/SPY/refresh?force_reference_refresh=true")
    assert first.status_code == 200

    second = client.post("/api/dashboard/SPY/refresh?force_reference_refresh=true")
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "REFRESH_COOLDOWN"


def test_old_config_schema_raises_a_clear_migration_error(tmp_path):
    from app import _load_settings

    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps(
            {
                "source_mode": "fixture",
                "db_path": "data/x.duckdb",
                "symbols": ["SPY"],
                "default_symbol": "SPY",
                "refresh_min_interval_seconds": 60,
                "risk_free_rate": 0.04,
                "dividend_yields": {"SPY": 0.0},
            }
        )
    )
    with pytest.raises(ValueError, match="pre-ADR-0001"):
        _load_settings(path)
