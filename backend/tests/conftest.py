from datetime import UTC, date, datetime
from decimal import Decimal

from models import ChainRequest, ChainSnapshot, OptionQuote, Settings


def make_settings(db_path: str, source_mode: str = "fixture") -> Settings:
    return Settings(
        source_mode=source_mode,
        db_path=db_path,
        symbols=("SPY", "QQQ", "AAPL"),
        default_symbol="SPY",
        refresh_min_interval_seconds=60,
        risk_free_rate=0.04,
        dividend_yields={"SPY": 0.0, "QQQ": 0.0, "AAPL": 0.0},
    )


def make_snapshot(provider_id: str, symbol: str = "SPY") -> ChainSnapshot:
    call = OptionQuote(
        symbol=symbol,
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
    put = call.model_copy(update={"option_type": "P"})
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return ChainSnapshot(
        provider_id=provider_id,
        symbol=symbol,
        underlying_price=100.0,
        underlying_price_kind="last_trade",
        underlying_price_origin="chain_payload",
        collection_started_at=now,
        collected_at=now,
        chain_asof=now,
        spot_asof=None,
        oi_asof=None,
        contracts=(call, put),
        warnings=(),
        source_row_count=1,
        provider_response_count=1,
        raw_payload_json="{}",
    )


class StubProvider:
    def __init__(self, snapshot: ChainSnapshot | None = None, error: Exception | None = None) -> None:
        self.calls = 0
        self._snapshot = snapshot
        self._error = error

    def fetch_chain(self, request: ChainRequest) -> ChainSnapshot:
        self.calls += 1
        if self._error is not None:
            raise self._error
        return self._snapshot
