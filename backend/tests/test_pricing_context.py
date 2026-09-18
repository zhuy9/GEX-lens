"""N1, N3, N4, N6: the cash-PV pricing context and its consuming kernels
(ADR-0001 Section 9), plus Section 7.5's price-time alignment checks.

All fixtures are synthetic, chosen so that ADR-supplied `T`/`tau` values
land exactly (both anchor dates fall in EST, so no DST shift can perturb
the day-count arithmetic) -- not current rates or real distributions.
"""

import math
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from analytics import (
    analyze_snapshot_v2,
    bsm_gamma,
    bsm_price,
    build_expiry_pricing_context,
    check_price_time_alignment,
    price_quote_v2,
    solve_iv,
)
from models import OptionQuote, ResolvedDividend, dividend_ex_at
from provider import ProviderError

# 16:00 America/New_York on Jan 1 2026 -- winter (EST, UTC-5), so every
# offset below in whole days maps to an exact ACT/365F fraction with no
# DST perturbation.
VALUATION_AT = datetime(2026, 1, 1, 21, 0, tzinfo=UTC)


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


def make_dividend(event_id="e1", ex_date="2026-01-06", amount="1.25", payment_date=None) -> ResolvedDividend:
    return ResolvedDividend(
        event_id=event_id,
        ex_date=date.fromisoformat(ex_date),
        payment_date=date.fromisoformat(payment_date) if payment_date else None,
        amount=Decimal(amount),
        amount_status="owner_declared",
        source_ref="test",
        source_provider_id="test",
    )


# --- N1: no eligible cash events reduces to the existing q=0 kernel -----


def test_n1_no_dividends_matches_the_existing_q0_bsm_kernel():
    context = build_expiry_pricing_context(
        expiration=date(2027, 1, 1),
        valuation_at=VALUATION_AT,
        actual_spot=100.0,
        r_cc=0.05,
        dividend_events=(),
    )
    assert context.pv_dividends == 0.0
    assert context.model_spot == 100.0
    assert context.status == "OK"

    # T won't be exactly 1.0 here (Jan 1 2027 is a leap-adjacent boundary),
    # so pin the reference values directly against the kernels the v2 path
    # actually calls, at the exact T this context produced.
    price = bsm_price("C", context.model_spot, 100, context.T, context.r_cc, context.q_continuous, 0.20)
    gamma = bsm_gamma(context.model_spot, 100, context.T, context.r_cc, context.q_continuous, 0.20)
    legacy_price = bsm_price("C", 100.0, 100, context.T, 0.05, 0.0, 0.20)
    legacy_gamma = bsm_gamma(100.0, 100, context.T, 0.05, 0.0, 0.20)
    assert price == legacy_price
    assert gamma == legacy_gamma


def test_n1_reference_values_at_t_equals_one():
    # The ADR's own pinned N1 values, at the exact T=1/r/sigma it specifies.
    price = bsm_price("C", 100, 100, 1.0, 0.05, 0.0, 0.20)
    gamma = bsm_gamma(100, 100, 1.0, 0.05, 0.0, 0.20)
    assert price == pytest.approx(10.4505835722, abs=1e-8)
    assert gamma == pytest.approx(0.01876201735, abs=1e-10)
    iv, error = solve_iv("C", 100, 100, 1.0, 0.05, 0.0, price)
    assert error is None
    assert iv == pytest.approx(0.20, abs=1e-6)


# --- N3: cash event before expiry, payment after expiry -----------------


def test_n3_pinned_pv_model_spot_forward_price_and_gamma():
    # T=30/365 exactly: Jan 1 -> Jan 31, both EST, 30 calendar days.
    expiration = date(2026, 1, 31)
    # tau=40/365 exactly: pay_at is 40 calendar days after valuation_at's
    # 16:00-NY instant, landing on 16:00 NY too (both still EST).
    event = make_dividend(ex_date="2026-01-06", amount="1.25", payment_date="2026-02-10")

    context = build_expiry_pricing_context(
        expiration=expiration,
        valuation_at=VALUATION_AT,
        actual_spot=100.0,
        r_cc=0.04,
        dividend_events=(event,),
    )

    assert context.T == pytest.approx(30 / 365, abs=1e-12)
    assert context.pv_dividends == pytest.approx(1.24453254017385, abs=1e-8)
    assert context.model_spot == pytest.approx(98.75546745982615, abs=1e-8)
    assert context.forward == pytest.approx(99.08067726782153, abs=1e-8)
    assert context.status == "OK"
    assert context.used_event_ids == ("e1",)

    sigma = 0.25
    call = bsm_price("C", context.model_spot, 100, context.T, context.r_cc, 0.0, sigma)
    put = bsm_price("P", context.model_spot, 100, context.T, context.r_cc, 0.0, sigma)
    gamma = bsm_gamma(context.model_spot, 100, context.T, context.r_cc, 0.0, sigma)

    assert call == pytest.approx(2.4015898917023364, abs=1e-8)
    assert put == pytest.approx(3.3178951559197145, abs=1e-8)
    assert gamma == pytest.approx(0.056119740851123166, abs=1e-10)
    assert (call - put) == pytest.approx(-0.9163052642173852, abs=1e-8)

    iv_call, err_call = solve_iv("C", context.model_spot, 100, context.T, context.r_cc, 0.0, call)
    iv_put, err_put = solve_iv("P", context.model_spot, 100, context.T, context.r_cc, 0.0, put)
    assert err_call is None and err_put is None
    assert iv_call == pytest.approx(sigma, abs=1e-6)
    assert iv_put == pytest.approx(sigma, abs=1e-6)


# --- N4: derivative and horizon semantics --------------------------------


def test_n4_gamma_matches_a_central_finite_difference_in_actual_spot():
    expiration = date(2026, 1, 31)
    event = make_dividend(ex_date="2026-01-06", amount="1.25", payment_date="2026-02-10")
    r_cc, sigma, k, h = 0.04, 0.25, 100.0, 0.001 * 100.0

    def call_price(actual_spot: float) -> float:
        ctx = build_expiry_pricing_context(
            expiration=expiration,
            valuation_at=VALUATION_AT,
            actual_spot=actual_spot,
            r_cc=r_cc,
            dividend_events=(event,),
        )
        return bsm_price("C", ctx.model_spot, k, ctx.T, ctx.r_cc, 0.0, sigma)

    fd_gamma = (call_price(100 + h) - 2 * call_price(100) + call_price(100 - h)) / (h * h)
    ctx = build_expiry_pricing_context(
        expiration=expiration,
        valuation_at=VALUATION_AT,
        actual_spot=100.0,
        r_cc=r_cc,
        dividend_events=(event,),
    )
    analytic_gamma = bsm_gamma(ctx.model_spot, k, ctx.T, ctx.r_cc, 0.0, sigma)
    assert analytic_gamma == pytest.approx(fd_gamma, rel=1e-3)


def test_past_ex_date_with_future_payment_is_excluded():
    expiration = date(2026, 1, 31)
    event = make_dividend(ex_date="2025-12-20", payment_date="2026-01-15")  # ex-date before valuation
    context = build_expiry_pricing_context(
        expiration=expiration,
        valuation_at=VALUATION_AT,
        actual_spot=100.0,
        r_cc=0.04,
        dividend_events=(event,),
    )
    assert context.used_event_ids == ()
    assert context.pv_dividends == 0.0


def test_future_ex_date_after_expiry_is_excluded():
    expiration = date(2026, 1, 15)
    event = make_dividend(ex_date="2026-01-20")  # ex-date after this expiry
    context = build_expiry_pricing_context(
        expiration=expiration,
        valuation_at=VALUATION_AT,
        actual_spot=100.0,
        r_cc=0.04,
        dividend_events=(event,),
    )
    assert context.used_event_ids == ()


def test_ex_date_on_expiry_before_expiry_at_is_included():
    expiration = date(2026, 1, 20)
    event = make_dividend(ex_date="2026-01-20")  # 09:30 NY, still before 16:00 NY expiry_at
    context = build_expiry_pricing_context(
        expiration=expiration,
        valuation_at=VALUATION_AT,
        actual_spot=100.0,
        r_cc=0.04,
        dividend_events=(event,),
    )
    assert context.used_event_ids == ("e1",)


def test_valuation_exactly_at_ex_at_is_excluded():
    ex_at = dividend_ex_at(date(2026, 1, 10))
    event = make_dividend(ex_date="2026-01-10")
    context = build_expiry_pricing_context(
        expiration=date(2026, 1, 31),
        valuation_at=ex_at,
        actual_spot=100.0,
        r_cc=0.04,
        dividend_events=(event,),
    )
    assert context.used_event_ids == ()


def test_unknown_payment_date_discounts_to_ex_at_with_a_warning():
    event = make_dividend(ex_date="2026-01-10", payment_date=None)
    context = build_expiry_pricing_context(
        expiration=date(2026, 1, 31),
        valuation_at=VALUATION_AT,
        actual_spot=100.0,
        r_cc=0.04,
        dividend_events=(event,),
    )
    ex_at = dividend_ex_at(date(2026, 1, 10))
    expected_tau = (ex_at - VALUATION_AT).total_seconds() / 31_536_000
    expected_pv = 1.25 * math.exp(-0.04 * expected_tau)
    assert context.pv_dividends == pytest.approx(expected_pv, abs=1e-12)
    assert "DIVIDEND_PAYMENT_TIME_ASSUMED_AT_EX" in context.warnings


def test_ex_date_within_seven_days_is_flagged_near_ex_dividend():
    # Section 9.5: a modeling caution, not a trade/exercise signal.
    event = make_dividend(ex_date="2026-01-03")  # 2 days after VALUATION_AT (Jan 1)
    context = build_expiry_pricing_context(
        expiration=date(2026, 1, 31),
        valuation_at=VALUATION_AT,
        actual_spot=100.0,
        r_cc=0.04,
        dividend_events=(event,),
    )
    assert "NEAR_EX_DIVIDEND" in context.warnings


def test_ex_date_beyond_seven_days_is_not_flagged_near_ex_dividend():
    event = make_dividend(ex_date="2026-01-20")  # far beyond the 7-day window
    context = build_expiry_pricing_context(
        expiration=date(2026, 1, 31),
        valuation_at=VALUATION_AT,
        actual_spot=100.0,
        r_cc=0.04,
        dividend_events=(event,),
    )
    assert "NEAR_EX_DIVIDEND" not in context.warnings


def test_payment_date_before_ex_date_is_rejected_at_construction():
    with pytest.raises(ValidationError):
        make_dividend(ex_date="2026-01-10", payment_date="2026-01-05")


def test_dividend_pv_exceeding_actual_spot_is_invalid_with_no_clamping():
    event = make_dividend(ex_date="2026-01-10", amount="500.00")
    context = build_expiry_pricing_context(
        expiration=date(2026, 1, 31),
        valuation_at=VALUATION_AT,
        actual_spot=100.0,
        r_cc=0.04,
        dividend_events=(event,),
    )
    assert context.status == "INVALID_DIVIDEND_ADJUSTED_SPOT"
    assert context.model_spot < 0  # never clamped to a small positive number


def test_two_future_events_each_sum_their_pv_exactly_once():
    e1 = make_dividend(event_id="e1", ex_date="2026-01-06", amount="1.00")
    e2 = make_dividend(event_id="e2", ex_date="2026-01-20", amount="1.50")
    context = build_expiry_pricing_context(
        expiration=date(2026, 1, 31),
        valuation_at=VALUATION_AT,
        actual_spot=100.0,
        r_cc=0.04,
        dividend_events=(e1, e2),
    )
    assert set(context.used_event_ids) == {"e1", "e2"}

    solo_1 = build_expiry_pricing_context(
        expiration=date(2026, 1, 31),
        valuation_at=VALUATION_AT,
        actual_spot=100.0,
        r_cc=0.04,
        dividend_events=(e1,),
    )
    solo_2 = build_expiry_pricing_context(
        expiration=date(2026, 1, 31),
        valuation_at=VALUATION_AT,
        actual_spot=100.0,
        r_cc=0.04,
        dividend_events=(e2,),
    )
    assert context.pv_dividends == pytest.approx(solo_1.pv_dividends + solo_2.pv_dividends, abs=1e-12)


def test_winter_and_summer_ny_offsets_differ_by_the_dst_hour():
    winter = dividend_ex_at(date(2026, 1, 15))  # EST, UTC-5
    summer = dividend_ex_at(date(2026, 7, 15))  # EDT, UTC-4
    assert winter.astimezone(UTC).hour == 14  # 09:30 EST -> 14:30 UTC
    assert summer.astimezone(UTC).hour == 13  # 09:30 EDT -> 13:30 UTC


def test_tau_across_a_dst_transition_uses_real_elapsed_seconds_not_naive_days():
    # 2026's US spring-forward is March 8. A naive "N days * 86400" estimate
    # would be one hour too long across it; real UTC-instant subtraction
    # (via zoneinfo-aware datetimes) is not.
    before = dividend_ex_at(date(2026, 3, 1))  # EST
    after = dividend_ex_at(date(2026, 3, 15))  # EDT, same 09:30 NY time-of-day
    naive_seconds = 14 * 86400  # if DST were ignored
    real_seconds = (after - before).total_seconds()
    assert real_seconds == naive_seconds - 3600


# --- N6: cash-aware surface -----------------------------------------------


def test_n6_surface_recovers_constant_iv_across_maturities_before_and_after_an_event():
    sigma, r_cc = 0.25, 0.04
    event = make_dividend(ex_date="2026-01-20", amount="1.25", payment_date="2026-01-20")
    exp_before = date(2026, 1, 10)  # ex-date is after this expiry: ineligible
    exp_after = date(2026, 2, 15)  # ex-date is before this expiry: eligible

    ctx_before = build_expiry_pricing_context(
        expiration=exp_before,
        valuation_at=VALUATION_AT,
        actual_spot=100.0,
        r_cc=r_cc,
        dividend_events=(event,),
    )
    ctx_after = build_expiry_pricing_context(
        expiration=exp_after,
        valuation_at=VALUATION_AT,
        actual_spot=100.0,
        r_cc=r_cc,
        dividend_events=(event,),
    )
    assert ctx_before.pv_dividends == 0.0
    assert ctx_after.pv_dividends > 0.0
    contexts = {exp_before: ctx_before, exp_after: ctx_after}

    quotes = []
    for expiration, ctx in contexts.items():
        for j in range(-13, 13):
            strike = round(ctx.model_spot * math.exp(j * 0.01), 4)
            option_type = "P" if strike < ctx.model_spot else "C"
            price = bsm_price(option_type, ctx.model_spot, strike, ctx.T, ctx.r_cc, 0.0, sigma)
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

    priced, _, surface, _ = analyze_snapshot_v2(
        tuple(quotes),
        actual_spot=100.0,
        contexts=contexts,
        min_calendar_dte=1,
        max_calendar_dte=60,
        min_strike_pct=0.80,
        max_strike_pct=1.20,
        source_row_count=1,
    )
    assert surface.status == "READY"
    populated = [iv for row in surface.iv for iv in row if iv is not None]
    assert len(populated) > 20
    for iv in populated:
        assert iv == pytest.approx(sigma, abs=1e-6)


def test_price_quote_v2_excludes_contracts_for_an_invalid_context():
    event = make_dividend(ex_date="2026-01-10", amount="500.00")
    context = build_expiry_pricing_context(
        expiration=date(2026, 1, 31),
        valuation_at=VALUATION_AT,
        actual_spot=100.0,
        r_cc=0.04,
        dividend_events=(event,),
    )
    assert context.status == "INVALID_DIVIDEND_ADJUSTED_SPOT"
    priced = price_quote_v2(
        make_quote(expiration=date(2026, 1, 31)),
        context=context,
        min_calendar_dte=1,
        max_calendar_dte=60,
        min_strike_pct=0.80,
        max_strike_pct=1.20,
    )
    assert priced.exclusion_reason == "INVALID_DIVIDEND_ADJUSTED_SPOT"
    assert priced.iv is None and priced.gamma is None


def test_price_quote_v2_uses_actual_spot_for_scope_even_with_a_different_model_spot():
    event = make_dividend(ex_date="2026-01-10", amount="1.25")
    context = build_expiry_pricing_context(
        expiration=date(2026, 1, 31),
        valuation_at=VALUATION_AT,
        actual_spot=100.0,
        r_cc=0.04,
        dividend_events=(event,),
    )
    assert context.model_spot != context.actual_spot
    far_out_of_actual_scope = make_quote(
        expiration=date(2026, 1, 31), strike=Decimal("130")
    )  # 1.30x actual spot
    priced = price_quote_v2(
        far_out_of_actual_scope,
        context=context,
        min_calendar_dte=1,
        max_calendar_dte=60,
        min_strike_pct=0.80,
        max_strike_pct=1.20,
    )
    assert priced.exclusion_reason == "OUT_OF_SCOPE"


def test_gex_exposure_scales_by_actual_spot_not_model_spot_when_they_differ():
    # N5 (Section 14): "Repeat with model_spot != actual_spot and assert the
    # scaling still uses actual spot" -- for the GEX exposure number itself,
    # not just OUT_OF_SCOPE eligibility (covered above).
    event = make_dividend(ex_date="2026-01-10", amount="1.25")
    context = build_expiry_pricing_context(
        expiration=date(2026, 1, 31),
        valuation_at=VALUATION_AT,
        actual_spot=100.0,
        r_cc=0.04,
        dividend_events=(event,),
    )
    assert context.model_spot != context.actual_spot

    sigma = 0.20
    call_price = bsm_price("C", context.model_spot, 100.0, context.T, context.r_cc, 0.0, sigma)
    put_price = bsm_price("P", context.model_spot, 100.0, context.T, context.r_cc, 0.0, sigma)
    call = make_quote(
        option_type="C",
        bid=round(call_price - 0.01, 4),
        ask=round(call_price + 0.01, 4),
        open_interest=1000,
    )
    put = make_quote(
        option_type="P",
        bid=round(put_price - 0.01, 4),
        ask=round(put_price + 0.01, 4),
        open_interest=600,
    )
    priced, gex, _, _ = analyze_snapshot_v2(
        (call, put),
        actual_spot=context.actual_spot,
        contexts={call.expiration: context},
        min_calendar_dte=1,
        max_calendar_dte=60,
        min_strike_pct=0.80,
        max_strike_pct=1.20,
        source_row_count=2,
    )
    call_pq = next(pq for pq in priced if pq.quote.option_type == "C")
    put_pq = next(pq for pq in priced if pq.quote.option_type == "P")
    assert call_pq.gamma is not None
    assert put_pq.gamma is not None

    cell = gex.cells[0][0]
    assert cell is not None
    expected_call = call_pq.gamma * 1000 * 100 * context.actual_spot**2 * 0.01
    expected_put = put_pq.gamma * 600 * 100 * context.actual_spot**2 * 0.01
    assert cell.call_exposure == pytest.approx(expected_call, rel=1e-9)
    assert cell.put_exposure == pytest.approx(expected_put, rel=1e-9)
    # A wrong implementation that scaled by model_spot instead would give a
    # detectably different number here, since model_spot != actual_spot.
    wrong_call = call_pq.gamma * 1000 * 100 * context.model_spot**2 * 0.01
    assert cell.call_exposure != pytest.approx(wrong_call, rel=1e-9)


# --- Section 7.5: price-time alignment -----------------------------------


EX_AT = dividend_ex_at(date(2026, 1, 20))


def test_agreeing_precise_timestamps_are_aligned():
    before = EX_AT - timedelta(hours=1)
    assert (
        check_price_time_alignment(
            chain_asof=before, spot_asof=before, spot_asof_date=None, valuation_at=before, ex_at=EX_AT
        )
        is None
    )


def test_disagreeing_precise_timestamps_raise_price_time_mismatch():
    before, after = EX_AT - timedelta(minutes=1), EX_AT + timedelta(minutes=1)
    with pytest.raises(ProviderError) as exc:
        check_price_time_alignment(
            chain_asof=before, spot_asof=after, spot_asof_date=None, valuation_at=before, ex_at=EX_AT
        )
    assert exc.value.code == "DIVIDEND_PRICE_TIME_MISMATCH"


def test_cum_dividend_date_only_spot_with_post_ex_valuation_raises():
    with pytest.raises(ProviderError) as exc:
        check_price_time_alignment(
            chain_asof=None,
            spot_asof=None,
            spot_asof_date=date(2026, 1, 15),
            valuation_at=EX_AT + timedelta(hours=1),
            ex_at=EX_AT,
        )
    assert exc.value.code == "DIVIDEND_PRICE_TIME_MISMATCH"


def test_date_only_spot_consistent_with_pre_ex_valuation_is_aligned():
    assert (
        check_price_time_alignment(
            chain_asof=None,
            spot_asof=None,
            spot_asof_date=date(2026, 1, 15),
            valuation_at=EX_AT - timedelta(hours=1),
            ex_at=EX_AT,
        )
        is None
    )


def test_date_only_spot_observed_after_ex_date_is_aligned():
    assert (
        check_price_time_alignment(
            chain_asof=None,
            spot_asof=None,
            spot_asof_date=date(2026, 1, 25),
            valuation_at=EX_AT + timedelta(days=5),
            ex_at=EX_AT,
        )
        is None
    )


def test_no_usable_precision_returns_the_unverified_warning():
    assert (
        check_price_time_alignment(
            chain_asof=None,
            spot_asof=None,
            spot_asof_date=None,
            valuation_at=EX_AT - timedelta(hours=1),
            ex_at=EX_AT,
        )
        == "DIVIDEND_ALIGNMENT_UNVERIFIED"
    )


def test_context_warns_when_alignment_is_unverified_for_an_eligible_event():
    event = make_dividend(ex_date="2026-01-10")
    context = build_expiry_pricing_context(
        expiration=date(2026, 1, 31),
        valuation_at=VALUATION_AT,
        actual_spot=100.0,
        r_cc=0.04,
        dividend_events=(event,),
    )
    assert "DIVIDEND_ALIGNMENT_UNVERIFIED" in context.warnings


def test_context_raises_on_pre_ex_date_only_spot_with_post_ex_valuation_for_an_already_ex_event():
    # C01: the event's ex_at <= valuation_at (already past, PV-ineligible)
    # must still be checked for alignment -- the eligibility filter must not
    # skip this the way it used to.
    ex_at = dividend_ex_at(date(2026, 1, 10))
    event = make_dividend(ex_date="2026-01-10")
    with pytest.raises(ProviderError) as exc:
        build_expiry_pricing_context(
            expiration=date(2026, 1, 31),
            valuation_at=ex_at + timedelta(hours=1),
            actual_spot=100.0,
            r_cc=0.04,
            dividend_events=(event,),
            spot_asof_date=date(2026, 1, 5),  # before ex-date
        )
    assert exc.value.code == "DIVIDEND_PRICE_TIME_MISMATCH"


def test_context_raises_on_a_known_cross_ex_mismatch():
    ex_date = date(2026, 1, 10)
    ex_at = dividend_ex_at(ex_date)
    event = make_dividend(ex_date="2026-01-10")
    with pytest.raises(ProviderError) as exc:
        build_expiry_pricing_context(
            expiration=date(2026, 1, 31),
            valuation_at=VALUATION_AT,
            actual_spot=100.0,
            r_cc=0.04,
            dividend_events=(event,),
            chain_asof=ex_at - timedelta(minutes=1),
            spot_asof=ex_at + timedelta(minutes=1),
        )
    assert exc.value.code == "DIVIDEND_PRICE_TIME_MISMATCH"
