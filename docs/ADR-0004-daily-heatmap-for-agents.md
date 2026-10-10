# ADR-0004: Daily SPY/QQQ heatmap for an AI agent (Muse)

Status: accepted (2026-10-09)

## Problem

The owner wants a daily look at the SPY and QQQ GEX heatmaps without opening
the app. Posting them to a GitHub issue would make Nasdaq-derived data public,
because this repo is public. The owner instead uses an AI agent (Muse) that can
clone a public repo, run commands, and send the result privately.

## Decision

- Add `scripts/daily_heatmap.py`. One command fetches SPY and QQQ and saves
  `exports/heatmap-spy.png` and `exports/heatmap-qqq.png` (git-ignored) with
  matplotlib, so the agent needs no browser. (A first version wrote an HTML
  page to screenshot; that needed a headless browser and was replaced.)
  matplotlib is listed in `scripts/requirements.txt`, not in the backend's.
- Text is the agent's main input; the images are for people. The script
  writes `exports/heatmap.md` with the key numbers (gamma regime and flip,
  VEX, net GEX near spot, biggest strikes, walls, biggest expirations) and
  the same heatmap window as a Markdown table, so the agent quotes exact
  values instead of reading them off an image.
- `docs/interpret-heatmap.md` explains how to read the heatmap, with sources,
  and gives the agent a daily summary template.
- The script calls the backend's own refresh code (`app._collect_and_save`)
  with its own temporary settings and database. The math is the same as the
  app's. It does not read or change `backend/settings.json`, the local
  `reference_inputs.json`, or saved snapshots.
- Inputs: chain from Nasdaq, rate from the New York Fed, dividends from the
  committed `reference/dividend_schedules.json` (ADR-0003), SPX/NDX labels from
  `reference/conversion_ratios.json` (ADR-0002).
- Each image shows the signed GEX proxy per 1% move, the 17 strikes around
  spot, and the nearest 8 expirations, with the same colors as the app, plus
  spot and gamma-flip lines.
- The README has a short section that tells an agent exactly what to run and
  what to send back. CI runs the script with `--fixture` (no network) so it
  cannot break silently.

## PRD/ADR provisions overridden

- PRD section 1 "background collection" and the manual-only Nasdaq risk note
  in `docs/source-contract.md`: the owner accepts one scheduled run per day by
  their own agent (about 4 Nasdaq requests). The app itself still refreshes
  only by hand; nothing in the server polls.
- Fixed module layout: adds `scripts/daily_heatmap.py`. It imports the backend
  but is not part of it.

## Limits

- Before the 9:30 ET open, option prices are from the last close, so IVs and
  gammas reflect that close. Open interest is updated overnight.
- Nasdaq may block the agent's network. The script then fails with
  `UPSTREAM_ACCESS_DENIED` or `UPSTREAM_RATE_LIMITED`, and the agent must not
  retry.
- The page is a picture of one snapshot, not a feed. It is for research only.
