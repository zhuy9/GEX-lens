# Nasdaq option-chain source contract

## Current status (as of 2026-09-16)

**Technical verification: PASS**, with one noted low-risk extrapolation:
pagination and strike-coverage under `money=all` were directly confirmed for
SPY only, not QQQ/AAPL specifically (see "Residual, optional follow-up"
below). Field paths and request-parameter behavior are confirmed for all
three symbols. This document records only what has been directly observed in
real samples — nothing here is inferred from field names or assumed from the
PRD.

**Access authorization: a personal risk judgment, not a granted permission.**
This is the website-backed URL flagged in PRD Section 2, not Nasdaq's
separate, documented, authenticated Options Chain API [S2]. The owner has
reviewed `nasdaq.com/legal` (current Sections 2 and 7) and personally accepts
the risk of a small number of manual, personal-use requests against this URL.
That is a risk decision the owner made for themself, not a claim that Nasdaq
has granted permission for this access. **Technical verification and access
authorization are two separate questions; the PASS above answers only the
first one.**

The raw samples referenced throughout this document are stored under
`docs/samples/`, which is git-ignored and must never be committed or
redistributed (Nasdaq data; the ToS question above is explicitly unresolved,
not decided in the owner's favor).

## Endpoint and confirmed request parameters

```
GET https://api.nasdaq.com/api/quote/{symbol}/option-chain
```

Confirmed query parameters, using the combination this app actually sends
(`money=all`, not the initially-tried `money=at` — see the appendix for why):

| Parameter | Confirmed value/behavior |
|---|---|
| `assetclass` | `stocks` for AAPL, `etf` for SPY/QQQ — a per-symbol mapping the app must supply |
| `limit` | Real page-size cap on the flat `rows` array; 1000 confirmed to work |
| `offset` | Real, non-duplicating pagination; `offset >= totalRecord` is a valid (if unneeded) stop condition |
| `fromdate` / `todate` | Real, honored filters — `totalRecord` shrinks/grows with the requested window |
| `excode` | `oprac` used throughout; not independently varied |
| `callput` | `callput` (both sides) used throughout |
| `money` | `all` required to cover the PRD's 0.80x-1.20x strike band; `at` ("near the money") does not |
| `type` | `all` used throughout |

No auth header was used to obtain any sample. Verification date: 2026-09-16.

## Confirmed field paths

### Underlying price (spot)

- Path: `data.lastTrade`
- Type: a free-text string, **not** a numeric field:
  `"LAST TRADE: $332.41 (AS OF SEP 16, 2026)"`
- Parse with e.g. `^LAST TRADE: \$([\d,]+(?:\.\d+)?) \(AS OF (.+)\)$` to get
  `price = "332.41"`, `date_text = "SEP 16, 2026"`.
- **Update 2026-09-17 (ADR-0001 M6.4 live verification):** a whole-dollar
  price omits the decimal entirely -- confirmed live, AAPL traded at exactly
  `"LAST TRADE: $337 (AS OF SEP 17, 2026)"`, no `.00`. The original regex
  above required a decimal point and rejected this live response as
  `INVALID_UNDERLYING_PRICE`; the fractional part is optional, not always
  present (fixed in `nasdaq.py::_LAST_TRADE_RE`).
- The `(AS OF ...)` suffix is a **calendar date only, no time-of-day**. Per
  PRD Section 4.2, a date-only source value must not be converted into an
  invented midnight timestamp, so `spot_asof` must stay `null` for this
  source — do not synthesize a time. This also means the `MISALIGNED_TIMESTAMPS`
  comparison in PRD 5.2 will never have a spot-side timestamp to compare
  against for this source; expect `TIMESTAMP_ALIGNMENT_UNKNOWN` in practice.
- Confirmed structurally distinct from every option row's `c_Last` / `p_Last`
  (per-contract last-traded premium) — different JSON path entirely, so the
  parser cannot accidentally read a premium as spot.
- Confirmed for all three symbols: AAPL 332.41, SPY 754.05, QQQ 704.72.

### Chain-level timestamp

- Path: `data.table.asOf`
- Observed value: `null` in every sample collected. Unverified whether it is
  ever populated, or what format it would take if it were. Must remain
  `null`/unused when absent, and a provider must not guess at accepting any
  particular non-null shape without first re-verifying it against a real
  populated example (see `nasdaq.py::_parse_chain_asof`'s handling of this).

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
  - `strike`: numeric string, e.g. `"245.00"`, or `"1,000.00"` with a comma
    separator once strikes reach four digits — confirms PRD 5.4's
    comma/currency-stripping requirement is load-bearing, not defensive.
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
  - **Pagination note**: a continuation page can start mid-expiration with
    zero header rows in sight (`groups: []`) even though it isn't the first
    page of that expiration. `nasdaq.py` must carry the "current expiration"
    as state across paginated requests, not re-derive it from each page in
    isolation.

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

- `status.rCode` (`200` in every sample collected) lives **inside** the JSON
  body alongside the HTTP status code. `status.bCodeMessage` /
  `status.developerMessage` were both `null` on every success response seen.
  Their shape on failure (rate limit, invalid symbol, access denial) is
  **unverified** — `nasdaq.py`'s error mapping for those cases is a
  defensive default (unmatched codes → a 502 upstream-failure response), not
  independently confirmed against a real failure response.
- `data.totalRecord` is the exact total row count (headers + data) for the
  filtered result — identical across every page of one logical snapshot, and
  shrinks/grows with the `fromdate`/`todate` window. `offset >= totalRecord`
  is a valid stop condition, though not needed in practice since a short
  final page (fewer rows than `limit`) reliably signals the same thing —
  both were directly confirmed together against a real 1906-row SPY
  response (page 1: 1000 rows; page 2, `offset=1000`: 906 rows, no overlap,
  no gap, `1000 + 906 = 1906 = totalRecord` on both pages).
- `data.filterlist` is UI dropdown metadata for Nasdaq's own site; irrelevant
  to parsing.
- `data.table.headers` is display-label metadata only; irrelevant to parsing.

### Rejected candidate: `/info` endpoint (do not use)

`GET https://api.nasdaq.com/api/quote/{symbol}/info?assetclass=etf` is a
**separate** Nasdaq endpoint (`docs/samples/spy-info.jsonc`,
`docs/samples/qqq-info.jsonc`) returning `primaryData.lastSalePrice`
(`"$754.05"` for SPY, `"$704.72"` for QQQ — matching `data.lastTrade` from
the option-chain endpoint exactly, a useful one-time cross-check). **This
must never be called by `nasdaq.py`.** PRD Section 5.2 explicitly prohibits
a separate quote/spot endpoint; the underlying price must come only from the
option-chain response's `data.lastTrade` field.

## M0 acceptance status

| ID | Status | Notes |
|---|---|---|
| M0.1 | Met | Endpoint, full query-param set (`assetclass`, `limit`, `offset`, `fromdate`, `todate`, `excode`, `callput`, `money`, `type`), and verification date (2026-09-16) recorded. Access basis is the owner's personal-use judgment call on the website-backed URL, not the documented authenticated API. |
| M0.2 | Met | Real samples for all three symbols; `assetclass=stocks` (AAPL) vs. `assetclass=etf` (SPY/QQQ) confirmed. Full-band pagination directly confirmed for SPY; QQQ/AAPL confirmed for field paths and `money=at` mechanics, extrapolated (not directly sampled) for `money=all` specifically. |
| M0.3 | Met | Pagination fully confirmed: `offset`/`limit` walk forward with zero duplication, `totalRecord` is the exact filtered row total, a short final page reliably signals end-of-data, and cross-page expiration continuation is byte-clean. `money=all` covers the required 0.80x-1.20x band. |
| M0.4 | Met | `data.lastTrade` (spot) and `c_Last`/`p_Last` (premiums) are structurally distinct paths; exact locations recorded above. OI-date and multiplier gaps recorded above. |
| M0.5 | **PASS** | All prerequisite criteria met; see the one noted low-risk extrapolation in "Current status" above. This PASS is a technical-verification finding only — see "Current status" for why it does not, by itself, establish access authorization. |
| M0.6 | Met | First-page `data.lastTrade` yields a finite positive price for all three symbols (AAPL 332.41, SPY 754.05, QQQ 704.72) with no separate quote call. |

## Residual, optional follow-up (not blocking)

1. Re-run the `money=all` + large-`limit` test against QQQ or AAPL for full
   symbol-by-symbol certainty (currently extrapolated from SPY — the
   mechanism itself is server-side and symbol-agnostic, but this remains an
   extrapolation, not a direct observation, for those two symbols
   specifically).
2. One error-case response (invalid symbol, or a too-fast retry) to see the
   real shape of `status.rCode` / `bCodeMessage` on failure — needed before
   `nasdaq.py`'s error mapping can be considered verified end-to-end, though
   the mapping itself already fails safely (unmatched codes default to a
   502 upstream-failure response).

## Appendix: investigation history (2026-09-16, chronological, dated)

The sections below are the original, unedited evidence trail from the M0
investigation, preserved in the order the findings actually happened —
including dead ends and open questions at the time they were open. "Current
status" above is the resolved, current summary of this same evidence; this
appendix exists so every claim above can be traced back to the specific
sample and reasoning that established it. Do not edit these sections to
"clean up" a since-resolved uncertainty — that would remove the evidentiary
value of showing what was actually verified, and when.

### Initial AAPL sample: `limit` truncates mid-chain, pagination unverified

The first sample was fetched with `assetclass=stocks&limit=60`. Counting the
actual response:

- `data.table.rows` contains **exactly 60 entries** (2 group-header rows + 58
  data rows).
- `data.totalRecord` reports **421** total contract rows for AAPL, across all
  future expirations — the `fromdate` filter's dropdown lists expirations out
  to January 2029.
- There is **no cursor, offset, or "next page" field anywhere** in the
  response body.

So `limit` truncates the flat `rows` array at exactly N entries, cutting off
mid-expiration (the second expiration group, Sep 18 2026, is visibly
incomplete — only 11 of its strikes are present before the array ends). At
this point in the investigation, it was not yet known:

1. Whether raising `limit` (e.g. to 1000+) just returns more rows in one call
   up to `totalRecord`, or whether the server caps it lower.
2. Whether `fromdate` / `todate` are real, honored request parameters — their
   pipe-separated values only appeared as `filterlist` dropdown *option
   values* in this response, never yet confirmed as accepted request
   parameters.
3. Whether a large `limit` also pulls in expirations far beyond the 60-day
   DTE window this app needs, wasting the 10 MiB / 10,000-contract collection
   caps in PRD 5.2 — a `fromdate`/`todate`-scoped request looked like it
   might be the better design even if a large `limit` "worked."

All three are resolved below.

### SPY and QQQ samples: `money=at` doesn't cover the required strike band

Fetched with:

```
GET https://api.nasdaq.com/api/quote/{symbol}/option-chain?assetclass=etf&limit=60&fromdate=2026-09-16&todate=2026-09-30&excode=oprac&callput=callput&money=at&type=all
```

Stored at `docs/samples/spy-option-chain.json` and `docs/samples/qqq-option-chain.json`.

- `assetclass=etf` for SPY/QQQ vs. `assetclass=stocks` for AAPL — confirmed
  the request needs a per-symbol asset-class parameter (M0.2).
- Both samples confirmed the same field paths recorded for AAPL
  (`data.lastTrade`, row shape, `drillDownURL` call-only identifier, etc.).
  SPY: spot `754.05`. QQQ: spot `704.72`.
- `fromdate`/`todate` appeared to have *some* effect here — both responses
  showed only **one** expiration group before `limit=60` cut off, rather
  than jumping across years like the unscoped AAPL request did. (Later
  confirmed as real, honored parameters below.)

`money=at` ("Near the Money") does **not** cover the PRD's required
`0.80 * spot <= strike <= 1.20 * spot` band:

| Symbol | Spot | Required band (0.80x-1.20x) | `money=at` actual range | Covers required band? |
|---|---|---|---|---|
| SPY | 754.05 | 603.24 - 904.86 | 680 - 768 | **No** |
| QQQ | 704.72 | 563.78 - 845.66 | 635 - 693 | **No** |

SPY's near-the-money strikes also showed the increment pattern: $5 farther
from spot (680, 685, 690, ...), narrowing to $1 right around spot (718-768).
A full 0.80x-1.20x band at that same pattern would need roughly 90-110
strike rows per expiration (not 59), and this MVP needs that across every
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
  (somewhere between offset 59 and 240, not yet fetched at this point). This
  is the origin of the "parser must carry expiration state across pages"
  requirement recorded above under "Confirmed field paths."

`offset`/`limit` as a real pagination mechanism was confirmed at this point.
Still open:

1. Whether `offset`/`limit` pagination behaves the same way once `money=at`
   is replaced with `money=all` (the combination this app actually uses, per
   the strike-scope decision above) — not yet tested at this point.
2. Whether `totalRecord` represents the count *after* the `money`/`fromdate`/
   `todate` filters are applied (i.e., a reliable "keep paginating until
   `offset >= totalRecord`" stop condition) or an unfiltered total — not yet
   distinguished at this point, since both samples so far used identical
   filter params.
3. What a final/short page looks like (fewer than `limit` rows, or an empty
   `rows` array) — needed to detect "end of chain" without guessing.

All three are resolved below.

### CONFIRMED: `money=all` + large `limit` fully resolves pagination

Fetched SPY with
`assetclass=etf&limit=1000&offset=0&money=all&fromdate=2026-09-16&todate=2026-09-30&excode=oprac&callput=callput&type=all`,
then the same with `offset=1000`. Saved at
`docs/samples/spy-money-all-offset0.json` and `...-offset1000.json`.

- **`money=all` covers the required 0.80x-1.20x band.** Of the 6 expirations
  in page 1, 5 fully covered it (e.g. Sep 17: strikes 550-950 against a
  required 603.24-904.86); the 6th was simply mid-page when the 1000-row cap
  hit, not a coverage failure.
- **Strikes reach 4 digits with comma separators** (`"1,000.00"`) once the
  full band is in play — confirms PRD 5.4's comma-stripping requirement,
  which the earlier `money=at` samples never actually exercised (all
  strikes there were under 1000).
- **`totalRecord` is the exact total row count (headers + data) for the
  filtered result, not an unfiltered/all-future-dates total.** Page 1 had
  1000 rows, page 2 (`offset=1000`) had exactly 906, and `1000 + 906 = 1906
  = totalRecord` on both pages. This means `offset >= totalRecord` is a
  valid, confirmed stop condition — not needed in practice, since the short
  page below already signals the same thing.
- **A short final page (906 rows < the 1000 `limit`) is the real end-of-data
  signal.** `nasdaq.py`'s "a short page ends pagination" logic — previously
  a defensive, unverified assumption — was confirmed here empirically, not
  just inferred from REST convention.
- **Cross-page continuation is byte-clean.** Sep 24's group split across the
  page boundary with no repeated header (page 1: strikes 550-736; page 2:
  737-950, contiguous, zero overlap, zero gap) — exactly matching the
  expiration-carry-over state `nasdaq.py` already implements.
- **`fromdate`/`todate` are real, honored parameters**, not inert dropdown
  metadata: the 10 expirations covered (Sep 17-30) line up exactly with the
  requested 2-week window, and `totalRecord` shrinks/grows with that window
  (1906 here vs. AAPL's unscoped 421-across-years earlier).

Residual, low-risk gap (unchanged as of "Current status" above): this was
only run against **SPY**. QQQ and AAPL were confirmed under `money=at` and
the general field-path/pagination mechanism, but not specifically re-run
under `money=all` with a large `limit`. Since the mechanism (pagination,
`totalRecord` semantics, comma parsing) is server-side and symbol-agnostic,
this is expected to hold identically — but it remains an extrapolation, not
a direct observation, for those two symbols specifically.
