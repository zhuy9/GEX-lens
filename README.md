# GEX Lens

[![CI](https://github.com/zhuy9/GEX-lens/actions/workflows/ci.yml/badge.svg)](https://github.com/zhuy9/GEX-lens/actions/workflows/ci.yml)

A local, single-user dashboard for options gamma-exposure (GEX) and implied-volatility
analysis. Fetches one option-chain snapshot per manual refresh and renders a
strike-by-expiration GEX heatmap plus a 3D IV surface, computed with
Black-Scholes-Merton. Milestones M0-M5 are complete (see
[docs/m5-validation.md](docs/m5-validation.md) for the live verification run).

This is a personal research tool, not a trading system. See
[docs/options_analytics_mvp_prd.md](docs/options_analytics_mvp_prd.md) for the full
product requirements.

![GEX heatmap](docs/screenshots/gex-heatmap.png)

## Stack

- Frontend: React, TypeScript, Vite, shadcn/ui, Tailwind CSS, Plotly.js
- Backend: Python 3.12, FastAPI, Uvicorn, NumPy, SciPy
- Storage: DuckDB

## Setup

Requires Python 3.12 and Node.js 22.12+. No Docker, no external database server —
DuckDB is an embedded file under `backend/data/`.

Commands below are Windows PowerShell; macOS/Linux equivalents swap
`py -3.12`/`python3.12`, `.venv\Scripts\Activate.ps1`/`.venv/bin/activate`, and
`Copy-Item`/`cp`.

### Backend

```powershell
cd backend
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item settings.example.json settings.json   # never commit this file
python app.py
```

Serves on `http://127.0.0.1:8000`. Defaults to `source_mode: "fixture"` (synthetic
data, no network). Switching to `"nasdaq"` requires your own review of
[Nasdaq's terms](https://www.nasdaq.com/legal) — see below.

### Frontend

```powershell
cd frontend
npm ci
npm run dev
```

Open `http://127.0.0.1:5173` (proxies `/api` to the backend — start it first).

### Checks

```powershell
cd backend
pytest
ruff check .
ty check app.py provider.py nasdaq.py fixtures.py analytics.py storage.py models.py market_inputs.py rates.py
cd ..\frontend
npm run lint
npm run build
npm test
```

## Offline reconciliation

`backend/reconcile.py` is a read-only diagnostic: it reruns one saved snapshot's
normalized quotes under a scenario rate/dividend schedule and compares the result
to what was actually saved. It is not a backtester or an HTTP endpoint, and it
never fetches live data. **Stop the backend first** — it opens the same DuckDB
file read-only, and a running server already holds its own connection to it.

```powershell
cd backend
python reconcile.py --db data/options.duckdb `
  --snapshot-id <uuid> `
  --scenario-inputs reference_inputs.scenario.json `
  --out exports/<uuid>-pricing-comparison
```

Writes `summary.md`, `contracts.csv`, `cells.csv`, and `inputs.json` to `--out`
(refuses to overwrite a directory that already has a report). Exits non-zero
with `BASELINE_REPRODUCTION_FAILED` if it cannot first reproduce the snapshot's
own saved values — a discrepancy there is a bug, not a rate/dividend finding.

## Adding a data source

`OptionsDataProvider` ([backend/provider.py](backend/provider.py)) is a `Protocol`
with one method, `fetch_chain`. Add an adapter and one branch in the `source_mode`
factory in [backend/app.py](backend/app.py) — no changes to analytics, storage, or
the frontend required. See
[backend/tests/test_provider_boundary.py](backend/tests/test_provider_boundary.py)
for the boundary tests a new provider must satisfy.

Refreshes are manual only, and every provider must source the underlying price only
from the option-chain response itself — never a separate quote endpoint.

## License

[MIT](LICENSE) for original project code. Third-party components and market data
retain their own licenses; this license grants no rights to either.

## Data Sources and Usage Rights

Independent project, not affiliated with or endorsed by Nasdaq. Any Nasdaq
integration is unofficial, provides no data license, and requires your own review of
[Nasdaq's terms](https://www.nasdaq.com/legal) before use.

Fixtures, tests, and screenshots use synthetic data only. Never commit captured
market-data responses, databases, credentials, or market-data exports.

## Research Limitations

A snapshot-based research tool, not a real-time feed or investment recommendation —
IV/GEX outputs depend on model assumptions and are not trading signals.

The Nasdaq source has known gaps (no per-quote timestamps, no contract-multiplier
field, date-only spot pricing, and an unofficial, no-SLA endpoint), recorded in full
in [docs/source-contract.md](docs/source-contract.md) along with the M0/M5
verification evidence. Fixture mode is synthetic and never a substitute for that
live verification.
