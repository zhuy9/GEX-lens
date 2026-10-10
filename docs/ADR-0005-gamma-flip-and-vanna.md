# ADR-0005: Gamma flip and vanna exposure (VEX)

Status: accepted (2026-10-09)

## Problem

The daily summary (ADR-0004) says whether the market is in positive or
negative gamma, but not **how far** price is from changing that, and not how
hedging reacts when implied volatility (IV) moves. Two standard measures
answer these: the gamma flip and vanna exposure.

## Decision

Add three pure functions to `backend/analytics.py`, next to the existing BSM
code, using the same model, inputs, and call-positive/put-negative sign as
the GEX heatmap:

- `net_gex_at(legs, spot)`: total signed GEX per 1% move if the underlying
  were at `spot`. Each contract keeps its own IV (sticky strike). At today's
  spot it is the same per-contract formula as the heatmap.
- `gamma_flip(legs, spot)`: tries spot levels from -10% to +10% in 0.25%
  steps, finds where `net_gex_at` changes sign, and returns the crossing
  nearest today's spot (linear interpolation), or none.
- `vanna_exposure_by_strike(legs, spot)`: vanna (change in delta per change
  in IV) x open interest x 100 x spot x 1%, signed the same way. Unit: dollars
  of hedge change per 1 IV point.

`ExposureLeg` carries one priced contract with its expiry's time, rate, and
PV of dividends, so the model spot is `spot - PV(dividends)`, as in ADR-0001
Section 9. `scripts/daily_heatmap.py` builds the legs from its own temporary
database and prints the results and draws the flip line. They are **not**
saved in snapshots, sent by the API, or shown in the app.

How to read the output:

- **Environment** is the sign of net GEX at today's spot: positive (moves
  tend to be damped) or negative (moves tend to be amplified).
- **Gamma flip** is the nearest price where that sign changes. A flip close
  to spot means the regime can change with a small move.
- **VEX > 0**: if IV falls 1 point, hedging buys about VEX dollars of stock;
  if IV rises, it sells. VEX < 0 is the reverse.

## PRD provisions overridden

- PRD section 1 "Do not implement: gamma-flip/profile calculations": allowed
  for the daily script's summary only.
- PRD Section 6.3 "Calculate only IV and gamma; delta, theta, vega, and rho are
  out of scope": vanna is added, for the same summary only.

## Limits

- IV is held fixed while spot moves. In a real sell-off IV usually rises,
  so the true flip can differ.
- Only expirations 1-30 days out are counted (no 0DTE, nothing longer), so
  values will not match vendors such as SpotGamma.
- Same positioning assumption as GEX (investors sell calls and buy puts).
  Open interest does not show who is long or short.
- Vanna and charm flow ideas are mostly practitioner commentary, not
  peer-reviewed research (see `docs/interpret-heatmap.md`).
