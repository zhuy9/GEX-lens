# ADR-0001 validation and hand-off (M6)

- **Commit:** `8d9cd4af6eba891c285076580982fc94f50d6033`
- **Date:** 2026-09-17/18
- **Machine:** Darwin arm64 (Apple Silicon), 18 logical CPUs, 48 GB RAM --
  exceeds the 4-core/8-GB-or-better requirement.
- **Versions:** Python 3.12.14, Node 24.16.0, ruff 0.7.0, ty 0.0.1a6 (`ty` is
  explicitly pre-release software per its own warning banner -- see
  "Known limitations" below), fastapi 0.115.0, pydantic 2.9.2, duckdb
  1.5.5, httpx 0.27.2, scipy 1.14.1, numpy 2.1.2, vite 8.3.0, vitest 5.0.1,
  @biomejs/biome 2.5.14.

## M6.1: full check suite

Run from `backend/` (venv active) and `frontend/`:

| Command | Result |
|---|---|
| `pytest` | 226 passed |
| `ruff check .` | clean |
| `ty check app.py provider.py nasdaq.py fixtures.py analytics.py storage.py models.py market_inputs.py rates.py reconcile.py instruments.py` | clean |
| `npm run lint` (biome) | clean, 37 files |
| `tsc -b` | clean |
| `npm test` (vitest) | 35 passed, 5 files |
| `npm run build` (vite) | succeeds; one pre-existing chunk-size warning for the Plotly-dependent IV-surface bundle (code-split, not loaded until that panel renders) |

## M6.2: tests run with external HTTP disabled

Every HTTP-touching test (`test_nasdaq.py`, `test_rates.py`) constructs its
provider with `httpx.Client(transport=httpx.MockTransport(handler))` --
requests never leave the process; there is no real socket to disable. No
test in the suite constructs a provider without an injected transport or
stub. Parser tests use synthetic, structurally-faithful fixtures (documented
inline as such); the only responses derived from real captures are the
verification samples in `docs/samples/` (git-ignored, never used as pytest
fixtures -- confirmed by grep: nothing under `docs/samples/` is imported by
any test).

## M6.3: performance

```
M3.5 processing+persistence p95: 755.9ms over 20 runs   (budget: 5000ms)
M3.5 GET-latest p95: 4.91ms over 20 runs                  (budget: 500ms)
```

Both comfortably under budget on the recorded machine above. This exercises
the legacy (v1) path's 2,000-contract fixture, per the existing
`test_performance.py`; the v2 path's per-request overhead is one
`build_expiry_pricing_context` call per expiry (not per contract) on top of
the same BSM kernels, which is not independently re-benchmarked here -- the
ADR's M6.3 wording only requires the existing budget to keep passing, which
it does.

## M6.4: live-enabled symbols

Live config used (git-ignored `settings.json`): `source_mode=nasdaq`,
`rate_source=nyfed_sofr`, `dividend_sources={SPY: manual_schedule, QQQ:
nasdaq_dividends, AAPL: nasdaq_dividends}`.

| Symbol | Dividend source | Complete refresh | Cache-hit refresh | Warnings |
|---|---|---|---|---|
| QQQ | **nasdaq_dividends** (verified, M0) | snapshot `94f0f67d-...`, spot 716.92, in-scope 4106/5772 contracts | snapshot `45157cdc-...`, rate `fetched_at` identical to the first refresh (cache hit, zero new SOFR request) | `MULTIPLIER_ASSUMED`, `TIMESTAMP_ALIGNMENT_UNKNOWN`, `VALUATION_TIME_ASSUMED`, `DIVIDEND_AMOUNT_ESTIMATED`, `DIVIDEND_ALIGNMENT_UNVERIFIED`, `NEAR_EX_DIVIDEND`, `DIVIDEND_PAYMENT_TIME_ASSUMED_AT_EX` |
| AAPL | **nasdaq_dividends** (verified, M0) | snapshot `390d4096-...`, spot 337.00, in-scope 780/1400 contracts | (dividend feed cached at `21:31:04 CDT`, reused by the QQQ-then-AAPL sequence within the 6h TTL) | `MULTIPLIER_ASSUMED`, `TIMESTAMP_ALIGNMENT_UNKNOWN`, `VALUATION_TIME_ASSUMED`, `DIVIDEND_AMOUNT_ESTIMATED` |
| SPY | **manual_schedule** -- explicitly labeled, *not* a verified Nasdaq-dividend integration | **incomplete**: no reviewed schedule was supplied for SPY (owner declined to fabricate one; SPY is a confirmed real quarterly payer, see [dividend-source-contract.md](dividend-source-contract.md)) | n/a | n/a |

`reference_cache` after this sequence (one row per source, confirmed via
direct query): `rate/nyfed_sofr/USD`, `dividends/nasdaq_dividends/QQQ`,
`dividends/nasdaq_dividends/AAPL` -- exactly one rate request and one
dividend request per symbol across three total refresh attempts, the rest
served from cache.

Owner-reviewed forward estimates used for QQQ/AAPL (both `amount_status:
estimated`, both explicitly the owner's extrapolation from the real
historical cadence in [dividend-source-contract.md](dividend-source-contract.md),
not source-confirmed): QQQ ex-date 2026-09-22 ~$0.81; AAPL ex-date
2026-11-09 ~$0.27.

A real bug was found and fixed during this verification: the option-chain
spot regex rejected a whole-dollar price with no decimal (AAPL traded at
exactly `$337`) -- see `docs/source-contract.md`'s update and
`nasdaq.py::_LAST_TRADE_RE`.

## M6.5: hand-reproduction

Contract: AAPL $337.5 call, expiring 2026-09-21, from snapshot
`390d4096-6238-4485-9b52-ca9c11d72409`. Saved: bid 2.51, ask 2.69 (mid
2.60), IV 0.20354529730445362, gamma 0.057184229496735885, OI 668.

Independently re-solved (standalone scipy/brentq script, not by calling the
repository's `analytics.py` functions) from the saved model spot (337.0,
identical to actual spot -- no dividend event falls before this near-term
expiry), `r_cc=0.03670093256736925`, `q=0`, `T=0.010324805104737442`:

```
Independently solved IV:      0.20354529730445362  (exact match)
Independently computed gamma: 0.057184229496735885  (exact match)
```

Per-$1 conversion, single-side exposure (call, OI=668, multiplier=100,
S=337.0):

```
GEX per 1% move:  4,338,229.65
GEX per $1 move:  1,287,308.50
display_factor = 1/(0.01*337.0) = 0.29673590504451036
4,338,229.65 * 0.29673590504451036 = 1,287,308.50  (exact match)
```

No private normalized inputs or raw provider payloads are published here or
committed anywhere in the repository; the numbers above are this one
contract's already-public-market bid/ask/OI at a specific historical
instant, not raw Nasdaq response bodies.

## M6.6: saved-dashboard resilience

Reopened AAPL's saved dashboard (`GET /api/dashboard/AAPL`) with the chain,
rate, and dividend providers all replaced by stubs that unconditionally
raise `UPSTREAM_UNAVAILABLE`: the GET still returned the full saved
dashboard unchanged (spot, rate, market_inputs all intact). A subsequent
`POST .../refresh` correctly failed (502, `UPSTREAM_UNAVAILABLE`) and left
the saved snapshot byte-for-byte unchanged. Move-unit switching is pure
frontend state (M1) and was already covered by `App.test.tsx`'s "zero
network requests" tests; it does not depend on any of these providers.

## M6.7: vendor-comparison note

No side-by-side comparison against another vendor's live GEX output was
performed as part of this hand-off -- matching another vendor was never an
acceptance criterion for this ADR (Section 1: "This ADR does not assert
that dividends explain every difference from QuantWheel... Matching another
vendor is not an acceptance criterion"). What this application's own AAPL
snapshot confirms about itself: instrument AAPL (equity, standard
multiplier 100, American exercise style, not modeled as American), expiry
2026-09-21 (3 calendar days from valuation), strike range confirmed 0.80x-
1.20x actual spot, exposure unit `usd_delta_notional_per_1pct` with an
explicit per-$1 display conversion, valuation timestamp
`2026-09-18T01:33:16Z` with `TIMESTAMP_ALIGNMENT_UNKNOWN` (Nasdaq's spot
timestamp precision is date-only). Any other vendor's selected instrument,
formula, quote timestamp, IV inputs, or dividend model remain unknown and
are not asserted to match.

## M6.8: no overclaiming

Grepped README, all `docs/*.md`, backend `.py`, and frontend `.tsx` files
for American-engine / risk-free-curve / dealer-positioning / guaranteed-
live-data / authorized-Nasdaq-service language. The only matches are the
required disclaimers themselves (`SnapshotMeta.tsx`: "BSM approximation. OI
does not identify dealer positions."; PRD Section 9's "Call-minus-put GEX
proxy, never Actual dealer GEX"). README already states Nasdaq access is
"unofficial, provides no data license." No corrections were needed.

## Known limitations (carried forward, not resolved by this ADR)

- `ty` is explicitly pre-release software per its own startup warning;
  "clean" above means no diagnostics *it currently knows how to detect*,
  not a guarantee against every possible type error.
- SPY's cash-model dividend support remains incomplete pending either a
  real owner-supplied manual schedule or a different data source --
  documented as blocked, not silently defaulted.
- M0's Nasdaq dividend-endpoint access remains a personal ToS risk
  judgment (same basis as the already-reviewed chain endpoint), not a
  granted permission -- see "Access authorization" in
  [dividend-source-contract.md](dividend-source-contract.md).
- The cash-PV model is a deliberate European approximation for American-
  style equity/ETF options (Section 9.1); it does not model early exercise,
  and is not claimed to.

## Addendum: post-validation refactor

Commit `fa5e0a5` (after the one pinned above) moved external API constants
(Nasdaq chain/dividend URLs, NY Fed SOFR URL, timeouts, size caps) into a new
`api.py`, and let `instruments.py` reuse `models.InstrumentClass` instead of
redefining it. Rename-only, no logic change. `pytest` (226 passed), `ruff
check .`, and `ty check` (including `api.py`) were re-run clean against this
commit.
