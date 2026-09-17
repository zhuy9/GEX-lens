"""NasdaqProvider: HTTP, pagination, Nasdaq-to-canonical parsing.

Field paths and pagination behavior here follow docs/source-contract.md,
verified against real samples. Do not change parsing assumptions without
updating that document first.
"""

import math
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import httpx

from models import ChainRequest, ChainSnapshot, OptionQuote, ny_local_date
from provider import ProviderError

BASE_URL = "https://api.nasdaq.com/api/quote/{symbol}/option-chain"

# The only three symbols verified in docs/source-contract.md (M0). Adding a
# symbol here requires re-running M0 verification for it first.
ASSET_CLASS = {"SPY": "etf", "QQQ": "etf", "AAPL": "stocks"}

PAGE_LIMIT = 1000
MAX_REQUESTS = 10
MAX_RESPONSE_BYTES = 10 * 1024 * 1024
MAX_CONTRACTS = 10_000
COLLECTION_WINDOW_SECONDS = 30
CONNECT_TIMEOUT_SECONDS = 5.0
RW_POOL_TIMEOUT_SECONDS = 10.0

_HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
_LAST_TRADE_RE = re.compile(r"^LAST TRADE:\s*\$([\d,]+\.\d+)\s*\(AS OF (.+)\)$")
_HEADER_DATE_FMT = "%B %d, %Y"
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


def _parse_int(raw: object) -> int | None:
    value = _parse_num(raw)
    return None if value is None else int(value)


def _parse_count(raw: object) -> tuple[int | None, bool]:
    """Nonnegative whole-number count (OI/volume). Returns (value, invalid).

    A recognized missing sentinel is (None, False). A malformed value --
    fractional, negative, NaN, Infinity, or otherwise unparseable -- is
    (None, True): the source sent something wrong, not merely nothing.
    Callers must not silently truncate a count through float/int like
    _parse_int does; "0.9" truncating to 0 turns an invalid reading into a
    confident (and misleading) zero exposure.
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


def _parse_decimal(raw: object) -> Decimal:
    text = str(raw).strip().replace(",", "").replace("$", "")
    return Decimal(text)


def _drilldown_expiration_and_strike(url: str) -> tuple[date, Decimal] | None:
    match = _DRILLDOWN_RE.search(url)
    if not match:
        return None
    yy, mm, dd, strike_digits = match.groups()
    expiration = date(2000 + int(yy), int(mm), int(dd))
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
    total_record = _parse_int(data.get("totalRecord"))
    if total_record is None or total_record < 0:
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
            headers=_HEADERS,
        )

    def fetch_chain(self, request: ChainRequest) -> ChainSnapshot:
        symbol = request.symbol
        asset_class = ASSET_CLASS.get(symbol)
        if asset_class is None:
            raise ProviderError("UNSUPPORTED_SYMBOL", f"No Nasdaq asset-class mapping for {symbol!r}")

        collection_started_at = datetime.now(UTC)
        today = ny_local_date(collection_started_at)
        params_base = {
            "assetclass": asset_class,
            "limit": PAGE_LIMIT,
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
        price_changed = False
        response_count = 0
        total_record: int | None = None
        rows_seen_total = 0
        source_row_count = 0

        for page_index in range(MAX_REQUESTS):
            params = dict(params_base, offset=page_index * PAGE_LIMIT)
            response = self._get(symbol, params)
            response_count += 1
            total_bytes += len(response.content)
            if total_bytes > MAX_RESPONSE_BYTES:
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
            elif match:
                repeated_price = float(match.group(1).replace(",", ""))
                if underlying_price is not None and repeated_price != underlying_price:
                    price_changed = True

            rows_seen_total += len(page.rows)
            keys_before = set(by_key.keys())
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

            if saw_data_row and set(by_key.keys()) == keys_before:
                raise ProviderError("INCOMPLETE_CHAIN", "Pagination page repeated without progress")

            # A short page ends pagination; confirmed empirically against a
            # real 1906-row SPY response (docs/source-contract.md). Require
            # it to also match the verified totalRecord semantics (exact
            # row total, headers + data, identical on every page) so a
            # truncated/short response can't be mistaken for a complete one.
            if len(page.rows) < PAGE_LIMIT:
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
        if len(contracts) > MAX_CONTRACTS:
            raise ProviderError("INCOMPLETE_CHAIN", "Normalized contract cap exceeded")

        warnings: list[str] = ["MULTIPLIER_ASSUMED"]
        chain_asof = self._parse_chain_asof(chain_asof_raw)
        if chain_asof is None:
            warnings.append("VALUATION_TIME_ASSUMED")
        # Spot timestamp from this source is date-only (docs/source-contract.md);
        # it can never be compared at second-level precision.
        warnings.append("TIMESTAMP_ALIGNMENT_UNKNOWN")
        if price_changed:
            warnings.append("UNDERLYING_PRICE_CHANGED_DURING_COLLECTION")

        collected_at = datetime.now(UTC)
        if (collected_at - collection_started_at).total_seconds() > COLLECTION_WINDOW_SECONDS:
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
            oi_asof=None,
            contracts=contracts,
            warnings=tuple(warnings),
            source_row_count=source_row_count,
            provider_response_count=response_count,
            raw_payload_json=self._sanitize_raw_payload(params_base, raw_pages),
        )

    def _get(self, symbol: str, params: dict) -> httpx.Response:
        try:
            return self._client.get(BASE_URL.format(symbol=symbol), params=params)
        except httpx.TimeoutException as exc:
            raise ProviderError("UPSTREAM_TIMEOUT", str(exc)) from exc
        except httpx.HTTPError as exc:
            raise ProviderError("UPSTREAM_UNAVAILABLE", str(exc)) from exc

    def _parse_body(self, response: httpx.Response) -> dict:
        if response.status_code == 429:
            retry_after = _parse_retry_after(response.headers.get("Retry-After"))
            raise ProviderError("UPSTREAM_RATE_LIMITED", "Nasdaq rate limit", retry_after_seconds=retry_after)
        if response.status_code == 403:
            raise ProviderError("UPSTREAM_ACCESS_DENIED", "Nasdaq access denied (403)")
        if response.status_code != 200:
            raise ProviderError("UPSTREAM_UNAVAILABLE", f"Unexpected status {response.status_code}")
        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderError("SCHEMA_ERROR", f"Malformed JSON: {exc}") from exc
        if not isinstance(body, dict):
            raise ProviderError("SCHEMA_ERROR", "Response body is not a JSON object")
        status = body.get("status") or {}
        if status.get("rCode") != 200:
            raise ProviderError(
                "UPSTREAM_UNAVAILABLE", f"Nasdaq rCode={status.get('rCode')}: {status.get('bCodeMessage')}"
            )
        return body

    def _parse_chain_asof(self, raw: str | None) -> datetime | None:
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            return None

    def _sanitize_raw_payload(self, params: dict, pages: list[dict]) -> str:
        import json

        return json.dumps({"request_params": params, "pages": pages})
