import time

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
        # DTE scope is relative to the pricing timestamp (chain_asof), not
        # collection_started_at -- that's real wall-clock time (R11) and
        # unrelated to the fixed synthetic valuation clock expirations were
        # generated against.
        assert snapshot.chain_asof is not None
        for contract in snapshot.contracts:
            dte = calendar_dte(contract.expiration, snapshot.chain_asof)
            assert 1 <= dte <= 60


def test_fixture_provider_is_deterministic_across_calls():
    provider = FixtureProvider()
    request = ChainRequest(symbol="SPY", min_calendar_dte=1, max_calendar_dte=60)
    first = provider.fetch_chain(request)
    second = provider.fetch_chain(request)
    assert first.contracts == second.contracts
    assert first.underlying_price == second.underlying_price


def test_fixture_collection_time_advances_while_valuation_stays_fixed():
    # R11: only collection_started_at/collected_at are real wall-clock time;
    # chain_asof/quote_asof (what pricing/analytics actually use) stay
    # pinned to FIXED_VALUATION_AT so re-pricing the same fixture is still
    # deterministic.
    provider = FixtureProvider()
    request = ChainRequest(symbol="SPY", min_calendar_dte=1, max_calendar_dte=60)
    first = provider.fetch_chain(request)
    time.sleep(0.01)
    second = provider.fetch_chain(request)

    assert second.collected_at > first.collected_at
    assert first.chain_asof == second.chain_asof
    assert first.contracts[0].quote_asof == second.contracts[0].quote_asof


def test_fixture_provider_zero_http_dependency():
    import fixtures

    assert not hasattr(fixtures, "httpx")
