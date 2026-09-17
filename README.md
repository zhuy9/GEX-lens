# GEX Lens

A local, single-user dashboard for options gamma-exposure (GEX) and implied-volatility
analysis. It fetches one option-chain snapshot per manual refresh, computes IV and
gamma with Black-Scholes-Merton, and renders two views: a strike-by-expiration GEX
heatmap and a 3D IV surface.

This is a personal research tool, not a trading system. See
[docs/options_analytics_mvp_prd.md](docs/options_analytics_mvp_prd.md) for the full
product requirements, including scope, numerical methods, and acceptance criteria.

## Status

Pre-implementation. Milestone M0 (verifying authorized access to a live option-chain
source) has not started. No application code exists yet.

## Stack

- Frontend: React, TypeScript, Vite, shadcn/ui, Tailwind CSS, Plotly.js
- Backend: Python 3.12, FastAPI, Uvicorn, NumPy, SciPy
- Storage: DuckDB

## Setup

Not available yet. Setup and run instructions will be added once the M1 backend
scaffold lands (see the PRD's milestone list).

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
