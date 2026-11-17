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
from itertools import count
from pathlib import Path
from zoneinfo import ZoneInfo

import yfinance as yf

OUT = Path(__file__).resolve().parents[1] / "reference/dividend_schedules.json"
SYMBOLS = ("AAPL", "QQQ", "SPY")
COVERAGE_DAYS = 45  # > MAX_CALENDAR_DTE (30); quarterly spacing means <= 1 event
AAPL_SPACING = timedelta(weeks=13)
AAPL_GAP_DAYS = range(84, 99)  # 12-14 weeks; 2023-2026 gaps were 88-94


def third_friday(year: int, month: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(4 - first.weekday()) % 7 + 14)


def projected_ex_date(symbol: str, year: int, month: int) -> date:
    """SPY/QQQ only (Mar/Jun/Sep/Dec)."""
    friday = third_friday(year, month)
    if symbol == "QQQ":
        return friday + timedelta(days=3)  # Monday after the third Friday
    # ponytail: only Juneteenth lands on a quarterly third Friday in practice;
    # add a holiday calendar if Good Friday ever does.
    return friday - timedelta(days=1) if (friday.month, friday.day) == (6, 19) else friday


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
        gaps = [(b - a).days for a, b in zip(actual, actual[1:])]
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


def propose(symbol: str, today: date, now: datetime) -> dict:
    history = yf.Ticker(symbol).dividends.tail(4)
    if len(history) < 4:
        raise ValueError(f"Yahoo returned {len(history)} dividends, need 4")
    actual = [ts.date() for ts in history.index]
    last_date, last_amount = actual[-1], float(history.iloc[-1])

    coverage_end = today + timedelta(days=COVERAGE_DAYS)
    events = []
    for ex_date in next_ex_dates(symbol, actual):
        if ex_date > coverage_end:
            break
        if ex_date >= today:
            events.append(
                {
                    "event_id": f"{symbol.lower()}-{ex_date.year}-{ex_date.month:02d}",
                    "ex_date": ex_date.isoformat(),
                    "payment_date": None,
                    "amount": f"{last_amount:.3f}",
                    "amount_status": "estimated",
                    "source_ref": f"projected; amount = {last_date} actual {last_amount:.3f} (Yahoo)",
                }
            )
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
