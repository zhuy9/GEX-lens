"""Write projected SPY/QQQ dividend schedules to reference/dividend_schedules.json.

    uvx --with yfinance==1.7.0 python scripts/update_dividends.py

Run daily by .github/workflows/reference-update.yml (ADR-0003). Yahoo has
dividend history only, no announced future dates, so the next event is
projected: ex-date from each fund's quarterly pattern, amount = last actual
(amount_status "estimated"). The pattern is first checked against Yahoo's
last four ex-dates; a mismatch means the fund changed its calendar, and the
script fails without writing, so the backend's 7-day review-age check blocks
refreshes until a human looks.
"""

import json
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yfinance as yf

OUT = Path(__file__).resolve().parents[1] / "reference/dividend_schedules.json"
COVERAGE_DAYS = 45  # > MAX_CALENDAR_DTE (30); quarterly spacing means <= 1 event


def third_friday(year: int, month: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(4 - first.weekday()) % 7 + 14)


def projected_ex_date(symbol: str, year: int, month: int) -> date:
    friday = third_friday(year, month)
    if symbol == "QQQ":
        return friday + timedelta(days=3)  # Monday after the third Friday
    # ponytail: only Juneteenth lands on a quarterly third Friday in practice;
    # add a holiday calendar if Good Friday ever does.
    return friday - timedelta(days=1) if (friday.month, friday.day) == (6, 19) else friday


def quarters_after(d: date):
    year, month = d.year, (d.month - 1) // 3 * 3 + 3  # quarter-end month of d
    while True:
        month += 3
        if month > 12:
            year, month = year + 1, month - 12
        yield year, month


def propose(symbol: str, today: date, now: datetime) -> dict:
    history = yf.Ticker(symbol).dividends.tail(4)
    if len(history) < 4:
        sys.exit(f"{symbol}: Yahoo returned {len(history)} dividends, need 4")
    actual = [ts.date() for ts in history.index]
    expected = [projected_ex_date(symbol, d.year, d.month) for d in actual]
    if actual != expected:
        sys.exit(f"{symbol}: ex-date pattern changed (actual {actual}, rule {expected}); edit by hand")
    last_date, last_amount = actual[-1], float(history.iloc[-1])

    coverage_end = today + timedelta(days=COVERAGE_DAYS)
    events = []
    for year, month in quarters_after(last_date):
        ex_date = projected_ex_date(symbol, year, month)
        if ex_date > coverage_end:
            break
        if ex_date >= today:
            events.append(
                {
                    "event_id": f"{symbol.lower()}-{year}-q{month // 3}",
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
    # Real ex-dates from Yahoo history (2025-12 .. 2026-09).
    assert projected_ex_date("SPY", 2025, 12) == date(2025, 12, 19)
    assert projected_ex_date("SPY", 2026, 3) == date(2026, 3, 20)
    assert projected_ex_date("SPY", 2026, 6) == date(2026, 6, 18)  # Juneteenth Friday
    assert projected_ex_date("QQQ", 2026, 6) == date(2026, 6, 22)
    assert projected_ex_date("QQQ", 2025, 12) == date(2025, 12, 22)
    gen = quarters_after(date(2026, 9, 18))
    assert [next(gen), next(gen)] == [(2026, 12), (2027, 3)]


def main() -> None:
    _check()
    now = datetime.now(UTC)
    today = now.astimezone(ZoneInfo("America/New_York")).date()
    # Same shape as reference_inputs.json, so the backend reuses its loader.
    out = {
        "input_schema_version": 1,
        "manual_rate": None,
        "schedules": {symbol: propose(symbol, today, now) for symbol in ("QQQ", "SPY")},
    }
    OUT.write_text(json.dumps(out, indent="\t") + "\n")
    print(json.dumps(out))


if __name__ == "__main__":
    main()
