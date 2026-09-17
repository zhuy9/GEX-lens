"""NyFedSofrProvider: HTTP fetch and response validation for the SOFR batch.

Field paths follow ADR-0001 Section 6.1, captured from the response envelope
accessible during authoring; production access still needs its own M0
verification before "nyfed_sofr" is enabled live.
"""

import math
from datetime import UTC, date, datetime

import httpx

from models import RateBatch, RateObservation
from provider import ProviderError

BASE_URL = "https://markets.newyorkfed.org/api/rates/secured/sofr/last/5.json"
CONNECT_TIMEOUT_SECONDS = 5.0
RW_POOL_TIMEOUT_SECONDS = 10.0
MAX_RESPONSE_BYTES = 2 * 1024 * 1024  # Section 8.3's 2 MiB reference-response cap


def sofr_percent_to_rate_cc(percent_rate: float) -> float:
    """Section 6.2's flat constant daily-calendar financing proxy."""
    simple_rate = percent_rate / 100
    return 365 * math.log1p(simple_rate / 360)


def _parse_observation(raw: object) -> RateObservation | None:
    """None means "not a SOFR row" (e.g. a different `type`); a malformed
    SOFR row still raises, matching the chain adapter's schema-error style."""
    if not isinstance(raw, dict):
        raise ProviderError("SCHEMA_ERROR", "refRates entry is not an object")
    rate_type = raw.get("type")
    if not isinstance(rate_type, str):
        raise ProviderError("SCHEMA_ERROR", "refRates entry missing 'type'")
    if rate_type != "SOFR":
        return None

    effective_date_raw = raw.get("effectiveDate")
    if not isinstance(effective_date_raw, str):
        raise ProviderError("SCHEMA_ERROR", "refRates entry missing 'effectiveDate'")
    try:
        effective_date = date.fromisoformat(effective_date_raw)
    except ValueError as exc:
        raise ProviderError("SCHEMA_ERROR", f"Unparseable effectiveDate {effective_date_raw!r}") from exc

    percent_rate_raw = raw.get("percentRate")
    if isinstance(percent_rate_raw, bool) or not isinstance(percent_rate_raw, int | float):
        raise ProviderError("SCHEMA_ERROR", "refRates entry's percentRate is not a number")
    percent_rate = float(percent_rate_raw)
    if not math.isfinite(percent_rate):
        raise ProviderError("SCHEMA_ERROR", "refRates entry's percentRate is not finite")

    revision_indicator = raw.get("revisionIndicator")
    if revision_indicator is not None and not isinstance(revision_indicator, str):
        raise ProviderError("SCHEMA_ERROR", "revisionIndicator must be a string or null")

    return RateObservation(
        effective_date=effective_date,
        percent_rate=percent_rate,
        rate_type=rate_type,
        revision_indicator=revision_indicator,
    )


class NyFedSofrProvider:
    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(RW_POOL_TIMEOUT_SECONDS, connect=CONNECT_TIMEOUT_SECONDS),
        )

    def close(self) -> None:
        """Closes the underlying HTTP client if this provider built its own
        (mirrors NasdaqProvider.close's ownership rule)."""
        self._client.close()

    def fetch_rates(self) -> RateBatch:
        try:
            response = self._client.get(BASE_URL)
        except httpx.TimeoutException as exc:
            raise ProviderError("UPSTREAM_TIMEOUT", str(exc)) from exc
        except httpx.HTTPError as exc:
            raise ProviderError("UPSTREAM_UNAVAILABLE", str(exc)) from exc

        if len(response.content) > MAX_RESPONSE_BYTES:
            raise ProviderError("REFERENCE_RESPONSE_TOO_LARGE", "SOFR response exceeded the 2 MiB cap")
        if response.status_code == 429:
            raise ProviderError("UPSTREAM_RATE_LIMITED", "NY Fed rate limit")
        if response.status_code != 200:
            raise ProviderError("UPSTREAM_UNAVAILABLE", f"Unexpected status {response.status_code}")
        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderError("SCHEMA_ERROR", f"Malformed JSON: {exc}") from exc
        if not isinstance(body, dict):
            raise ProviderError("SCHEMA_ERROR", "Response body is not a JSON object")
        ref_rates = body.get("refRates")
        if not isinstance(ref_rates, list):
            raise ProviderError("SCHEMA_ERROR", "Response missing 'refRates' list")

        observations = tuple(obs for raw in ref_rates if (obs := _parse_observation(raw)) is not None)

        return RateBatch(
            provider_id="nyfed_sofr",
            fetched_at=datetime.now(UTC),
            source_ref=BASE_URL,
            observations=observations,
            raw_payload_json=response.text,
        )
