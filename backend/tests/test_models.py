from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from models import ChainSnapshot, DividendRecord, ExpectedDividend, OptionQuote, ScheduleReview, Settings

VALID = dict(
    source_mode="fixture",
    db_path="data/test.duckdb",
    refresh_min_interval_seconds=60,
    pricing_model="cash_pv_bsm_v2",
    rate_source="fixture",
    dividend_sources={"SPY": "fixture", "QQQ": "fixture", "AAPL": "fixture"},
    reference_inputs_path="reference_inputs.json",
)


def test_valid_settings_parses():
    settings = Settings(**VALID)
    assert settings.source_mode == "fixture"


@pytest.mark.parametrize(
    "override",
    [
        # Enabled symbols now come from instruments.py, not settings.
        {"symbols": ("SPY",)},
        {"refresh_min_interval_seconds": 59},
        # C08: only one pricing algorithm is ever executed -- an unsupported
        # model string must fail at startup, not be silently echoed.
        {"pricing_model": "some_other_model_v3"},
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


# C02: ScheduleReview must reject duplicate/out-of-coverage events at the
# canonical boundary, not let the resolver sum them silently.


def _expected(event_id, ex_date, amount="1.00") -> ExpectedDividend:
    return ExpectedDividend(
        event_id=event_id,
        ex_date=date.fromisoformat(ex_date),
        payment_date=None,
        amount=Decimal(amount),
        amount_status="declared",
        source_ref="test",
    )


def _review_kwargs(**overrides) -> dict:
    defaults = dict(
        symbol="AAPL",
        reviewed_at=datetime(2026, 1, 1, tzinfo=UTC),
        coverage_start=date(2026, 1, 1),
        coverage_end=date(2026, 4, 1),
        no_other_events_expected=True,
        source_refs=("test",),
        expected_events=(),
    )
    defaults.update(overrides)
    return defaults


def test_duplicate_event_id_is_rejected():
    with pytest.raises(ValidationError):
        ScheduleReview(
            **_review_kwargs(expected_events=(_expected("e1", "2026-02-05"), _expected("e1", "2026-03-05")))
        )


def test_duplicate_ex_date_under_different_ids_is_rejected():
    with pytest.raises(ValidationError):
        ScheduleReview(
            **_review_kwargs(expected_events=(_expected("e1", "2026-02-05"), _expected("e2", "2026-02-05")))
        )


def test_event_outside_coverage_is_rejected():
    with pytest.raises(ValidationError):
        ScheduleReview(
            **_review_kwargs(coverage_end=date(2026, 2, 1), expected_events=(_expected("e1", "2026-03-05"),))
        )


def test_unique_in_coverage_events_are_accepted():
    review = ScheduleReview(
        **_review_kwargs(expected_events=(_expected("e1", "2026-02-05"), _expected("e2", "2026-03-05")))
    )
    assert len(review.expected_events) == 2


# A payment date before its own ex-date must fail as early as possible --
# ResolvedDividend already enforced this, but ExpectedDividend (reviewed
# local config) and DividendRecord (raw provider data) did not, so a bad
# value could reach deep into resolution before failing there instead.


def test_expected_dividend_rejects_payment_before_ex_date():
    with pytest.raises(ValidationError):
        ExpectedDividend(
            event_id="e1",
            ex_date=date(2026, 2, 5),
            payment_date=date(2026, 1, 30),
            amount=Decimal("1.00"),
            amount_status="declared",
            source_ref="test",
        )


def test_dividend_record_rejects_payment_before_ex_date():
    with pytest.raises(ValidationError):
        DividendRecord(
            provider_record_id=None,
            symbol="AAPL",
            currency="USD",
            ex_date=date(2026, 2, 5),
            payment_date=date(2026, 1, 30),
            declaration_date=None,
            amount=Decimal("1.00"),
            kind="ordinary_cash",
            source_ref="test",
        )
