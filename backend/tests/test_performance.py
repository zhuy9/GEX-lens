"""M3.5: processing+persistence and GET-latest timing."""

import time
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import storage
from analytics import analyze_snapshot, bsm_price, year_fraction
from models import ALGORITHM_VERSION, DashboardResponse, OptionQuote, Parameters

VALUATION_AT = datetime(2026, 1, 1, tzinfo=UTC)
SPOT = 100.0
SIGMA = 0.20  # flat vol used only to generate realistic bid/ask below
WARMUP_RUNS = 3
MEASURED_RUNS = 20


def _make_2000_contracts() -> tuple[OptionQuote, ...]:
    """20 expirations x 50 strikes x 2 sides = 2000 unique contracts, priced
    with the real BSM formula (not a flat bid/ask) so most of them actually
    reach IV solving instead of exiting early on MODEL_PRICE_BOUNDS -- a
    flat premium is only plausible near one strike/expiration, understating
    the real cost of a full refresh."""
    base_date = date(2026, 1, 2)
    contracts = []
    for e in range(20):
        expiration = base_date + timedelta(days=e + 1)
        t = year_fraction(expiration, VALUATION_AT)
        for s in range(50):
            strike = Decimal(80 + s)
            for option_type in ("C", "P"):
                mid = max(0.05, bsm_price(option_type, SPOT, float(strike), t, 0.04, 0.0, SIGMA))
                spread = max(0.01, mid * 0.02)
                contracts.append(
                    OptionQuote(
                        symbol="SPY",
                        expiration=expiration,
                        strike=strike,
                        option_type=option_type,
                        bid=round(mid - spread / 2, 2),
                        ask=round(mid + spread / 2, 2),
                        last=round(mid, 2),
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
            spot=SPOT,
            r=0.04,
            q=0.0,
            valuation_at=VALUATION_AT,
            min_calendar_dte=1,
            max_calendar_dte=60,
            min_strike_pct=0.80,
            max_strike_pct=1.20,
            source_row_count=1000,
        )
        # A real DashboardResponse, not a placeholder -- persistence timing
        # should reflect the actual payload shape/size the refresh route
        # saves (the full GEX/surface grids), not an unrelated small dict.
        dashboard = DashboardResponse(
            schema_version=1,
            snapshot_id=uuid4(),
            symbol="SPY",
            source_mode="fixture",
            collected_at=VALUATION_AT,
            valuation_at=VALUATION_AT,
            chain_asof=VALUATION_AT,
            spot_asof=None,
            oi_asof=None,
            spot=SPOT,
            spot_kind="last_trade",
            spot_origin="chain_payload",
            parameters=Parameters(
                r=0.04,
                q=0.0,
                multiplier_assumed=False,
                min_calendar_dte=1,
                max_calendar_dte=60,
                min_strike_pct=0.80,
                max_strike_pct=1.20,
                pricing_time_convention="16:00 America/New_York on expiration date",
                algorithm_version=ALGORITHM_VERSION,
            ),
            warnings=(),
            quality=quality,
            gex=gex,
            surface=surface,
        )
        storage.save_snapshot(
            db_path,
            source_mode="fixture",
            symbol="SPY",
            snapshot_id=dashboard.snapshot_id,
            collected_at=VALUATION_AT,
            valuation_at=VALUATION_AT,
            raw_payload_json="{}",
            dashboard_json=dashboard.model_dump(mode="json"),
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
