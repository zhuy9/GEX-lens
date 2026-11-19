# ADR-0004: Daily SPY/QQQ Heatmap for AI Agents (Muse)

**Status:** Accepted (2026-10-09; updated 2026-11-18)

## Problem

The owner wants a daily view of `SPY` and `QQQ` GEX heatmaps without opening the app or posting Nasdaq-derived data to a public GitHub issue. An outside AI agent (Muse) can clone the repo, run one command, and send the summary and images privately.

## Decision

- **Script (`scripts/daily_heatmap.py`):**
  - Reuses the backend's own `app._collect_and_save` with temporary settings and a temporary DuckDB file (never touches `backend/settings.json`, `reference_inputs.json`, or saved snapshots).
  - Supports `--fixture` for zero-network CI verification.
- **Outputs (git-ignored under `exports/`):**
  - `exports/heatmap.md`: Key summary metrics (spot, gamma regime & flip, VEX, near-spot net GEX, top strikes, walls, largest expirations) plus the exact heatmap window as a Markdown table so the agent quotes numbers without OCR.
  - `exports/heatmap-spy.png` and `exports/heatmap-qqq.png`: Rendered via Matplotlib (`scripts/requirements.txt`, never a backend dependency), matching app colors across the 17 spot-centered strikes and nearest 8 expirations.
  - **Atomic PNG writes & metadata:** Writes via a temporary directory (`os.replace`) so each run gets a fresh filesystem birth time, and embeds an RFC 1123 `Creation Time` PNG `tEXt` chunk.
- **Price level markers:**
  - Dashed lines for `spot` (blue), `gamma flip` (orange), nearest-expiry `call wall` (green), and `put wall` (red).
  - Out-of-window levels render as edge labels with `↑` / `↓` and `%` distance from spot.
- **Data inputs:** Nasdaq chains, NY Fed SOFR rates, `reference/dividend_schedules.json` (ADR-0003), and `reference/conversion_ratios.json` (ADR-0002).
- **Interpretation guide:** `docs/interpret-heatmap.md` documents how to read the heatmap and provides Muse's summary template.

## PRD / ADR Overrides

- **PRD Section 7** (*"Do not infer support/resistance levels"*): Allowed for drawing the nearest-expiry call wall and put wall (already present in positioning profiles; approved 2026-11-08).
- **PRD Section 1** (*"Manual refresh only"*): Allows one scheduled daily agent run (~4 Nasdaq requests); the web app itself still has zero polling.
- **Module layout:** Adds `scripts/daily_heatmap.py`; imports `backend/` without adding dependencies to `backend/`.

## Limits

- **Pre-open data:** Before 09:30 ET, option prices reflect the prior close while open interest reflects the overnight update.
- **Upstream blocks:** `UPSTREAM_ACCESS_DENIED` or `UPSTREAM_RATE_LIMITED` fails immediately; the agent must report and not retry.
- **Scope:** Single-snapshot research visual, not a live feed or trading signal.
