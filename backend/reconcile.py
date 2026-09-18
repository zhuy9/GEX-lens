"""ADR-0001 Section 12: offline, read-only same-snapshot reconciliation.

A diagnostic CLI, never an HTTP endpoint or backtester. Opens the database
read-only, makes zero network calls, creates no schema, and mutates no
existing tables. Run from backend/, with the app server stopped first (a
running server holds its own connection to the same DuckDB file).

    python reconcile.py --db data/options.duckdb \\
        --snapshot-id <uuid> \\
        --scenario-inputs reference_inputs.scenario.json \\
        --out exports/<uuid>-pricing-comparison
"""

import argparse
import csv
import hashlib
import json
import math
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import duckdb

from analytics import (
    IV_HIGH,
    IV_LOW,
    IV_MAXITER,
    IV_RTOL,
    IV_XTOL,
    MAX_RELATIVE_SPREAD,
    MIN_MID,
    MIN_TIME_VALUE,
    MODEL_BOUNDS_TOLERANCE,
    REPRICE_TOLERANCE,
    _exposure_for,
    analyze_snapshot_v2,
    build_expiry_pricing_context,
)
from market_inputs import canonical_json, load_local_reference_inputs, resolve_dividend_schedule, resolve_rate
from models import (
    ExpiryPricingContext,
    GexData,
    OptionQuote,
    PricedQuote,
    ResolvedDividend,
    SurfaceData,
    calendar_dte,
    model_expiry_at_utc,
    year_fraction,
)

SCENARIOS = ("saved", "rate_only", "cash_only", "rate_and_cash")

# N7 tolerances for baseline reproduction (Section 14).
IV_GAMMA_ABS_TOL, IV_GAMMA_REL_TOL = 1e-8, 1e-7
GEX_ABS_TOL, GEX_REL_TOL = 0.01, 1e-7


def _approx(new: float | None, old: float | None, abs_tol: float, rel_tol: float) -> bool:
    """Null-safe: null must match null, never treated as zero."""
    if new is None or old is None:
        return new is None and old is None
    return abs(new - old) <= max(abs_tol, rel_tol * abs(old))


def _pct_diff(new: float | None, old: float | None) -> tuple[float | None, str | None]:
    """Section 12: 100*(new-old)/abs(old); null with a reason when old==0
    or either value is missing."""
    if new is None or old is None:
        return None, "missing_value"
    if old == 0:
        return None, "old_is_zero"
    return 100 * (new - old) / abs(old), None


def _abs_diff(new: float | None, old: float | None) -> float | None:
    """Section 12: "Always report the dollar difference when both values
    exist" -- a plain new-old, with no old==0 special case since a raw
    subtraction is always defined."""
    if new is None or old is None:
        return None
    return new - old


def _quote_time_hash(
    contracts: tuple[OptionQuote, ...],
    actual_spot: float,
    valuation_at: datetime,
    min_dte: int,
    max_dte: int,
    min_pct: float,
    max_pct: float,
) -> str:
    """N7 (Section 14): a hash of only the inputs that never vary across
    this CLI's four scenarios -- quote/OI/spot/time, scope, and quality
    thresholds -- isolated from rate/dividend inputs so the "unchanged
    normalized quote/OI/spot/time hash component" invariant can be
    demonstrated, not merely assumed. Deliberately local to this file
    rather than imported from app.py: reconcile.py must not depend on the
    live FastAPI app (Section 12)."""
    contract_rows = sorted(
        (
            {
                "symbol": c.symbol,
                "expiration": c.expiration.isoformat(),
                "strike": str(c.strike),
                "option_type": c.option_type,
                "bid": c.bid,
                "ask": c.ask,
                "last": c.last,
                "volume": c.volume,
                "open_interest": c.open_interest,
                "multiplier": c.multiplier,
                "flags": sorted(c.flags),
            }
            for c in contracts
        ),
        key=lambda row: (row["symbol"], row["expiration"], row["strike"], row["option_type"]),
    )
    payload = {
        "actual_spot": actual_spot,
        "contracts": contract_rows,
        "valuation_at": valuation_at.astimezone(UTC).isoformat(),
        "min_calendar_dte": min_dte,
        "max_calendar_dte": max_dte,
        "min_strike_pct": min_pct,
        "max_strike_pct": max_pct,
        "quality_thresholds": {
            "min_mid": MIN_MID,
            "max_relative_spread": MAX_RELATIVE_SPREAD,
            "model_bounds_tolerance": MODEL_BOUNDS_TOLERANCE,
            "min_time_value": MIN_TIME_VALUE,
            "iv_low": IV_LOW,
            "iv_high": IV_HIGH,
            "iv_xtol": IV_XTOL,
            "iv_rtol": IV_RTOL,
            "iv_maxiter": IV_MAXITER,
            "reprice_tolerance": REPRICE_TOLERANCE,
        },
    }
    return hashlib.sha256(canonical_json(payload).encode()).hexdigest()


def _original_metadata(dashboard: dict) -> dict:
    """Model/instrument/provenance metadata the report must surface
    (Section 12) -- "unavailable" for a legacy v1 snapshot, which predates
    ADR-0001 and never resolved any of this."""
    if dashboard["schema_version"] == 1:
        return {
            "algorithm_version": "1",
            "model_id": "legacy_continuous_yield",
            "instrument_class": None,
            "rate_provenance": "unavailable (legacy snapshot)",
            "review": "unavailable (legacy snapshot)",
        }
    parameters = dashboard["parameters"]
    market_inputs = dashboard["market_inputs"]
    rate = market_inputs["rate"]
    review = market_inputs["dividend_schedule"]["review"]
    return {
        "algorithm_version": parameters["algorithm_version"],
        "model_id": parameters["model_id"],
        "instrument_class": dashboard["instrument"]["instrument_class"],
        "rate_provenance": {
            "source_provider_id": rate["source_provider_id"],
            "effective_date": rate["effective_date"],
            "fetched_at": rate["fetched_at"],
        },
        "review": {
            "coverage_start": review["coverage_start"],
            "coverage_end": review["coverage_end"],
            "reviewed_at": review["reviewed_at"],
            "source_refs": review["source_refs"],
        },
    }


@dataclass
class SavedSnapshot:
    snapshot_id: uuid.UUID
    symbol: str
    source_mode: str
    collected_at: datetime
    valuation_at: datetime
    dashboard: dict
    contracts: tuple[OptionQuote, ...]
    saved_priced: dict[tuple[object, Decimal, str], dict]


def _load_snapshot(db_path: str, snapshot_id: uuid.UUID) -> SavedSnapshot:
    conn = duckdb.connect(db_path, read_only=True)
    try:
        row = conn.execute(
            "SELECT symbol, source_mode, collected_at, valuation_at, dashboard_json "
            "FROM snapshots WHERE snapshot_id = ?",
            [snapshot_id],
        ).fetchone()
        if row is None:
            raise SystemExit(f"No snapshot found for {snapshot_id}")
        symbol, source_mode, collected_at, valuation_at, dashboard_json = row
        dashboard = json.loads(dashboard_json)

        rows = conn.execute(
            """
            SELECT expiration, strike, option_type, bid, ask, last, volume,
                   open_interest, multiplier, provider_contract_id, quote_asof,
                   mid, iv, gamma, exclusion_reason, flags
            FROM option_quotes WHERE snapshot_id = ?
            """,
            [snapshot_id],
        ).fetchall()
    finally:
        conn.close()

    contracts = []
    saved_priced = {}
    for r in rows:
        (
            expiration,
            strike_raw,
            option_type,
            bid,
            ask,
            last,
            volume,
            oi,
            multiplier,
            provider_contract_id,
            quote_asof,
            mid,
            iv,
            gamma,
            exclusion_reason,
            flags_json,
        ) = r
        strike = Decimal(str(strike_raw))
        quote = OptionQuote(
            symbol=symbol,
            expiration=expiration,
            strike=strike,
            option_type=option_type,
            bid=bid,
            ask=ask,
            last=last,
            volume=volume,
            open_interest=oi,
            multiplier=multiplier,
            provider_contract_id=provider_contract_id,
            quote_asof=quote_asof,
            flags=tuple(json.loads(flags_json)),
        )
        contracts.append(quote)
        saved_priced[(expiration, strike, option_type)] = {
            "mid": mid,
            "iv": iv,
            "gamma": gamma,
            "exclusion_reason": exclusion_reason,
        }

    return SavedSnapshot(
        snapshot_id=snapshot_id,
        symbol=symbol,
        source_mode=source_mode,
        collected_at=collected_at,
        valuation_at=valuation_at,
        dashboard=dashboard,
        contracts=tuple(contracts),
        saved_priced=saved_priced,
    )


def _original_inputs(dashboard: dict) -> tuple[float, tuple[ResolvedDividend, ...], float]:
    """(r_cc_or_r, dividend_events, q) reproducing the saved snapshot's own
    pricing inputs -- v1's flat continuous yield, or v2's resolved rate and
    cash schedule."""
    if dashboard["schema_version"] == 1:
        return dashboard["parameters"]["r"], (), dashboard["parameters"]["q"]
    market_inputs = dashboard["market_inputs"]
    events = tuple(ResolvedDividend.model_validate(e) for e in market_inputs["dividend_schedule"]["events"])
    return market_inputs["rate"]["rate_cc"], events, 0.0


def _resolve_scenario(
    scenario_path: Path,
    symbol: str,
    valuation_at: datetime,
    latest_in_scope_expiration,
) -> tuple[float, tuple[ResolvedDividend, ...], bool]:
    """Reads the scenario file explicitly supplied by --scenario-inputs
    (never the live reference cache or a fresh fetch) through the same
    manual-path resolvers the live app uses, so the same rules (freshness,
    review coverage, matching) apply here.

    Also reports whether this scenario's inputs were reviewed/entered after
    the snapshot's own valuation time (Section 12/14's `known_after_valuation`
    label) -- using the review's own reviewed_at/entered_at timestamp, not a
    per-event declaration date, since "a declaration date alone does not
    establish when this application knew an event."."""
    scenario = load_local_reference_inputs(scenario_path)
    manual_rate = scenario.manual_rate
    if manual_rate is None:
        raise SystemExit(f"{scenario_path} must supply manual_rate")
    if symbol not in scenario.schedules:
        raise SystemExit(f"{scenario_path} has no reviewed schedule for {symbol!r}")
    review = scenario.schedules[symbol]

    resolved_rate = resolve_rate(
        rate_source="manual",
        manual=manual_rate,
        provider=None,
        valuation_at=valuation_at,
        attempt_started_at=valuation_at,
        db_path="unused",
    )
    schedule, _ = resolve_dividend_schedule(
        symbol=symbol,
        dividend_source="manual_schedule",
        review=review,
        provider=None,
        valuation_at=valuation_at,
        attempt_started_at=valuation_at,
        latest_in_scope_expiration=latest_in_scope_expiration,
        db_path="unused",
    )
    rate_is_hindsight = manual_rate.entered_at > valuation_at
    review_is_hindsight = review.reviewed_at > valuation_at
    known_after_valuation = rate_is_hindsight or review_is_hindsight
    return resolved_rate.rate_cc, schedule.events, known_after_valuation


def _legacy_context(
    expiration, valuation_at: datetime, actual_spot: float, r: float, q: float
) -> ExpiryPricingContext:
    """A small explicit legacy context (Section 12), not a copy of the old
    backend or a generic engine framework: same shape as the cash-PV
    context, with pv_dividends=0 and model_spot=actual_spot."""
    t = year_fraction(expiration, valuation_at)
    expiry_at = model_expiry_at_utc(expiration)
    forward = actual_spot * math.exp((r - q) * t)
    return ExpiryPricingContext(
        expiration=expiration,
        valuation_at=valuation_at,
        expiry_at=expiry_at,
        T=t,
        actual_spot=actual_spot,
        model_spot=actual_spot,
        r_cc=r,
        q_continuous=q,
        pv_dividends=0.0,
        forward=forward,
        used_event_ids=(),
        warnings=(),
        status="OK",
    )


def _build_scenario_contexts(
    scenario: str,
    expirations,
    valuation_at: datetime,
    actual_spot: float,
    original_r: float,
    original_q: float,
    original_events: tuple[ResolvedDividend, ...],
    original_is_legacy: bool,
    scenario_r: float,
    scenario_events: tuple[ResolvedDividend, ...],
) -> dict:
    """Section 12's four-scenario matrix: saved/rate_only reuse whichever
    model the original snapshot actually used; cash_only/rate_and_cash
    always substitute the scenario's cash schedule with continuous q=0."""
    if scenario == "saved":
        r, events, use_legacy = original_r, original_events, original_is_legacy
    elif scenario == "rate_only":
        r, events, use_legacy = scenario_r, original_events, original_is_legacy
    elif scenario == "cash_only":
        r, events, use_legacy = original_r, scenario_events, False
    elif scenario == "rate_and_cash":
        r, events, use_legacy = scenario_r, scenario_events, False
    else:
        raise ValueError(f"Unknown scenario: {scenario!r}")

    if use_legacy:
        return {exp: _legacy_context(exp, valuation_at, actual_spot, r, original_q) for exp in expirations}
    return {
        exp: build_expiry_pricing_context(exp, valuation_at, actual_spot, r, events) for exp in expirations
    }


def _check_baseline_reproduction(
    priced: tuple[PricedQuote, ...],
    saved_priced: dict,
    gex: GexData,
    saved_gex: dict,
    surface: SurfaceData,
    saved_surface: dict,
) -> list[str]:
    """N7: unchanged exclusion reasons; IV/gamma within tolerance; unscaled
    GEX and surface IV within tolerance; never treats a missing value as
    zero to pass."""
    mismatches = []
    for pq in priced:
        key = (pq.quote.expiration, pq.quote.strike, pq.quote.option_type)
        saved = saved_priced.get(key)
        if saved is None:
            mismatches.append(f"{key}: contract missing from saved rows")
            continue
        if pq.exclusion_reason != saved["exclusion_reason"]:
            mismatches.append(
                f"{key}: exclusion_reason {pq.exclusion_reason!r} != saved {saved['exclusion_reason']!r}"
            )
            continue
        if not _approx(pq.iv, saved["iv"], IV_GAMMA_ABS_TOL, IV_GAMMA_REL_TOL):
            mismatches.append(f"{key}: iv {pq.iv} != saved {saved['iv']}")
        if not _approx(pq.gamma, saved["gamma"], IV_GAMMA_ABS_TOL, IV_GAMMA_REL_TOL):
            mismatches.append(f"{key}: gamma {pq.gamma} != saved {saved['gamma']}")

    saved_cells = {
        (date.fromisoformat(exp), Decimal(strike)): cell
        for exp, row in zip(saved_gex["expirations"], saved_gex["cells"], strict=True)
        for strike, cell in zip(saved_gex["strikes"], row, strict=True)
    }
    for e_idx, expiration in enumerate(gex.expirations):
        for s_idx, strike in enumerate(gex.strikes):
            cell = gex.cells[e_idx][s_idx]
            saved_cell = saved_cells.get((expiration, strike))
            key = (expiration, strike)
            if (cell is None) != (saved_cell is None):
                mismatches.append(f"cell {key}: completeness differs from saved")
                continue
            if cell is None:
                continue
            if cell.status != saved_cell["status"]:
                mismatches.append(f"cell {key}: status {cell.status} != saved {saved_cell['status']}")
            for field in ("signed_proxy", "gross_exposure"):
                if not _approx(getattr(cell, field), saved_cell[field], GEX_ABS_TOL, GEX_REL_TOL):
                    mismatches.append(
                        f"cell {key}: {field} {getattr(cell, field)} != saved {saved_cell[field]}"
                    )

    surface_iv = surface.iv
    if surface.status != saved_surface["status"]:
        mismatches.append(f"surface: status {surface.status} != saved {saved_surface['status']}")
    elif [e.isoformat() for e in surface.expirations] != saved_surface["expirations"]:
        mismatches.append("surface: expirations differ from saved")
    elif surface.status == "READY" and surface_iv is not None:
        for e_idx, row in enumerate(surface_iv):
            for k_idx, iv in enumerate(row):
                saved_iv = saved_surface["iv"][e_idx][k_idx]
                if not _approx(iv, saved_iv, IV_GAMMA_ABS_TOL, IV_GAMMA_REL_TOL):
                    mismatches.append(f"surface[{e_idx}][{k_idx}]: iv {iv} != saved {saved_iv}")

    return mismatches


def _write_contracts_csv(
    path: Path, priced_by_scenario: dict, contexts_by_scenario: dict, saved_priced: dict
) -> None:
    fieldnames = [
        "scenario",
        "symbol",
        "expiration",
        "strike",
        "option_type",
        "bid",
        "ask",
        "mid",
        "open_interest",
        "actual_spot",
        "model_spot",
        "r_cc",
        "q_continuous",
        "dividend_pv",
        "forward",
        "iv",
        "gamma",
        "exposure_per_1pct",
        "exposure_per_1dollar",
        "exclusion_reason",
        "iv_pct_diff_from_saved",
        "iv_pct_diff_reason",
        "iv_abs_diff_from_saved",
        "gamma_pct_diff_from_saved",
        "gamma_pct_diff_reason",
        "gamma_abs_diff_from_saved",
    ]
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for scenario in SCENARIOS:
            contexts = contexts_by_scenario[scenario]
            for pq in priced_by_scenario[scenario]:
                key = (pq.quote.expiration, pq.quote.strike, pq.quote.option_type)
                saved = saved_priced.get(key, {})
                ctx = contexts[pq.quote.expiration]
                iv_pct_diff, iv_reason = _pct_diff(pq.iv, saved.get("iv"))
                gamma_pct_diff, gamma_reason = _pct_diff(pq.gamma, saved.get("gamma"))
                exposure_per_1pct = _exposure_for(pq, ctx.actual_spot)
                display_factor = 1 / (0.01 * ctx.actual_spot)
                writer.writerow(
                    {
                        "scenario": scenario,
                        "symbol": pq.quote.symbol,
                        "expiration": pq.quote.expiration.isoformat(),
                        "strike": str(pq.quote.strike),
                        "option_type": pq.quote.option_type,
                        "bid": pq.quote.bid,
                        "ask": pq.quote.ask,
                        "mid": pq.mid,
                        "open_interest": pq.quote.open_interest,
                        "actual_spot": ctx.actual_spot,
                        "model_spot": ctx.model_spot,
                        "r_cc": ctx.r_cc,
                        "q_continuous": ctx.q_continuous,
                        "dividend_pv": ctx.pv_dividends,
                        "forward": ctx.forward,
                        "iv": pq.iv,
                        "gamma": pq.gamma,
                        "exposure_per_1pct": exposure_per_1pct,
                        "exposure_per_1dollar": (
                            None if exposure_per_1pct is None else exposure_per_1pct * display_factor
                        ),
                        "exclusion_reason": pq.exclusion_reason,
                        "iv_pct_diff_from_saved": iv_pct_diff,
                        "iv_pct_diff_reason": iv_reason,
                        "iv_abs_diff_from_saved": _abs_diff(pq.iv, saved.get("iv")),
                        "gamma_pct_diff_from_saved": gamma_pct_diff,
                        "gamma_pct_diff_reason": gamma_reason,
                        "gamma_abs_diff_from_saved": _abs_diff(pq.gamma, saved.get("gamma")),
                    }
                )


def _write_cells_csv(path: Path, gex_by_scenario: dict[str, GexData], actual_spot: float) -> None:
    fieldnames = [
        "scenario",
        "expiration",
        "strike",
        "status",
        "call_exposure_per_1pct",
        "put_exposure_per_1pct",
        "signed_proxy_per_1pct",
        "gross_exposure_per_1pct",
        "call_exposure_per_1dollar",
        "put_exposure_per_1dollar",
        "signed_proxy_per_1dollar",
        "gross_exposure_per_1dollar",
        "signed_proxy_pct_diff_from_saved",
        "signed_proxy_pct_diff_reason",
        "signed_proxy_abs_diff_from_saved",
        "gross_exposure_pct_diff_from_saved",
        "gross_exposure_pct_diff_reason",
        "gross_exposure_abs_diff_from_saved",
    ]
    factor = 1 / (0.01 * actual_spot)  # Section 10's frontend display conversion, applied here for the report

    def scaled(value: float | None) -> float | None:
        return None if value is None else value * factor

    saved_gex = gex_by_scenario["saved"]
    saved_by_key = {
        (expiration, strike): saved_gex.cells[e_idx][s_idx]
        for e_idx, expiration in enumerate(saved_gex.expirations)
        for s_idx, strike in enumerate(saved_gex.strikes)
    }

    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for scenario in SCENARIOS:
            gex = gex_by_scenario[scenario]
            for e_idx, expiration in enumerate(gex.expirations):
                for s_idx, strike in enumerate(gex.strikes):
                    cell = gex.cells[e_idx][s_idx]
                    saved_cell = saved_by_key.get((expiration, strike))
                    saved_signed = saved_cell.signed_proxy if saved_cell else None
                    saved_gross = saved_cell.gross_exposure if saved_cell else None
                    cell_signed = cell.signed_proxy if cell else None
                    cell_gross = cell.gross_exposure if cell else None
                    signed_pct_diff, signed_reason = _pct_diff(cell_signed, saved_signed)
                    gross_pct_diff, gross_reason = _pct_diff(cell_gross, saved_gross)
                    writer.writerow(
                        {
                            "scenario": scenario,
                            "expiration": expiration.isoformat(),
                            "strike": str(strike),
                            "status": cell.status if cell else "MISSING",
                            "call_exposure_per_1pct": cell.call_exposure if cell else None,
                            "put_exposure_per_1pct": cell.put_exposure if cell else None,
                            "signed_proxy_per_1pct": cell.signed_proxy if cell else None,
                            "gross_exposure_per_1pct": cell.gross_exposure if cell else None,
                            "call_exposure_per_1dollar": scaled(cell.call_exposure if cell else None),
                            "put_exposure_per_1dollar": scaled(cell.put_exposure if cell else None),
                            "signed_proxy_per_1dollar": scaled(cell.signed_proxy if cell else None),
                            "gross_exposure_per_1dollar": scaled(cell.gross_exposure if cell else None),
                            "signed_proxy_pct_diff_from_saved": signed_pct_diff,
                            "signed_proxy_pct_diff_reason": signed_reason,
                            "signed_proxy_abs_diff_from_saved": _abs_diff(cell_signed, saved_signed),
                            "gross_exposure_pct_diff_from_saved": gross_pct_diff,
                            "gross_exposure_pct_diff_reason": gross_reason,
                            "gross_exposure_abs_diff_from_saved": _abs_diff(cell_gross, saved_gross),
                        }
                    )


def _write_inputs_json(
    path: Path,
    saved: SavedSnapshot,
    original_r: float,
    original_q: float,
    original_events: tuple[ResolvedDividend, ...],
    scenario_r: float,
    scenario_events: tuple[ResolvedDividend, ...],
    known_after_valuation: bool,
    quote_time_hash_value: str,
) -> None:
    payload = {
        "snapshot_id": str(saved.snapshot_id),
        "symbol": saved.symbol,
        "source_mode": saved.source_mode,
        "collected_at": str(saved.collected_at),
        "valuation_at": str(saved.valuation_at),
        "schema_version": saved.dashboard["schema_version"],
        "calculation_input_hash": saved.dashboard.get("calculation_input_hash"),
        "reference_bundle_hash": saved.dashboard.get("market_inputs", {}).get("reference_bundle_hash"),
        "quote_time_hash": quote_time_hash_value,
        "original_metadata": _original_metadata(saved.dashboard),
        "original": {
            "rate_cc_or_r": original_r,
            "q": original_q,
            "dividend_events": [json.loads(e.model_dump_json()) for e in original_events],
        },
        "scenario": {
            "rate_cc": scenario_r,
            "dividend_events": [json.loads(e.model_dump_json()) for e in scenario_events],
        },
        "label": "counterfactual sensitivity, not a historical trading backtest",
        "known_after_valuation": known_after_valuation,
    }
    path.write_text(json.dumps(payload, indent=2, default=str))


def _event_timing_note(event: ResolvedDividend, expirations) -> str:
    eligible_for = sorted(e for e in expirations if event.ex_date <= e)
    return (
        f"ex-date {event.ex_date} precedes {len(eligible_for)} in-scope expiry(ies)"
        if eligible_for
        else (f"ex-date {event.ex_date} precedes no in-scope expiry")
    )


def _write_summary_md(
    path: Path,
    saved: SavedSnapshot,
    baseline_ok: bool,
    mismatches: list[str],
    results: dict,
    original_is_legacy: bool,
    original_events: tuple[ResolvedDividend, ...],
    scenario_events: tuple[ResolvedDividend, ...],
    expirations,
    known_after_valuation: bool,
    quote_time_hash_value: str,
) -> None:
    metadata = _original_metadata(saved.dashboard)
    lines = [
        f"# Reconciliation report: {saved.symbol} snapshot {saved.snapshot_id}",
        "",
        "counterfactual sensitivity, not a historical trading backtest.",
    ]
    if known_after_valuation:
        lines.append(
            "**known_after_valuation**: the scenario's rate/dividend inputs were reviewed/entered "
            "after this snapshot's valuation time -- a declaration date alone does not establish "
            "when this application knew the event."
        )
    lines += [
        "",
        f"- Source mode: {saved.source_mode}",
        f"- Collected at: {saved.collected_at}",
        f"- Valuation at (fixed for every scenario): {saved.valuation_at}",
        f"- Saved schema version: {saved.dashboard['schema_version']} "
        f"({'legacy continuous-yield' if original_is_legacy else 'cash_pv_bsm_v2'})",
        f"- Instrument class: {metadata['instrument_class']}",
        f"- Model: {metadata['model_id']} (algorithm_version {metadata['algorithm_version']})",
        f"- Original rate provenance: {metadata['rate_provenance']}",
        f"- Original review: {metadata['review']}",
        f"- calculation_input_hash: {saved.dashboard.get('calculation_input_hash')}",
        f"- reference_bundle_hash: {saved.dashboard.get('market_inputs', {}).get('reference_bundle_hash')}",
        f"- quote_time_hash (identical across all four scenarios by construction): {quote_time_hash_value}",
        "",
        "## Baseline reproduction (`saved` scenario vs. the actual saved snapshot)",
        "",
    ]
    if baseline_ok:
        lines.append(
            "PASSED: exclusion reasons, IV, and gamma reproduce the saved snapshot within N7 tolerances."
        )
    else:
        lines.append("**FAILED: BASELINE_REPRODUCTION_FAILED**")
        lines.append("")
        lines.append("Do not attribute the discrepancies below to a rate or dividend difference:")
        lines.extend(f"- {m}" for m in mismatches)
    lines.append("")

    lines.append("## Per-scenario quality counts")
    lines.append("")
    lines.append("| Scenario | In-scope | Valid IVs | Complete GEX cells |")
    lines.append("|---|---:|---:|---:|")
    for scenario in SCENARIOS:
        q = results[scenario]["quality"]
        lines.append(f"| {scenario} | {q.in_scope_contracts} | {q.valid_ivs} | {q.complete_gex_cells} |")
    lines.append("")
    lines.append(
        "Counts above are independent per-scenario totals -- do not sum different eligible "
        "populations and describe the result solely as a gamma change."
    )
    lines.append("")

    lines.append("## Dividend events")
    lines.append("")
    lines.append("### Original (as saved)")
    if not original_events:
        lines.append("None (legacy continuous-yield model or an empty reviewed schedule).")
    for e in original_events:
        lines.append(
            f"- {e.ex_date}: ${e.amount} ({e.amount_status}) -- {_event_timing_note(e, expirations)}"
        )
    lines.append("")
    lines.append("### Scenario")
    if not scenario_events:
        lines.append("None.")
    for e in scenario_events:
        lines.append(
            f"- {e.ex_date}: ${e.amount} ({e.amount_status}) -- {_event_timing_note(e, expirations)}"
        )
    lines.append("")

    lines.append("## Notes")
    lines.append("")
    lines.append(
        "- Scenario effects interact: `rate_only`'s change plus `cash_only`'s change does not "
        "necessarily equal `rate_and_cash`'s change."
    )
    lines.append(
        "- IV is re-solved from each scenario's unchanged saved midpoint in every scenario, "
        "including `saved` itself -- this is not the same as reusing the originally saved IV."
    )
    lines.append("- See contracts.csv and cells.csv for per-contract/per-cell values and diffs from `saved`.")
    lines.append("")

    path.write_text("\n".join(lines))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--db", required=True, help="Path to the DuckDB file (opened read-only)")
    parser.add_argument("--snapshot-id", required=True)
    parser.add_argument("--scenario-inputs", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)

    if args.out.exists() and any(args.out.iterdir()):
        print(f"{args.out} already contains files; refusing to overwrite a prior report.", file=sys.stderr)
        return 2
    args.out.mkdir(parents=True, exist_ok=True)

    saved = _load_snapshot(args.db, uuid.UUID(args.snapshot_id))
    dashboard = saved.dashboard
    original_r, original_events, original_q = _original_inputs(dashboard)
    original_is_legacy = dashboard["schema_version"] == 1

    parameters = dashboard["parameters"]
    min_dte, max_dte = parameters["min_calendar_dte"], parameters["max_calendar_dte"]
    min_pct, max_pct = parameters["min_strike_pct"], parameters["max_strike_pct"]
    actual_spot = dashboard["spot"]

    expirations = {c.expiration for c in saved.contracts}
    latest_in_scope_expiration = max(
        (e for e in expirations if min_dte <= calendar_dte(e, saved.valuation_at) <= max_dte),
        default=None,
    )
    scenario_r, scenario_events, known_after_valuation = _resolve_scenario(
        args.scenario_inputs, saved.symbol, saved.valuation_at, latest_in_scope_expiration
    )
    quote_time_hash_value = _quote_time_hash(
        saved.contracts, actual_spot, saved.valuation_at, min_dte, max_dte, min_pct, max_pct
    )

    priced_by_scenario, gex_by_scenario, quality_by_scenario, contexts_by_scenario = {}, {}, {}, {}
    for scenario in SCENARIOS:
        # N7: every scenario prices the identical quote/OI/spot/time population
        # (Section 12); this hash never depends on scenario_r/scenario_events,
        # so recomputing it per scenario must always match the value above.
        recomputed = _quote_time_hash(
            saved.contracts, actual_spot, saved.valuation_at, min_dte, max_dte, min_pct, max_pct
        )
        assert recomputed == quote_time_hash_value
        contexts = _build_scenario_contexts(
            scenario,
            expirations,
            saved.valuation_at,
            actual_spot,
            original_r,
            original_q,
            original_events,
            original_is_legacy,
            scenario_r,
            scenario_events,
        )
        contexts_by_scenario[scenario] = contexts
        priced, gex, surface, quality = analyze_snapshot_v2(
            saved.contracts,
            actual_spot=actual_spot,
            contexts=contexts,
            min_calendar_dte=min_dte,
            max_calendar_dte=max_dte,
            min_strike_pct=min_pct,
            max_strike_pct=max_pct,
            source_row_count=len(saved.contracts) // 2,
        )
        priced_by_scenario[scenario] = priced
        gex_by_scenario[scenario] = gex
        quality_by_scenario[scenario] = quality
        if scenario == "saved":
            saved_scenario_surface = surface

    mismatches = _check_baseline_reproduction(
        priced_by_scenario["saved"],
        saved.saved_priced,
        gex_by_scenario["saved"],
        dashboard["gex"],
        saved_scenario_surface,
        dashboard["surface"],
    )
    baseline_ok = not mismatches

    _write_contracts_csv(
        args.out / "contracts.csv", priced_by_scenario, contexts_by_scenario, saved.saved_priced
    )
    _write_cells_csv(args.out / "cells.csv", gex_by_scenario, actual_spot)
    _write_inputs_json(
        args.out / "inputs.json",
        saved,
        original_r,
        original_q,
        original_events,
        scenario_r,
        scenario_events,
        known_after_valuation,
        quote_time_hash_value,
    )
    _write_summary_md(
        args.out / "summary.md",
        saved,
        baseline_ok,
        mismatches,
        {s: {"quality": quality_by_scenario[s]} for s in SCENARIOS},
        original_is_legacy,
        original_events,
        scenario_events,
        expirations,
        known_after_valuation,
        quote_time_hash_value,
    )

    if not baseline_ok:
        print(f"BASELINE_REPRODUCTION_FAILED: see {args.out / 'summary.md'}", file=sys.stderr)
        return 1
    print(f"Reconciliation report written to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
