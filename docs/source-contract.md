# Nasdaq option-chain source contract

Status: **BLOCKED (partial verification)**. This document records only what has
been directly observed in a real sample. Nothing here is inferred from field
names or assumed from the PRD.

## Endpoint

```
GET https://api.nasdaq.com/api/quote/{symbol}/option-chain?assetclass=stocks&limit=60
```

- This is the website-backed URL flagged in PRD Section 2, not Nasdaq's
  separate documented authenticated Options Chain API [S2].
- No auth header was used to obtain the sample below.
- Access basis: the owner has reviewed `nasdaq.com/legal` (current Sections 2
  and 7) and personally accepts the risk of a small number of manual,
  personal-use requests against this URL. This is not a claim that Nasdaq has
  granted permission.
- Verification date: 2026-09-16.
- Verified symbols so far: **AAPL only**. SPY and QQQ are not yet verified
  (blocks M0.2).

The raw sample is stored at `docs/samples/aapl-option-chain.json`, which is
git-ignored and must never be committed or redistributed (Nasdaq data, ToS
question unresolved).

## Confirmed field paths (AAPL, 2026-09-16)

### Underlying price (spot)

- Path: `data.lastTrade`
- Type: a free-text string, **not** a numeric field:
  `"LAST TRADE: $332.41 (AS OF SEP 16, 2026)"`
- Parse with e.g. `^LAST TRADE: \$([\d,]+\.\d+) \(AS OF (.+)\)$` to get
  `price = "332.41"`, `date_text = "SEP 16, 2026"`.
- The `(AS OF ...)` suffix is a **calendar date only, no time-of-day**. Per
  PRD Section 4.2, a date-only source value must not be converted into an
  invented midnight timestamp, so `spot_asof` must stay `null` for this
  source — do not synthesize a time. This also means the `MISALIGNED_TIMESTAMPS`
  comparison in PRD 5.2 will never have a spot-side timestamp to compare
  against for this source; expect `TIMESTAMP_ALIGNMENT_UNKNOWN` in practice.
- Confirmed structurally distinct from every option row's `c_Last` / `p_Last`
  (per-contract last-traded premium) — different JSON path entirely, so the
  parser cannot accidentally read a premium as spot.

### Chain-level timestamp

- Path: `data.table.asOf`
- Observed value: `null` in this sample. Unverified whether it is ever
  populated. Must remain `null` when absent per PRD.

### Option rows

- Path: `data.table.rows[]` — a **flat array mixing two row shapes**:

  **Group header row** (marks the start of a new expiration date):
  `expirygroup` holds a full date string with the year, e.g.
  `"September 16, 2026"`; every other field is `null`. Must be dropped per
  PRD 5.4 ("Remove expiration-heading rows"), but its year must be carried
  forward — see below.

  **Data row**: `expirygroup` is `""` (empty string, not null).
  - `expiryDate`: short form **with no year**, e.g. `"Sep 16"`, `"Sep 18"`.
    Must be combined with the most recently seen group-header's year, or
    cross-checked against `drillDownURL` (see below).
  - `strike`: numeric string, e.g. `"245.00"`. No thousands separator or
    currency symbol observed at these strike levels; keep PRD 5.4's
    comma/currency stripping anyway for higher-priced names.
  - `c_Last`, `c_Change`, `c_Bid`, `c_Ask`, `c_Volume`, `c_Openinterest`:
    call-side fields. Numeric strings, or the sentinel `"--"` for missing
    (maps to `null` per PRD 5.4).
  - `p_Last`, `p_Change`, `p_Bid`, `p_Ask`, `p_Volume`, `p_Openinterest`:
    symmetric put-side fields.
  - `c_colour` / `p_colour`: booleans. Appear to flag in/out-of-the-money
    highlighting for Nasdaq's own UI. Not authoritative moneyness (compute
    independently from strike vs. spot) and not part of the canonical
    `OptionQuote` model.
  - `drillDownURL`: e.g.
    `"/market-activity/stocks/aapl/option-chain/call-put-options/aapl--260916c00245000"`.
    Encodes symbol, `YYMMDD` expiration (`260916` = 2026-09-16), a single
    option-type letter, and strike × 1000 zero-padded to 8 digits
    (`00245000` = 245.000).
    - **Only ever encodes the call side (`c`) in every row observed.** There
      is no equivalent identifier field for the put contract.
    - Good cross-check for the full expiration date (including year).
    - Decision for `provider_contract_id`: populate it for calls (parsed from
      `drillDownURL`), leave it `null` for puts. Do not invent a put ID by
      string-substituting `c` → `p` — Nasdaq never actually returns that
      value.

### Fields never present in this payload

- No per-row quote timestamp — `quote_asof` stays `null` for every contract
  from this source.
- No open-interest-as-of date — `oi_asof` stays `null`; OI freshness is
  unknown from this endpoint.
- No contract multiplier field — every contract needs `MULTIPLIER_ASSUMED`
  per PRD 5.4's 100-share assumption.
- No explicit nonstandard/adjusted-contract flag — the source gives no signal
  to detect adjusted deliverables. This is a real coverage gap the parser
  cannot compensate for; record it, don't paper over it.

### Envelope / status

- `status.rCode` (`200` here) lives **inside** the JSON body alongside the
  HTTP status code. `status.bCodeMessage` / `status.developerMessage` were
  both `null` on this success response. Their shape on failure (rate limit,
  invalid symbol, access denial) is **unverified** — needed before
  `nasdaq.py`'s error mapping can be trusted.
- `data.filterlist` is UI dropdown metadata for Nasdaq's own site; irrelevant
  to parsing.
- `data.table.headers` is display-label metadata only; irrelevant to parsing.

## Critical open finding: pagination is unverified, and `limit` truncates mid-chain

The sample was fetched with `assetclass=stocks&limit=60`. Counting the actual
response:

- `data.table.rows` contains **exactly 60 entries** (2 group-header rows + 58
  data rows).
- `data.totalRecord` reports **421** total contract rows for AAPL, across all
  future expirations — the `fromdate` filter's dropdown lists expirations out
  to January 2029.
- There is **no cursor, offset, or "next page" field anywhere** in the
  response body.

So `limit` truncates the flat `rows` array at exactly N entries, cutting off
mid-expiration (the second expiration group, Sep 18 2026, is visibly
incomplete — only 11 of its strikes are present before the array ends). Not
yet known:

1. Whether raising `limit` (e.g. to 1000+) just returns more rows in one call
   up to `totalRecord`, or whether the server caps it lower.
2. Whether `fromdate` / `todate` are real, honored request parameters — their
   pipe-separated values currently only appear as `filterlist` dropdown
   *option values* in the response, never confirmed as accepted request
   parameters.
3. Whether a large `limit` also pulls in expirations far beyond the 60-day
   DTE window this app needs, wasting the 10 MiB / 10,000-contract collection
   caps in PRD 5.2 — a `fromdate`/`todate`-scoped request may be the better
   design even if a large `limit` "works."

**M0.2 and M0.3 are not satisfied.** PRD Section 2 explicitly forbids
inventing "pagination behavior," so this stays open rather than guessed.

## M0 acceptance status (interim)

| ID | Status | Notes |
|---|---|---|
| M0.1 | Partial | Endpoint and one query-param combination recorded; verification date 2026-09-16. Access basis is the owner's personal-use judgment call on the website-backed URL, not the documented authenticated API. |
| M0.2 | Not met | Only AAPL verified. SPY and QQQ samples not yet provided. |
| M0.3 | Not met | Field paths for the visible 58 rows are recorded, but complete-pagination behavior is unverified (see above). |
| M0.4 | Met for AAPL | `data.lastTrade` (spot) and `c_Last`/`p_Last` (premiums) are structurally distinct paths; exact locations recorded above. OI-date and multiplier gaps recorded above. |
| M0.5 | BLOCKED | Cannot assign PASS while M0.2/M0.3 are open; PRD disallows representing incomplete verification as PASS. |
| M0.6 | Met for AAPL only | First-page `data.lastTrade` yields a finite positive price (332.41) with no separate quote call. SPY/QQQ unconfirmed. |

## Needed to close M0

1. SPY and QQQ samples from the same endpoint (same query shape is fine).
2. One more AAPL sample with a much larger `limit` (e.g. `limit=3000`) to
   check whether the full 421-record chain returns in one call, or whether
   the server caps it below that.
3. If (2) still truncates: a sample using explicit `fromdate`/`todate` query
   parameters, to test whether they are real, honored request parameters.
4. Ideally, one error-case response (invalid symbol, or a too-fast retry) to
   see the real shape of `status.rCode` / `bCodeMessage` on failure.
