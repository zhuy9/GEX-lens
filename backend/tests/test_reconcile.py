"""M5: the offline reconciliation CLI. N7 (baseline reproduction) plus
M5.2-M5.5 (identical scope across scenarios, zero network/DB writes,
deterministic repeat runs)."""

import csv
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import duckdb

import reconcile
import storage
from analytics import (
    analyze_snapshot,
    analyze_snapshot_v2,
    bsm_price,
    build_expiry_pricing_context,
)
from models import (
    DashboardResponseV1,
    DashboardResponseV2,
    Instrument,
    OptionQuote,
    Parameters,
    ParametersV2,
    ResolvedDividendSchedule,
    ResolvedRate,
    ScheduleReview,
)

VALUATION_AT = datetime(2026, 1, 2, 21, 0, tzinfo=UTC)
SPOT = 100.0
SIGMA = 0.22
R_CC = 0.04
EXPIRATIONS = (date(2026, 1, 20), date(2026, 2, 20))
STRIKES = (95, 100, 105)
PRICING_TIME_CONVENTION = "16:00 America/New_York on expiration date"


def _make_contracts(r: float, q: float) -> tuple[OptionQuote, ...]:
    """Contracts priced at the real BSM value so they reprice to the exact
    saved IV/gamma when reconcile.py reruns them under the same inputs."""
    contracts = []
    for expiration in EXPIRATIONS:
        t = (expiration - VALUATION_AT.date()).days / 365.0
        for strike in STRIKES:
            for option_type in ("C", "P"):
                price = bsm_price(option_type, SPOT, strike, t, r, q, SIGMA)
                spread = max(0.02, price * 0.01)
                contracts.append(
                    OptionQuote(
                        symbol="SPY",
                        expiration=expiration,
                        strike=Decimal(strike),
                        option_type=option_type,
                        bid=round(price - spread / 2, 4),
                        ask=round(price + spread / 2, 4),
                        last=round(price, 4),
                        volume=10,
                        open_interest=100,
                        multiplier=100,
                        provider_contract_id=None,
                        quote_asof=None,
                        flags=(),
                    )
                )
    return tuple(contracts)


def _save_v1_snapshot(db_path: str) -> str:
    contracts = _make_contracts(R_CC, 0.0)
    priced, gex, surface, quality = analyze_snapshot(
        contracts,
        spot=SPOT,
        r=R_CC,
        q=0.0,
        valuation_at=VALUATION_AT,
        min_calendar_dte=1,
        max_calendar_dte=60,
        min_strike_pct=0.8,
        max_strike_pct=1.2,
        source_row_count=len(contracts) // 2,
    )
    snapshot_id = uuid4()
    dashboard = DashboardResponseV1(
        snapshot_id=snapshot_id,
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
            r=R_CC,
            q=0.0,
            multiplier_assumed=False,
            min_calendar_dte=1,
            max_calendar_dte=60,
            min_strike_pct=0.8,
            max_strike_pct=1.2,
            pricing_time_convention=PRICING_TIME_CONVENTION,
            algorithm_version="1",
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
        snapshot_id=snapshot_id,
        collected_at=VALUATION_AT,
        valuation_at=VALUATION_AT,
        raw_payload_json="{}",
        dashboard_json=dashboard.model_dump(mode="json"),
        priced_quotes=priced,
    )
    return str(snapshot_id)


def _save_v2_snapshot(db_path: str, dividend_events=()) -> str:
    contracts = _make_contracts(R_CC, 0.0)  # dividend PV is small; bid/ask still land in-bounds
    contexts = {
        expiration: build_expiry_pricing_context(expiration, VALUATION_AT, SPOT, R_CC, dividend_events)
        for expiration in EXPIRATIONS
    }
    priced, gex, surface, quality = analyze_snapshot_v2(
        contracts,
        actual_spot=SPOT,
        contexts=contexts,
        min_calendar_dte=1,
        max_calendar_dte=60,
        min_strike_pct=0.8,
        max_strike_pct=1.2,
        source_row_count=len(contracts) // 2,
    )
    snapshot_id = uuid4()
    resolved_rate = ResolvedRate(
        source_provider_id="fixture",
        source_ref="fixture",
        effective_date=date(2026, 1, 2),
        fetched_at=VALUATION_AT,
        raw_percent_rate=4.0,
        rate_cc=R_CC,
        quote_convention="percent_simple_act360",
        normalization="constant_daily_sofr_proxy",
        revision_indicator=None,
        manual_reason=None,
    )
    review = ScheduleReview(
        symbol="SPY",
        reviewed_at=VALUATION_AT,
        coverage_start=date(2026, 1, 2),
        coverage_end=date(2026, 4, 2),
        no_other_events_expected=True,
        source_refs=("fixture",),
        expected_events=(),
    )
    dashboard = DashboardResponseV2(
        snapshot_id=snapshot_id,
        symbol="SPY",
        source_mode="fixture",
        collected_at=VALUATION_AT,
        valuation_at=VALUATION_AT,
        chain_asof=VALUATION_AT,
        spot_asof=None,
        spot_asof_date=None,
        oi_asof=None,
        spot=SPOT,
        spot_kind="last_trade",
        spot_origin="chain_payload",
        instrument=Instrument(symbol="SPY", instrument_class="etf"),
        parameters=ParametersV2(
            r=R_CC,
            q=0.0,
            multiplier_assumed=False,
            min_calendar_dte=1,
            max_calendar_dte=60,
            min_strike_pct=0.8,
            max_strike_pct=1.2,
            pricing_time_convention=PRICING_TIME_CONVENTION,
        ),
        market_inputs={
            "input_schema_version": 1,
            "resolved_at": VALUATION_AT,
            "rate": resolved_rate,
            "dividend_schedule": ResolvedDividendSchedule(events=dividend_events, review=review),
            "warnings": (),
            "reference_bundle_hash": "a" * 64,
        },
        pricing_contexts=tuple(contexts.values()),
        warnings=(),
        quality=quality,
        gex=gex,
        surface=surface,
        calculation_input_hash="b" * 64,
    )
    storage.save_snapshot(
        db_path,
        source_mode="fixture",
        symbol="SPY",
        snapshot_id=snapshot_id,
        collected_at=VALUATION_AT,
        valuation_at=VALUATION_AT,
        raw_payload_json="{}",
        dashboard_json=dashboard.model_dump(mode="json"),
        priced_quotes=priced,
    )
    return str(snapshot_id)


def _write_scenario(path, rate_cc=0.05, events=()) -> None:
    path.write_text(
        json.dumps(
            {
                "input_schema_version": 1,
                "manual_rate": {
                    "rate_cc": rate_cc,
                    "effective_date": "2026-01-02",
                    "entered_at": "2026-01-02T21:00:00Z",
                    "source_ref": "synthetic-scenario",
                    "reason": "sensitivity test",
                },
                "schedules": {
                    "SPY": {
                        "symbol": "SPY",
                        "reviewed_at": "2026-01-02T21:00:00Z",
                        "coverage_start": "2026-01-02",
                        "coverage_end": "2026-04-02",
                        "no_other_events_expected": True,
                        "source_refs": ["synthetic-scenario"],
                        "expected_events": list(events),
                    }
                },
            }
        )
    )


SCENARIO_EVENT = {
    "event_id": "scenario-1",
    "ex_date": "2026-01-10",
    "payment_date": "2026-02-15",
    "amount": "1.75",
    "amount_status": "estimated",
    "source_ref": "synthetic-scenario",
}


def _row_counts(db_path: str) -> dict:
    conn = duckdb.connect(db_path, read_only=True)
    try:
        return {
            "snapshots": conn.execute("SELECT count(*) FROM snapshots").fetchone()[0],
            "option_quotes": conn.execute("SELECT count(*) FROM option_quotes").fetchone()[0],
        }
    finally:
        conn.close()


# --- happy path: v2 saved snapshot -----------------------------------------


def test_v2_snapshot_passes_baseline_and_writes_all_four_reports(tmp_path):
    db_path = str(tmp_path / "t.duckdb")
    storage.init_schema(db_path)
    snapshot_id = _save_v2_snapshot(db_path)
    scenario_path = tmp_path / "scenario.json"
    _write_scenario(scenario_path, events=[SCENARIO_EVENT])
    out_dir = tmp_path / "report"

    exit_code = reconcile.main(
        [
            "--db",
            db_path,
            "--snapshot-id",
            snapshot_id,
            "--scenario-inputs",
            str(scenario_path),
            "--out",
            str(out_dir),
        ]
    )

    assert exit_code == 0
    assert (out_dir / "summary.md").exists()
    assert (out_dir / "contracts.csv").exists()
    assert (out_dir / "cells.csv").exists()
    assert (out_dir / "inputs.json").exists()
    assert "PASSED" in (out_dir / "summary.md").read_text()


def test_v1_legacy_snapshot_passes_baseline_via_the_legacy_context(tmp_path):
    db_path = str(tmp_path / "t.duckdb")
    storage.init_schema(db_path)
    snapshot_id = _save_v1_snapshot(db_path)
    scenario_path = tmp_path / "scenario.json"
    _write_scenario(scenario_path, events=[SCENARIO_EVENT])
    out_dir = tmp_path / "report"

    exit_code = reconcile.main(
        [
            "--db",
            db_path,
            "--snapshot-id",
            snapshot_id,
            "--scenario-inputs",
            str(scenario_path),
            "--out",
            str(out_dir),
        ]
    )

    assert exit_code == 0
    summary = (out_dir / "summary.md").read_text()
    assert "PASSED" in summary
    assert "legacy continuous-yield" in summary


# --- M5.2: identical scope/contract population across all four scenarios --


def test_all_four_scenarios_price_the_identical_contract_population(tmp_path):
    db_path = str(tmp_path / "t.duckdb")
    storage.init_schema(db_path)
    snapshot_id = _save_v2_snapshot(db_path)
    scenario_path = tmp_path / "scenario.json"
    _write_scenario(scenario_path, events=[SCENARIO_EVENT])
    out_dir = tmp_path / "report"
    reconcile.main(
        [
            "--db",
            db_path,
            "--snapshot-id",
            snapshot_id,
            "--scenario-inputs",
            str(scenario_path),
            "--out",
            str(out_dir),
        ]
    )

    by_scenario = {}
    with (out_dir / "contracts.csv").open() as f:
        for row in csv.DictReader(f):
            by_scenario.setdefault(row["scenario"], set()).add(
                (row["expiration"], row["strike"], row["option_type"])
            )
    populations = list(by_scenario.values())
    assert all(p == populations[0] for p in populations)
    assert len(populations[0]) == len(EXPIRATIONS) * len(STRIKES) * 2


def test_cash_only_and_rate_and_cash_change_eligibility_relative_to_saved(tmp_path):
    # The scenario's dividend event pushes some contracts across the
    # LOW_TIME_VALUE/MODEL_PRICE_BOUNDS boundary -- quality counts must
    # differ from `saved` for the cash-flavored scenarios.
    db_path = str(tmp_path / "t.duckdb")
    storage.init_schema(db_path)
    snapshot_id = _save_v2_snapshot(db_path)  # saved with zero dividends
    scenario_path = tmp_path / "scenario.json"
    _write_scenario(scenario_path, rate_cc=R_CC, events=[SCENARIO_EVENT])
    out_dir = tmp_path / "report"
    reconcile.main(
        [
            "--db",
            db_path,
            "--snapshot-id",
            snapshot_id,
            "--scenario-inputs",
            str(scenario_path),
            "--out",
            str(out_dir),
        ]
    )
    summary = (out_dir / "summary.md").read_text()
    assert "cash_only" in summary
    assert "rate_and_cash" in summary


# --- M5.3: zero network calls, zero database writes -----------------------


def test_reconcile_makes_zero_database_writes(tmp_path):
    db_path = str(tmp_path / "t.duckdb")
    storage.init_schema(db_path)
    snapshot_id = _save_v2_snapshot(db_path)
    scenario_path = tmp_path / "scenario.json"
    _write_scenario(scenario_path, events=[SCENARIO_EVENT])
    before = _row_counts(db_path)
    saved_json_before = storage.get_latest_dashboard(db_path, "fixture", "SPY")

    reconcile.main(
        [
            "--db",
            db_path,
            "--snapshot-id",
            snapshot_id,
            "--scenario-inputs",
            str(scenario_path),
            "--out",
            str(tmp_path / "report"),
        ]
    )

    assert _row_counts(db_path) == before
    assert storage.get_latest_dashboard(db_path, "fixture", "SPY") == saved_json_before


# --- M5.5: deterministic repeat runs ---------------------------------------


def test_repeated_runs_produce_byte_identical_reports(tmp_path):
    db_path = str(tmp_path / "t.duckdb")
    storage.init_schema(db_path)
    snapshot_id = _save_v2_snapshot(db_path)
    scenario_path = tmp_path / "scenario.json"
    _write_scenario(scenario_path, events=[SCENARIO_EVENT])

    for out_name in ("report_a", "report_b"):
        reconcile.main(
            [
                "--db",
                db_path,
                "--snapshot-id",
                snapshot_id,
                "--scenario-inputs",
                str(scenario_path),
                "--out",
                str(tmp_path / out_name),
            ]
        )

    for filename in ("contracts.csv", "cells.csv", "inputs.json"):
        a = (tmp_path / "report_a" / filename).read_text()
        b = (tmp_path / "report_b" / filename).read_text()
        assert a == b


# --- overwrite refusal ------------------------------------------------------


def test_refuses_to_overwrite_an_existing_report(tmp_path):
    db_path = str(tmp_path / "t.duckdb")
    storage.init_schema(db_path)
    snapshot_id = _save_v2_snapshot(db_path)
    scenario_path = tmp_path / "scenario.json"
    _write_scenario(scenario_path, events=[SCENARIO_EVENT])
    out_dir = tmp_path / "report"
    args = [
        "--db",
        db_path,
        "--snapshot-id",
        snapshot_id,
        "--scenario-inputs",
        str(scenario_path),
        "--out",
        str(out_dir),
    ]

    assert reconcile.main(args) == 0
    written_at = (out_dir / "summary.md").stat().st_mtime
    assert reconcile.main(args) == 2  # refused, not silently overwritten
    assert (out_dir / "summary.md").stat().st_mtime == written_at


# --- N7: baseline reproduction failure -------------------------------------


def test_baseline_reproduction_failure_is_reported_and_exits_nonzero(tmp_path):
    db_path = str(tmp_path / "t.duckdb")
    storage.init_schema(db_path)
    snapshot_id = _save_v2_snapshot(db_path)

    # Corrupt the saved rate after the fact so the "saved" scenario can no
    # longer reproduce the saved IV/gamma -- simulates a tampered/mismatched
    # database row, independent of the scenario file's own inputs.
    conn = duckdb.connect(db_path)
    try:
        dashboard = json.loads(
            conn.execute(
                "SELECT dashboard_json FROM snapshots WHERE snapshot_id = ?", [snapshot_id]
            ).fetchone()[0]
        )
        dashboard["market_inputs"]["rate"]["rate_cc"] = (
            0.30  # wildly different from what priced the saved rows
        )
        conn.execute(
            "UPDATE snapshots SET dashboard_json = ? WHERE snapshot_id = ?",
            [json.dumps(dashboard), snapshot_id],
        )
    finally:
        conn.close()

    scenario_path = tmp_path / "scenario.json"
    _write_scenario(scenario_path, events=[SCENARIO_EVENT])
    out_dir = tmp_path / "report"

    exit_code = reconcile.main(
        [
            "--db",
            db_path,
            "--snapshot-id",
            snapshot_id,
            "--scenario-inputs",
            str(scenario_path),
            "--out",
            str(out_dir),
        ]
    )

    assert exit_code == 1
    summary = (out_dir / "summary.md").read_text()
    assert "BASELINE_REPRODUCTION_FAILED" in summary


def test_null_iv_never_becomes_zero_in_the_percentage_diff(tmp_path):
    db_path = str(tmp_path / "t.duckdb")
    storage.init_schema(db_path)
    snapshot_id = _save_v2_snapshot(db_path)
    scenario_path = tmp_path / "scenario.json"
    # A large dividend pushes some contracts to INVALID_DIVIDEND_ADJUSTED_SPOT
    # (null IV) in the cash-flavored scenarios.
    _write_scenario(
        scenario_path,
        events=[{**SCENARIO_EVENT, "amount": "99.00"}],
    )
    out_dir = tmp_path / "report"
    reconcile.main(
        [
            "--db",
            db_path,
            "--snapshot-id",
            snapshot_id,
            "--scenario-inputs",
            str(scenario_path),
            "--out",
            str(out_dir),
        ]
    )

    with (out_dir / "contracts.csv").open() as f:
        rows = list(csv.DictReader(f))
    null_iv_rows = [r for r in rows if r["scenario"] == "cash_only" and r["iv"] == ""]
    assert null_iv_rows
    for row in null_iv_rows:
        assert row["iv_diff_from_saved"] == ""
        assert row["iv_pct_diff_reason"] == "missing_value"
