# GEX Lens

A local, single-user dashboard for options gamma-exposure (GEX) and implied-volatility
analysis. It fetches one option-chain snapshot per manual refresh, computes IV and
gamma with Black-Scholes-Merton, and renders two views: a strike-by-expiration GEX
heatmap and a 3D IV surface.

This is a personal research tool, not a trading system. See
[docs/options_analytics_mvp_prd.md](docs/options_analytics_mvp_prd.md) for the full
product requirements, including scope, numerical methods, and acceptance criteria.

## Status

M0-M5 complete: source contract verified, backend API and React frontend
built and tested, and a live hand-off validation pass performed against
real Nasdaq data for all three symbols (see
[docs/m5-validation.md](docs/m5-validation.md) for the recorded run).

## Stack

- Frontend: React, TypeScript, Vite, shadcn/ui, Tailwind CSS, Plotly.js
- Backend: Python 3.12, FastAPI, Uvicorn, NumPy, SciPy
- Storage: DuckDB

## Setup

Requires Python 3.12 and Node.js 18+. No Docker and no external database
server — DuckDB is an embedded file under `backend/data/`.

Commands below are Windows PowerShell, copy-paste ready. macOS/Linux
equivalents are the same commands with `python3.12` for `py -3.12`,
`.venv/bin/activate` for `.venv\Scripts\Activate.ps1`, and `cp` for
`Copy-Item`.

### Backend

```powershell
cd backend
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item settings.example.json settings.json   # edit as needed; never commit this file
python app.py
```

The API serves on `http://127.0.0.1:8000`. `settings.json`'s `source_mode`
defaults to `"fixture"`, which needs no external network access. Switching
it to `"nasdaq"` requires completing your own review of Nasdaq's terms (see
[Data Sources and Usage Rights](#data-sources-and-usage-rights) below).

### Frontend

```powershell
cd frontend
npm install
npm run dev
```

Open `http://127.0.0.1:5173`. The dev server proxies `/api` requests to the
backend on port 8000, so start the backend first.

### Checks

```powershell
cd backend
pytest
ruff check .
ty check .
cd ..\frontend
npm run lint
npm run build
npm test
```

## Adding a data source

`OptionsDataProvider` ([backend/provider.py](backend/provider.py)) is a
`Protocol` with one method, `fetch_chain`. `NasdaqProvider` and
`FixtureProvider` both implement it independently — neither imports the
other (see the boundary tests in
[backend/tests/test_provider_boundary.py](backend/tests/test_provider_boundary.py),
including a `StubProvider` that reaches both charts' saved API output
without constructing either real provider). Swapping or adding a source
means writing a new adapter against this Protocol and adding one branch to
the `source_mode` factory in [backend/app.py](backend/app.py); it requires
no changes to `analytics.py`, `storage.py`, or the frontend.

Refreshes are manual only — the UI never polls, auto-refreshes on focus, or
reconnects in the background. Every provider is also bound to the
chain-only underlying-price rule: the spot price comes solely from the
option-chain response's own last-trade field, never a separate quote
endpoint, a contract premium, or a selected strike.

## License

Original project code is licensed under the [MIT License](LICENSE).
Third-party components retain their respective licenses and notices.

The software license does not grant rights to third-party market data,
data services, or trademarks.

## Data Sources and Usage Rights

This is an independent project. It is not affiliated with, endorsed by,
or sponsored by Nasdaq.

Any Nasdaq integration is unofficial and does not provide a market-data
license or entitlement. Before enabling it, obtain the permissions
required for your intended use. Review [Nasdaq's terms](https://www.nasdaq.com/legal)
and any applicable data-provider agreement.

Development fixtures, automated tests, and demonstration screenshots
use independently generated synthetic data. Do not submit captured
market-data responses, populated databases, credentials, or market-data
exports in commits, pull requests, or public issues.

## Research Limitations

This application is a snapshot-based research tool, not a real-time
exchange feed or an investment recommendation. Implied volatility and
gamma-exposure outputs depend on model assumptions and input quality; they
are not guaranteed trading signals.

Known source and model gaps, recorded during M0/M5 verification against a
real Nasdaq sample (see [docs/source-contract.md](docs/source-contract.md)
for the full evidence trail):

- The Nasdaq source has no per-quote timestamp, no open-interest-as-of
  date, no contract multiplier field, and no adjusted/nonstandard-contract
  flag. Every snapshot therefore carries `MULTIPLIER_ASSUMED` (100 shares
  per contract, unverifiable per contract) and cannot detect adjusted
  deliverables.
- The underlying price's only timestamp is a calendar date with no
  time-of-day, so chain/spot timestamp alignment can never be confirmed
  (`TIMESTAMP_ALIGNMENT_UNKNOWN` on every snapshot). Separately, the
  chain-level pricing timestamp (`data.table.asOf`) has never been observed
  populated, so `valuation_at` falls back to collection-start time
  (`VALUATION_TIME_ASSUMED`). 16:00 America/New_York is a different,
  unconditional convention: the assumed *expiration* pricing time used for
  every contract's `T`, not a valuation-time fallback.
- Full-band (`money=all`, large `limit`) pagination was directly confirmed
  against SPY; QQQ and AAPL are confirmed for field paths and pagination
  mechanics generally, but not re-run symbol-by-symbol under that exact
  parameter combination — an extrapolation, not a direct observation, for
  those two.
- This uses Nasdaq's website-backed endpoint under the owner's own
  personal-use risk judgment, not the documented, authenticated Options
  Chain API — it carries no SLA and can change or break without notice.
  Fixture mode is synthetic data for development and is never a substitute
  for this live verification.
