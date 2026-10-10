# ADR-0002: Display-only index conversion ratios

Status: accepted (2026-10-09)

## Decision

Show the SPX equivalent of each SPY strike, and the NDX equivalent of each
QQQ strike, on the GEX heatmap's strike axis and spot row.

- `.github/workflows/ratio-update.yml` runs once per weekday at 22:00 UTC
  (after the 16:00 ET close in EDT and EST) and on manual dispatch.
- `.github/scripts/update_ratios.py` downloads 1-minute closes for SPY,
  `^GSPC`, QQQ, `^NDX` from Yahoo via yfinance, takes the last minute where all
  four have a close, and writes `index / etf` to
  `reference/conversion_ratios.json`. Out-of-range or non-finite ratios
  fail the run without touching the file.
- The Action commits only when the file changes. The last committed value is
  the fallback; the UI shows its as-of time in the strike header tooltip.
- The frontend imports the JSON at build time. Symbols without an entry
  (AAPL) show no conversion.

## PRD/ADR-0001 provisions overridden

- PRD section 1 "background collection" / "additional live data providers":
  allowed for this one scheduled, ratio-only Yahoo fetch.
- PRD section 4 / ADR-0001 section 4 module layout: adds
  `.github/scripts/update_ratios.py` and the JSON file. Nothing is added to
  `backend/`; yfinance is not a backend dependency.

## Limits

- Display only. Spot still comes only from the option-chain payload; the
  ratio never enters analytics, storage, or snapshots.
- A ratio from yesterday's close is off by the drift since then (SPY/QQQ
  expense accrual and dividend steps): well under one index point per day,
  plus a step on each ETF ex-dividend date until the next run.
- yfinance is unofficial and Yahoo's terms restrict automated use; this is the
  owner's risk decision, as with the Nasdaq endpoint. Only the derived ratios
  are committed.
