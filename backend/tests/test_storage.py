from datetime import UTC, date, datetime
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
    now = datetime.now(UTC)
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


def test_health_check_succeeds_without_any_saved_data(tmp_path):
    # R13: health has no reason to decode a dashboard snapshot -- confirm it
    # works even when none exists yet.
    db_path = str(tmp_path / "test.duckdb")
    storage.init_schema(db_path)
    storage.health_check(db_path)  # must not raise


def test_option_quote_fields_round_trip_into_their_own_columns(tmp_path):
    # R13: CSV row order, the read_csv() type map, and both the INSERT
    # target and SELECT column lists must all agree, or a value lands in
    # the wrong column silently (types like DOUBLE/DOUBLE wouldn't even
    # raise). Distinct, non-confusable values per field catch a swap.
    db_path = str(tmp_path / "test.duckdb")
    storage.init_schema(db_path)
    quote = OptionQuote(
        symbol="SPY",
        expiration=date(2026, 1, 31),
        strike=Decimal("101.50"),
        option_type="P",
        bid=11.11,
        ask=22.22,
        last=33.33,
        volume=444,
        open_interest=5555,
        multiplier=100,
        provider_contract_id="CID-1",
        quote_asof=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
        flags=("MULTIPLIER_ASSUMED",),
    )
    pq = PricedQuote(quote=quote, mid=16.665, iv=0.42, gamma=0.0123, exclusion_reason=None)
    snapshot_id = uuid4()
    now = datetime.now(UTC)
    storage.save_snapshot(
        db_path,
        source_mode="fixture",
        symbol="SPY",
        snapshot_id=snapshot_id,
        collected_at=now,
        valuation_at=now,
        raw_payload_json="{}",
        dashboard_json={"snapshot_id": str(snapshot_id)},
        priced_quotes=(pq,),
    )

    conn = duckdb.connect(db_path)
    try:
        row = conn.execute(
            """
            SELECT symbol, expiration, strike, option_type, bid, ask, last,
                   volume, open_interest, multiplier, provider_contract_id,
                   mid, iv, gamma, exclusion_reason, flags
            FROM option_quotes WHERE snapshot_id = ?
            """,
            [snapshot_id],
        ).fetchone()
    finally:
        conn.close()

    assert row is not None
    assert row == (
        "SPY",
        date(2026, 1, 31),
        Decimal("101.500000"),
        "P",
        11.11,
        22.22,
        33.33,
        444,
        5555,
        100,
        "CID-1",
        16.665,
        0.42,
        0.0123,
        None,
        '["MULTIPLIER_ASSUMED"]',
    )


def test_untrusted_string_with_newline_and_quote_round_trips(tmp_path):
    # provider_contract_id is raw provider text. An embedded newline used to
    # break DuckDB's CSV parse (CRLF rows + a bare LF inside a quoted field)
    # and fail the whole save.
    db_path = str(tmp_path / "test.duckdb")
    storage.init_schema(db_path)
    weird = 'a,b"c\nd'
    pq = make_priced_quote()
    pq = pq.model_copy(update={"quote": pq.quote.model_copy(update={"provider_contract_id": weird})})
    snapshot_id = uuid4()
    now = datetime.now(UTC)
    storage.save_snapshot(
        db_path,
        source_mode="fixture",
        symbol="SPY",
        snapshot_id=snapshot_id,
        collected_at=now,
        valuation_at=now,
        raw_payload_json="{}",
        dashboard_json={"snapshot_id": str(snapshot_id)},
        priced_quotes=(pq,),
    )

    conn = duckdb.connect(db_path)
    try:
        value = _scalar(
            conn, "SELECT provider_contract_id FROM option_quotes WHERE snapshot_id = ?", [snapshot_id]
        )
    finally:
        conn.close()
    assert value == weird


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
    now = datetime.now(UTC)
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


# --- ADR-0001 Section 8.1: reference_cache ---------------------------------


def test_reference_cache_round_trips_and_reports_none_when_absent(tmp_path):
    db_path = str(tmp_path / "t.duckdb")
    storage.init_schema(db_path)
    assert storage.get_reference_cache(db_path, kind="rate", provider_id="nyfed_sofr", subject="USD") is None

    fetched_at = datetime.now(UTC)
    storage.upsert_reference_cache(
        db_path,
        kind="rate",
        provider_id="nyfed_sofr",
        subject="USD",
        fetched_at=fetched_at,
        normalized_json={"provider_id": "nyfed_sofr"},
        raw_payload_json='{"refRates": []}',
    )
    cached = storage.get_reference_cache(db_path, kind="rate", provider_id="nyfed_sofr", subject="USD")
    assert cached is not None
    assert cached["normalized_json"] == {"provider_id": "nyfed_sofr"}
    assert cached["raw_payload_json"] == '{"refRates": []}'


def test_reference_cache_upsert_replaces_the_prior_entry_for_the_same_key(tmp_path):
    db_path = str(tmp_path / "t.duckdb")
    storage.init_schema(db_path)
    common = dict(db_path=db_path, kind="dividends", provider_id="fixture", subject="SPY")
    storage.upsert_reference_cache(
        **common, fetched_at=datetime(2026, 1, 1, tzinfo=UTC),
        normalized_json={"v": 1}, raw_payload_json="{}",
    )
    storage.upsert_reference_cache(
        **common, fetched_at=datetime(2026, 1, 2, tzinfo=UTC),
        normalized_json={"v": 2}, raw_payload_json="{}",
    )
    cached = storage.get_reference_cache(db_path, kind="dividends", provider_id="fixture", subject="SPY")
    assert cached["normalized_json"] == {"v": 2}  # only the latest entry survives, per key
