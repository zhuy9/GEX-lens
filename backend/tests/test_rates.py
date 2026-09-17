"""N2: SOFR quote conversion, response validation, and eligibility rules."""

import math
from datetime import UTC, date, datetime

import httpx
import pytest

import rates
from market_inputs import _select_eligible_rate
from models import RateObservation
from provider import ProviderError

VALUATION_AT = datetime(2026, 1, 10, 15, 0, tzinfo=UTC)  # NY-local Jan 10


def make_provider(handler) -> rates.NyFedSofrProvider:
    transport = httpx.MockTransport(handler)
    return rates.NyFedSofrProvider(client=httpx.Client(transport=transport))


def _handler(ref_rates=None, status_code=200, text=None):
    def handler(request):
        if text is not None:
            return httpx.Response(status_code, text=text)
        return httpx.Response(status_code, json={"refRates": ref_rates or []})

    return handler


def _obs(
    effective_date: str, percent_rate: float = 4.0, revision_indicator: str | None = None
) -> RateObservation:
    return RateObservation(
        effective_date=date.fromisoformat(effective_date),
        percent_rate=percent_rate,
        rate_type="SOFR",
        revision_indicator=revision_indicator,
    )


# --- N2: normalization -------------------------------------------------


def test_sofr_percent_to_rate_cc_matches_the_pinned_value():
    r_cc = rates.sofr_percent_to_rate_cc(4.0)
    assert abs(r_cc - 0.04055330263601719) <= 1e-12
    assert abs(math.exp(r_cc / 365) - (1 + 0.04 / 360)) <= 1e-14


@pytest.mark.parametrize("percent", [0.0, -0.5, 10.0])
def test_sofr_percent_to_rate_cc_accepts_zero_negative_and_positive(percent):
    assert math.isfinite(rates.sofr_percent_to_rate_cc(percent))


# --- N2/N8: response validation -----------------------------------------


def test_fetch_rates_filters_to_sofr_type_only():
    provider = make_provider(
        _handler(
            [
                {
                    "effectiveDate": "2026-01-09",
                    "type": "SOFR",
                    "percentRate": 4.0,
                    "revisionIndicator": None,
                },
                {
                    "effectiveDate": "2026-01-09",
                    "type": "OBFR",
                    "percentRate": 3.9,
                    "revisionIndicator": None,
                },
            ]
        )
    )
    batch = provider.fetch_rates()
    assert len(batch.observations) == 1
    assert batch.observations[0].rate_type == "SOFR"


def test_boolean_percent_rate_is_rejected_not_treated_as_a_number():
    provider = make_provider(
        _handler(
            [{"effectiveDate": "2026-01-09", "type": "SOFR", "percentRate": True, "revisionIndicator": None}]
        )
    )
    with pytest.raises(ProviderError) as exc:
        provider.fetch_rates()
    assert exc.value.code == "SCHEMA_ERROR"


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_percent_rate_is_rejected(bad):
    provider = make_provider(
        _handler(
            [{"effectiveDate": "2026-01-09", "type": "SOFR", "percentRate": bad, "revisionIndicator": None}]
        )
    )
    with pytest.raises(ProviderError) as exc:
        provider.fetch_rates()
    assert exc.value.code == "SCHEMA_ERROR"


def test_missing_effective_date_is_schema_error():
    provider = make_provider(_handler([{"type": "SOFR", "percentRate": 4.0, "revisionIndicator": None}]))
    with pytest.raises(ProviderError) as exc:
        provider.fetch_rates()
    assert exc.value.code == "SCHEMA_ERROR"


def test_malformed_json_is_schema_error():
    provider = make_provider(_handler(text="not json"))
    with pytest.raises(ProviderError) as exc:
        provider.fetch_rates()
    assert exc.value.code == "SCHEMA_ERROR"


def test_missing_refrates_list_is_schema_error():
    def handler(request):
        return httpx.Response(200, json={"somethingElse": []})

    provider = make_provider(handler)
    with pytest.raises(ProviderError) as exc:
        provider.fetch_rates()
    assert exc.value.code == "SCHEMA_ERROR"


def test_429_raises_upstream_rate_limited():
    provider = make_provider(_handler(status_code=429))
    with pytest.raises(ProviderError) as exc:
        provider.fetch_rates()
    assert exc.value.code == "UPSTREAM_RATE_LIMITED"


def test_timeout_raises_upstream_timeout():
    def handler(request):
        raise httpx.TimeoutException("timed out")

    provider = make_provider(handler)
    with pytest.raises(ProviderError) as exc:
        provider.fetch_rates()
    assert exc.value.code == "UPSTREAM_TIMEOUT"


# --- eligibility/selection (market_inputs._select_eligible_rate) --------


def test_selects_most_recent_eligible_observation_and_ignores_future_dates():
    obs = (_obs("2026-01-08"), _obs("2026-01-09", percent_rate=4.1), _obs("2026-01-15"))
    selected = _select_eligible_rate(obs, VALUATION_AT)
    assert selected.effective_date == date(2026, 1, 9)
    assert selected.percent_rate == 4.1


def test_no_eligible_observation_raises_rate_unavailable():
    with pytest.raises(ProviderError) as exc:
        _select_eligible_rate((_obs("2026-01-15"),), VALUATION_AT)
    assert exc.value.code == "RATE_UNAVAILABLE"


def test_exact_duplicate_observations_collapse():
    selected = _select_eligible_rate((_obs("2026-01-09"), _obs("2026-01-09")), VALUATION_AT)
    assert selected.effective_date == date(2026, 1, 9)


def test_conflicting_same_date_observations_reject_selection():
    obs = (_obs("2026-01-09", percent_rate=4.0), _obs("2026-01-09", percent_rate=4.2))
    with pytest.raises(ProviderError) as exc:
        _select_eligible_rate(obs, VALUATION_AT)
    assert exc.value.code == "RATE_CONFLICT"


def test_observation_beyond_seven_days_old_is_stale():
    with pytest.raises(ProviderError) as exc:
        _select_eligible_rate((_obs("2026-01-02"),), VALUATION_AT)  # 8 calendar days before Jan 10
    assert exc.value.code == "RATE_STALE"


def test_observation_exactly_seven_days_old_is_still_eligible():
    selected = _select_eligible_rate((_obs("2026-01-03"),), VALUATION_AT)  # exactly 7 days before
    assert selected.effective_date == date(2026, 1, 3)
