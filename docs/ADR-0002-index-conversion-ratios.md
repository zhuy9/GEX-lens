# ADR-0002: Display-Only Index Conversion Ratios

**Status:** Accepted (2026-10-09)

## Problem

Users often think in index points (`SPX` / `NDX`), while the app fetches and prices ETF option chains (`SPY` / `QQQ`).

## Decision

- **UI display:** Show the approximate `SPX` level next to each `SPY` strike and `NDX` next to each `QQQ` strike on the heatmap axis and spot row.
- **Daily job:** [.github/workflows/reference-update.yml](../.github/workflows/reference-update.yml) runs `scripts/update_ratios.py` weekdays at `22:00 UTC` (after the 16:00 ET close in both EST and EDT) and on manual dispatch.
- **Computation:** `scripts/update_ratios.py` fetches 1-minute closes for `SPY`, `^GSPC`, `QQQ`, `^NDX` via `yfinance`, takes the latest minute with all four present, and writes `index / etf` to [reference/conversion_ratios.json](../reference/conversion_ratios.json).
- **Failure handling:** Out-of-range or non-finite ratios fail the step without overwriting the file; the Action commits only on diff.
- **Frontend consumption:** Built directly into the frontend from JSON; `AAPL` (no entry) displays no conversion.

## PRD / ADR-0001 Overrides

- **ADR-0001 Section 10** (*"No SPY-to-SPX strike rescaling"*): Allows display-only index approximations labeled with their ratio and as-of time; all strikes and calculations remain in ETF terms.
- **PRD Section 1** (*"No background collection / additional live providers"*): Allows this single scheduled ratio-only Yahoo fetch.
- **PRD Section 4 / ADR-0001 Section 4** (*Module layout*): Adds `scripts/update_ratios.py` and `reference/conversion_ratios.json`; `yfinance` never enters `backend/`.

## Limits

- **Display only:** Ratios never feed spot, analytics, storage, or snapshots.
- **Intraday drift:** Yesterday's close ratio drifts slightly intraday (< 1 index point/day) and steps on ETF ex-dividend dates until the next run.
- **Source terms:** `yfinance` is unofficial; only derived ratios are committed.

See [ADR-0003](ADR-0003-automated-dividend-schedules.md) for the dividend schedules updated by the same workflow.
