# M5 validation results

Live hand-off validation run against real Nasdaq data, `source_mode="nasdaq"`,
2026-09-17 (UTC times below). Records the acceptance-test evidence required
by PRD section 12, M5. Recorded in commit `7cd7032`, against the application
code as of that commit's parent; no CI workflow existed yet at that point (one
was added later — see [.github/workflows/ci.yml](../.github/workflows/ci.yml)
and its status badge in the project README). A future live-validation pass
should link both the commit it ran against and that run's actual CI result,
not only its own manual observations.

## M5.1 - Automated tests pass without internet access

- Backend: `pytest` -> 77 passed (mocked upstream HTTP; no real network
  calls in the suite).
- Frontend: `npm test -- --run` -> 8 passed.

## M5.2 - README PowerShell commands

`README.md`'s Setup and Checks sections now use Windows PowerShell syntax
(`py -3.12`, `Activate.ps1`, `Copy-Item`) as the primary, copy-paste path.
No Docker, no external database server (DuckDB is an embedded file).

## M5.3 - Live refresh for every symbol, plus a second SPY refresh outside cooldown

| Symbol | snapshot_id | collected_at (UTC) | source_rows | normalized_contracts | in_scope_contracts | valid_ivs | known_oi_contracts | complete_gex_cells | warnings |
|---|---|---|---|---|---|---|---|---|---|
| SPY (1st) | `ad06fb55-2601-4022-8f0e-5120b76b096c` | 2026-09-17T05:54:47.911807Z | 2879 | 5758 | 4476 | 3314 | 3447 | 924 | MULTIPLIER_ASSUMED, VALUATION_TIME_ASSUMED, TIMESTAMP_ALIGNMENT_UNKNOWN |
| QQQ | `93da7bc2-3ce7-49ce-8cac-a26e96bc72b3` | 2026-09-17T05:56:41.634798Z | 2856 | 5712 | 4002 | 3432 | 2884 | 1026 | same three |
| AAPL | `aea47b9e-ec9e-4b9c-8ad3-57a3468f4f6d` | 2026-09-17T06:05:01.258367Z | 700 | 1400 | 762 | 573 | 595 | 176 | same three |
| SPY (2nd, outside cooldown) | `de9c3f82-76ff-4390-b560-f995f52ad940` | 2026-09-17T06:28:22.231995Z | 2879 | 5758 | 4476 | 3316 | 3447 | 924 | same three |

The second SPY refresh ran at 06:28:22Z, 22+ minutes after the first
(06:05:57Z `refresh_not_before`), well outside the 60-second cooldown.
It produced a new snapshot ID and a small IV re-solve difference (3314 -> 3316
valid IVs). This snapshot carries `VALUATION_TIME_ASSUMED` (chain_asof was
null, so `valuation_at` fell back to `collection_started_at`), so each
contract's `T` also shifted by the ~22 minutes between the two collections,
independently of whether any quote actually changed. No raw-input comparison
between the two snapshots was performed, so this difference is recorded as
observed, not attributed to live quotes moving specifically — that would
require comparing the underlying bid/ask/OI values directly, which this run
did not do. No `INCOMPLETE_CHAIN`, `INVALID_UNDERLYING_PRICE`, or
`COLLECTION_WINDOW_EXCEEDED` rejections on any of the four refreshes.

## M5.4 - Hand-check five source contracts, reproduce one GEX cell

Source: raw Nasdaq payload persisted in the AAPL snapshot's
`snapshots.raw_payload` (DuckDB), snapshot `aea47b9e-ec9e-4b9c-8ad3-57a3468f4f6d`,
compared field-by-field against the normalized `option_quotes` rows for the
same snapshot.

| # | Contract | Raw source fields | Normalized fields | Match |
|---|---|---|---|---|
| 1 | AAPL 2026-09-18 50.00 C | `c_Bid="281.10" c_Ask="283.55" c_Last="282.14" c_Volume="4" c_Openinterest="13"` | `bid=281.1 ask=283.55 last=282.14 volume=4 open_interest=13` | Yes |
| 2 | AAPL 2026-09-18 50.00 P | `p_Bid="--" p_Ask="0.01" p_Last="0.01" p_Volume="--" p_Openinterest="1590"` | `bid=None ask=0.01 last=0.01 volume=None open_interest=1590` | Yes ("--" -> null) |
| 3 | AAPL 2026-09-18 145.00 C | `c_Bid="185.60" c_Ask="188.70" c_Last="185.38" c_Volume="--" c_Openinterest="56"` | `bid=185.6 ask=188.7 last=185.38 volume=None open_interest=56` | Yes (only volume null) |
| 4 | AAPL 2026-09-28 327.50 P | `p_Bid="3.15" p_Ask="4.15" p_Last="3.40" p_Volume="39" p_Openinterest="26"` | `bid=3.15 ask=4.15 last=3.4 volume=39 open_interest=26` | Yes |
| 5 | AAPL 2026-10-30 170.00 P | `p_Bid="--" p_Ask="2.14" p_Last="--" p_Volume="--" p_Openinterest="--"` | `bid=None ask=2.14 last=None volume=None open_interest=None` | Yes (four nulls, one survivor) |

All five: `provider_contract_id` populated from `drillDownURL` for calls,
`None` for puts (source never encodes a put ID), matching the recorded
source contract. Strikes 50/145/170 correctly carry `OUT_OF_SCOPE` (below
`0.80 * 332.41 = 265.93`); strike 327.50 is in scope.

### GEX cell reproduction: AAPL 2026-09-28, strike 327.50

Inputs: `spot=332.41`, `r=0.04`, `q=0.00`, `valuation_at=2026-09-17T06:04:57.652727Z`,
call `bid=8.20 ask=9.15 oi=2`, put `bid=3.15 ask=4.15 oi=26`, multiplier=100.

Independently computed (standalone script, not calling `analytics.py`):
`t = 0.03172572131129503` years (16:00 America/New_York on 2026-09-28,
converted to UTC, minus `valuation_at`); `call_mid=8.675`, `put_mid=3.65`;
solved `call_iv=0.23966610888840487`, `put_iv=0.25338553386433527` via
Brent's method on the BSM price equation; `call_gamma=0.025955997268378685`,
`put_gamma=0.024735767128657406`; `call_exposure = call_gamma * 2 * 100 *
332.41^2 * 0.01 = 5736.088933618514`; `put_exposure = put_gamma * 26 * 100 *
332.41^2 * 0.01 = 71063.54890218205`; `signed_proxy = -65327.45996856354`;
`gross_exposure = 76799.63783580056`.

The app's `GET /api/dashboard/AAPL` GEX cell at that expiration/strike
returned the identical values to the last displayed digit:
`call_gamma=0.025955997268378685`, `put_gamma=0.024735767128657406`,
`call_exposure=5736.088933618514`, `put_exposure=71063.54890218205`,
`signed_proxy=-65327.45996856354`, `gross_exposure=76799.63783580056`,
`status="COMPLETE"`.

## M5.5 - Restart with provider access disabled

Stopped the running backend, restarted it with `HTTP_PROXY`/`HTTPS_PROXY`
pointed at an unreachable local address (process-scoped, no system network
or firewall changes). `GET /api/dashboard/{symbol}` for SPY, QQQ, and AAPL
all returned `200` with the exact same `snapshot_id`/`collected_at` as
before the restart - the last saved snapshot rendered unchanged with zero
provider access. A refresh attempt in this state failed as expected
(`502 UPSTREAM_UNAVAILABLE`, `WinError 10061` - connection actively
refused), confirming the network really was cut and GET's success was not
a false negative. The backend was then restarted normally to restore live
operation.

## M5.6 - Model limitations and source gaps

Recorded in `README.md`'s Research Limitations section: no per-quote or
OI-as-of timestamps, no contract-multiplier or adjusted-contract field from
the source, spot has no time-of-day, `money=all` pagination extrapolated
(not directly sampled) for QQQ/AAPL specifically, and the endpoint itself
is an unofficial, owner-accepted-risk website URL, not the documented
authenticated API. Fixture mode remains explicitly synthetic and is not a
substitute for this live run.

## M5.7 - Extensibility documentation

Recorded in `README.md`'s "Adding a data source" section: the
`OptionsDataProvider` Protocol, the `source_mode` factory branch in
`backend/app.py`, the `StubProvider` boundary-test demonstration, the
manual-refresh-only policy, and the chain-only underlying-price rule.
