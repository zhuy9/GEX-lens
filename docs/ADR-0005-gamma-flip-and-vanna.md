# ADR-0005: Gamma Flip and Vanna Exposure (VEX)

**Status:** Accepted (2026-10-09)

## Problem

Net GEX at spot shows the current gamma sign, but not **how far spot must move to flip sign** or **how dealer hedging reacts when implied volatility (IV) moves**.

## Decision

- **Pure functions in `backend/analytics.py`** (using `ExposureLeg` with `model_spot = spot - pv_dividends` and call-positive / put-negative convention):
  - `net_gex_at(legs, spot)`: Total signed GEX per 1% move at a hypothetical `spot` (sticky-strike IV per contract).
  - `gamma_flip(legs, spot)`: Scans `-10%` to `+10%` in `0.25%` steps (`FLIP_GRID`), linearly interpolates sign crossings, and returns the crossing nearest today's `spot` (or `None`).
  - `vanna_exposure_by_strike(legs, spot)`: `bsm_vanna * open_interest * 100 * spot * 0.01` (dollars of hedge change per `+1` IV point).
- **Persistence & UI (`build_gamma_profile`):**
  - Computed on every refresh and stored as optional `gamma_profile` (`net_gex_at_spot`, `gamma_flip`, `vanna_exposure`, `top_vanna_strikes`) on `DashboardResponseV2` (older snapshots remain valid without it).
  - Displayed in the dashboard regime line, as an amber gamma-flip row on the heatmap, and in `scripts/daily_heatmap.py`.
- **Reading the numbers:**
  - **Environment:** `net_gex_at_spot > 0` (damping) vs. `< 0` (amplifying).
  - **Gamma flip:** Nearest sign-change price; within `~0.5%` of spot indicates a fragile regime.
  - **VEX > 0:** Falling IV causes hedging to **buy** stock; rising IV causes hedging to **sell** (`VEX < 0` is the reverse).

## PRD Overrides

- **PRD Section 1** (*"Do not implement gamma-flip/profile calculations"*): Allowed for the dashboard and daily summary.
- **PRD Section 6.3** (*"Calculate only IV and gamma"*): Adds vanna for `gamma_profile` and the daily summary.

## Limits

- **Sticky-strike IV:** Spot scans hold each contract's IV fixed; in real sell-offs IV rises, so the realized flip level can shift.
- **1–30 DTE scope:** Excludes 0DTE and >30 DTE expirations, so totals will differ from external vendors.
- **Positioning & research:** Assumes dealers are long calls / short puts; vanna/charm flow narratives are practitioner rules of thumb rather than peer-reviewed consensus (see [docs/interpret-heatmap.md](interpret-heatmap.md)).
