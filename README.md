# GEX-lens

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

MIT. See [LICENSE](LICENSE).
