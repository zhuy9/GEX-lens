from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

import duckdb
import pytest

import storage
from models import OptionQuote, PricedQuote


def make_priced_quote(strike="100") -> PricedQuote:
    quote = OptionQuote(
        symbol="SPY",
        expiration=date(2026, 1, 31),
        strike=Decimal(strike),
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
    return PricedQuote(quote=quote, mid=1.05, iv=0.2, gamma=0.01, exclusion_reason=None)


def _scalar(conn: duckdb.DuckDBPyConnection, sql: str, params: list | None = None) -> int:
    row = conn.execute(sql, params or [])
    fetched = row.fetchone()
    assert fetched is not None
    return fetched[0]


def save_one(db_path: str, *, source_mode="fixture", symbol="SPY", strike="100"):
    snapshot_id = uuid4()
    now = datetime.now(timezone.utc)
    storage.save_snapshot(
        db_path,
        source_mode=source_mode,
        symbol=symbol,
        snapshot_id=snapshot_id,
        collected_at=now,
        valuation_at=now,
        raw_payload_json="{}",
        dashboard_json={"snapshot_id": str(snapshot_id), "symbol": symbol},
        priced_quotes=(make_priced_quote(strike),),
    )
    return snapshot_id


def test_schema_creates_and_latest_survives_reconnect(tmp_path):
    # M1.1, M1.4
    db_path = str(tmp_path / "test.duckdb")
    storage.init_schema(db_path)
    assert storage.get_latest_dashboard(db_path, "fixture", "SPY") is None

    save_one(db_path)
    dashboard = storage.get_latest_dashboard(db_path, "fixture", "SPY")
    assert dashboard is not None
    assert dashboard["symbol"] == "SPY"


def test_latest_is_filtered_by_source_mode_and_symbol(tmp_path):
    # M1.6
    db_path = str(tmp_path / "test.duckdb")
    storage.init_schema(db_path)
    save_one(db_path, source_mode="fixture", symbol="SPY")
    save_one(db_path, source_mode="nasdaq", symbol="SPY")
    save_one(db_path, source_mode="fixture", symbol="QQQ")

    assert storage.get_latest_dashboard(db_path, "fixture", "SPY") is not None
    assert storage.get_latest_dashboard(db_path, "nasdaq", "SPY") is not None
    assert storage.get_latest_dashboard(db_path, "fixture", "AAPL") is None


def test_pruning_keeps_newest_20_per_source_and_symbol(tmp_path):
    # M1.6
    db_path = str(tmp_path / "test.duckdb")
    storage.init_schema(db_path)
    for _ in range(21):
        save_one(db_path, source_mode="fixture", symbol="SPY")
    save_one(db_path, source_mode="nasdaq", symbol="SPY")  # different source, must survive untouched
    save_one(db_path, source_mode="fixture", symbol="QQQ")  # different symbol, must survive untouched

    conn = duckdb.connect(db_path)
    try:
        spy_fixture_count = _scalar(
            conn, "SELECT count(*) FROM snapshots WHERE source_mode='fixture' AND symbol='SPY'"
        )
        nasdaq_count = _scalar(conn, "SELECT count(*) FROM snapshots WHERE source_mode='nasdaq'")
        qqq_count = _scalar(conn, "SELECT count(*) FROM snapshots WHERE symbol='QQQ'")
    finally:
        conn.close()

    assert spy_fixture_count == 20
    assert nasdaq_count == 1
    assert qqq_count == 1


def test_failed_transaction_leaves_no_partial_snapshot(tmp_path):
    # M1.5
    db_path = str(tmp_path / "test.duckdb")
    storage.init_schema(db_path)
    save_one(db_path)

    snapshot_id = uuid4()
    now = datetime.now(timezone.utc)
    bad_quote = make_priced_quote().model_copy(
        update={"quote": make_priced_quote().quote.model_copy(update={"symbol": None})}
    )

    with pytest.raises(duckdb.Error):
        storage.save_snapshot(
            db_path,
            source_mode="fixture",
            symbol="SPY",
            snapshot_id=snapshot_id,
            collected_at=now,
            valuation_at=now,
            raw_payload_json="{}",
            dashboard_json={"snapshot_id": str(snapshot_id)},
            priced_quotes=(bad_quote,),
        )

    conn = duckdb.connect(db_path)
    try:
        count = _scalar(conn, "SELECT count(*) FROM snapshots WHERE snapshot_id = ?", [snapshot_id])
        orphan_quotes = _scalar(
            conn, "SELECT count(*) FROM option_quotes WHERE snapshot_id = ?", [snapshot_id]
        )
    finally:
        conn.close()
    assert count == 0
    assert orphan_quotes == 0
