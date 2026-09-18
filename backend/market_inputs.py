"""Resolve cached/selected reference sources into frozen MarketInputs.

ADR-0001 Sections 6-8. No IV/GEX calculations here (analytics.py owns
those) -- this module only resolves *which* rate and dividend inputs a
snapshot uses, validates coverage, and combines reviewed schedules.
"""

import hashlib
import json
from datetime import date, datetime
from pathlib import Path

import storage
from models import (
    DividendFeedSnapshot,
    DividendRecord,
    ExpectedDividend,
    LocalReferenceInputs,
    ManualRateInput,
    MarketInputs,
    RateBatch,
    RateObservation,
    ResolvedDividend,
    ResolvedDividendSchedule,
    ResolvedRate,
    ScheduleReview,
    ny_local_date,
)
from provider import DividendDataProvider, ProviderError, RateDataProvider
from rates import sofr_percent_to_rate_cc

RATE_CACHE_TTL_SECONDS = 3_600
DIVIDEND_CACHE_TTL_SECONDS = 21_600
RATE_FRESHNESS_DAYS = 7
REVIEW_MAX_AGE_DAYS = 7


def load_local_reference_inputs(path: Path) -> LocalReferenceInputs:
    """Read+validate `reference_inputs.json` once per permitted refresh
    attempt (Section 5.2). A malformed file fails the attempt outright --
    it must never fall back to a prior in-memory version."""
    try:
        raw = json.loads(path.read_text())
    except OSError as exc:
        raise ProviderError("REFERENCE_INPUT_FILE_INVALID", f"Cannot read {path}: {exc}") from exc
    except ValueError as exc:
        raise ProviderError("REFERENCE_INPUT_FILE_INVALID", f"Malformed JSON in {path}: {exc}") from exc
    try:
        return LocalReferenceInputs.model_validate(raw)
    except Exception as exc:  # pydantic.ValidationError, kept generic: no pydantic import needed here
        raise ProviderError("REFERENCE_INPUT_FILE_INVALID", f"Invalid {path}: {exc}") from exc


# --- rate resolution (Section 6) --------------------------------------------


def _select_eligible_rate(
    observations: tuple[RateObservation, ...], valuation_at: datetime
) -> RateObservation:
    ny_date = ny_local_date(valuation_at)
    eligible = [o for o in observations if o.effective_date <= ny_date]
    if not eligible:
        raise ProviderError("RATE_UNAVAILABLE", "No eligible SOFR observation on or before valuation date")
    latest_date = max(o.effective_date for o in eligible)
    candidates = [o for o in eligible if o.effective_date == latest_date]
    unique = {(c.percent_rate, c.rate_type, c.revision_indicator) for c in candidates}
    if len(unique) > 1:
        raise ProviderError("RATE_CONFLICT", f"Conflicting SOFR observations for {latest_date}")
    if (ny_date - latest_date).days > RATE_FRESHNESS_DAYS:
        raise ProviderError("RATE_STALE", f"Most recent eligible SOFR observation {latest_date} is stale")
    return candidates[0]


def _fresh_cached_entry(
    db_path: str, *, kind: str, provider_id: str, subject: str, ttl_seconds: int, attempt_started_at: datetime
) -> dict | None:
    """The cached normalized payload if it exists and is within TTL, else
    None. Section 8.2: a negative age (clock skew, or a fetch that
    postdates this attempt) is never eligible."""
    cached = storage.get_reference_cache(db_path, kind=kind, provider_id=provider_id, subject=subject)
    if cached is None:
        return None
    age = (attempt_started_at - cached["fetched_at"]).total_seconds()
    return cached["normalized_json"] if 0 <= age <= ttl_seconds else None


def _fetch_rate_batch_cached(
    provider: RateDataProvider,
    provider_id: str,
    *,
    db_path: str,
    attempt_started_at: datetime,
    force_refresh: bool,
) -> RateBatch:
    if not force_refresh:
        normalized = _fresh_cached_entry(
            db_path,
            kind="rate",
            provider_id=provider_id,
            subject="USD",
            ttl_seconds=RATE_CACHE_TTL_SECONDS,
            attempt_started_at=attempt_started_at,
        )
        if normalized is not None:
            return RateBatch.model_validate(normalized)
    batch = provider.fetch_rates()
    storage.upsert_reference_cache(
        db_path,
        kind="rate",
        provider_id=provider_id,
        subject="USD",
        fetched_at=batch.fetched_at,
        normalized_json=batch.model_dump(mode="json"),
        raw_payload_json=batch.raw_payload_json,
    )
    return batch


def resolve_rate(
    *,
    rate_source: str,
    manual: ManualRateInput | None,
    provider: RateDataProvider | None,
    valuation_at: datetime,
    attempt_started_at: datetime,
    db_path: str,
    force_refresh: bool = False,
) -> ResolvedRate:
    if rate_source == "manual":
        if manual is None:
            raise ProviderError("RATE_UNAVAILABLE", "rate_source=manual requires manual_rate")
        ny_date = ny_local_date(valuation_at)
        if (ny_date - manual.effective_date).days > RATE_FRESHNESS_DAYS:
            raise ProviderError("RATE_STALE", "Manual rate's effective_date is stale for this valuation")
        return ResolvedRate(
            source_provider_id="manual",
            source_ref=manual.source_ref,
            effective_date=manual.effective_date,
            fetched_at=attempt_started_at,
            raw_percent_rate=None,
            rate_cc=manual.rate_cc,
            quote_convention="continuous_act365f",
            normalization="manual_already_continuous",
            revision_indicator=None,
            manual_reason=manual.reason,
        )

    if provider is None:
        raise ProviderError("RATE_UNAVAILABLE", f"No RateDataProvider configured for {rate_source!r}")
    batch = _fetch_rate_batch_cached(
        provider,
        rate_source,
        db_path=db_path,
        attempt_started_at=attempt_started_at,
        force_refresh=force_refresh,
    )
    selected = _select_eligible_rate(batch.observations, valuation_at)
    return ResolvedRate(
        source_provider_id=batch.provider_id,
        source_ref=batch.source_ref,
        effective_date=selected.effective_date,
        fetched_at=batch.fetched_at,
        raw_percent_rate=selected.percent_rate,
        rate_cc=sofr_percent_to_rate_cc(selected.percent_rate),
        quote_convention="percent_simple_act360",
        normalization="constant_daily_sofr_proxy",
        revision_indicator=selected.revision_indicator,
        manual_reason=None,
    )


# --- dividend resolution (Section 7) ----------------------------------------


def _validate_review(
    review: ScheduleReview,
    *,
    valuation_at: datetime,
    attempt_started_at: datetime,
    latest_in_scope_expiration: date | None,
) -> None:
    valuation_date = ny_local_date(valuation_at)
    if review.coverage_start > valuation_date:
        raise ProviderError(
            "DIVIDEND_COVERAGE_INCOMPLETE",
            f"{review.symbol}: coverage starts {review.coverage_start}, "
            f"after valuation date {valuation_date}",
        )
    if latest_in_scope_expiration is not None and review.coverage_end < latest_in_scope_expiration:
        raise ProviderError(
            "DIVIDEND_COVERAGE_INCOMPLETE",
            f"{review.symbol}: coverage ends {review.coverage_end}, "
            f"before latest in-scope expiration {latest_in_scope_expiration}",
        )
    review_age_days = (ny_local_date(attempt_started_at) - ny_local_date(review.reviewed_at)).days
    if not (0 <= review_age_days <= REVIEW_MAX_AGE_DAYS):
        raise ProviderError(
            "DIVIDEND_REVIEW_REQUIRED",
            f"{review.symbol}: review is {review_age_days} days old (reviewed_at={review.reviewed_at})",
        )


def _normalize_source_records(
    records: tuple[DividendRecord, ...], valuation_at: datetime
) -> dict[date, DividendRecord]:
    """Section 7.2: exact duplicates collapse; conflicting non-null amounts
    for the same ex-date fail. Only ordinary_cash records are usable here --
    special/other/unknown distributions are unsupported for automatic pricing.
    A declaration date after valuation_at is information this application
    could not have had at valuation time; live refreshes must reject it,
    not silently price with foresight."""
    by_ex_date: dict[date, DividendRecord] = {}
    for record in records:
        if record.kind != "ordinary_cash":
            continue
        declaration_date = record.declaration_date
        if declaration_date is not None and declaration_date > ny_local_date(valuation_at):
            raise ProviderError(
                "INPUT_KNOWLEDGE_AFTER_VALUATION",
                f"Declaration date {declaration_date} for ex-date {record.ex_date} "
                f"postdates the valuation date",
            )
        existing = by_ex_date.get(record.ex_date)
        if existing is None or existing == record:
            by_ex_date[record.ex_date] = record
            continue
        if existing.amount is not None and record.amount is not None and existing.amount != record.amount:
            raise ProviderError(
                "DIVIDEND_CONFLICT", f"Conflicting source records for ex-date {record.ex_date}"
            )
        by_ex_date[record.ex_date] = existing if existing.amount is not None else record
    return by_ex_date


def _resolve_payment_date(event: ExpectedDividend, source: DividendRecord) -> date | None:
    if event.payment_date is None:
        return source.payment_date
    if source.payment_date is None or event.payment_date == source.payment_date:
        return event.payment_date
    if event.amount_status == "declared":
        raise ProviderError(
            "DIVIDEND_CONFLICT",
            f"Payment date conflict for {event.event_id}: owner={event.payment_date}, "
            f"source={source.payment_date}",
        )
    return source.payment_date  # estimated event: a source payment date may replace it


def _resolve_one_event(
    event: ExpectedDividend,
    source: DividendRecord | None,
    *,
    dividend_source: str,
    warnings: list[str],
) -> ResolvedDividend:
    if dividend_source == "manual_schedule":
        manual_amount = event.amount
        if manual_amount is None:
            raise ProviderError(
                "DIVIDEND_PENDING_SOURCE_INVALID",
                f"pending_source is invalid under manual_schedule (event {event.event_id})",
            )
        return ResolvedDividend(
            event_id=event.event_id,
            ex_date=event.ex_date,
            payment_date=event.payment_date,
            amount=manual_amount,
            amount_status="owner_declared" if event.amount_status == "declared" else "estimated",
            source_ref=event.source_ref,
            source_provider_id="manual_schedule",
        )

    source_amount = source.amount if source is not None else None
    if source is not None and source_amount is not None:
        if event.amount_status == "declared" and event.amount is not None and event.amount != source_amount:
            raise ProviderError(
                "DIVIDEND_CONFLICT", f"Source amount conflicts with declared event {event.event_id}"
            )
        if event.amount_status == "estimated":
            warnings.append("DIVIDEND_ESTIMATE_REPLACED")
        return ResolvedDividend(
            event_id=event.event_id,
            ex_date=event.ex_date,
            payment_date=_resolve_payment_date(event, source),
            amount=source_amount,
            amount_status="source_reported",
            source_ref=source.source_ref,
            source_provider_id=dividend_source,
        )

    owner_amount = event.amount
    if event.amount_status == "estimated" and owner_amount is not None:
        warnings.append("DIVIDEND_AMOUNT_ESTIMATED")
        return ResolvedDividend(
            event_id=event.event_id,
            ex_date=event.ex_date,
            payment_date=event.payment_date,
            amount=owner_amount,
            amount_status="estimated",
            source_ref=event.source_ref,
            source_provider_id="owner_review",
        )
    if event.amount_status == "declared" and owner_amount is not None:
        return ResolvedDividend(
            event_id=event.event_id,
            ex_date=event.ex_date,
            payment_date=event.payment_date,
            amount=owner_amount,
            amount_status="owner_declared",
            source_ref=event.source_ref,
            source_provider_id="owner_review",
        )
    raise ProviderError(
        "DIVIDEND_AMOUNT_UNRESOLVED",
        f"event {event.event_id} (ex-date {event.ex_date}) has no usable source or owner amount",
    )


def _fetch_dividend_feed_cached(
    provider: DividendDataProvider,
    provider_id: str,
    symbol: str,
    *,
    db_path: str,
    attempt_started_at: datetime,
    force_refresh: bool,
) -> DividendFeedSnapshot:
    if not force_refresh:
        normalized = _fresh_cached_entry(
            db_path,
            kind="dividends",
            provider_id=provider_id,
            subject=symbol,
            ttl_seconds=DIVIDEND_CACHE_TTL_SECONDS,
            attempt_started_at=attempt_started_at,
        )
        if normalized is not None:
            return DividendFeedSnapshot.model_validate(normalized)
    feed = provider.fetch_dividends(symbol)
    storage.upsert_reference_cache(
        db_path,
        kind="dividends",
        provider_id=provider_id,
        subject=symbol,
        fetched_at=feed.fetched_at,
        normalized_json=feed.model_dump(mode="json"),
        raw_payload_json=feed.raw_payload_json,
    )
    return feed


def resolve_dividend_schedule(
    *,
    dividend_source: str,
    review: ScheduleReview,
    provider: DividendDataProvider | None,
    valuation_at: datetime,
    attempt_started_at: datetime,
    latest_in_scope_expiration: date | None,
    db_path: str,
    force_refresh: bool = False,
) -> tuple[ResolvedDividendSchedule, tuple[str, ...]]:
    _validate_review(
        review,
        valuation_at=valuation_at,
        attempt_started_at=attempt_started_at,
        latest_in_scope_expiration=latest_in_scope_expiration,
    )

    warnings: list[str] = []
    source_by_ex_date: dict[date, DividendRecord] = {}
    if dividend_source != "manual_schedule":
        if provider is None:
            raise ProviderError(
                "DIVIDEND_COVERAGE_UNVERIFIED", f"No DividendDataProvider for {dividend_source!r}"
            )
        feed = _fetch_dividend_feed_cached(
            provider,
            dividend_source,
            review.symbol,
            db_path=db_path,
            attempt_started_at=attempt_started_at,
            force_refresh=force_refresh,
        )
        source_by_ex_date = _normalize_source_records(feed.records, valuation_at)

        expected_ex_dates = {event.ex_date for event in review.expected_events}
        unexpected = sorted(
            ex_date
            for ex_date in source_by_ex_date
            if ex_date not in expected_ex_dates and review.coverage_start <= ex_date <= review.coverage_end
        )
        if unexpected:
            raise ProviderError(
                "DIVIDEND_REVIEW_REQUIRED",
                f"{review.symbol}: source reports unreviewed ex-date(s) {unexpected}",
            )

    resolved = tuple(
        _resolve_one_event(
            event, source_by_ex_date.get(event.ex_date), dividend_source=dividend_source, warnings=warnings
        )
        for event in review.expected_events
    )
    return ResolvedDividendSchedule(events=resolved, review=review), tuple(warnings)


# --- combining both into one frozen bundle (Section 5.1, 11.2) -------------


def canonical_json(value: object) -> str:
    """Section 11.2's canonicalization: sorted keys, compact separators,
    stable string representation. Shared by reference_bundle_hash here and
    app.py's calculation_input_hash -- both hashes need the same recipe."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def resolve_market_inputs(
    *,
    rate: ResolvedRate,
    dividend_schedule: ResolvedDividendSchedule,
    dividend_warnings: tuple[str, ...],
    resolved_at: datetime,
) -> MarketInputs:
    bundle = {
        "rate": rate.model_dump(mode="json"),
        "dividend_schedule": dividend_schedule.model_dump(mode="json"),
    }
    reference_bundle_hash = hashlib.sha256(canonical_json(bundle).encode()).hexdigest()
    # Section 6.2: "always emit FLAT_OVERNIGHT_RATE_PROXY for this source" --
    # derived from rate.normalization rather than threaded as a separate
    # parameter, since that field is exactly what distinguishes the SOFR
    # proxy conversion from a manual (already continuous) rate.
    is_flat_proxy = rate.normalization == "constant_daily_sofr_proxy"
    rate_warnings = ("FLAT_OVERNIGHT_RATE_PROXY",) if is_flat_proxy else ()
    warnings = rate_warnings + dividend_warnings
    return MarketInputs(
        resolved_at=resolved_at,
        rate=rate,
        dividend_schedule=dividend_schedule,
        warnings=warnings,
        reference_bundle_hash=reference_bundle_hash,
    )
