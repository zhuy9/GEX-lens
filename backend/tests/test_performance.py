"""M3.5: processing+persistence and GET-latest timing."""

import time
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import storage
from analytics import analyze_snapshot
from models import OptionQuote

VALUATION_AT = datetime(2026, 1, 1, tzinfo=UTC)
WARMUP_RUNS = 3
MEASURED_RUNS = 20


def _make_2000_contracts() -> tuple[OptionQuote, ...]:
    """20 expirations x 50 strikes x 2 sides = 2000 unique contracts."""
    base_date = date(2026, 1, 2)
    contracts = []
    for e in range(20):
        expiration = base_date + timedelta(days=e + 1)
        for s in range(50):
            strike = Decimal(80 + s)
            for option_type in ("C", "P"):
                contracts.append(
                    OptionQuote(
                        symbol="SPY",
                        expiration=expiration,
                        strike=strike,
                        option_type=option_type,
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
                )
    return tuple(contracts)


def _p95(durations: list[float]) -> float:
    durations = sorted(durations)
    return durations[int(0.95 * len(durations))]


def test_processing_and_persistence_p95_under_5_seconds(tmp_path, capsys):
    contracts = _make_2000_contracts()
    assert len(contracts) == 2000
    db_path = str(tmp_path / "perf.duckdb")
    storage.init_schema(db_path)

    def run_once() -> None:
        priced, gex, surface, quality = analyze_snapshot(
            contracts,
            spot=100.0,
            r=0.04,
            q=0.0,
            valuation_at=VALUATION_AT,
            min_calendar_dte=1,
            max_calendar_dte=60,
            min_strike_pct=0.80,
            max_strike_pct=1.20,
            source_row_count=1000,
        )
        storage.save_snapshot(
            db_path,
            source_mode="fixture",
            symbol="SPY",
            snapshot_id=uuid4(),
            collected_at=VALUATION_AT,
            valuation_at=VALUATION_AT,
            raw_payload_json="{}",
            dashboard_json={"ok": True},
            priced_quotes=priced,
        )

    for _ in range(WARMUP_RUNS):
        run_once()

    durations = []
    for _ in range(MEASURED_RUNS):
        start = time.perf_counter()
        run_once()
        durations.append(time.perf_counter() - start)

    p95 = _p95(durations)
    with capsys.disabled():
        print(f"\nM3.5 processing+persistence p95: {p95 * 1000:.1f}ms over {MEASURED_RUNS} runs")
    assert p95 <= 5.0


def test_get_latest_dashboard_p95_under_500ms(tmp_path, capsys):
    db_path = str(tmp_path / "get_perf.duckdb")
    storage.init_schema(db_path)
    storage.save_snapshot(
        db_path,
        source_mode="fixture",
        symbol="SPY",
        snapshot_id=uuid4(),
        collected_at=VALUATION_AT,
        valuation_at=VALUATION_AT,
        raw_payload_json="{}",
        dashboard_json={"padding": "x" * 100_000},
        priced_quotes=(),
    )

    for _ in range(WARMUP_RUNS):
        storage.get_latest_dashboard(db_path, "fixture", "SPY")

    durations = []
    for _ in range(MEASURED_RUNS):
        start = time.perf_counter()
        storage.get_latest_dashboard(db_path, "fixture", "SPY")
        durations.append(time.perf_counter() - start)

    p95 = _p95(durations)
    with capsys.disabled():
        print(f"\nM3.5 GET-latest p95: {p95 * 1000:.2f}ms over {MEASURED_RUNS} runs")
    assert p95 <= 0.5
