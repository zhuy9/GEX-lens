# ADR-0003: Automated SPY/QQQ/AAPL Dividend Schedules

**Status:** Accepted (2026-10-09; amended 2026-11-18)

## Problem

- ADR-0001 Section 7.3 requires an owner-reviewed dividend schedule no older than `7` days (`REVIEW_MAX_AGE_DAYS`).
- Weekly manual updates for `SPY`, `QQQ`, and `AAPL` are repetitive toil when ex-dates and amounts follow predictable rules or can be read from Yahoo.

## Decision

- **Daily job:** [.github/workflows/reference-update.yml](../.github/workflows/reference-update.yml) (weekdays `22:00 UTC`) runs `scripts/update_dividends.py` using `yfinance` and writes `reference/dividend_schedules.json` in `reference_inputs.json` format:
  - **Window:** `today .. today + 45d` (`COVERAGE_DAYS = 45`, sized for `MAX_CALENDAR_DTE = 30` + `7d` buffer).
  - **Amount:** Last actual payout from Yahoo (`amount_status: "estimated"`, `source_refs: ["auto:update_dividends.py:..."]`).
- **Ex-date rules:**
  - `SPY`: 3rd Friday of Mar/Jun/Sep/Dec (shifts to Thursday if Good Friday or Juneteenth).
  - `QQQ`: Monday after the 3rd Friday of Mar/Jun/Sep/Dec.
  - `AAPL`: Uses Yahoo `Ticker.info['exDividendDate']` (and `'dividendDate'`) when already declared past the last actual ex-date; otherwise projects `last_ex_date + 13 weeks`.
    - *Pre-ex dead zone guard:* If `+13w` passed by `<= 7` days without a new Yahoo history row yet, holds an unconfirmed event on the next weekday so AAPL does not silently drop to `$0` right before going ex; fails beyond 14 weeks.
- **Validation & isolation:** Each symbol validates Yahoo's last 4 ex-dates against its pattern (AAPL: every gap 12–14 weeks). A mismatch fails **that symbol only** (keeping its prior entry so the 7-day check blocks it within a week), updates the remaining symbols, and exits non-zero.
- **Backend precedence (`backend/app.py`, `backend/market_inputs.py`):**
  - Loads both local `reference_inputs.json` (optional when `rate_source != "manual"` and `reference/dividend_schedules.json` exists) and `reference/dividend_schedules.json`.
  - Among fresh reviews (`<= 7d`), a review containing `"declared"` events beats `"estimated"`-only schedules, breaking ties by newest `reviewed_at`.
- **Sanity cross-check (`backend/analytics.py`):**
  - `parity_dividend_warnings()` compares scheduled `pv_dividends` against median near-ATM (`±5%`) put-call parity per expiry and emits `DIVIDEND_PARITY_DIVERGENCE` on mismatch.

## PRD / ADR-0001 Overrides

- **ADR-0001 Section 7.3** (*Owner review*): Automated projections stand in for manual weekly sign-off; the unchanged 7-day age check acts as the pipeline health check (`DIVIDEND_REVIEW_REQUIRED`).
- **PRD Section 1** (*No background collection*): Extended to this daily dividend-history fetch.

## Limits

- **Estimated amounts:** Uses the last actual amount (e.g., QQQ Sep 2026 estimated `0.81` vs. actual `0.751`; Apple typically raises in May).
- **Special dividends:** Not projected; any special dividend in Yahoo's last 4 rows breaks the pattern check and blocks that symbol until manually reviewed.
- **AAPL timing:** Apple sets ex-dates with earnings (Fridays 2023–24, Mondays 2025–26, Thu 2026-11-12). Before Yahoo `Ticker.info` surfaces a newly declared date, the `+13w` estimate can be off by ~1–3 days.
