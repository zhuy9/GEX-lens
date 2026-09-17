"""NasdaqProvider: HTTP, pagination, Nasdaq-to-canonical parsing.

Field paths and pagination behavior here follow docs/source-contract.md,
verified against real samples. Do not change parsing assumptions without
updating that document first.
"""

import math
import re
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
        all_rows: list[dict] = []
        seen_contract_keys: set[tuple] = set()
        total_bytes = 0
        underlying_price: float | None = None
        chain_asof_raw: str | None = None
        price_changed = False
        response_count = 0

        for page_index in range(MAX_REQUESTS):
            params = dict(params_base, offset=page_index * PAGE_LIMIT)
            response = self._get(symbol, params)
            response_count += 1
            total_bytes += len(response.content)
            if total_bytes > MAX_RESPONSE_BYTES:
                raise ProviderError("INCOMPLETE_CHAIN", "Response size cap exceeded")

            body = self._parse_body(response)
            data = body.get("data") or {}
            raw_pages.append(body)

            last_trade = data.get("lastTrade")
            match = _LAST_TRADE_RE.match(last_trade or "")
            if page_index == 0:
                if not match:
                    raise ProviderError("INVALID_UNDERLYING_PRICE", "Could not parse lastTrade field")
                underlying_price = float(match.group(1).replace(",", ""))
                chain_asof_raw = (data.get("table") or {}).get("asOf")
            elif match:
                repeated_price = float(match.group(1).replace(",", ""))
                if underlying_price is not None and repeated_price != underlying_price:
                    price_changed = True

            rows = (data.get("table") or {}).get("rows") or []
            all_rows.extend(rows)

            data_rows = [r for r in rows if r.get("expirygroup") == ""]
            if data_rows:
                new_keys = {
                    _drilldown_expiration_and_strike(r["drillDownURL"])
                    or (r.get("expiryDate"), r.get("strike"))
                    for r in data_rows
                    if r.get("drillDownURL") or r.get("strike")
                }
                if new_keys and new_keys.issubset(seen_contract_keys):
                    raise ProviderError("INCOMPLETE_CHAIN", "Pagination page repeated without progress")
                seen_contract_keys |= new_keys

            # A short page ends pagination; confirmed empirically against a
            # real 1906-row SPY response (docs/source-contract.md).
            if len(rows) < PAGE_LIMIT:
                break
        else:
            raise ProviderError(
                "INCOMPLETE_CHAIN", "Exceeded max provider requests before pagination completed"
            )

        if underlying_price is None or not math.isfinite(underlying_price) or underlying_price <= 0:
            raise ProviderError("INVALID_UNDERLYING_PRICE", "Missing or invalid underlying price")

        contracts, source_row_count = self._normalize_rows(symbol, all_rows)
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
            retry_after = _parse_int(response.headers.get("Retry-After"))
            raise ProviderError("UPSTREAM_RATE_LIMITED", "Nasdaq rate limit", retry_after_seconds=retry_after)
        if response.status_code == 403:
            raise ProviderError("UPSTREAM_ACCESS_DENIED", "Nasdaq access denied (403)")
        if response.status_code != 200:
            raise ProviderError("UPSTREAM_UNAVAILABLE", f"Unexpected status {response.status_code}")
        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderError("SCHEMA_ERROR", f"Malformed JSON: {exc}") from exc
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

    def _normalize_rows(self, symbol: str, rows: list[dict]) -> tuple[tuple[OptionQuote, ...], int]:
        by_key: dict[tuple, OptionQuote] = {}
        current_expiration: date | None = None
        source_row_count = 0

        for row in rows:
            group = row.get("expirygroup") or ""
            if group:
                try:
                    current_expiration = datetime.strptime(group, _HEADER_DATE_FMT).date()
                except ValueError as exc:
                    raise ProviderError("SCHEMA_ERROR", f"Unrecognized expiration header {group!r}") from exc
                continue

            url = row.get("drillDownURL")
            parsed = _drilldown_expiration_and_strike(url) if url else None
            if parsed is not None:
                drilldown_expiration, strike = parsed
                if current_expiration is not None and drilldown_expiration != current_expiration:
                    raise ProviderError(
                        "SCHEMA_ERROR",
                        f"Expiration mismatch: header={current_expiration}, "
                        f"drillDownURL={drilldown_expiration}",
                    )
                expiration = drilldown_expiration
            elif current_expiration is not None:
                expiration = current_expiration
                strike = _parse_decimal(row["strike"])
            else:
                raise ProviderError("SCHEMA_ERROR", "Data row with no known expiration")

            source_row_count += 1
            for option_type, prefix, provider_id in (("C", "c", url), ("P", "p", None)):
                quote = OptionQuote(
                    symbol=symbol,
                    expiration=expiration,
                    strike=strike,
                    option_type=option_type,
                    bid=_parse_num(row.get(f"{prefix}_Bid")),
                    ask=_parse_num(row.get(f"{prefix}_Ask")),
                    last=_parse_num(row.get(f"{prefix}_Last")),
                    volume=_parse_int(row.get(f"{prefix}_Volume")),
                    open_interest=_parse_int(row.get(f"{prefix}_Openinterest")),
                    multiplier=100,
                    provider_contract_id=provider_id,
                    quote_asof=None,
                    flags=("MULTIPLIER_ASSUMED",),
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
                        raise ProviderError("CONFLICTING_CONTRACTS", f"Conflicting duplicate contract {key}")
                    continue
                by_key[key] = quote

        return tuple(by_key.values()), source_row_count

    def _sanitize_raw_payload(self, params: dict, pages: list[dict]) -> str:
        import json

        return json.dumps({"request_params": params, "pages": pages})
