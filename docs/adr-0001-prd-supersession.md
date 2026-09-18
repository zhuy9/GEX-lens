# ADR-0001 PRD supersession note (M0.1)

- **Baseline commit reviewed by ADR-0001:** `7137567b8b4faf79ad9a47f69517a731617b0290`
- **Status:** ADR-0001 is accepted. Its precedence rule applies: it overrides
  only the PRD provisions listed below. Every other PRD provision (React +
  shadcn/ui, the HTML GEX table, Plotly IV surface, DuckDB, one Python
  process, manual collection, the refresh lock/cooldown, 1-60 calendar DTE,
  0.80-1.20x strike scope, the three-symbol allowlist, no separate spot/quote
  request, null-vs-zero semantics, no trading signals) remains in force
  unchanged. Later amendment (2026-09-17, outside ADR-0001): the allowlist
  moved from `settings.json` to `backend/instruments.py`; see PRD 3.1.

This is a standalone copy of ADR-0001 Section 3's table (an M0 deliverable in
its own right, per Section 15's M0 acceptance criteria), with an added
column pointing at where each replacement actually lives in code today.

| Superseded PRD provision | Replacement (ADR-0001) | Implemented in |
|---|---|---|
| PRD 5.1: rates/dividend yields supplied only through settings | Bounded, cached reference-data requests during a user-initiated refresh, with provenance and dated manual alternatives. | `market_inputs.py`, `rates.py`, `nasdaq.py::NasdaqDividendProvider`, `settings.json`'s `rate_source`/`dividend_sources` |
| PRD 6.1: continuous-yield BSM only; no discrete-dividend service | Cash schedule + cash-PV BSM approximation (Section 9) for new snapshots. | `analytics.py::build_expiry_pricing_context`, `models.py::ExpiryPricingContext` |
| PRD 6.3: one BSM input path | Same BSM kernels, fed by an expiry-specific pricing context; legacy continuous-yield path retained for offline reproduction. | `analytics.py::price_quote_v2` (v2), `analytics.py::price_quote` (legacy, kept for `reconcile.py`) |
| PRD 7: only per-1% display | Per-1% canonical storage; explicit per-$1 display conversion added. | `models.py::GexData.canonical_unit`, `frontend/src/components/GexHeatmap.tsx` (`display_factor`) |
| PRD 8: forward `S * exp((r-q)*T)` | Cash-model snapshots use the Section 9 forward instead. | `analytics.py::build_expiry_pricing_context` (`forward` field) |
| PRD 9: exactly two tables | One bounded reference-input cache table added; existing snapshot/option-quote tables unchanged. | `schema.sql`'s `reference_cache` table |
| PRD 10: schema version 1 and empty refresh request | Read versions 1 and 2; write version 2; one optional boolean refresh query parameter. | `models.py::DashboardResponseV1/V2`, `app.py`'s `/api/dashboard/{symbol}/refresh?force_reference_refresh=` |
| PRD 1: no historical replay | A local, read-only diagnostic CLI that reprocesses one saved snapshot and writes a comparison report -- not a history UI or backtester. | `reconcile.py` |
| PRD 4: exact seven application modules | `market_inputs.py`, `rates.py`, and `reconcile.py` added; no service/repository layers. | repo root of `backend/` |
| PRD 11: no financial calculations in React | Only the GEX unit conversion and formatting is permitted in React; IV, gamma, dividend adjustment, and forward stay in Python. | `frontend/src/components/GexHeatmap.tsx`, `IvSurface.tsx` |

## Other M0 deliverables

- [dividend-source-contract.md](dividend-source-contract.md) -- M0.2/M0.3
- [rate-source-contract.md](rate-source-contract.md) -- M0.4
- ADR-0001 itself -- [ADR-0001-pricing-inputs-dividends-and-gex-units.md](ADR-0001-pricing-inputs-dividends-and-gex-units.md)
- Project-agent instructions updated to recognize this ADR: see the root
  [CLAUDE.md](../CLAUDE.md)'s "ADR-0001" section.
