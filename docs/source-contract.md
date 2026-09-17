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
| M0.2 | Partial | Samples obtained for all three symbols; `assetclass=stocks` (AAPL) vs. `assetclass=etf` (SPY/QQQ) confirmed. Full-chain coverage per symbol still unverified (see M0.3). |
| M0.3 | Not met | Field paths are recorded for all three symbols, but complete-pagination behavior is unverified, and `money=at` was found to violate the required 0.80x-1.20x strike scope (see below). Retest with `money=all`/no filter and a larger `limit` pending. |
| M0.4 | Met for AAPL | `data.lastTrade` (spot) and `c_Last`/`p_Last` (premiums) are structurally distinct paths; exact locations recorded above. OI-date and multiplier gaps recorded above. |
| M0.5 | BLOCKED | Cannot assign PASS while M0.2/M0.3 are open; PRD disallows representing incomplete verification as PASS. |
| M0.6 | Met for AAPL only | First-page `data.lastTrade` yields a finite positive price (332.41) with no separate quote call. SPY/QQQ unconfirmed. |

## Needed to close M0

1. ~~SPY and QQQ samples from the same endpoint~~ — done, see below.
2. Confirm whether `fromdate`/`todate` reliably return every expiration in
   range once `limit` is large enough (see below — inconclusive so far).
3. Ideally, one error-case response (invalid symbol, or a too-fast retry) to
   see the real shape of `status.rCode` / `bCodeMessage` on failure.

## SPY and QQQ samples (2026-09-16)

Fetched with:

```
GET https://api.nasdaq.com/api/quote/{symbol}/option-chain?assetclass=etf&limit=60&fromdate=2026-09-16&todate=2026-09-30&excode=oprac&callput=callput&money=at&type=all
```

Stored at `docs/samples/spy-option-chain.json` and `docs/samples/qqq-option-chain.json` (git-ignored).

- `assetclass=etf` for SPY/QQQ vs. `assetclass=stocks` for AAPL — confirms
  the request needs a per-symbol asset-class parameter (M0.2). This app
  would need to know each configured symbol's asset class; not yet decided
  where that mapping lives.
- Both samples confirm the same field paths recorded above for AAPL
  (`data.lastTrade`, row shape, `drillDownURL` call-only identifier, etc.).
  SPY: spot `754.05`. QQQ: spot `704.72`.
- `fromdate`/`todate` appear to have *some* effect — both responses show
  only **one** expiration group (`September 16, 2026`) before `limit=60`
  cuts off, rather than jumping across years like the unscoped AAPL request
  did. But since `limit=60` is also small enough to be the actual cause, this
  is not yet confirmed as a real, honored parameter — needs a retest with a
  larger `limit` to see if a second in-range expiration (e.g. Sep 18) shows
  up.

### Conflict with PRD Section 3's fixed strike scope

`money=at` ("Near the Money") does **not** cover the PRD's required
`0.80 * spot <= strike <= 1.20 * spot` band:

| Symbol | Spot | Required band (0.80x-1.20x) | `money=at` actual range | Covers required band? |
|---|---|---|---|---|
| SPY | 754.05 | 603.24 - 904.86 | 680 - 768 | **No** |
| QQQ | 704.72 | 563.78 - 845.66 | 635 - 693 | **No** |

SPY's near-the-money strikes also show the increment pattern: $5 farther from
spot (680, 685, 690, ...), narrowing to $1 right around spot (718-768). A
full 0.80x-1.20x band at that same pattern would need roughly 90-110 strike
rows per expiration (not 59), and this MVP needs that across every
expiration in the 1-60 DTE window (SPY/QQQ typically have same-day, Mon/Wed/Fri
weekly, and monthly expirations, so several per symbol) — likely several
hundred rows per symbol, not 100. This is well within the PRD's 10-request /
10 MiB / 10,000-contract collection caps if paginated with a large `limit`
per request and `money=all` (or `money` omitted), but not with `money=at`.

**Decision (2026-09-16, owner):** keep the PRD's 0.80x-1.20x band. Use
`money=all` (or omit `money`) and raise `limit` substantially (test 1000+)
instead of `money=at`.

### Pagination: `offset` is real and confirmed non-duplicating

Fetched `.../SPY/option-chain?assetclass=etf&limit=60&offset=240&fromdate=2026-09-16&todate=2026-09-30&excode=oprac&callput=callput&money=at&type=all`,
saved at `docs/samples/spy-option-chain-offset240.json`. Compared against the
`offset=0` page (`spy-option-chain.json`) by `drillDownURL`, not just strike
number:

- **Zero overlapping `drillDownURL` values** between the two pages — this is
  not a repeated/duplicate page.
- `totalRecord` (1255) and `lastTrade` (spot, unchanged) are identical across
  pages, as expected for one logical snapshot.
- The `offset=240` page's rows are all `expiryDate: "Sep 18"`, a **different**
  expiration than page 1's `"Sep 16"`. Its strike range (708-767) looks
  similar to page 1's Sep 16 range (680-768) only because "near the money" is
  relative to the same current spot price, regardless of expiration — not
  because the data repeats.
- **This page has no expiration-group header row at all** (`groups: []`),
  even though it lands mid-way through the Sep 18 group. The header row only
  appears once, on whichever earlier page first reaches that expiration
  (somewhere between offset 59 and 240, not yet fetched). **Parser
  consequence: `nasdaq.py` must carry the "current expiration year" as
  state across paginated requests**, not re-derive it from each page in
  isolation — a page can start mid-expiration with no header in sight.

`offset`/`limit` as a real pagination mechanism is now confirmed. What is
still open:

1. Whether `offset`/`limit` pagination behaves the same way once `money=at`
   is replaced with `money=all` (the combination this app will actually use,
   per the strike-scope decision above) — not yet tested.
2. Whether `totalRecord` represents the count *after* the `money`/`fromdate`/
   `todate` filters are applied (i.e., a reliable "keep paginating until
   `offset >= totalRecord`" stop condition) or an unfiltered total — not yet
   distinguished, since both samples so far used identical filter params.
3. What a final/short page looks like (fewer than `limit` rows, or an empty
   `rows` array) — needed to detect "end of chain" without guessing.

### Next verification needed

One more SPY (or QQQ) sample using `money=all` (or the `money` param
dropped entirely) with a large `limit` (try 1000), `offset=0`, still scoped
by `fromdate`/`todate`, to confirm the full 0.80x-1.20x strike band appears
for the first expiration in a single request under the filter combination
this app will actually ship with.
