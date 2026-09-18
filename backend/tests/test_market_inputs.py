"""N8: dividend-source safety and Section 7.3's deterministic resolution,
plus Section 8's reference caching (M2.2-M2.8)."""

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

import fixtures
import market_inputs
import rates
import storage
from models import (
    DividendFeedSnapshot,
    DividendRecord,
    ExpectedDividend,
    ManualRateInput,
    RateBatch,
    RateObservation,
    ScheduleReview,
)
from provider import ProviderError

VALUATION_AT = datetime(2026, 1, 10, 15, 0, tzinfo=UTC)  # NY-local Jan 10
ATTEMPT_AT = VALUATION_AT


def _review(
    expected_events=(),
    reviewed_at=VALUATION_AT,
    coverage_start=date(2026, 1, 1),
    coverage_end=date(2026, 4, 1),
    symbol="AAPL",
) -> ScheduleReview:
    return ScheduleReview(
        symbol=symbol,
        reviewed_at=reviewed_at,
        coverage_start=coverage_start,
        coverage_end=coverage_end,
        no_other_events_expected=True,
        source_refs=("test",),
        expected_events=expected_events,
    )


def _expected(
    event_id, ex_date, amount=None, amount_status="declared", payment_date=None
) -> ExpectedDividend:
    return ExpectedDividend(
        event_id=event_id,
        ex_date=date.fromisoformat(ex_date),
        payment_date=date.fromisoformat(payment_date) if payment_date else None,
        amount=Decimal(amount) if amount is not None else None,
        amount_status=amount_status,
        source_ref="test",
    )


def _record(
    ex_date, amount=None, payment_date=None, kind="ordinary_cash", declaration_date=None
) -> DividendRecord:
    return DividendRecord(
        provider_record_id=None,
        symbol="AAPL",
        currency="USD",
        ex_date=date.fromisoformat(ex_date),
        payment_date=date.fromisoformat(payment_date) if payment_date else None,
        declaration_date=date.fromisoformat(declaration_date) if declaration_date else None,
        amount=Decimal(amount) if amount is not None else None,
        kind=kind,
        source_ref="test",
    )


class StubDividendProvider:
    """fetched_at defaults to ATTEMPT_AT, not the real wall clock: tests pin
    ATTEMPT_AT to a fixed past instant, and comparing a real fetch timestamp
    against that fake "now" would make every cache-age check look negative
    (and therefore ineligible) regardless of what is actually being tested."""

    def __init__(self, records=(), provider_id="stub", fetched_at=ATTEMPT_AT):
        self.records = records
        self.provider_id = provider_id
        self.fetched_at = fetched_at
        self.calls = 0

    def fetch_dividends(self, symbol):
        self.calls += 1
        return DividendFeedSnapshot(
            provider_id=self.provider_id,
            symbol=symbol,
            fetched_at=self.fetched_at,
            source_asof=None,
            records=self.records,
            raw_payload_json="{}",
            warnings=(),
        )


class StubRateProvider:
    """See StubDividendProvider's fetched_at note above."""

    def __init__(self, observations, provider_id="stub", fetched_at=ATTEMPT_AT):
        self.observations = observations
        self.provider_id = provider_id
        self.fetched_at = fetched_at
        self.calls = 0

    def fetch_rates(self):
        self.calls += 1
        return RateBatch(
            provider_id=self.provider_id,
            fetched_at=self.fetched_at,
            source_ref="stub",
            observations=self.observations,
            raw_payload_json="{}",
        )


def _resolve_dividends(db_path, **overrides):
    kwargs = dict(
        dividend_source="manual_schedule",
        review=_review(),
        provider=None,
        valuation_at=VALUATION_AT,
        attempt_started_at=ATTEMPT_AT,
        latest_in_scope_expiration=None,
        db_path=db_path,
    )
    kwargs.update(overrides)
    return market_inputs.resolve_dividend_schedule(**kwargs)


# --- manual_schedule ------------------------------------------------------


def test_manual_schedule_uses_each_declared_and_estimated_entry(tmp_path):
    review = _review(
        expected_events=(
            _expected("e1", "2026-02-05", amount="0.25", amount_status="declared"),
            _expected("e2", "2026-03-05", amount="0.26", amount_status="estimated"),
        )
    )
    schedule, warnings = _resolve_dividends(str(tmp_path / "t.duckdb"), review=review)
    assert [e.amount_status for e in schedule.events] == ["owner_declared", "estimated"]
    assert warnings == ()


def test_manual_schedule_rejects_pending_source():
    review = _review(expected_events=(_expected("e1", "2026-02-05", amount_status="pending_source"),))
    with pytest.raises(ProviderError) as exc:
        _resolve_dividends("unused", review=review)
    assert exc.value.code == "DIVIDEND_PENDING_SOURCE_INVALID"


def test_empty_reviewed_schedule_is_allowed():
    schedule, _ = _resolve_dividends("unused", review=_review(expected_events=()))
    assert schedule.events == ()


# --- review validation (M2.3) --------------------------------------------


def test_review_older_than_seven_days_requires_re_review():
    review = _review(reviewed_at=datetime(2026, 1, 2, 15, 0, tzinfo=UTC))  # 8 days old at Jan 10
    with pytest.raises(ProviderError) as exc:
        _resolve_dividends("unused", review=review)
    assert exc.value.code == "DIVIDEND_REVIEW_REQUIRED"


def test_review_exactly_seven_days_old_is_still_valid():
    review = _review(reviewed_at=datetime(2026, 1, 3, 15, 0, tzinfo=UTC))  # exactly 7 days old
    schedule, _ = _resolve_dividends("unused", review=review)
    assert schedule.events == ()


def test_coverage_end_before_latest_in_scope_expiration_is_incomplete():
    review = _review(coverage_end=date(2026, 2, 1))
    with pytest.raises(ProviderError) as exc:
        _resolve_dividends("unused", review=review, latest_in_scope_expiration=date(2026, 3, 1))
    assert exc.value.code == "DIVIDEND_COVERAGE_INCOMPLETE"


def test_no_in_scope_contracts_skips_the_end_coverage_check():
    review = _review(coverage_end=date(2026, 1, 20))  # short coverage, but nothing to cover
    schedule, _ = _resolve_dividends("unused", review=review, latest_in_scope_expiration=None)
    assert schedule.events == ()


def test_coverage_start_after_valuation_date_is_incomplete():
    review = _review(coverage_start=date(2026, 1, 15))  # starts after the Jan 10 valuation date
    with pytest.raises(ProviderError) as exc:
        _resolve_dividends("unused", review=review)
    assert exc.value.code == "DIVIDEND_COVERAGE_INCOMPLETE"


# --- provider-backed matching, conflicts, estimate replacement (N8) -----


def test_source_amount_replaces_matching_estimate_and_warns(tmp_path):
    db_path = str(tmp_path / "t.duckdb")
    storage.init_schema(db_path)
    review = _review(
        expected_events=(_expected("e1", "2026-02-05", amount="0.24", amount_status="estimated"),)
    )
    provider = StubDividendProvider(records=(_record("2026-02-05", amount="0.25"),))
    schedule, warnings = _resolve_dividends(
        db_path, dividend_source="test_stub", review=review, provider=provider
    )
    event = schedule.events[0]
    assert event.amount == Decimal("0.25")
    assert event.amount_status == "source_reported"
    assert "DIVIDEND_ESTIMATE_REPLACED" in warnings


def test_source_amount_conflicting_with_declared_owner_amount_fails(tmp_path):
    db_path = str(tmp_path / "t.duckdb")
    storage.init_schema(db_path)
    review = _review(
        expected_events=(_expected("e1", "2026-02-05", amount="0.24", amount_status="declared"),)
    )
    provider = StubDividendProvider(records=(_record("2026-02-05", amount="0.25"),))
    with pytest.raises(ProviderError) as exc:
        _resolve_dividends(db_path, dividend_source="test_stub", review=review, provider=provider)
    assert exc.value.code == "DIVIDEND_CONFLICT"


def test_pending_source_filled_by_matching_source_amount(tmp_path):
    db_path = str(tmp_path / "t.duckdb")
    storage.init_schema(db_path)
    review = _review(expected_events=(_expected("e1", "2026-02-05", amount_status="pending_source"),))
    provider = StubDividendProvider(records=(_record("2026-02-05", amount="0.25"),))
    schedule, _ = _resolve_dividends(db_path, dividend_source="test_stub", review=review, provider=provider)
    assert schedule.events[0].amount == Decimal("0.25")
    assert schedule.events[0].amount_status == "source_reported"


def test_pending_source_with_no_matching_source_record_is_unresolved(tmp_path):
    db_path = str(tmp_path / "t.duckdb")
    storage.init_schema(db_path)
    review = _review(expected_events=(_expected("e1", "2026-02-05", amount_status="pending_source"),))
    provider = StubDividendProvider(records=())
    with pytest.raises(ProviderError) as exc:
        _resolve_dividends(db_path, dividend_source="test_stub", review=review, provider=provider)
    assert exc.value.code == "DIVIDEND_AMOUNT_UNRESOLVED"


def test_new_unreviewed_source_event_invalidates_the_review(tmp_path):
    db_path = str(tmp_path / "t.duckdb")
    storage.init_schema(db_path)
    review = _review(expected_events=())  # nothing expected
    provider = StubDividendProvider(records=(_record("2026-02-05", amount="0.25"),))  # surprise event
    with pytest.raises(ProviderError) as exc:
        _resolve_dividends(db_path, dividend_source="test_stub", review=review, provider=provider)
    assert exc.value.code == "DIVIDEND_REVIEW_REQUIRED"


def test_duplicate_source_rows_for_same_ex_date_collapse(tmp_path):
    db_path = str(tmp_path / "t.duckdb")
    storage.init_schema(db_path)
    review = _review(
        expected_events=(_expected("e1", "2026-02-05", amount="0.25", amount_status="estimated"),)
    )
    provider = StubDividendProvider(
        records=(_record("2026-02-05", amount="0.25"), _record("2026-02-05", amount="0.25"))
    )
    schedule, _ = _resolve_dividends(db_path, dividend_source="test_stub", review=review, provider=provider)
    assert len(schedule.events) == 1


def test_conflicting_duplicate_source_rows_for_same_ex_date_fail(tmp_path):
    db_path = str(tmp_path / "t.duckdb")
    storage.init_schema(db_path)
    review = _review(
        expected_events=(_expected("e1", "2026-02-05", amount="0.25", amount_status="estimated"),)
    )
    provider = StubDividendProvider(
        records=(_record("2026-02-05", amount="0.25"), _record("2026-02-05", amount="0.30"))
    )
    with pytest.raises(ProviderError) as exc:
        _resolve_dividends(db_path, dividend_source="test_stub", review=review, provider=provider)
    assert exc.value.code == "DIVIDEND_CONFLICT"


def test_unsupported_record_kind_is_ignored_not_used_for_pricing(tmp_path):
    db_path = str(tmp_path / "t.duckdb")
    storage.init_schema(db_path)
    review = _review(
        expected_events=(_expected("e1", "2026-02-05", amount="0.25", amount_status="declared"),)
    )
    provider = StubDividendProvider(records=(_record("2026-02-05", amount="99.0", kind="other"),))
    schedule, _ = _resolve_dividends(db_path, dividend_source="test_stub", review=review, provider=provider)
    assert schedule.events[0].amount == Decimal("0.25")  # owner's own declared amount, not the "other" record
    assert schedule.events[0].amount_status == "owner_declared"


def test_source_record_declared_after_valuation_is_rejected(tmp_path):
    # Section 7.2: a declaration date later than valuation_at is information
    # this application could not have had at valuation time.
    db_path = str(tmp_path / "t.duckdb")
    storage.init_schema(db_path)
    review = _review(
        expected_events=(_expected("e1", "2026-02-05", amount="0.25", amount_status="estimated"),)
    )
    provider = StubDividendProvider(
        records=(
            _record("2026-02-05", amount="0.25", declaration_date="2026-06-01"),
        )  # after VALUATION_AT (Jan 10)
    )
    with pytest.raises(ProviderError) as exc:
        _resolve_dividends(db_path, dividend_source="test_stub", review=review, provider=provider)
    assert exc.value.code == "INPUT_KNOWLEDGE_AFTER_VALUATION"


def test_history_only_feed_with_no_upcoming_rows_uses_owner_review_estimate(tmp_path):
    """A dividend-history endpoint's past-only response never silently
    implies zero future dividends (Section 1.1/E4); the reviewed owner
    estimate still carries the event."""
    db_path = str(tmp_path / "t.duckdb")
    storage.init_schema(db_path)
    review = _review(
        expected_events=(_expected("e1", "2026-02-05", amount="0.25", amount_status="estimated"),)
    )
    provider = StubDividendProvider(records=(_record("2025-11-05", amount="0.24"),))  # only a past record
    schedule, warnings = _resolve_dividends(
        db_path, dividend_source="test_stub", review=review, provider=provider
    )
    assert schedule.events[0].amount_status == "estimated"
    assert "DIVIDEND_AMOUNT_ESTIMATED" in warnings


def test_payment_date_conflict_on_declared_event_fails(tmp_path):
    db_path = str(tmp_path / "t.duckdb")
    storage.init_schema(db_path)
    review = _review(
        expected_events=(
            _expected("e1", "2026-02-05", amount="0.25", amount_status="declared", payment_date="2026-02-10"),
        )
    )
    provider = StubDividendProvider(
        records=(_record("2026-02-05", amount="0.25", payment_date="2026-02-12"),)
    )
    with pytest.raises(ProviderError) as exc:
        _resolve_dividends(db_path, dividend_source="test_stub", review=review, provider=provider)
    assert exc.value.code == "DIVIDEND_CONFLICT"


def test_source_payment_date_replaces_an_estimated_events_payment_date(tmp_path):
    db_path = str(tmp_path / "t.duckdb")
    storage.init_schema(db_path)
    review = _review(
        expected_events=(
            _expected(
                "e1", "2026-02-05", amount="0.24", amount_status="estimated", payment_date="2026-02-10"
            ),
        )
    )
    provider = StubDividendProvider(
        records=(_record("2026-02-05", amount="0.25", payment_date="2026-02-12"),)
    )
    schedule, _ = _resolve_dividends(db_path, dividend_source="test_stub", review=review, provider=provider)
    assert schedule.events[0].payment_date == date(2026, 2, 12)


# --- manual rate resolution ------------------------------------------------


def test_manual_rate_source_makes_zero_provider_calls():
    manual = ManualRateInput(
        rate_cc=0.04,
        effective_date=date(2026, 1, 9),
        entered_at=datetime(2026, 1, 9, 12, 0, tzinfo=UTC),
        source_ref="local-file",
        reason="test",
    )
    resolved = market_inputs.resolve_rate(
        rate_source="manual",
        manual=manual,
        provider=None,
        valuation_at=VALUATION_AT,
        attempt_started_at=ATTEMPT_AT,
        db_path="unused",
    )
    assert resolved.rate_cc == 0.04
    assert resolved.normalization == "manual_already_continuous"
    assert resolved.quote_convention == "continuous_act365f"
    # fetched_at is when this attempt read the file (attempt_started_at), not
    # the owner's entered_at -- those can be days apart.
    assert resolved.fetched_at == ATTEMPT_AT
    assert resolved.fetched_at != manual.entered_at


def test_manual_rate_with_stale_effective_date_is_rejected():
    manual = ManualRateInput(
        rate_cc=0.04,
        effective_date=date(2026, 1, 1),
        entered_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
        source_ref="local-file",
        reason="test",
    )
    with pytest.raises(ProviderError) as exc:
        market_inputs.resolve_rate(
            rate_source="manual",
            manual=manual,
            provider=None,
            valuation_at=VALUATION_AT,
            attempt_started_at=ATTEMPT_AT,
            db_path="unused",
        )
    assert exc.value.code == "RATE_STALE"


# --- M2.4/M2.5: reference caching ------------------------------------------


def _rate_provider() -> StubRateProvider:
    return StubRateProvider(
        observations=(
            RateObservation(
                effective_date=date(2026, 1, 9), percent_rate=4.0, rate_type="SOFR", revision_indicator=None
            ),
        )
    )


def test_rate_cache_hit_makes_zero_provider_calls(tmp_path):
    db_path = str(tmp_path / "t.duckdb")
    storage.init_schema(db_path)
    provider = _rate_provider()
    first = market_inputs.resolve_rate(
        rate_source="test_stub",
        manual=None,
        provider=provider,
        valuation_at=VALUATION_AT,
        attempt_started_at=ATTEMPT_AT,
        db_path=db_path,
    )
    assert provider.calls == 1
    second = market_inputs.resolve_rate(
        rate_source="test_stub",
        manual=None,
        provider=provider,
        valuation_at=VALUATION_AT,
        attempt_started_at=ATTEMPT_AT,
        db_path=db_path,
    )
    assert provider.calls == 1  # cache hit
    assert second.rate_cc == first.rate_cc


def test_rate_cache_miss_after_ttl_expiry_makes_one_new_call(tmp_path):
    db_path = str(tmp_path / "t.duckdb")
    storage.init_schema(db_path)
    provider = _rate_provider()
    market_inputs.resolve_rate(
        rate_source="test_stub",
        manual=None,
        provider=provider,
        valuation_at=VALUATION_AT,
        attempt_started_at=ATTEMPT_AT,
        db_path=db_path,
    )
    assert provider.calls == 1
    later = ATTEMPT_AT + timedelta(seconds=market_inputs.RATE_CACHE_TTL_SECONDS + 1)
    market_inputs.resolve_rate(
        rate_source="test_stub",
        manual=None,
        provider=provider,
        valuation_at=later,
        attempt_started_at=later,
        db_path=db_path,
    )
    assert provider.calls == 2


def test_force_refresh_bypasses_ttl_even_when_cache_is_fresh(tmp_path):
    db_path = str(tmp_path / "t.duckdb")
    storage.init_schema(db_path)
    provider = _rate_provider()
    market_inputs.resolve_rate(
        rate_source="test_stub",
        manual=None,
        provider=provider,
        valuation_at=VALUATION_AT,
        attempt_started_at=ATTEMPT_AT,
        db_path=db_path,
    )
    assert provider.calls == 1
    market_inputs.resolve_rate(
        rate_source="test_stub",
        manual=None,
        provider=provider,
        valuation_at=VALUATION_AT,
        attempt_started_at=ATTEMPT_AT,
        db_path=db_path,
        force_refresh=True,
    )
    assert provider.calls == 2


def test_failed_refetch_after_cache_expiry_preserves_the_previous_cache_entry(tmp_path):
    db_path = str(tmp_path / "t.duckdb")
    storage.init_schema(db_path)
    market_inputs.resolve_rate(
        rate_source="test_stub",
        manual=None,
        provider=_rate_provider(),
        valuation_at=VALUATION_AT,
        attempt_started_at=ATTEMPT_AT,
        db_path=db_path,
    )
    later = ATTEMPT_AT + timedelta(seconds=market_inputs.RATE_CACHE_TTL_SECONDS + 1)

    class FailingRateProvider:
        def fetch_rates(self):
            raise ProviderError("UPSTREAM_UNAVAILABLE", "boom")

    with pytest.raises(ProviderError):
        market_inputs.resolve_rate(
            rate_source="test_stub",
            manual=None,
            provider=FailingRateProvider(),
            valuation_at=later,
            attempt_started_at=later,
            db_path=db_path,
        )
    cached = storage.get_reference_cache(db_path, kind="rate", provider_id="test_stub", subject="USD")
    assert cached is not None  # the earlier successful entry was never erased


def test_dividend_cache_hit_makes_zero_provider_calls(tmp_path):
    db_path = str(tmp_path / "t.duckdb")
    storage.init_schema(db_path)
    review = _review(expected_events=())
    provider = StubDividendProvider(records=())
    _resolve_dividends(db_path, dividend_source="test_stub", review=review, provider=provider)
    assert provider.calls == 1
    _resolve_dividends(db_path, dividend_source="test_stub", review=review, provider=provider)
    assert provider.calls == 1  # cache hit


# --- M2.6: arbitrary non-Nasdaq/non-NY-Fed stubs resolve the same shape ---


def test_arbitrary_stub_providers_resolve_the_same_canonical_market_inputs(tmp_path):
    db_path = str(tmp_path / "t.duckdb")
    storage.init_schema(db_path)
    custom_rate_provider = _rate_provider()
    custom_rate_provider.provider_id = "totally-custom-rate-stub"
    rate = market_inputs.resolve_rate(
        rate_source="totally-custom-rate-stub",
        manual=None,
        provider=custom_rate_provider,
        valuation_at=VALUATION_AT,
        attempt_started_at=ATTEMPT_AT,
        db_path=db_path,
    )
    schedule, warnings = _resolve_dividends(
        db_path,
        dividend_source="another-custom-dividend-stub",
        review=_review(expected_events=()),
        provider=StubDividendProvider(records=()),
    )
    inputs = market_inputs.resolve_market_inputs(
        rate=rate, dividend_schedule=schedule, dividend_warnings=warnings, resolved_at=VALUATION_AT
    )
    assert inputs.rate.source_provider_id == "totally-custom-rate-stub"
    assert inputs.dividend_schedule.review.symbol == "AAPL"
    assert len(inputs.reference_bundle_hash) == 64  # sha256 hex digest


def test_reference_bundle_hash_is_deterministic_for_identical_inputs(tmp_path):
    db_path = str(tmp_path / "t.duckdb")
    storage.init_schema(db_path)

    def build():
        rate = market_inputs.resolve_rate(
            rate_source="stub",
            manual=None,
            provider=_rate_provider(),
            valuation_at=VALUATION_AT,
            attempt_started_at=ATTEMPT_AT,
            db_path=db_path,
            force_refresh=True,
        )
        schedule, warnings = _resolve_dividends(db_path, review=_review(expected_events=()))
        return market_inputs.resolve_market_inputs(
            rate=rate, dividend_schedule=schedule, dividend_warnings=warnings, resolved_at=VALUATION_AT
        )

    assert build().reference_bundle_hash == build().reference_bundle_hash


def test_live_rate_emits_flat_overnight_rate_proxy_warning(tmp_path):
    # Section 6.2: "always emit FLAT_OVERNIGHT_RATE_PROXY for this source" --
    # any live (non-manual) rate uses the same constant-daily-SOFR-proxy
    # conversion, not just nyfed_sofr specifically.
    db_path = str(tmp_path / "t.duckdb")
    storage.init_schema(db_path)
    rate = market_inputs.resolve_rate(
        rate_source="stub",
        manual=None,
        provider=_rate_provider(),
        valuation_at=VALUATION_AT,
        attempt_started_at=ATTEMPT_AT,
        db_path=db_path,
    )
    schedule, warnings = _resolve_dividends(db_path, review=_review(expected_events=()))
    inputs = market_inputs.resolve_market_inputs(
        rate=rate, dividend_schedule=schedule, dividend_warnings=warnings, resolved_at=VALUATION_AT
    )
    assert "FLAT_OVERNIGHT_RATE_PROXY" in inputs.warnings


def test_manual_rate_does_not_emit_flat_overnight_rate_proxy_warning(tmp_path):
    db_path = str(tmp_path / "t.duckdb")
    storage.init_schema(db_path)
    manual = ManualRateInput(
        rate_cc=0.04,
        effective_date=date(2026, 1, 9),
        entered_at=datetime(2026, 1, 9, 12, 0, tzinfo=UTC),
        source_ref="local-file",
        reason="test",
    )
    rate = market_inputs.resolve_rate(
        rate_source="manual",
        manual=manual,
        provider=None,
        valuation_at=VALUATION_AT,
        attempt_started_at=ATTEMPT_AT,
        db_path=db_path,
    )
    schedule, warnings = _resolve_dividends(db_path, review=_review(expected_events=()))
    inputs = market_inputs.resolve_market_inputs(
        rate=rate, dividend_schedule=schedule, dividend_warnings=warnings, resolved_at=VALUATION_AT
    )
    assert "FLAT_OVERNIGHT_RATE_PROXY" not in inputs.warnings


# --- local reference-input file --------------------------------------------


def test_load_local_reference_inputs_parses_manual_rate_and_schedules(tmp_path):
    path = tmp_path / "reference_inputs.json"
    path.write_text(
        json.dumps(
            {
                "input_schema_version": 1,
                "manual_rate": {
                    "rate_cc": 0.04,
                    "effective_date": "2026-01-09",
                    "entered_at": "2026-01-09T12:00:00Z",
                    "source_ref": "local",
                    "reason": "test",
                },
                "schedules": {
                    "SPY": {
                        "symbol": "SPY",
                        "reviewed_at": "2026-01-02T21:00:00Z",
                        "coverage_start": "2026-01-02",
                        "coverage_end": "2026-04-02",
                        "no_other_events_expected": True,
                        "source_refs": ["synthetic"],
                        "expected_events": [
                            {
                                "event_id": "e1",
                                "ex_date": "2026-01-07",
                                "payment_date": "2026-02-11",
                                "amount": "1.25",
                                "amount_status": "estimated",
                                "source_ref": "synthetic",
                            }
                        ],
                    }
                },
            }
        )
    )
    parsed = market_inputs.load_local_reference_inputs(path)
    assert parsed.manual_rate.rate_cc == 0.04
    assert parsed.schedules["SPY"].expected_events[0].amount == Decimal("1.25")


def test_load_local_reference_inputs_rejects_malformed_json(tmp_path):
    path = tmp_path / "reference_inputs.json"
    path.write_text("{not json")
    with pytest.raises(ProviderError) as exc:
        market_inputs.load_local_reference_inputs(path)
    assert exc.value.code == "REFERENCE_INPUT_FILE_INVALID"


def test_load_local_reference_inputs_rejects_a_missing_file(tmp_path):
    with pytest.raises(ProviderError) as exc:
        market_inputs.load_local_reference_inputs(tmp_path / "missing.json")
    assert exc.value.code == "REFERENCE_INPUT_FILE_INVALID"


# --- M2.7: fixture providers need neither network nor an untracked file ---


def test_fixture_rate_provider_makes_zero_network_calls_and_normalizes_like_sofr():
    batch = fixtures.FixtureRateProvider().fetch_rates()
    assert batch.provider_id == "fixture"
    selected = market_inputs._select_eligible_rate(batch.observations, fixtures.FIXED_VALUATION_AT)
    assert abs(rates.sofr_percent_to_rate_cc(selected.percent_rate) - 0.04055330263601719) <= 1e-12


def test_fixture_dividend_provider_and_review_resolve_to_an_empty_schedule(tmp_path):
    db_path = str(tmp_path / "t.duckdb")
    storage.init_schema(db_path)
    review = fixtures.fixture_schedule_review("SPY")
    schedule, warnings = market_inputs.resolve_dividend_schedule(
        dividend_source="fixture",
        review=review,
        provider=fixtures.FixtureDividendProvider(),
        valuation_at=fixtures.FIXED_VALUATION_AT,
        attempt_started_at=fixtures.FIXED_VALUATION_AT,
        latest_in_scope_expiration=None,
        db_path=db_path,
    )
    assert schedule.events == ()
    assert warnings == ()
