# NY Fed SOFR rate source contract (ADR-0001 M0.4)

## Current status (as of 2026-09-17)

**Technical verification: PASS.** Live response confirmed to match
ADR-0001 Section 6.1's documented contract exactly, field-for-field. This is
a public, unauthenticated New York Fed data API — no ToS risk judgment is
needed here the way the Nasdaq endpoints require (see
[dividend-source-contract.md](dividend-source-contract.md)).

Raw sample stored at `docs/samples/sofr-last5-2026-09-17.json`
(git-ignored; public data, but not committed to keep the pattern consistent
with the Nasdaq samples).

## Endpoint

```
GET https://markets.newyorkfed.org/api/rates/secured/sofr/last/5.json
```

No headers, authentication, or query parameters required.

## Confirmed response shape

```json
{
  "refRates": [
    {
      "effectiveDate": "2026-09-16",
      "type": "SOFR",
      "percentRate": 3.62,
      "percentPercentile1": 3.58,
      "percentPercentile25": 3.60,
      "percentPercentile75": 3.67,
      "percentPercentile99": 3.70,
      "volumeInBillions": 2931,
      "revisionIndicator": ""
    },
    ...
  ]
}
```

Confirmed against a real 5-observation response:

| Field | Confirmed shape |
|---|---|
| `effectiveDate` | ISO `YYYY-MM-DD` string |
| `type` | `"SOFR"` for every observation in the sample (no other `type` values were present to filter out, but the adapter's `type == "SOFR"` filter is retained defensively) |
| `percentRate` | JSON number, e.g. `3.62` (a percentage, not a decimal fraction) |
| `revisionIndicator` | an **empty string `""`**, not `null`, when there is no revision — confirmed live; `rates.py::_parse_observation` already accepts both `""` and `null` as valid (only rejects non-string, non-null) |
| `volumeInBillions`, `percentPercentile*` | present but unused by this adapter (not part of the ADR's normalization) |

Rows are returned most-recent-first (`2026-09-16` down to `2026-09-10` for
`last/5`); the adapter still sorts explicitly rather than relying on this.

## Normalization verified against a real value

Ran the live response through `rates.NyFedSofrProvider` and
`sofr_percent_to_rate_cc` directly (2026-09-17):

```
2026-09-16: percentRate=3.62 -> r_cc=0.036701
2026-09-15: percentRate=3.64 -> r_cc=0.036904
```

Matches Section 6.2's formula (`L = p/100`, `r_cc = 365*log1p(L/360)`)
exactly — this is the same formula already pinned against the ADR's
synthetic N2 example (`percentRate=4.0 -> r_cc=0.04055330263601719`) in
`tests/test_rates.py`; this section only confirms the *live response shape*
feeding that already-tested formula, not a new calculation path.

**Quote convention vs. normalization, kept separate per Section 6.2:** the
source's own convention is `percent_simple_act360` (a simple annualized
percentage, actual/360 money-market convention); this application's
normalization produces `constant_daily_sofr_proxy` (a flat continuously
compounded ACT/365F proxy). The two labels are recorded separately in
`ResolvedRate` and neither is presented as the other.

## Access

Public, unauthenticated, no API key. No rate limit was hit obtaining one
sample; the existing 5-second connect / 10-second read timeout and 2 MiB
response cap (`rates.py`) are applied regardless.
