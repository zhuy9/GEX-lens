from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from models import ChainSnapshot, OptionQuote, Settings

VALID = dict(
    source_mode="fixture",
    db_path="data/test.duckdb",
    symbols=("SPY", "QQQ", "AAPL"),
    default_symbol="SPY",
    refresh_min_interval_seconds=60,
    pricing_model="cash_pv_bsm_v2",
    rate_source="fixture",
    dividend_sources={"SPY": "fixture", "QQQ": "fixture", "AAPL": "fixture"},
    reference_inputs_path="reference_inputs.json",
)


def test_valid_settings_parses():
    settings = Settings(**VALID)
    assert settings.default_symbol == "SPY"


@pytest.mark.parametrize(
    "override",
    [
        {"symbols": ()},
        {"symbols": ("SPY", "SPY")},
        {"symbols": ("SPY", "QQQ", "AAPL", "MSFT")},
        {"default_symbol": "MSFT"},
        {"dividend_sources": {"SPY": "fixture", "QQQ": "fixture"}},
        {
            "dividend_sources": {
                "SPY": "fixture",
                "QQQ": "fixture",
                "AAPL": "fixture",
                "MSFT": "fixture",
            }
        },
        {"refresh_min_interval_seconds": 59},
    ],
)
def test_invalid_settings_rejected(override):
    # M1.8
    with pytest.raises(ValidationError):
        Settings(**{**VALID, **override})


# R06: canonical datetime fields must be timezone-aware -- enforced once at
# the model boundary so no provider adapter can inject a naive value that
# later crashes when subtracted from an aware expiry timestamp.


def _quote_kwargs(**overrides) -> dict:
    defaults = dict(
        symbol="TEST",
        expiration=date(2026, 1, 31),
        strike=Decimal("100"),
        option_type="C",
        bid=1.0,
        ask=1.1,
        last=1.05,
        volume=10,
        open_interest=100,
        multiplier=100,
        provider_contract_id=None,
        quote_asof=None,
        flags=(),
    )
    defaults.update(overrides)
    return defaults


def test_naive_quote_asof_is_rejected():
    with pytest.raises(ValidationError):
        OptionQuote(**_quote_kwargs(quote_asof=datetime(2026, 1, 15, 16, 0)))


def test_aware_quote_asof_is_accepted():
    quote = OptionQuote(**_quote_kwargs(quote_asof=datetime(2026, 1, 15, 16, 0, tzinfo=UTC)))
    assert quote.quote_asof is not None


def _snapshot_kwargs(**overrides) -> dict:
    call = OptionQuote(**_quote_kwargs())
    now = datetime(2026, 1, 1, tzinfo=UTC)
    defaults = dict(
        provider_id="test",
        symbol="TEST",
        underlying_price=100.0,
        underlying_price_kind="last_trade",
        underlying_price_origin="chain_payload",
        collection_started_at=now,
        collected_at=now,
        chain_asof=now,
        spot_asof=None,
        oi_asof=None,
        contracts=(call,),
        warnings=(),
        source_row_count=1,
        provider_response_count=1,
        raw_payload_json="{}",
    )
    defaults.update(overrides)
    return defaults


@pytest.mark.parametrize(
    "field", ["collection_started_at", "collected_at", "chain_asof", "spot_asof"]
)
def test_naive_snapshot_datetime_is_rejected(field):
    with pytest.raises(ValidationError):
        ChainSnapshot(**_snapshot_kwargs(**{field: datetime(2026, 1, 15, 16, 0)}))
