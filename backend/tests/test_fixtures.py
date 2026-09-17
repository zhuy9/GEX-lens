from fixtures import FixtureProvider
from models import ChainRequest, calendar_dte


def test_fixture_provider_returns_in_scope_contracts_for_each_symbol():
    provider = FixtureProvider()
    for symbol in ("SPY", "QQQ", "AAPL"):
        request = ChainRequest(symbol=symbol, min_calendar_dte=1, max_calendar_dte=60)
        snapshot = provider.fetch_chain(request)
        assert snapshot.symbol == symbol
        assert snapshot.provider_id == "fixture"
        assert snapshot.underlying_price > 0
        assert len(snapshot.contracts) > 0
        for contract in snapshot.contracts:
            dte = calendar_dte(contract.expiration, snapshot.collection_started_at)
            assert 1 <= dte <= 60


def test_fixture_provider_is_deterministic_across_calls():
    provider = FixtureProvider()
    request = ChainRequest(symbol="SPY", min_calendar_dte=1, max_calendar_dte=60)
    first = provider.fetch_chain(request)
    second = provider.fetch_chain(request)
    assert first.contracts == second.contracts
    assert first.underlying_price == second.underlying_price


def test_fixture_provider_zero_http_dependency():
    import fixtures

    assert not hasattr(fixtures, "httpx")
