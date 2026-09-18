# Nasdaq dividend-history source contract (ADR-0001 M0)

## Current status (as of 2026-09-17)

**Technical verification: PASS for AAPL and QQQ. FAIL (confirmed unavailable) for SPY.**
This document records only what has been directly observed in real samples —
nothing here is inferred from field names or assumed from the ADR's illustration.

**Access authorization: a personal risk judgment, not a granted permission.**
Same URL family as the already-reviewed option-chain endpoint
(`api.nasdaq.com/api/quote/{symbol}/...`), covered by the owner's existing
review of `nasdaq.com/legal` (current Sections 2 and 7) and personal
acceptance of the risk of a small number of manual, personal-use requests.
That is a risk decision the owner made for themself, not a claim that Nasdaq
has granted permission for this specific endpoint. **Technical verification
and access authorization are two separate questions; the PASS above answers
only the first.**

The raw samples referenced throughout this document are stored under
`docs/samples/` (git-ignored, never committed or redistributed).

## Endpoint

```
GET https://api.nasdaq.com/api/quote/{symbol}/dividends?assetclass={assetclass}
```

Same headers as the option-chain adapter (`User-Agent`, `Accept`); no
authentication used to obtain any sample.

## Per-symbol matrix (M0.3)

| Symbol | assetclass sent | Result | Status |
|---|---|---|---|
| AAPL | `stocks` | 83 real rows, back to 1988 | **Nasdaq-dividend verified** |
| QQQ | `etf` | 57 real rows, back to ~2012 | **Nasdaq-dividend verified** |
| SPY | `etf` | `rCode: 200`, `dividends.rows: null`, `message: "Dividend History for Non-Nasdaq symbols is not available"` | **Blocked — confirmed unavailable, not merely unverified** |

**AAPL's success does not establish SPY or QQQ's support** — confirmed
directly: QQQ (Nasdaq-100-tracking, itself Nasdaq-listed) has real coverage,
while SPY (NYSE Arca-listed, despite being an equally liquid ETF) is
explicitly rejected by this endpoint's own success-shaped envelope. Do not
copy the chain endpoint's stock/ETF `assetclass` mapping into the dividend
adapter's *symbol allowlist* without this per-symbol check — the parameter
mapping is not what gates coverage here; the security's primary listing
exchange is.

`dividend_sources` per Section 5.2 must therefore be:

```json
{"SPY": "manual_schedule", "QQQ": "nasdaq_dividends", "AAPL": "nasdaq_dividends"}
```

SPY has no live-adapter path available at all pending a data source
change — `manual_schedule` is not a temporary placeholder for SPY, it is
the only option this source supports.

## Response envelope and failure shape

Success (AAPL/QQQ):

```json
{
  "data": {
    "dividendHeaderValues": [...],
    "exDividendDate": "08/10/2026",
    "dividends": {
      "asOf": null,
      "headers": {"exOrEffDate": "...", "type": "...", "amount": "...",
                   "declarationDate": "...", "recordDate": "...", "paymentDate": "..."},
      "rows": [ /* see below */ ]
    }
  },
  "message": null,
  "status": {"rCode": 200, "bCodeMessage": null, "developerMessage": null}
}
```

Unavailable (SPY, and presumably any other non-Nasdaq-listed symbol):

```json
{
  "data": {
    "dividendHeaderValues": [{"label": "Ex-Dividend Date", "value": "N/A"}, ...],
    "dividends": {"asOf": null, "headers": null, "rows": null}
  },
  "message": "Dividend History for Non-Nasdaq symbols is not available",
  "status": {"rCode": 200, "bCodeMessage": null, "developerMessage": null}
}
```

`status.rCode == 200` in **both** cases — the adapter must check
`data.dividends.rows` for `null` explicitly and treat it as
`DIVIDEND_COVERAGE_UNVERIFIED` (this symbol has no coverage from this
source), never as a valid empty schedule. The existing chain adapter's
`rCode`-based failure check (`nasdaq.py::_parse_body`) is insufficient here
by itself; this endpoint's own failure shape is a "successful" envelope
with a null data payload and a human-readable `message`.

## Confirmed field paths (`data.dividends.rows[]`)

| Field | Confirmed shape | Missing-value sentinel |
|---|---|---|
| `exOrEffDate` | `MM/DD/YYYY` string, e.g. `"08/10/2026"` | never observed missing |
| `type` | `"Cash"` in every observed row (AAPL, QQQ) | n/a — no other value observed; treat any other string as unsupported (kind=`other`), not silently as ordinary cash |
| `amount` | `"$" + digits + "." + 2-5 digits`, e.g. `"$0.27"`, `"$0.81349"` | never observed missing; do not assume exactly 2 decimal places |
| `declarationDate` | `MM/DD/YYYY` string | `"N/A"` — AAPL: missing on 29/83 rows (older history); QQQ: missing on 56/57 rows (declaration date is essentially never populated for this ETF) |
| `recordDate` | `MM/DD/YYYY` string | never observed missing |
| `paymentDate` | `MM/DD/YYYY` string | `"N/A"` — AAPL: missing on the same 29 rows as declarationDate; QQQ: never missing |
| `currency` | `"USD"` in every observed row | n/a |

This is the **ex-date** (`exOrEffDate` — "Ex/EFF Date" per the response's own
`headers.exOrEffDate` label), confirmed distinct from `recordDate` and
`paymentDate`. Rows are returned most-recent-first; no pagination parameters
were needed or used — AAPL's full 83-row history (back to 1988) and QQQ's
57-row history each returned complete in one request, under the existing
2 MiB response cap (largest observed: 13.3 KB).

`data.dividends.asOf` was `null` in every sample (AAPL, QQQ, and SPY's
null-payload case) — unverified whether it is ever populated; treat as
always absent per the same rule as the chain endpoint's `data.table.asOf`.

## Future coverage

Both AAPL's and QQQ's most-recent row was already in the past relative to
the capture date (AAPL: ex-date 2026-08-10, captured 2026-09-17) — **this
endpoint returns history only, never an upcoming/declared-but-not-yet-ex
event**. This directly confirms the ADR's Section 1.1 caution: this source
cannot supply a forward dividend schedule on its own, and `ScheduleReview`'s
owner-reviewed coverage remains mandatory even for a "verified" symbol.

## Distribution type and security identity

Only `type: "Cash"` was observed for AAPL and QQQ; no special, stock, or
preferred distribution rows were present in either sample to verify against.
The adapter must still guard for a non-`"Cash"` type defensively (map to
`kind="other"`, excluded from automatic pricing per Section 7.2) since this
is a real possibility for other symbols/history, not something this sample
set rules out.

Security identity is established only by the request URL's `{symbol}` path
segment and the corresponding `assetclass` parameter — the response body
does not echo back a ticker or CIK to cross-check against. A mismatched
symbol/assetclass pairing was not tested; the existing chain adapter's
verified `assetclass` mapping (`stocks` for AAPL, `etf` for SPY/QQQ) is
reused for the *parameter value* only, not as a stand-in for the coverage
check above.

## Access

No authentication, API key, or session cookie was used or required. Subject
to the same `nasdaq.com/legal` risk-acceptance as the chain endpoint (see
"Access authorization" above) — not re-litigated per symbol.
