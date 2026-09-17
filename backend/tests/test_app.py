import threading
import time

from conftest import StubProvider, make_settings, make_snapshot
from fastapi.testclient import TestClient

from app import create_app


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
