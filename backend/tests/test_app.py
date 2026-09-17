import math
import threading
import time
from datetime import datetime

from conftest import StubProvider, make_settings, make_snapshot
from fastapi.testclient import TestClient

from app import create_app
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

    class SlowProvider:
        def fetch_chain(self, request):
            release.wait(timeout=5)
            return make_snapshot(provider_id="fixture")

    client = TestClient(create_app(settings, provider=SlowProvider()))
    results: list[int] = []

    def run() -> None:
        results.append(client.post("/api/dashboard/SPY/refresh").status_code)

    thread = threading.Thread(target=run)
    thread.start()
    time.sleep(0.2)  # let the thread acquire the refresh lock and block

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
