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


def _body(last_trade, rows, as_of=None, rcode=200):
    return {
        "data": {
            "totalRecord": len(rows),
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
    monkeypatch.setattr(nasdaq, "PAGE_LIMIT", 2)
    page1 = [_header_row("January 15, 2026"), _data_row("aapl", "260115", "95.00", "00095000")]
    page2 = [_data_row("aapl", "260115", "100.00", "00100000")]  # no header; must inherit Jan 15 2026

    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(dict(request.url.params).get("offset", "0"))
        calls.append(offset)
        rows = page1 if offset == 0 else page2
        return httpx.Response(200, json=_body("LAST TRADE: $100.00 (AS OF JAN 15, 2026)", rows))

    provider = make_provider(handler)
    snapshot = provider.fetch_chain(ChainRequest(symbol="AAPL", min_calendar_dte=1, max_calendar_dte=60))

    assert calls == [0, 2]
    strikes = {c.strike for c in snapshot.contracts}
    assert Decimal("95") in strikes
    assert Decimal("100") in strikes
    assert all(c.expiration == date(2026, 1, 15) for c in snapshot.contracts)


def test_repeated_page_without_progress_is_incomplete_chain(monkeypatch):
    # PRD 5.2: a page that repeats without progress -> INCOMPLETE_CHAIN
    monkeypatch.setattr(nasdaq, "PAGE_LIMIT", 2)
    page1 = [_header_row("January 15, 2026"), _data_row("aapl", "260115", "95.00", "00095000")]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_body("LAST TRADE: $100.00 (AS OF JAN 15, 2026)", page1))

    provider = make_provider(handler)
    with pytest.raises(ProviderError) as exc_info:
        provider.fetch_chain(ChainRequest(symbol="AAPL", min_calendar_dte=1, max_calendar_dte=60))
    assert exc_info.value.code == "INCOMPLETE_CHAIN"


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
