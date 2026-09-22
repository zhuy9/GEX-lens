"""NasdaqProvider/NasdaqDividendProvider: HTTP, pagination, Nasdaq-to-canonical
parsing. Neither adapter calls the other's fetch method (ADR-0001 4.2).

Field paths and pagination behavior here follow docs/source-contract.md and
docs/dividend-source-contract.md, verified against real samples. Do not
change parsing assumptions without updating those documents first.
"""

import math
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation

import httpx
from pydantic import ValidationError

from api import (
    CHAIN_COLLECTION_WINDOW_SECONDS,
    CHAIN_MAX_CONTRACTS,
    CHAIN_MAX_REQUESTS,
    CHAIN_MAX_RESPONSE_BYTES,
    CHAIN_PAGE_LIMIT,
    CONNECT_TIMEOUT_SECONDS,
    NASDAQ_CHAIN_URL,
    NASDAQ_DIVIDENDS_URL,
    NASDAQ_HEADERS,
    REFERENCE_MAX_RESPONSE_BYTES,
    RW_POOL_TIMEOUT_SECONDS,
)
from instruments import INSTRUMENTS
from models import (
    ChainRequest,
    ChainSnapshot,
    DividendFeedSnapshot,
    DividendRecord,
    OptionQuote,
    ny_local_date,
)
from provider import ProviderError

# Confirmed live 2026-09-17: a whole-dollar price (e.g. AAPL at exactly
# "$337") omits the decimal entirely -- the fractional part is optional,
# not always present.
_LAST_TRADE_RE = re.compile(r"^LAST TRADE:\s*\$([\d,]+(?:\.\d+)?)\s*\(AS OF (.+)\)$")
_HEADER_DATE_FMT = "%B %d, %Y"
# The lastTrade "(AS OF ...)" suffix, e.g. "SEP 16, 2026" -- a calendar date
# only, no time-of-day (docs/source-contract.md).
_LAST_TRADE_DATE_FMT = "%b %d, %Y"
# aapl--260916c00245000 -> YY MM DD, C/P, strike*1000 zero-padded to 8 digits.
# Only ever seen on the call side (docs/source-contract.md).
_DRILLDOWN_RE = re.compile(r"--(\d{2})(\d{2})(\d{2})[cp](\d{8})$")


def _parse_num(raw: object) -> float | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if text in ("", "--", "N/A"):
        return None
    try:
        return float(text.replace(",", "").replace("$", ""))
    except ValueError:
        return None


def _parse_count(raw: object) -> tuple[int | None, bool]:
    """Nonnegative whole-number count (OI/volume). Returns (value, invalid).

    A recognized missing sentinel is (None, False). A malformed value --
    fractional, negative, NaN, Infinity, or otherwise unparseable -- is
    (None, True): the source sent something wrong, not merely nothing.
    Never truncate fractional counts into a misleading zero exposure.
    """
    if raw is None:
        return None, False
    text = str(raw).strip()
    if text in ("", "--", "N/A"):
        return None, False
    try:
        value = float(text.replace(",", "").replace("$", ""))
    except ValueError:
        return None, True
    if not math.isfinite(value) or value < 0 or value != int(value):
        return None, True
    return int(value), False


def _parse_retry_after(raw: str | None) -> int | None:
    """HTTP Retry-After delay in seconds. Not an option-count field: RFC 7231
    only specifies a delta-seconds integer, so this stays a simple, separate
    parser rather than reusing the strict market-data count parser."""
    if raw is None:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return int(value)


def _http_get(client: httpx.Client, url: str, params: dict) -> httpx.Response:
    """Shared by both Nasdaq adapters (ADR-0001 4.2: they may share a small
    HTTP helper; neither calls the other's fetch method)."""
    try:
        return client.get(url, params=params)
    except httpx.TimeoutException as exc:
        raise ProviderError("UPSTREAM_TIMEOUT", str(exc)) from exc
    except httpx.HTTPError as exc:
        raise ProviderError("UPSTREAM_UNAVAILABLE", str(exc)) from exc


def _parse_json_envelope(response: httpx.Response, *, schema_error_code: str, check_403: bool) -> dict:
    """Shared status/JSON/rCode validation. Byte-size limits differ between
    the two adapters (chain: cumulative across pages; dividends: one
    response) and stay each caller's own concern, checked before this."""
    if response.status_code == 429:
        retry_after = _parse_retry_after(response.headers.get("Retry-After"))
        raise ProviderError("UPSTREAM_RATE_LIMITED", "Nasdaq rate limit", retry_after_seconds=retry_after)
    if check_403 and response.status_code == 403:
        raise ProviderError("UPSTREAM_ACCESS_DENIED", "Nasdaq access denied (403)")
    if response.status_code != 200:
        raise ProviderError("UPSTREAM_UNAVAILABLE", f"Unexpected status {response.status_code}")
    try:
        body = response.json()
    except ValueError as exc:
        raise ProviderError(schema_error_code, f"Malformed JSON: {exc}") from exc
    if not isinstance(body, dict):
        raise ProviderError(schema_error_code, "Response body is not a JSON object")
    status = body.get("status") or {}
    if status.get("rCode") != 200:
        raise ProviderError(
            "UPSTREAM_UNAVAILABLE", f"Nasdaq rCode={status.get('rCode')}: {status.get('bCodeMessage')}"
        )
    return body


def _parse_decimal(raw: object) -> Decimal:
    # Strike text is untrusted: fail as SCHEMA_ERROR, never a raw
    # InvalidOperation/ValidationError that surfaces as HTTP 500.
    text = str(raw).strip().replace(",", "").replace("$", "")
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise ProviderError("SCHEMA_ERROR", f"Unparseable strike {raw!r}") from exc
    if not value.is_finite() or value <= 0:
        raise ProviderError("SCHEMA_ERROR", f"Invalid strike {raw!r}")
    return value


def _drilldown_expiration_and_strike(url: str) -> tuple[date, Decimal] | None:
    match = _DRILLDOWN_RE.search(url)
    if not match:
        return None
    yy, mm, dd, strike_digits = match.groups()
    try:
        expiration = date(2000 + int(yy), int(mm), int(dd))
    except ValueError as exc:
        raise ProviderError("SCHEMA_ERROR", f"Invalid date in drillDownURL {url!r}") from exc
    strike = Decimal(strike_digits) / Decimal(1000)
    return expiration, strike


@dataclass(frozen=True)
class _PageResult:
    """One page's validated envelope. Distinguishes a genuinely empty page
    (rows=[]) from a malformed one (missing/null data, table, or rows) --
    `data.get(...) or {}`-style fallbacks erase that distinction."""

    last_trade_raw: str | None
    chain_asof_raw: str | None
    total_record: int
    rows: tuple[dict, ...]


def _parse_page(body: dict) -> _PageResult:
    data = body.get("data")
    if not isinstance(data, dict):
        raise ProviderError("SCHEMA_ERROR", "Response missing 'data' object")
    table = data.get("table")
    if not isinstance(table, dict):
        raise ProviderError("SCHEMA_ERROR", "Response missing 'data.table' object")
    rows = table.get("rows")
    if not isinstance(rows, list):
        raise ProviderError("SCHEMA_ERROR", "'data.table.rows' is missing or not a list")
    total_record, _ = _parse_count(data.get("totalRecord"))
    if total_record is None:
        raise ProviderError("SCHEMA_ERROR", "Missing or invalid 'data.totalRecord'")
    return _PageResult(
        last_trade_raw=data.get("lastTrade"),
        chain_asof_raw=table.get("asOf"),
        total_record=total_record,
        rows=tuple(rows),
    )


def _row_identity(row: dict, current_expiration: date | None) -> tuple[date, Decimal]:
    """(expiration, strike) identity for one data row. Shared by pagination-
    progress tracking and normalization, so there is exactly one fallback
    rule (drillDownURL, else the carried header + the row's own strike), not
    two independently-maintained copies of it."""
    url = row.get("drillDownURL")
    parsed = _drilldown_expiration_and_strike(url) if url else None
    if parsed is not None:
        drilldown_expiration, strike = parsed
        if current_expiration is not None and drilldown_expiration != current_expiration:
            raise ProviderError(
                "SCHEMA_ERROR",
                f"Expiration mismatch: header={current_expiration}, drillDownURL={drilldown_expiration}",
            )
        return drilldown_expiration, strike
    if current_expiration is not None and row.get("strike") is not None:
        return current_expiration, _parse_decimal(row["strike"])
    raise ProviderError("SCHEMA_ERROR", "Data row with no known expiration/strike")


class NasdaqProvider:
    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(RW_POOL_TIMEOUT_SECONDS, connect=CONNECT_TIMEOUT_SECONDS),
            headers=NASDAQ_HEADERS,
        )

    def close(self) -> None:
        """Closes the underlying HTTP client. Only meaningful when this
        provider built its own client (the production path via
        build_provider()); app.py must not call this on a provider whose
        client an injected/test caller owns and is responsible for closing."""
        self._client.close()

    def fetch_chain(self, request: ChainRequest) -> ChainSnapshot:
        symbol = request.symbol
        instrument = INSTRUMENTS.get(symbol)
        if instrument is None:
            raise ProviderError("UNSUPPORTED_SYMBOL", f"No Nasdaq asset-class mapping for {symbol!r}")
        asset_class = instrument.chain_asset_class

        collection_started_at = datetime.now(UTC)
        today = ny_local_date(collection_started_at)
        params_base = {
            "assetclass": asset_class,
            "limit": CHAIN_PAGE_LIMIT,
            "fromdate": today.isoformat(),
            "todate": (today + timedelta(days=request.max_calendar_dte)).isoformat(),
            "excode": "oprac",
            "callput": "callput",
            "money": "all",
            "type": "all",
        }

        raw_pages: list[dict] = []
        by_key: dict[tuple, OptionQuote] = {}
        current_expiration: date | None = None
        total_bytes = 0
        underlying_price: float | None = None
        chain_asof_raw: str | None = None
        spot_asof_date: date | None = None
        price_changed = False
        response_count = 0
        total_record: int | None = None
        rows_seen_total = 0
        source_row_count = 0

        for page_index in range(CHAIN_MAX_REQUESTS):
            params = dict(params_base, offset=page_index * CHAIN_PAGE_LIMIT)
            response = self._get(symbol, params)
            response_count += 1
            total_bytes += len(response.content)
            if total_bytes > CHAIN_MAX_RESPONSE_BYTES:
                raise ProviderError("INCOMPLETE_CHAIN", "Response size cap exceeded")

            body = self._parse_body(response)
            raw_pages.append(body)
            page = _parse_page(body)

            if total_record is None:
                total_record = page.total_record
            elif page.total_record != total_record:
                raise ProviderError(
                    "INCOMPLETE_CHAIN",
                    f"totalRecord changed mid-collection: {total_record} -> {page.total_record}",
                )

            match = _LAST_TRADE_RE.match(page.last_trade_raw or "")
            if page_index == 0:
                if not match:
                    raise ProviderError("INVALID_UNDERLYING_PRICE", "Could not parse lastTrade field")
                underlying_price = float(match.group(1).replace(",", ""))
                chain_asof_raw = page.chain_asof_raw
                spot_asof_date = self._parse_last_trade_date(match.group(2))
            elif match:
                repeated_price = float(match.group(1).replace(",", ""))
                if underlying_price is not None and repeated_price != underlying_price:
                    price_changed = True

            rows_seen_total += len(page.rows)
            count_before = len(by_key)
            saw_data_row = False
            for row in page.rows:
                if row.get("expirygroup"):
                    header = row["expirygroup"]
                    try:
                        current_expiration = datetime.strptime(header, _HEADER_DATE_FMT).date()
                    except ValueError as exc:
                        raise ProviderError(
                            "SCHEMA_ERROR", f"Unrecognized expiration header {header!r}"
                        ) from exc
                    continue

                saw_data_row = True
                source_row_count += 1
                expiration, strike = _row_identity(row, current_expiration)
                url = row.get("drillDownURL")
                for option_type, prefix, provider_id in (("C", "c", url), ("P", "p", None)):
                    volume, volume_invalid = _parse_count(row.get(f"{prefix}_Volume"))
                    open_interest, oi_invalid = _parse_count(row.get(f"{prefix}_Openinterest"))
                    flags = ["MULTIPLIER_ASSUMED"]
                    if volume_invalid:
                        flags.append("INVALID_VOLUME")
                    if oi_invalid:
                        flags.append("INVALID_OPEN_INTEREST")
                    quote = OptionQuote(
                        symbol=symbol,
                        expiration=expiration,
                        strike=strike,
                        option_type=option_type,
                        bid=_parse_num(row.get(f"{prefix}_Bid")),
                        ask=_parse_num(row.get(f"{prefix}_Ask")),
                        last=_parse_num(row.get(f"{prefix}_Last")),
                        volume=volume,
                        open_interest=open_interest,
                        multiplier=100,
                        provider_contract_id=provider_id,
                        quote_asof=None,
                        flags=tuple(flags),
                    )
                    key = (quote.symbol, quote.expiration, quote.strike, quote.option_type)
                    existing = by_key.get(key)
                    if existing is not None:
                        same = (
                            existing.bid,
                            existing.ask,
                            existing.last,
                            existing.volume,
                            existing.open_interest,
                        ) == (quote.bid, quote.ask, quote.last, quote.volume, quote.open_interest)
                        if not same:
                            raise ProviderError(
                                "CONFLICTING_CONTRACTS", f"Conflicting duplicate contract {key}"
                            )
                        continue
                    by_key[key] = quote

            if saw_data_row and len(by_key) == count_before:
                raise ProviderError("INCOMPLETE_CHAIN", "Pagination page repeated without progress")

            # A short page ends pagination; confirmed empirically against a
            # real 1906-row SPY response (docs/source-contract.md). Require
            # it to also match the verified totalRecord semantics (exact
            # row total, headers + data, identical on every page) so a
            # truncated/short response can't be mistaken for a complete one.
            if len(page.rows) < CHAIN_PAGE_LIMIT:
                if rows_seen_total != total_record:
                    raise ProviderError(
                        "INCOMPLETE_CHAIN",
                        f"Short page after {rows_seen_total} rows, but totalRecord={total_record}",
                    )
                break
        else:
            raise ProviderError(
                "INCOMPLETE_CHAIN", "Exceeded max provider requests before pagination completed"
            )

        if underlying_price is None or not math.isfinite(underlying_price) or underlying_price <= 0:
            raise ProviderError("INVALID_UNDERLYING_PRICE", "Missing or invalid underlying price")

        contracts = tuple(by_key.values())
        if len(contracts) > CHAIN_MAX_CONTRACTS:
            raise ProviderError("INCOMPLETE_CHAIN", "Normalized contract cap exceeded")

        # VALUATION_TIME_ASSUMED is provider-independent (any provider whose
        # chain_asof is null falls back to collection_started_at) and is
        # added once, at the orchestration boundary in app.py, not here.
        warnings: list[str] = ["MULTIPLIER_ASSUMED"]
        chain_asof = self._parse_chain_asof(chain_asof_raw)
        # Spot timestamp from this source is date-only (docs/source-contract.md);
        # it can never be compared at second-level precision.
        warnings.append("TIMESTAMP_ALIGNMENT_UNKNOWN")
        if price_changed:
            warnings.append("UNDERLYING_PRICE_CHANGED_DURING_COLLECTION")

        collected_at = datetime.now(UTC)
        if (collected_at - collection_started_at).total_seconds() > CHAIN_COLLECTION_WINDOW_SECONDS:
            raise ProviderError("COLLECTION_WINDOW_EXCEEDED", "Collection took too long")

        return ChainSnapshot(
            provider_id="nasdaq",
            symbol=symbol,
            underlying_price=underlying_price,
            underlying_price_kind="last_trade",
            underlying_price_origin="chain_payload",
            collection_started_at=collection_started_at,
            collected_at=collected_at,
            chain_asof=chain_asof,
            spot_asof=None,
            spot_asof_date=spot_asof_date,
            oi_asof=None,
            contracts=contracts,
            warnings=tuple(warnings),
            source_row_count=source_row_count,
            provider_response_count=response_count,
            raw_payload_json=self._sanitize_raw_payload(params_base, raw_pages),
        )

    def _get(self, symbol: str, params: dict) -> httpx.Response:
        return _http_get(self._client, NASDAQ_CHAIN_URL.format(symbol=symbol), params)

    def _parse_body(self, response: httpx.Response) -> dict:
        return _parse_json_envelope(response, schema_error_code="SCHEMA_ERROR", check_403=True)

    def _parse_chain_asof(self, raw: str | None) -> datetime | None:
        """PRD 6.1: only accept a value that is unambiguously a
        timezone-aware pricing timestamp. docs/source-contract.md records
        that data.table.asOf has never been observed populated and its
        format is unverified -- a naive or date-only value (e.g.
        "2026-09-17", which fromisoformat happily parses as a naive
        midnight) must fall back to the documented VALUATION_TIME_ASSUMED
        path, not be guessed at and passed downstream to fail when it is
        later subtracted from an aware expiry timestamp."""
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(UTC)

    def _parse_last_trade_date(self, raw: str) -> date | None:
        """The lastTrade "(AS OF ...)" text is a verified calendar date, not
        a guess -- fail loudly on an unrecognized shape rather than silently
        leaving spot_asof_date null (Section 7.5 needs this evidence to
        validate ex-dividend alignment)."""
        try:
            return datetime.strptime(raw.strip(), _LAST_TRADE_DATE_FMT).date()
        except ValueError as exc:
            raise ProviderError("SCHEMA_ERROR", f"Unparseable lastTrade AS OF date {raw!r}") from exc

    def _sanitize_raw_payload(self, params: dict, pages: list[dict]) -> str:
        import json

        return json.dumps({"request_params": params, "pages": pages})


def _parse_mdy_date(raw: str) -> date | None:
    """MM/DD/YYYY, or None for the "N/A" missing-value sentinel confirmed
    live for declarationDate/paymentDate (docs/dividend-source-contract.md).
    A recognized missing sentinel is not a synthesized value (Section 7.2)."""
    if raw == "N/A":
        return None
    try:
        return datetime.strptime(raw, "%m/%d/%Y").date()
    except ValueError as exc:
        raise ProviderError("DIVIDEND_SCHEMA_ERROR", f"Unparseable date {raw!r}") from exc


def _parse_dividend_amount(raw: str) -> Decimal:
    try:
        return Decimal(raw.strip().lstrip("$"))
    except InvalidOperation as exc:
        raise ProviderError("DIVIDEND_SCHEMA_ERROR", f"Unparseable amount {raw!r}") from exc


class NasdaqDividendProvider:
    """Verified for AAPL/QQQ only (docs/dividend-source-contract.md). SPY's
    dividend-history feature explicitly does not cover non-Nasdaq-listed
    symbols and must stay on manual_schedule -- not requested here at all."""

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(RW_POOL_TIMEOUT_SECONDS, connect=CONNECT_TIMEOUT_SECONDS),
            headers=NASDAQ_HEADERS,
        )

    def close(self) -> None:
        self._client.close()

    def fetch_dividends(self, symbol: str) -> DividendFeedSnapshot:
        instrument = INSTRUMENTS.get(symbol)
        asset_class = instrument.dividend_asset_class if instrument else None
        if asset_class is None:
            raise ProviderError("UNSUPPORTED_SYMBOL", f"No Nasdaq dividend coverage for {symbol!r}")

        url = NASDAQ_DIVIDENDS_URL.format(symbol=symbol)
        response = _http_get(self._client, url, {"assetclass": asset_class})
        if len(response.content) > REFERENCE_MAX_RESPONSE_BYTES:
            raise ProviderError("DIVIDEND_SCHEMA_ERROR", "Response exceeded the 2 MiB reference cap")
        body = _parse_json_envelope(response, schema_error_code="DIVIDEND_SCHEMA_ERROR", check_403=False)

        data = body.get("data")
        if not isinstance(data, dict):
            raise ProviderError("DIVIDEND_SCHEMA_ERROR", "Response missing 'data' object")
        dividends = data.get("dividends")
        if not isinstance(dividends, dict):
            raise ProviderError("DIVIDEND_SCHEMA_ERROR", "Response missing 'data.dividends' object")
        rows = dividends.get("rows")
        if rows is None:
            # Confirmed live shape for a symbol this feature does not cover:
            # a "successful" rCode=200 envelope with a null payload and a
            # human-readable message, not an error status (Section 7.2: not
            # a successful empty response).
            raise ProviderError(
                "DIVIDEND_COVERAGE_UNVERIFIED",
                f"Nasdaq has no dividend-history coverage for {symbol!r}: {body.get('message')}",
            )
        if not isinstance(rows, list):
            raise ProviderError("DIVIDEND_SCHEMA_ERROR", "'data.dividends.rows' is not a list")

        records = []
        for row in rows:
            ex_date = _parse_mdy_date(row.get("exOrEffDate", ""))
            if ex_date is None:
                raise ProviderError("DIVIDEND_SCHEMA_ERROR", "Row missing exOrEffDate")
            raw_amount = row.get("amount")
            if raw_amount is None:
                raise ProviderError("DIVIDEND_SCHEMA_ERROR", "Row missing amount")
            try:
                record = DividendRecord(
                    provider_record_id=None,
                    symbol=symbol,
                    currency=row.get("currency") or "USD",
                    ex_date=ex_date,
                    payment_date=_parse_mdy_date(row.get("paymentDate", "N/A")),
                    declaration_date=_parse_mdy_date(row.get("declarationDate", "N/A")),
                    amount=_parse_dividend_amount(raw_amount),
                    kind="ordinary_cash" if row.get("type") == "Cash" else "other",
                    source_ref=NASDAQ_DIVIDENDS_URL.format(symbol=symbol),
                )
            except ValidationError as exc:
                # e.g. a payment date before the ex-date: untrusted upstream
                # data, never a raw 500 from a model invariant.
                raise ProviderError("DIVIDEND_SCHEMA_ERROR", f"Invalid dividend row: {exc}") from exc
            records.append(record)

        return DividendFeedSnapshot(
            provider_id="nasdaq_dividends",
            symbol=symbol,
            fetched_at=datetime.now(UTC),
            source_asof=None,  # confirmed always null live (docs/dividend-source-contract.md)
            records=tuple(records),
            raw_payload_json=response.text,
            warnings=(),
        )
