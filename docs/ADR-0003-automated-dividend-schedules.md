# ADR-0003: Automated SPY/QQQ/AAPL dividend schedules

Status: accepted (2026-10-09)

## Problem

ADR-0001 7.3 requires an owner-reviewed schedule no older than 7 days
(`REVIEW_MAX_AGE_DAYS`). For SPY and QQQ the review is mechanical (same
quarterly calendar, amount = last actual), so re-typing it weekly is pure toil.

## Decision

- `.github/workflows/reference-update.yml` (weekdays 22:00 UTC) runs
  `scripts/update_dividends.py`, which reads Yahoo dividend history (yfinance)
  and writes `reference/dividend_schedules.json` in the `reference_inputs.json`
  format: coverage today..today+45d, any projected ex-date in it with the last
  actual amount, `amount_status: "estimated"`, `reviewed_at` = run time,
  `source_refs: ["auto:update_dividends.py:..."]`.
- Projection: SPY ex-date = third Friday of Mar/Jun/Sep/Dec (Thursday when
  that is Juneteenth); QQQ = the following Monday. The script first checks
  the rule against Yahoo's last four ex-dates and fails without writing on
  any mismatch.
- For `dividend_sources = "manual_schedule"`, `app.py` loads both the local
  file and this one with the same loader and uses the entry with the newer
  `reviewed_at`. A hand-edited local entry therefore overrides until the next
  run is newer.

## PRD/ADR-0001 provisions overridden

- ADR-0001 7.3 owner review: for SPY/QQQ, the scheduled projection stands in
  for the owner's review. The 7-day age check is unchanged and now doubles as
  the pipeline health check: if the job stops (or refuses), refreshes block
  with `DIVIDEND_REVIEW_REQUIRED` within a week.
- PRD section 1 "background collection": extended to this daily
  dividend-history fetch (ADR-0002 already allowed the ratio fetch).

## Limits

- Amounts are last-actual estimates (e.g. QQQ Sep 2026 estimate 0.81 vs actual
  0.751); declared amounts are not fetched. Special distributions are not
  projected; one in Yahoo's last four breaks the pattern check and stops the
  job.
- AAPL was added on 2026-10-09: ex-date = second Monday of Feb/May/Aug/Nov
  (matches its last seven ex-dates). Apple usually raises its dividend in
  May, so the May estimate can be a little low until it is declared.
