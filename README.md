# GEX Lens

A local, single-user dashboard for options gamma-exposure (GEX) and implied-volatility
analysis. It fetches one option-chain snapshot per manual refresh, computes IV and
gamma with Black-Scholes-Merton, and renders two views: a strike-by-expiration GEX
heatmap and a 3D IV surface.

This is a personal research tool, not a trading system. See
[docs/options_analytics_mvp_prd.md](docs/options_analytics_mvp_prd.md) for the full
product requirements, including scope, numerical methods, and acceptance criteria.

## Status

M0-M4 complete: source contract verified, backend API, and React frontend are
in place and tested. M5 (live hand-off validation) is the remaining milestone.

## Stack

- Frontend: React, TypeScript, Vite, shadcn/ui, Tailwind CSS, Plotly.js
- Backend: Python 3.12, FastAPI, Uvicorn, NumPy, SciPy
- Storage: DuckDB

## Setup

Requires Python 3.12 and Node.js 18+.

### Backend

```bash
cd backend
python3.12 -m venv .venv      # Windows with the `py` launcher: `py -3.12 -m venv .venv`
.venv/Scripts/activate        # Windows; use `source .venv/bin/activate` on macOS/Linux
pip install -r requirements.txt
cp settings.example.json settings.json   # edit as needed; never commit this file
python app.py
```

The API serves on `http://127.0.0.1:8000`. `settings.json`'s `source_mode`
defaults to `"fixture"`, which needs no external network access. Switching
it to `"nasdaq"` requires completing your own review of Nasdaq's terms (see
[Data Sources and Usage Rights](#data-sources-and-usage-rights) below).

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Open `http://127.0.0.1:5173`. The dev server proxies `/api` requests to the
backend on port 8000, so start the backend first.

### Checks

```bash
cd backend && pytest && ruff check . && ty check .
cd frontend && npm run lint && npm run build && npm test
```

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
exchange feed or an investment recommendation.

Source data may be delayed, incomplete, or unavailable. Collection time
does not necessarily represent the time of the underlying market quote.

Implied volatility and gamma-exposure outputs depend on model assumptions
and input quality. They should not be treated as guaranteed trading signals.
