"""Write projected SPY/QQQ/AAPL dividend schedules to reference/dividend_schedules.json.

    uvx --with yfinance==1.7.0 python scripts/update_dividends.py

Run daily by .github/workflows/reference-update.yml (ADR-0003). Yahoo has
dividend history only, no announced future dates, so the next event is
projected from Yahoo's last four ex-dates (amount = last actual,
amount_status "estimated"): SPY/QQQ by their fixed quarterly calendar, AAPL
by 13-week spacing (Apple sets each date with earnings; no calendar rule
fits). History that breaks the pattern fails that symbol only: its previous
entry is kept unchanged, so the backend's 7-day review-age check blocks that
symbol's refreshes until a human looks, the other symbols still update, and
the run exits non-zero.
"""

import json
import sys
from datetime import UTC, date, datetime, timedelta
from itertools import chain, count, pairwise
from pathlib import Path
from zoneinfo import ZoneInfo

import yfinance as yf

OUT = Path(__file__).resolve().parents[1] / "reference/dividend_schedules.json"
SYMBOLS = ("AAPL", "QQQ", "SPY")
COVERAGE_DAYS = 45  # > MAX_CALENDAR_DTE (30); quarterly spacing means <= 1 event
AAPL_SPACING = timedelta(weeks=13)
AAPL_GAP_DAYS = range(84, 99)  # 12-14 weeks; 2023-2026 gaps were 88-94
AAPL_SHIFT_GRACE_DAYS = 7  # keep unconfirmed +13w event up to 14w from last actual


def easter_sunday(year: int) -> date:
    """Gregorian Easter Sunday (Meeus/Jones/Butcher algorithm)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    ell = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ell) // 451
    month, day = divmod(h + ell - 7 * m + 114, 31)
    return date(year, month, day + 1)


def good_friday(year: int) -> date:
    return easter_sunday(year) - timedelta(days=2)


def third_friday(year: int, month: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(4 - first.weekday()) % 7 + 14)


def projected_ex_date(symbol: str, year: int, month: int) -> date:
    """SPY/QQQ only (Mar/Jun/Sep/Dec)."""
    friday = third_friday(year, month)
    if symbol == "QQQ":
        return friday + timedelta(days=3)  # Monday after the third Friday
    if friday == good_friday(year) or (friday.month, friday.day) == (6, 19):
        return friday - timedelta(days=1)
    return friday


def next_weekday(d: date) -> date:
    return d + timedelta(days=3 if d.weekday() == 4 else 2 if d.weekday() == 5 else 1)


def declared_from_info(info: dict | None, last_actual: date) -> tuple[date, date | None] | None:
    """Read upcoming declared (ex_date, payment_date) from Yahoo's Ticker.info
    when it already postdates `last_actual` (stocks only; ETFs 404 on calendar)."""
    if not info:
        return None
    raw_ex = info.get("exDividendDate")
    if not isinstance(raw_ex, int | float):
        return None
    ex_date = datetime.fromtimestamp(raw_ex, UTC).date()
    if ex_date <= last_actual:
        return None
    raw_pay = info.get("dividendDate")
    payment_date = datetime.fromtimestamp(raw_pay, UTC).date() if isinstance(raw_pay, int | float) else None
    if payment_date is not None and payment_date < ex_date:
        payment_date = None
    return ex_date, payment_date


def quarters_after(d: date):
    """(year, month) every 3 months after the month of ex-date `d`."""
    year, month = d.year, d.month
    while True:
        month += 3
        if month > 12:
            year, month = year + 1, month - 12
        yield year, month


def next_ex_dates(symbol: str, actual: list[date]):
    """Projected ex-dates after `actual` (Yahoo's last four), in order.
    Raises ValueError when `actual` does not fit the symbol's pattern."""
    if symbol == "AAPL":
        gaps = [(b - a).days for a, b in pairwise(actual)]
        if not all(g in AAPL_GAP_DAYS for g in gaps):
            raise ValueError(f"ex-date spacing changed (actual {actual}, gaps {gaps} days); edit by hand")
        # ponytail: +13 weeks has landed within 3 days of Apple's actual date
        # since 2023. When the real date is later, AAPL shows no dividend from
        # the projected date until Yahoo lists it; Apple's declared date (no
        # source fetched yet) would close that gap.
        return (actual[-1] + AAPL_SPACING * k for k in count(1))
    expected = [projected_ex_date(symbol, d.year, d.month) for d in actual]
    if actual != expected:
        raise ValueError(f"ex-date pattern changed (actual {actual}, rule {expected}); edit by hand")
    return (projected_ex_date(symbol, year, month) for year, month in quarters_after(actual[-1]))


def _make_event(
    symbol: str,
    ex_date: date,
    last_amount: float,
    *,
    payment_date: date | None = None,
    source_ref: str,
) -> dict:
    return {
        "event_id": f"{symbol.lower()}-{ex_date.year}-{ex_date.month:02d}",
        "ex_date": ex_date.isoformat(),
        "payment_date": payment_date.isoformat() if payment_date else None,
        "amount": f"{last_amount:.3f}",
        "amount_status": "estimated",
        "source_ref": source_ref,
    }


def build_events(
    symbol: str,
    actual: list[date],
    last_amount: float,
    today: date,
    coverage_end: date,
    *,
    declared: tuple[date, date | None] | None = None,
) -> list[dict]:
    last_date = actual[-1]
    projected_iter = next_ex_dates(symbol, actual)
    if symbol == "AAPL":
        first_projected = next(projected_iter)
        if declared is not None:
            ex_date, payment_date = declared
            if today <= ex_date <= coverage_end:
                return [
                    _make_event(
                        symbol,
                        ex_date,
                        last_amount,
                        payment_date=payment_date,
                        source_ref=f"Yahoo exDividendDate; amount = {last_date} actual {last_amount:.3f}",
                    )
                ]
            return []

        # If the real ex-date slipped past +13w and Yahoo history has not
        # recorded it yet, keep an unconfirmed placeholder on the next weekday
        # within the 14w grace window rather than dropping AAPL to $0.
        if first_projected < today:
            days_past = (today - first_projected).days
            if days_past > AAPL_SHIFT_GRACE_DAYS:
                raise ValueError(
                    f"no AAPL dividend recorded within {days_past} days after projected {first_projected}"
                )
            held_date = next_weekday(today)
            if held_date <= coverage_end:
                return [
                    _make_event(
                        symbol,
                        held_date,
                        last_amount,
                        source_ref=(
                            f"projected (past +13w {first_projected}, awaiting actual); "
                            f"amount = {last_date} actual {last_amount:.3f} (Yahoo)"
                        ),
                    )
                ]
            return []

        candidates = chain([first_projected], projected_iter)
    else:
        candidates = projected_iter

    events = []
    for ex_date in candidates:
        if ex_date > coverage_end:
            break
        if ex_date >= today:
            events.append(
                _make_event(
                    symbol,
                    ex_date,
                    last_amount,
                    source_ref=f"projected; amount = {last_date} actual {last_amount:.3f} (Yahoo)",
                )
            )
    return events


def propose(symbol: str, today: date, now: datetime) -> dict:
    ticker = yf.Ticker(symbol)
    history = ticker.dividends.tail(4)
    if len(history) < 4:
        raise ValueError(f"Yahoo returned {len(history)} dividends, need 4")
    actual = [ts.date() for ts in history.index]
    last_date, last_amount = actual[-1], float(history.iloc[-1])

    declared = None
    if symbol == "AAPL":
        try:
            declared = declared_from_info(ticker.info, last_date)
        except Exception:
            declared = None

    coverage_end = today + timedelta(days=COVERAGE_DAYS)
    events = build_events(symbol, actual, last_amount, today, coverage_end, declared=declared)
    return {
        "symbol": symbol,
        "reviewed_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "coverage_start": today.isoformat(),
        "coverage_end": coverage_end.isoformat(),
        "no_other_events_expected": True,
        "source_refs": [f"auto:update_dividends.py:yahoo-history:{today}"],
        "expected_events": events,
    }


def _check() -> None:
    # Real ex-dates from Yahoo history (2025-12 .. 2026-11).
    assert good_friday(2008) == date(2008, 3, 21)
    assert good_friday(2026) == date(2026, 4, 3)
    assert projected_ex_date("SPY", 2008, 3) == date(2008, 3, 20)  # Good Friday 3rd Friday
    assert projected_ex_date("SPY", 2025, 12) == date(2025, 12, 19)
    assert projected_ex_date("SPY", 2026, 3) == date(2026, 3, 20)
    assert projected_ex_date("SPY", 2026, 6) == date(2026, 6, 18)  # Juneteenth Friday
    assert projected_ex_date("QQQ", 2026, 6) == date(2026, 6, 22)
    assert projected_ex_date("QQQ", 2025, 12) == date(2025, 12, 22)
    gen = quarters_after(date(2026, 9, 18))
    assert [next(gen), next(gen)] == [(2026, 12), (2027, 3)]
    spy = [date(2025, 12, 19), date(2026, 3, 20), date(2026, 6, 18), date(2026, 9, 18)]
    assert next(next_ex_dates("SPY", spy)) == date(2026, 12, 18)
    aapl = [date(2026, 2, 9), date(2026, 5, 11), date(2026, 8, 10), date(2026, 11, 12)]  # Thu breaks Mondays
    assert next(next_ex_dates("AAPL", aapl)) == date(2027, 2, 11)
    # 1) Pre-ex dead zone: +13w landed on Mon Nov 9, today is Tue Nov 10 -> held to Wed Nov 11
    aapl_pre = [date(2025, 11, 10), date(2026, 2, 9), date(2026, 5, 11), date(2026, 8, 10)]
    held = build_events("AAPL", aapl_pre, 0.27, date(2026, 11, 10), date(2026, 12, 25))
    assert len(held) == 1 and held[0]["ex_date"] == "2026-11-11"
    # 2) Declared info overrides +13w projection with exact ex-date and payment_date
    decl = declared_from_info({"exDividendDate": 1794441600, "dividendDate": 1794787200}, date(2026, 8, 10))
    assert decl == (date(2026, 11, 12), date(2026, 11, 16))
    got = build_events("AAPL", aapl_pre, 0.27, date(2026, 11, 10), date(2026, 12, 25), declared=decl)
    assert len(got) == 1 and got[0]["ex_date"] == "2026-11-12" and got[0]["payment_date"] == "2026-11-16"
    # 3) Beyond 7-day grace window fails loudly
    try:
        build_events("AAPL", aapl_pre, 0.27, date(2026, 11, 18), date(2027, 1, 2))
    except ValueError:
        pass
    else:
        raise AssertionError("AAPL past grace window should fail")
    for symbol, bad in (
        ("AAPL", [aapl[1], date(2026, 6, 1), aapl[2], aapl[3]]),  # special dividend
        ("SPY", spy[:3] + [date(2026, 9, 21)]),  # moved date
    ):
        try:
            next_ex_dates(symbol, bad)
        except ValueError:
            continue
        raise AssertionError(f"{symbol} pattern check missed {bad}")


def main() -> None:
    _check()
    now = datetime.now(UTC)
    today = now.astimezone(ZoneInfo("America/New_York")).date()
    previous = json.loads(OUT.read_text())["schedules"] if OUT.exists() else {}
    schedules, failed = {}, []
    for symbol in SYMBOLS:
        try:
            schedules[symbol] = propose(symbol, today, now)
        except Exception as exc:  # pattern break or Yahoo error: this symbol only
            failed.append(symbol)
            print(f"{symbol}: {exc}; kept previous entry", file=sys.stderr)
            if symbol in previous:
                schedules[symbol] = previous[symbol]
    # Same shape as reference_inputs.json, so the backend reuses its loader.
    out = {"input_schema_version": 1, "manual_rate": None, "schedules": schedules}
    OUT.write_text(json.dumps(out, indent="\t") + "\n")
    print(json.dumps(out))
    if failed:
        sys.exit(f"failed: {', '.join(failed)}")


if __name__ == "__main__":
    main()
