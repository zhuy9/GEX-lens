import math
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from analytics import (
    analyze_snapshot,
    bsm_gamma,
    bsm_price,
    build_gex,
    build_surface,
    price_quote,
    solve_iv,
)
from models import OptionQuote, ny_local_date, year_fraction

VALUATION_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)


def make_quote(**overrides) -> OptionQuote:
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
    return OptionQuote(**defaults)


def test_bsm_price_and_gamma_match_reference_values():
    # M2.1
    price = bsm_price("C", 100, 100, 1.0, 0.05, 0.0, 0.20)
    gamma = bsm_gamma(100, 100, 1.0, 0.05, 0.0, 0.20)
    assert price == pytest.approx(10.4505835722, abs=1e-8)
    assert gamma == pytest.approx(0.01876201735, abs=1e-10)

    iv, error = solve_iv("C", 100, 100, 1.0, 0.05, 0.0, price)
    assert error is None
    assert iv == pytest.approx(0.20, abs=1e-6)


def test_put_call_parity_and_gamma_finite_difference():
    # M2.2
    s, k, t, r, q, sigma = 100, 105, 0.5, 0.04, 0.02, 0.25
    call = bsm_price("C", s, k, t, r, q, sigma)
    put = bsm_price("P", s, k, t, r, q, sigma)
    parity_error = abs((call - put) - (s * math.exp(-q * t) - k * math.exp(-r * t)))
    assert parity_error < 1e-8

    h = 0.001 * s
    analytic_gamma = bsm_gamma(s, k, t, r, q, sigma)
    fd_gamma = (
        bsm_price("C", s + h, k, t, r, q, sigma)
        - 2 * bsm_price("C", s, k, t, r, q, sigma)
        + bsm_price("C", s - h, k, t, r, q, sigma)
    ) / (h * h)
    assert analytic_gamma == pytest.approx(fd_gamma, rel=1e-4)


@pytest.mark.parametrize(
    "overrides,expected_reason",
    [
        ({"expiration": date(2025, 12, 31)}, "OUT_OF_SCOPE"),  # 0 DTE relative to valuation
        ({"strike": Decimal("1000")}, "OUT_OF_SCOPE"),  # far outside 0.8x-1.2x
        ({"flags": ("NONSTANDARD_CONTRACT",)}, "NONSTANDARD_CONTRACT"),
        ({"bid": None}, "INVALID_BID_ASK"),
        ({"bid": 2.0, "ask": 1.0}, "INVALID_BID_ASK"),
        ({"bid": 0.01, "ask": 0.02}, "LOW_MID"),
        ({"bid": 0.5, "ask": 3.0}, "WIDE_SPREAD"),
        ({"bid": 150.0, "ask": 150.5}, "MODEL_PRICE_BOUNDS"),  # above the call's upper bound (spot=100)
    ],
)
def test_quote_eligibility_exclusion_reasons(overrides, expected_reason):
    # M2.3
    quote = make_quote(**overrides)
    priced = price_quote(
        quote,
        spot=100.0,
        r=0.04,
        q=0.0,
        valuation_at=VALUATION_AT,
        min_calendar_dte=1,
        max_calendar_dte=60,
    )
    assert priced.exclusion_reason == expected_reason
    assert priced.iv is None
    assert priced.gamma is None


def test_low_time_value_is_excluded_when_within_bounds_but_near_intrinsic():
    # M2.3: mid must be *within* the model bounds but too close to the lower
    # bound to imply meaningful time value.
    quote = make_quote(expiration=date(2026, 1, 31))
    t = (date(2026, 1, 31) - date(2025, 12, 31)).days / 365.0
    lower = max(0.0, 100.0 * math.exp(0) - 100.0 * math.exp(-0.04 * t))
    mid = lower + 0.005
    quote = quote.model_copy(update={"bid": mid, "ask": mid})
    priced = price_quote(
        quote, spot=100.0, r=0.04, q=0.0, valuation_at=VALUATION_AT, min_calendar_dte=1, max_calendar_dte=60
    )
    assert priced.exclusion_reason == "LOW_TIME_VALUE"


def test_missing_oi_does_not_block_iv_but_blocks_gex():
    quote = make_quote(open_interest=None)
    priced = price_quote(
        quote, spot=100.0, r=0.04, q=0.0, valuation_at=VALUATION_AT, min_calendar_dte=1, max_calendar_dte=60
    )
    assert priced.exclusion_reason is None
    assert priced.iv is not None


def test_zero_oi_yields_zero_exposure_even_without_iv():
    # PRD 7: explicit OI=0 -> zero exposure even if IV cannot be calculated
    quote = make_quote(open_interest=0, bid=0.01, ask=0.02)  # LOW_MID -> no gamma
    priced = price_quote(
        quote, spot=100.0, r=0.04, q=0.0, valuation_at=VALUATION_AT, min_calendar_dte=1, max_calendar_dte=60
    )
    assert priced.gamma is None
    gex = build_gex((priced,), spot=100.0)
    cell = gex.cells[0][0]
    assert cell is not None
    assert cell.call_exposure == 0.0
    assert cell.put_exposure is None
    assert cell.status == "INCOMPLETE"


def test_gex_exposure_formula_matches_reference_values():
    # M2.4
    call = make_quote(option_type="C", open_interest=1000)
    put = make_quote(option_type="P", open_interest=600)
    priced = []
    for contract in (call, put):
        pq = price_quote(
            contract,
            spot=100.0,
            r=0.04,
            q=0.0,
            valuation_at=VALUATION_AT,
            min_calendar_dte=1,
            max_calendar_dte=60,
        )
        priced.append(pq.model_copy(update={"gamma": 0.02}))
    gex = build_gex(tuple(priced), spot=100.0)
    cell = gex.cells[0][0]
    assert cell is not None
    assert cell.call_exposure == pytest.approx(200_000.0)
    assert cell.put_exposure == pytest.approx(120_000.0)
    assert cell.signed_proxy == pytest.approx(80_000.0)
    assert cell.gross_exposure == pytest.approx(320_000.0)
    assert cell.status == "COMPLETE"


def test_missing_side_yields_null_gex_cell():
    # M2.5
    call = make_quote(option_type="C", open_interest=1000)
    priced_call = price_quote(
        call, spot=100.0, r=0.04, q=0.0, valuation_at=VALUATION_AT, min_calendar_dte=1, max_calendar_dte=60
    )
    priced_call = priced_call.model_copy(update={"gamma": 0.02})
    gex = build_gex((priced_call,), spot=100.0)
    cell = gex.cells[0][0]
    assert cell is not None
    assert cell.signed_proxy is None
    assert cell.gross_exposure is None
    assert cell.status == "INCOMPLETE"


def test_constant_iv_surface_recovers_input_iv():
    # M2.6
    spot, r, q, sigma = 100.0, 0.04, 0.0, 0.25
    baseline = ny_local_date(VALUATION_AT)
    quotes = []
    for dte in (30, 60):
        expiration = baseline + timedelta(days=dte)
        # T must match price_quote()'s exact convention (16:00 NY expiry), not
        # a naive dte/365 approximation, or the recovered IV will drift off
        # the input sigma by the difference between the two T values.
        t = year_fraction(expiration, VALUATION_AT)
        # Stay inside the 0.8x-1.2x strike scope and away from the deep-OTM
        # tails, which legitimately fail LOW_MID at this vol/tenor.
        for j in range(-13, 13):
            k = j * 0.01
            strike = round(spot * math.exp(k), 4)
            option_type = "P" if strike < spot else "C"
            price = bsm_price(option_type, spot, strike, t, r, q, sigma)
            quotes.append(
                make_quote(
                    expiration=expiration,
                    strike=Decimal(str(strike)),
                    option_type=option_type,
                    bid=price,
                    ask=price,
                    last=price,
                )
            )
    priced = tuple(
        price_quote(
            q_, spot=spot, r=r, q=q, valuation_at=VALUATION_AT, min_calendar_dte=1, max_calendar_dte=60
        )
        for q_ in quotes
    )
    surface = build_surface(priced, spot, r, q, VALUATION_AT)
    assert surface.status == "READY"
    iv_grid = surface.iv
    assert iv_grid is not None
    populated = 0
    for row in iv_grid:
        for iv in row:
            if iv is not None:
                assert iv == pytest.approx(sigma, abs=1e-6)
                populated += 1
    assert populated > 20  # a meaningful, non-vacuous chunk of the grid is filled


def test_analyze_snapshot_is_deterministic():
    # M2.7
    quotes = (make_quote(option_type="C"), make_quote(option_type="P"))
    kwargs = dict(
        spot=100.0,
        r=0.04,
        q=0.0,
        valuation_at=VALUATION_AT,
        min_calendar_dte=1,
        max_calendar_dte=60,
        source_row_count=1,
    )
    first = analyze_snapshot(quotes, **kwargs)
    second = analyze_snapshot(quotes, **kwargs)
    assert first[0] == second[0]
    assert first[1] == second[1]
    assert first[2] == second[2]
    assert first[3] == second[3]
