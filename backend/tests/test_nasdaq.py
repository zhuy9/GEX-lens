"""Nasdaq parser tests against a synthetic, structurally-faithful fixture.

Shapes here mirror the real quirks recorded in docs/source-contract.md
(group headers without a year, drillDownURL only on the call side, a
continuation page with no header row, the lastTrade string format) but use
made-up numbers -- the real captured sample is private and git-ignored.
"""

from datetime import date
from decimal import Decimal

import httpx
import pytest

import nasdaq
from models import ChainRequest
from provider import ProviderError


def _body(last_trade, rows, as_of=None, rcode=200, total_record=None):
    # total_record defaults to len(rows), correct for a single-page response.
    # Multipage tests must pass the real combined total explicitly -- every
    # page of one logical snapshot reports the same totalRecord
    # (docs/source-contract.md), it is not "this page's row count".
    return {
        "data": {
            "totalRecord": len(rows) if total_record is None else total_record,
            "lastTrade": last_trade,
            "table": {"asOf": as_of, "headers": {}, "rows": rows},
            "filterlist": {},
        },
        "message": None,
        "status": {"rCode": rcode, "bCodeMessage": None, "developerMessage": None},
    }


def _header_row(text):
    return {
        "expirygroup": text,
        "expiryDate": None,
        "c_Last": None,
        "c_Change": None,
        "c_Bid": None,
        "c_Ask": None,
        "c_Volume": None,
        "c_Openinterest": None,
        "c_colour": False,
        "strike": None,
        "p_Last": None,
        "p_Change": None,
        "p_Bid": None,
        "p_Ask": None,
        "p_Volume": None,
        "p_Openinterest": None,
        "p_colour": False,
        "drillDownURL": None,
    }


def _data_row(symbol_lower, yymmdd, strike_str, strike_padded, c_last="1.00", p_last="1.10"):
    return {
        "expirygroup": "",
        "expiryDate": "Jan 15",
        "c_Last": c_last,
        "c_Change": "0.10",
        "c_Bid": "0.95",
        "c_Ask": "1.05",
        "c_Volume": "10",
        "c_Openinterest": "100",
        "c_colour": True,
        "strike": strike_str,
        "p_Last": p_last,
        "p_Change": "-0.05",
        "p_Bid": "1.05",
        "p_Ask": "1.15",
        "p_Volume": "5",
        "p_Openinterest": "50",
        "p_colour": False,
        "drillDownURL": (
            f"/market-activity/stocks/{symbol_lower}/option-chain/call-put-options/"
            f"{symbol_lower}--{yymmdd}c{strike_padded}"
        ),
    }


def make_provider(handler) -> nasdaq.NasdaqProvider:
    transport = httpx.MockTransport(handler)
    return nasdaq.NasdaqProvider(client=httpx.Client(transport=transport))


@pytest.mark.parametrize("total", ["NaN", "Infinity", "0.9", "-1", True])
def test_invalid_total_record_is_a_schema_error(total):
    provider = make_provider(lambda _: httpx.Response(
        200, json=_body("LAST TRADE: $100.00 (AS OF JAN 15, 2026)", [], total_record=total)
    ))
    with pytest.raises(ProviderError) as error:
        provider.fetch_chain(ChainRequest(symbol="AAPL", min_calendar_dte=1, max_calendar_dte=60))
    assert error.value.code == "SCHEMA_ERROR"


def test_single_page_parses_spot_and_contracts():
    rows = [
        _header_row("January 15, 2026"),
        _data_row("aapl", "260115", "95.00", "00095000"),
        _data_row("aapl", "260115", "100.00", "00100000"),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_body("LAST TRADE: $100.00 (AS OF JAN 15, 2026)", rows))

    provider = make_provider(handler)
    snapshot = provider.fetch_chain(ChainRequest(symbol="AAPL", min_calendar_dte=1, max_calendar_dte=60))

    assert snapshot.underlying_price == 100.0
    assert snapshot.spot_asof is None  # date-only source precision; never invented
    assert len(snapshot.contracts) == 4  # 2 strikes x 2 sides
    call_95 = next(c for c in snapshot.contracts if c.option_type == "C" and c.strike == 95)
    assert call_95.expiration == date(2026, 1, 15)
    assert call_95.provider_contract_id is not None
    put_95 = next(c for c in snapshot.contracts if c.option_type == "P" and c.strike == 95)
    assert put_95.provider_contract_id is None  # source never gives a put-side id
    assert "MULTIPLIER_ASSUMED" in snapshot.warnings


def test_last_trade_as_of_date_becomes_spot_asof_date():
    # C01: the verified lastTrade "(AS OF ...)" calendar date must reach
    # ChainSnapshot.spot_asof_date, not be silently dropped.
    rows = [_header_row("January 15, 2026"), _data_row("aapl", "260115", "95.00", "00095000")]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_body("LAST TRADE: $100.00 (AS OF JAN 15, 2026)", rows))

    provider = make_provider(handler)
    snapshot = provider.fetch_chain(ChainRequest(symbol="AAPL", min_calendar_dte=1, max_calendar_dte=60))
    assert snapshot.spot_asof_date == date(2026, 1, 15)
    assert snapshot.spot_asof is None  # still no invented time-of-day


def test_unparseable_last_trade_as_of_date_is_a_schema_error():
    rows = [_header_row("January 15, 2026"), _data_row("aapl", "260115", "95.00", "00095000")]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_body("LAST TRADE: $100.00 (AS OF garbage)", rows))

    provider = make_provider(handler)
    with pytest.raises(ProviderError) as exc:
        provider.fetch_chain(ChainRequest(symbol="AAPL", min_calendar_dte=1, max_calendar_dte=60))
    assert exc.value.code == "SCHEMA_ERROR"


# R06: data.table.asOf has never been observed populated and its format is
# unverified (docs/source-contract.md) -- only an unambiguous, timezone-aware
# value may become chain_asof. Anything else must fall back safely, not
# crash later when subtracted from an aware expiry timestamp.
@pytest.mark.parametrize(
    "as_of",
    [
        None,
        "2026-01-15",  # date-only: fromisoformat parses this as naive midnight
        "2026-01-15T16:00:00",  # naive full datetime
        "not a date",
    ],
)
def test_unusable_chain_asof_falls_back_without_crashing(as_of):
    rows = [_header_row("January 15, 2026"), _data_row("aapl", "260115", "95.00", "00095000")]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_body("LAST TRADE: $100.00 (AS OF JAN 15, 2026)", rows, as_of=as_of))

    provider = make_provider(handler)
    snapshot = provider.fetch_chain(ChainRequest(symbol="AAPL", min_calendar_dte=1, max_calendar_dte=60))
    assert snapshot.chain_asof is None


def test_aware_chain_asof_is_accepted_and_normalized_to_utc():
    rows = [_header_row("January 15, 2026"), _data_row("aapl", "260115", "95.00", "00095000")]
    as_of = "2026-01-15T16:00:00-05:00"  # aware, non-UTC

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_body("LAST TRADE: $100.00 (AS OF JAN 15, 2026)", rows, as_of=as_of))

    provider = make_provider(handler)
    snapshot = provider.fetch_chain(ChainRequest(symbol="AAPL", min_calendar_dte=1, max_calendar_dte=60))
    assert snapshot.chain_asof is not None
    assert snapshot.chain_asof.tzinfo is not None
    assert snapshot.chain_asof.utcoffset().total_seconds() == 0
    assert snapshot.chain_asof.hour == 21  # 16:00-05:00 -> 21:00 UTC


def test_spot_price_is_never_confused_with_a_premium():
    # M1.9
    rows = [
        _header_row("January 15, 2026"),
        _data_row("aapl", "260115", "95.00", "00095000", c_last="332.41", p_last="332.41"),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_body("LAST TRADE: $100.00 (AS OF JAN 15, 2026)", rows))

    provider = make_provider(handler)
    snapshot = provider.fetch_chain(ChainRequest(symbol="AAPL", min_calendar_dte=1, max_calendar_dte=60))
    assert snapshot.underlying_price == 100.0  # not 332.41, despite identical-looking premiums


def test_whole_dollar_spot_price_with_no_decimal_is_parsed():
    # Confirmed live 2026-09-17: AAPL traded at exactly $337, and the source
    # omits the decimal entirely for a whole-dollar price ("$337", not
    # "$337.00") -- must not be treated as an invalid/unparseable spot.
    rows = [_header_row("January 15, 2026"), _data_row("aapl", "260115", "95.00", "00095000")]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_body("LAST TRADE: $337 (AS OF SEP 17, 2026)", rows))

    provider = make_provider(handler)
    snapshot = provider.fetch_chain(ChainRequest(symbol="AAPL", min_calendar_dte=1, max_calendar_dte=60))
    assert snapshot.underlying_price == 337.0


@pytest.mark.parametrize("bad_last_trade", [None, "", "garbage", "LAST TRADE: (AS OF JAN 15, 2026)"])
def test_missing_or_invalid_spot_raises_without_fallback(bad_last_trade):
    # M1.10
    rows = [_header_row("January 15, 2026"), _data_row("aapl", "260115", "95.00", "00095000")]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_body(bad_last_trade, rows))

    provider = make_provider(handler)
    with pytest.raises(ProviderError) as exc_info:
        provider.fetch_chain(ChainRequest(symbol="AAPL", min_calendar_dte=1, max_calendar_dte=60))
    assert exc_info.value.code == "INVALID_UNDERLYING_PRICE"


def test_continuation_page_without_header_uses_carried_expiration(monkeypatch):
    monkeypatch.setattr(nasdaq, "CHAIN_PAGE_LIMIT", 2)
    page1 = [_header_row("January 15, 2026"), _data_row("aapl", "260115", "95.00", "00095000")]
    page2 = [_data_row("aapl", "260115", "100.00", "00100000")]  # no header; must inherit Jan 15 2026

    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(dict(request.url.params).get("offset", "0"))
        calls.append(offset)
        rows = page1 if offset == 0 else page2
        return httpx.Response(
            200,
            json=_body(
                "LAST TRADE: $100.00 (AS OF JAN 15, 2026)",
                rows,
                total_record=len(page1) + len(page2),
            ),
        )

    provider = make_provider(handler)
    snapshot = provider.fetch_chain(ChainRequest(symbol="AAPL", min_calendar_dte=1, max_calendar_dte=60))

    assert calls == [0, 2]
    strikes = {c.strike for c in snapshot.contracts}
    assert Decimal("95") in strikes
    assert Decimal("100") in strikes
    assert all(c.expiration == date(2026, 1, 15) for c in snapshot.contracts)


def test_repeated_page_without_progress_is_incomplete_chain(monkeypatch):
    # PRD 5.2: a page that repeats without progress -> INCOMPLETE_CHAIN
    monkeypatch.setattr(nasdaq, "CHAIN_PAGE_LIMIT", 2)
    page1 = [_header_row("January 15, 2026"), _data_row("aapl", "260115", "95.00", "00095000")]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_body("LAST TRADE: $100.00 (AS OF JAN 15, 2026)", page1))

    provider = make_provider(handler)
    with pytest.raises(ProviderError) as exc_info:
        provider.fetch_chain(ChainRequest(symbol="AAPL", min_calendar_dte=1, max_calendar_dte=60))
    assert exc_info.value.code == "INCOMPLETE_CHAIN"


def test_changed_underlying_price_on_later_page_adds_warning_and_keeps_first_price(monkeypatch):
    # M1.11
    monkeypatch.setattr(nasdaq, "CHAIN_PAGE_LIMIT", 2)
    page1 = [_header_row("January 15, 2026"), _data_row("aapl", "260115", "95.00", "00095000")]
    page2 = [_data_row("aapl", "260115", "100.00", "00100000")]

    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(dict(request.url.params).get("offset", "0"))
        rows = page1 if offset == 0 else page2
        last_trade = (
            "LAST TRADE: $100.00 (AS OF JAN 15, 2026)"
            if offset == 0
            else "LAST TRADE: $101.50 (AS OF JAN 15, 2026)"
        )
        return httpx.Response(200, json=_body(last_trade, rows, total_record=len(page1) + len(page2)))

    provider = make_provider(handler)
    snapshot = provider.fetch_chain(ChainRequest(symbol="AAPL", min_calendar_dte=1, max_calendar_dte=60))

    assert snapshot.underlying_price == 100.0  # first page's price wins, never overwritten
    assert "UNDERLYING_PRICE_CHANGED_DURING_COLLECTION" in snapshot.warnings


def test_comma_formatted_values_are_parsed():
    # M1.2
    rows = [
        _header_row("January 15, 2026"),
        _data_row("aapl", "260115", "1,250.00", "01250000", c_last="1,234.56"),
    ]
    rows[1]["c_Volume"] = "12,345"
    rows[1]["c_Openinterest"] = "1,234,567"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_body("LAST TRADE: $1,200.00 (AS OF JAN 15, 2026)", rows))

    provider = make_provider(handler)
    snapshot = provider.fetch_chain(ChainRequest(symbol="AAPL", min_calendar_dte=1, max_calendar_dte=60))

    assert snapshot.underlying_price == 1200.0
    call = next(c for c in snapshot.contracts if c.option_type == "C")
    assert call.strike == Decimal("1250.00")
    assert call.last == 1234.56
    assert call.volume == 12345
    assert call.open_interest == 1234567


# R03: fractional/negative/NaN/Infinity counts must not silently truncate
# into a plausible-looking (and wrong) integer or crash with a raw Python
# exception, and a malformed count must not disable IV/gamma computation --
# only the count itself becomes null, flagged, not the whole contract.
@pytest.mark.parametrize(
    "raw,expected_flag",
    [
        ("1.9", "INVALID_OPEN_INTEREST"),
        ("0.9", "INVALID_OPEN_INTEREST"),
        ("-1000", "INVALID_OPEN_INTEREST"),
        ("NaN", "INVALID_OPEN_INTEREST"),
        ("Infinity", "INVALID_OPEN_INTEREST"),
        ("garbage", "INVALID_OPEN_INTEREST"),
    ],
)
def test_malformed_open_interest_becomes_null_and_flagged_not_truncated(raw, expected_flag):
    rows = [_header_row("January 15, 2026"), _data_row("aapl", "260115", "95.00", "00095000")]
    rows[1]["c_Openinterest"] = raw

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_body("LAST TRADE: $100.00 (AS OF JAN 15, 2026)", rows))

    provider = make_provider(handler)
    snapshot = provider.fetch_chain(ChainRequest(symbol="AAPL", min_calendar_dte=1, max_calendar_dte=60))

    call = next(c for c in snapshot.contracts if c.option_type == "C")
    assert call.open_interest is None
    assert expected_flag in call.flags
    put = next(c for c in snapshot.contracts if c.option_type == "P")
    assert "INVALID_OPEN_INTEREST" not in put.flags  # only the malformed side is flagged


def test_zero_open_interest_is_not_flagged_invalid():
    rows = [_header_row("January 15, 2026"), _data_row("aapl", "260115", "95.00", "00095000")]
    rows[1]["c_Openinterest"] = "0"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_body("LAST TRADE: $100.00 (AS OF JAN 15, 2026)", rows))

    provider = make_provider(handler)
    snapshot = provider.fetch_chain(ChainRequest(symbol="AAPL", min_calendar_dte=1, max_calendar_dte=60))
    call = next(c for c in snapshot.contracts if c.option_type == "C")
    assert call.open_interest == 0
    assert "INVALID_OPEN_INTEREST" not in call.flags


def test_timeout_raises_upstream_timeout():
    # M3.3
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out", request=request)

    provider = make_provider(handler)
    with pytest.raises(ProviderError) as exc_info:
        provider.fetch_chain(ChainRequest(symbol="AAPL", min_calendar_dte=1, max_calendar_dte=60))
    assert exc_info.value.code == "UPSTREAM_TIMEOUT"


def test_403_raises_upstream_access_denied():
    # M3.3
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "forbidden"})

    provider = make_provider(handler)
    with pytest.raises(ProviderError) as exc_info:
        provider.fetch_chain(ChainRequest(symbol="AAPL", min_calendar_dte=1, max_calendar_dte=60))
    assert exc_info.value.code == "UPSTREAM_ACCESS_DENIED"


def test_429_raises_upstream_rate_limited_with_retry_after():
    # M3.3
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "120"}, json={})

    provider = make_provider(handler)
    with pytest.raises(ProviderError) as exc_info:
        provider.fetch_chain(ChainRequest(symbol="AAPL", min_calendar_dte=1, max_calendar_dte=60))
    assert exc_info.value.code == "UPSTREAM_RATE_LIMITED"
    assert exc_info.value.retry_after_seconds == 120


def test_malformed_json_raises_schema_error():
    # M3.3
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json{{{")

    provider = make_provider(handler)
    with pytest.raises(ProviderError) as exc_info:
        provider.fetch_chain(ChainRequest(symbol="AAPL", min_calendar_dte=1, max_calendar_dte=60))
    assert exc_info.value.code == "SCHEMA_ERROR"


# R02: a valid lastTrade/rCode must not be enough to accept a malformed or
# truncated envelope as a successful, complete snapshot.


def _envelope(data):
    return {
        "data": data,
        "message": None,
        "status": {"rCode": 200, "bCodeMessage": None, "developerMessage": None},
    }


@pytest.mark.parametrize(
    "data",
    [
        {"lastTrade": "LAST TRADE: $100.00 (AS OF JAN 15, 2026)"},  # missing table
        {"lastTrade": "LAST TRADE: $100.00 (AS OF JAN 15, 2026)", "table": None},  # null table
        {
            "lastTrade": "LAST TRADE: $100.00 (AS OF JAN 15, 2026)",
            "table": {"asOf": None, "rows": None},  # null rows
        },
        {
            "lastTrade": "LAST TRADE: $100.00 (AS OF JAN 15, 2026)",
            "table": {"asOf": None, "rows": None},
            "totalRecord": 2,  # a nonzero total makes the missing rows load-bearing, not incidental
        },
    ],
)
def test_malformed_envelope_is_rejected_not_accepted_as_empty(data):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_envelope(data))

    provider = make_provider(handler)
    with pytest.raises(ProviderError) as exc_info:
        provider.fetch_chain(ChainRequest(symbol="AAPL", min_calendar_dte=1, max_calendar_dte=60))
    assert exc_info.value.code == "SCHEMA_ERROR"


def test_array_valued_response_root_is_rejected():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[1, 2, 3])

    provider = make_provider(handler)
    with pytest.raises(ProviderError) as exc_info:
        provider.fetch_chain(ChainRequest(symbol="AAPL", min_calendar_dte=1, max_calendar_dte=60))
    assert exc_info.value.code == "SCHEMA_ERROR"


def test_row_with_no_header_and_no_drilldown_url_is_schema_error():
    # No preceding expiration header and no drillDownURL: identity cannot be
    # determined at all. Must raise ProviderError, not a raw KeyError/TypeError.
    rows = [_data_row("aapl", "260115", "95.00", "00095000")]
    rows[0]["drillDownURL"] = None

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_body("LAST TRADE: $100.00 (AS OF JAN 15, 2026)", rows))

    provider = make_provider(handler)
    with pytest.raises(ProviderError) as exc_info:
        provider.fetch_chain(ChainRequest(symbol="AAPL", min_calendar_dte=1, max_calendar_dte=60))
    assert exc_info.value.code == "SCHEMA_ERROR"


@pytest.mark.parametrize(
    ("strike", "yymmdd", "use_url"),
    [
        ("--", "260115", False),  # unparseable strike text
        ("NaN", "260115", False),  # parses as Decimal, but not a real strike
        ("0.00", "260115", False),  # nonpositive strike
        ("95.00", "261399", True),  # drillDownURL with an impossible date
    ],
)
def test_malformed_row_identity_is_schema_error_not_a_crash(strike, yymmdd, use_url):
    row = _data_row("aapl", yymmdd, strike, "00095000")
    if not use_url:
        del row["drillDownURL"]
    rows = [_header_row("January 15, 2026"), row]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_body("LAST TRADE: $100.00 (AS OF JAN 15, 2026)", rows))

    provider = make_provider(handler)
    with pytest.raises(ProviderError) as exc_info:
        provider.fetch_chain(ChainRequest(symbol="AAPL", min_calendar_dte=1, max_calendar_dte=60))
    assert exc_info.value.code == "SCHEMA_ERROR"


def test_missing_drilldown_url_falls_back_to_header_and_strike():
    # A put-side-only row (or any row missing the URL key entirely, not just
    # null) still resolves via the carried header + its own strike field.
    row = _data_row("aapl", "260115", "95.00", "00095000")
    del row["drillDownURL"]
    rows = [_header_row("January 15, 2026"), row]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_body("LAST TRADE: $100.00 (AS OF JAN 15, 2026)", rows))

    provider = make_provider(handler)
    snapshot = provider.fetch_chain(ChainRequest(symbol="AAPL", min_calendar_dte=1, max_calendar_dte=60))
    assert len(snapshot.contracts) == 2
    assert all(c.provider_contract_id is None for c in snapshot.contracts)


def test_premature_short_page_inconsistent_with_total_record_is_rejected():
    # The response claims far more rows exist than the (short) page actually
    # contains -- must not be accepted as a complete two-contract snapshot.
    rows = [_header_row("January 15, 2026"), _data_row("aapl", "260115", "95.00", "00095000")]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_body("LAST TRADE: $100.00 (AS OF JAN 15, 2026)", rows, total_record=1906),
        )

    provider = make_provider(handler)
    with pytest.raises(ProviderError) as exc_info:
        provider.fetch_chain(ChainRequest(symbol="AAPL", min_calendar_dte=1, max_calendar_dte=60))
    assert exc_info.value.code == "INCOMPLETE_CHAIN"


def test_empty_intermediate_page_inconsistent_with_total_record_is_rejected(monkeypatch):
    monkeypatch.setattr(nasdaq, "CHAIN_PAGE_LIMIT", 2)
    page1 = [_header_row("January 15, 2026"), _data_row("aapl", "260115", "95.00", "00095000")]

    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(dict(request.url.params).get("offset", "0"))
        rows = page1 if offset == 0 else []  # page 2 goes empty without finishing
        return httpx.Response(
            200,
            json=_body("LAST TRADE: $100.00 (AS OF JAN 15, 2026)", rows, total_record=10),
        )

    provider = make_provider(handler)
    with pytest.raises(ProviderError) as exc_info:
        provider.fetch_chain(ChainRequest(symbol="AAPL", min_calendar_dte=1, max_calendar_dte=60))
    assert exc_info.value.code == "INCOMPLETE_CHAIN"


def test_total_record_changing_between_pages_is_rejected(monkeypatch):
    monkeypatch.setattr(nasdaq, "CHAIN_PAGE_LIMIT", 2)
    page1 = [_header_row("January 15, 2026"), _data_row("aapl", "260115", "95.00", "00095000")]
    page2 = [_data_row("aapl", "260115", "100.00", "00100000")]

    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(dict(request.url.params).get("offset", "0"))
        rows = page1 if offset == 0 else page2
        total = 3 if offset == 0 else 999  # inconsistent with page1's stated total
        return httpx.Response(
            200,
            json=_body("LAST TRADE: $100.00 (AS OF JAN 15, 2026)", rows, total_record=total),
        )

    provider = make_provider(handler)
    with pytest.raises(ProviderError) as exc_info:
        provider.fetch_chain(ChainRequest(symbol="AAPL", min_calendar_dte=1, max_calendar_dte=60))
    assert exc_info.value.code == "INCOMPLETE_CHAIN"


def test_exact_multiple_of_page_limit_requires_a_trailing_empty_page(monkeypatch):
    # total rows == 2 full pages exactly; neither looks "short", so a third,
    # explicitly empty page is required to confirm completion.
    monkeypatch.setattr(nasdaq, "CHAIN_PAGE_LIMIT", 2)
    page1 = [_header_row("January 15, 2026"), _data_row("aapl", "260115", "95.00", "00095000")]
    page2 = [
        _data_row("aapl", "260115", "100.00", "00100000"),
        _data_row("aapl", "260115", "105.00", "00105000"),
    ]
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(dict(request.url.params).get("offset", "0"))
        calls.append(offset)
        rows = {0: page1, 2: page2}.get(offset, [])
        return httpx.Response(
            200,
            json=_body(
                "LAST TRADE: $100.00 (AS OF JAN 15, 2026)",
                rows,
                total_record=len(page1) + len(page2),
            ),
        )

    provider = make_provider(handler)
    snapshot = provider.fetch_chain(ChainRequest(symbol="AAPL", min_calendar_dte=1, max_calendar_dte=60))
    assert calls == [0, 2, 4]
    assert len(snapshot.contracts) == 6  # 3 strikes x 2 sides


def test_legitimate_empty_complete_result_is_accepted():
    # rows=[] with totalRecord=0 on the very first page: a real "nothing
    # matched this query" result, not a malformed one.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_body("LAST TRADE: $100.00 (AS OF JAN 15, 2026)", [], total_record=0))

    provider = make_provider(handler)
    snapshot = provider.fetch_chain(ChainRequest(symbol="AAPL", min_calendar_dte=1, max_calendar_dte=60))
    assert snapshot.contracts == ()
    assert snapshot.underlying_price == 100.0


def test_exceeding_max_requests_without_finishing_is_incomplete_chain(monkeypatch):
    # M3.3: pagination that never reaches a short page must fail loudly,
    # not loop forever or silently truncate.
    monkeypatch.setattr(nasdaq, "CHAIN_PAGE_LIMIT", 2)
    monkeypatch.setattr(nasdaq, "CHAIN_MAX_REQUESTS", 3)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(dict(request.url.params).get("offset", "0"))
        calls.append(offset)
        base = 100 + offset  # unique strikes per page; pagination never "finishes"
        rows = [
            _data_row("aapl", "260115", f"{base}.00", f"{base * 1000:08d}"),
            _data_row("aapl", "260115", f"{base + 1}.00", f"{(base + 1) * 1000:08d}"),
        ]
        return httpx.Response(200, json=_body("LAST TRADE: $100.00 (AS OF JAN 15, 2026)", rows))

    provider = make_provider(handler)
    with pytest.raises(ProviderError) as exc_info:
        provider.fetch_chain(ChainRequest(symbol="AAPL", min_calendar_dte=1, max_calendar_dte=60))
    assert exc_info.value.code == "INCOMPLETE_CHAIN"
    assert calls == [0, 2, 4]


def test_unsupported_symbol_rejected_without_http():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise AssertionError("must not make an HTTP call for an unmapped symbol")

    provider = make_provider(handler)
    with pytest.raises(ProviderError) as exc_info:
        provider.fetch_chain(ChainRequest(symbol="MSFT", min_calendar_dte=1, max_calendar_dte=60))
    assert exc_info.value.code == "UNSUPPORTED_SYMBOL"
    assert calls == []


# --- NasdaqDividendProvider (docs/dividend-source-contract.md) -------------


def make_dividend_provider(handler) -> nasdaq.NasdaqDividendProvider:
    transport = httpx.MockTransport(handler)
    return nasdaq.NasdaqDividendProvider(client=httpx.Client(transport=transport))


def _dividend_row(ex="08/10/2026", amount="$0.27", pay="08/13/2026", decl="07/30/2026", type_="Cash"):
    return {
        "exOrEffDate": ex,
        "type": type_,
        "amount": amount,
        "declarationDate": decl,
        "recordDate": ex,
        "paymentDate": pay,
        "currency": "USD",
    }


def _dividend_body(rows, message=None, rcode=200):
    return {
        "data": {
            "dividendHeaderValues": [],
            "dividends": {"asOf": None, "headers": {}, "rows": rows},
        },
        "message": message,
        "status": {"rCode": rcode, "bCodeMessage": None, "developerMessage": None},
    }


def test_dividend_provider_parses_a_real_shaped_response():
    rows = [
        _dividend_row(),
        _dividend_row(ex="05/11/2026", amount="$0.27", pay="05/14/2026", decl="04/30/2026"),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["assetclass"] == "stocks"
        return httpx.Response(200, json=_dividend_body(rows))

    provider = make_dividend_provider(handler)
    feed = provider.fetch_dividends("AAPL")
    assert feed.provider_id == "nasdaq_dividends"
    assert len(feed.records) == 2
    first = feed.records[0]
    assert first.ex_date == date(2026, 8, 10)
    assert first.payment_date == date(2026, 8, 13)
    assert first.declaration_date == date(2026, 7, 30)
    assert first.amount == Decimal("0.27")
    assert first.kind == "ordinary_cash"
    assert first.currency == "USD"


def test_dividend_provider_uses_the_verified_etf_assetclass_for_qqq():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["assetclass"] == "etf"
        return httpx.Response(200, json=_dividend_body([_dividend_row(amount="$0.81349")]))

    provider = make_dividend_provider(handler)
    feed = provider.fetch_dividends("QQQ")
    assert feed.records[0].amount == Decimal("0.81349")


def test_spy_is_rejected_without_an_http_call_not_guessed_at():
    # Confirmed live 2026-09-17 (docs/dividend-source-contract.md): SPY has
    # no Nasdaq dividend-history coverage at all. Its INSTRUMENTS entry has
    # dividend_asset_class=None, so this must fail before any request, the
    # same as an unmapped chain symbol.
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not make an HTTP call for a known-unsupported symbol")

    provider = make_dividend_provider(handler)
    with pytest.raises(ProviderError) as exc_info:
        provider.fetch_dividends("SPY")
    assert exc_info.value.code == "UNSUPPORTED_SYMBOL"


def test_null_rows_with_200_status_is_coverage_unverified_not_empty_success():
    # Confirmed live shape when a real request IS made for an unsupported
    # symbol: rCode=200 with a null rows payload and a human message --
    # never a valid empty schedule (Section 7.2).
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_dividend_body(None, message="Dividend History for Non-Nasdaq symbols is not available"),
        )

    provider = make_dividend_provider(handler)
    with pytest.raises(ProviderError) as exc_info:
        provider.fetch_dividends("AAPL")
    assert exc_info.value.code == "DIVIDEND_COVERAGE_UNVERIFIED"


def test_na_sentinel_becomes_null_not_a_synthesized_date():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_dividend_body([_dividend_row(pay="N/A", decl="N/A")]))

    provider = make_dividend_provider(handler)
    feed = provider.fetch_dividends("AAPL")
    assert feed.records[0].payment_date is None
    assert feed.records[0].declaration_date is None


def test_non_cash_type_maps_to_other_not_ordinary_cash():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_dividend_body([_dividend_row(type_="Stock")]))

    provider = make_dividend_provider(handler)
    feed = provider.fetch_dividends("AAPL")
    assert feed.records[0].kind == "other"


def test_dividend_amount_with_more_than_two_decimal_places_is_preserved():
    # Confirmed live: QQQ amounts have 4-5 decimal places, not always 2.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_dividend_body([_dividend_row(amount="$0.73282")]))

    provider = make_dividend_provider(handler)
    feed = provider.fetch_dividends("QQQ")
    assert feed.records[0].amount == Decimal("0.73282")


def test_dividend_malformed_amount_is_schema_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_dividend_body([_dividend_row(amount="not-a-number")]))

    provider = make_dividend_provider(handler)
    with pytest.raises(ProviderError) as exc_info:
        provider.fetch_dividends("AAPL")
    assert exc_info.value.code == "DIVIDEND_SCHEMA_ERROR"


def test_dividend_missing_amount_is_schema_error_not_a_crash():
    row = _dividend_row()
    del row["amount"]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_dividend_body([row]))

    provider = make_dividend_provider(handler)
    with pytest.raises(ProviderError) as exc_info:
        provider.fetch_dividends("AAPL")
    assert exc_info.value.code == "DIVIDEND_SCHEMA_ERROR"


def test_dividend_payment_date_before_ex_date_is_schema_error_not_a_crash():
    # Untrusted upstream data: an inverted payment/ex date must fail as a
    # clean provider error, not a raw pydantic ValidationError surfacing as
    # an unhandled 500.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_dividend_body([_dividend_row(ex="08/10/2026", pay="08/01/2026")]))

    provider = make_dividend_provider(handler)
    with pytest.raises(ProviderError) as exc_info:
        provider.fetch_dividends("AAPL")
    assert exc_info.value.code == "DIVIDEND_SCHEMA_ERROR"


def test_dividend_429_raises_upstream_rate_limited():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "60"}, json={})

    provider = make_dividend_provider(handler)
    with pytest.raises(ProviderError) as exc_info:
        provider.fetch_dividends("AAPL")
    assert exc_info.value.code == "UPSTREAM_RATE_LIMITED"
    assert exc_info.value.retry_after_seconds == 60


def test_dividend_malformed_json_is_schema_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json{{{")

    provider = make_dividend_provider(handler)
    with pytest.raises(ProviderError) as exc_info:
        provider.fetch_dividends("AAPL")
    assert exc_info.value.code == "DIVIDEND_SCHEMA_ERROR"
