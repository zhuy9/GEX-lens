# ADR-0001: Traceable pricing inputs, cash dividends, and comparable GEX units

- **Status:** Proposed implementation decision; not an assertion that the changes are implemented.
- **Date:** 2026-09-17.
- **Project:** GEX Lens (`zhuy9/GEX-lens`).
- **Baseline reviewed:** `7137567b8b4faf79ad9a47f69517a731617b0290`.
- **Audience:** Coding agent implementing the next increment after the original MVP.
- **Suggested repository path:** `docs/adr/0001-pricing-inputs-dividends-and-gex-units.md`.
- **Precedence:** Once accepted by the owner, this ADR overrides only the PRD provisions listed in Section 3. Everything else remains in force.

## 1. Problem and reason for this decision

The application can render a correct-looking heatmap while using unsuitable pricing assumptions or displaying a different exposure unit from another application. A visually similar result is not a correctness test.

The current pipeline solves implied volatility from each option's bid/ask midpoint, calculates gamma, then weights gamma by open interest. Therefore, changing the interest rate or dividend model requires solving IV again. It is incorrect to change only the final GEX multiplier or to reuse IV inferred under different pricing assumptions. [R1]

The repository's example configuration uses `risk_free_rate=0.04` and zero dividend yields. It does **not** establish that the user's running snapshot used those values. Saved `parameters.r` and `parameters.q` are the authoritative inputs for an existing calculation. The problem is undocumented or stale assumptions, not the mere existence of configuration values. [R2]

The observed comparison also involves separate issues:

| Issue | Why it needs its own treatment |
|---|---|
| Exposure unit | Per-$1 and per-1%-move delta-notional exposures differ by `0.01 * actual_spot`, even with identical gamma and OI. QuantWheel documents a formula selector that typically uses per-$1. [E1] |
| Interest rate | A fixed placeholder does not identify the observation date, quote convention, or discounting assumption. |
| Dividends | A continuous annual yield spreads distributions through time. An upcoming cash distribution is a dated event. Its ex-date determines whether it affects an option's remaining life. |
| Exercise model | Standard equity/ETF options permit early exercise. Adding cash dividends to European BSM does not turn it into an American-option model. [E2][E3] |
| Instrument and timestamps | SPY is not SPX. Similar strikes or exposure patterns do not establish matching instruments, snapshots, or contracts. The existing live adapter supports SPY, QQQ, and AAPL, not SPX. [R3] |
| Reproducibility | Two live refreshes change market inputs as well as assumptions; they cannot isolate the effect of a changed rate or dividend model. |

**This ADR does not assert that dividends explain every difference from QuantWheel.** The screenshots do not establish QuantWheel's exact selected instrument, formula, quote timestamps, IV inputs, or dividend model. Matching another vendor is not an acceptance criterion.

### 1.1 New dividend endpoint supplied by the owner

The owner supplied:

```text
Webpage:
https://www.nasdaq.com/market-activity/stocks/aapl/dividend-history

Candidate request:
https://api.nasdaq.com/api/quote/AAPL/dividends?assetclass=stocks
```

The new dividend-response attachment was not available to the ADR author. The accessible earlier JSON is an **option-chain** response, not this dividend response. An attempted live read of the dividend endpoint did not return usable JSON in this environment. Consequently, this ADR specifies the canonical contract but does **not** invent raw JSON field paths.

Nasdaq's public dividend-history page says historical data is not split-adjusted, may combine regular and special dividends, may include preferred securities, and describes upcoming ex-date coverage for the next month. These are specific reasons not to assume this endpoint supplies a complete, homogeneous 60-day forward cash-dividend schedule. [E4]

## 2. Decision

Implement the following as one bounded post-MVP increment:

1. Preserve the option-chain provider interface and the chain-contained underlying-price rule.
2. Add independent, synchronous interfaces for rate observations and dividend records.
3. Use the New York Fed SOFR API as the default live rate observation source, converted to an explicitly labeled flat financing-rate proxy. Permit an explicitly selected, dated manual rate instead; do not silently fall back to it.
4. Add a Nasdaq dividend adapter only after its schema and applicable access rights are verified. Support an explicitly selected local cash-dividend schedule when that adapter is unavailable or does not cover an instrument.
5. Require a reviewed forward dividend schedule, including labeled estimates where needed. Historical records or missing upcoming records must not silently imply zero future dividends.
6. Price new dashboards with the cash-dividend present-value BSM approximation specified in Section 9. Retain continuous-yield BSM only for reproducing and comparing old snapshots.
7. Add a per-$1/per-1% display selector without changing stored GEX units or requesting new data.
8. Persist the exact resolved pricing inputs and calculation version with every new snapshot.
9. Add an offline, read-only same-snapshot reconciliation command.

**Do not introduce an American numerical engine in this increment.** The selected cash-PV approximation is a deliberate improvement over omitted distributions, not a claim of exact equity/ETF option pricing. Its remaining early-exercise limitation must be visible. An American engine requires a separate decision with its own convergence, exercise, and performance tests.

## 3. PRD changes authorized by this ADR

| Existing provision | Replacement or clarification | Reason |
|---|---|---|
| PRD 5.1: rates/dividend yields supplied only through settings | Permit bounded, cached reference-data requests during a user-initiated refresh; require provenance and dated manual alternatives. | Remove untracked placeholders without background collection. |
| PRD 6.1: continuous-yield BSM only; no discrete-dividend service | Use the cash schedule and cash-PV BSM approximation in Section 9 for new snapshots. | Model the timing and amount of ordinary distributions. |
| PRD 6.3: one BSM input path | Retain the BSM mathematical kernels; pass an expiry-specific pricing context. Retain a legacy continuous-yield path for offline reproduction. | Use the same resolved inputs for price bounds, IV, gamma, and the surface. |
| PRD 7: only per-1% display | Keep per-1% canonical storage; add an explicit per-$1 display conversion. | Make units comparable without duplicating analytical state. |
| PRD 8: forward `S * exp((r-q)*T)` | For new cash-model snapshots, use the forward in Section 9. | Keep moneyness and option-side selection consistent with pricing. |
| PRD 9: exactly two tables | Add one bounded reference-input cache table. Existing snapshot and option-quote tables remain. | Cache reusable rate/dividend responses without another service. |
| PRD 10: schema version 1 and empty refresh request | Read versions 1 and 2; write version 2. Permit one optional boolean refresh query parameter. | Preserve saved data while adding explicit model metadata. |
| PRD 1: no historical replay | Permit only a local diagnostic CLI that reprocesses a specific saved snapshot and writes a comparison report. | Isolate modeling changes; this is not a history UI or backtester. |
| PRD 4: exact seven application modules | Allow `market_inputs.py`, `rates.py`, and the diagnostic entrypoint described below. | Separate new responsibilities without service/repository layers. |
| PRD 11: no financial calculations in React | Permit only the specified GEX **unit conversion** and formatting in React. IV, gamma, dividend adjustment, and forward calculations stay in Python. | Avoid a server round trip for a display choice. |

The following remain unchanged: React + shadcn/ui; HTML GEX table; Plotly IV surface; DuckDB; one Python process; manual collection; refresh lock and cooldown; 1-60 calendar DTE; strike scope 0.80-1.20 times actual spot; the initial three-symbol allowlist (moved from `settings.json` to `backend/instruments.py` on 2026-09-17; see PRD 3.1); no separate spot/quote request; null-versus-zero semantics; no trading signals or orders.

**0DTE, SPX/XSP/index support, gamma-flip calculations, and new charts are not added.** When comparing with a tool that includes 0DTE, compare common expirations or explicitly state the scope difference.

## 4. Architecture and ownership

```text
User clicks Refresh
        |
        v
Existing refresh gate: allowlist -> lock -> cooldown
        |
        v
OptionsDataProvider.fetch_chain(request)
        |
        v
Canonical chain + actual spot + source/collection timestamps
        |
        v
Resolve reference inputs at the snapshot's valuation time
  |                                  |
  +-- RateDataProvider/cache         +-- DividendDataProvider/cache
  |                                  +-- reviewed local schedule
  +------------------+---------------+
                     |
              Frozen MarketInputs
                     |
          One pricing context per expiry
                     |
       IV -> gamma -> GEX and IV surface
                     |
          Atomic analytical snapshot save
                     |
              One dashboard response
                     |
     React: exposure mode + move-unit selector
```

Reference requests occur **after** the chain has been collected so resolution can use its fixed valuation time. They do not change `collection_started_at`, `collected_at`, or `valuation_at`. The existing 30-second chain-collection validity window remains a chain-only measure, not a claim about total refresh duration.

### 4.1 File responsibilities

| File | Responsibility |
|---|---|
| `provider.py` | Existing option interface/error plus the two reference-data Protocols. No concrete adapters. |
| `models.py` | Canonical input records, pricing context, settings, and versioned API models. |
| `nasdaq.py` | Existing chain adapter plus independent `NasdaqDividendProvider`. All Nasdaq response field mapping stays here. |
| `rates.py` | `NyFedSofrProvider`: one verified rate endpoint and its response normalization. |
| `fixtures.py` | Synthetic option, rate, and dividend providers; zero HTTP. |
| `market_inputs.py` | Resolve cached/selected reference sources, combine reviewed schedules, validate coverage, and construct frozen inputs. No IV/GEX calculations. |
| `analytics.py` | Deterministic pricing contexts, IV, gamma, GEX, and surface calculations. No HTTP, file reads, clock reads, or database access. |
| `storage.py` | Existing atomic analytical storage, reference-cache reads/upserts, and read-only snapshot input loading. No provider parsing. |
| `app.py` | Provider selection at startup, refresh orchestration, existing routes, version handling, and errors. |
| `reconcile.py` | Local diagnostic CLI. Uses saved normalized quotes, not raw Nasdaq parsing or network providers. |

Use functions and frozen typed records. No plugin registry, pricing-engine class hierarchy, dependency-injection framework, scheduler, queue, ORM, Redis, or new process. Cache once per input source, not once per option or expiry.

### 4.2 Mandatory interfaces

```python
class OptionsDataProvider(Protocol):
    def fetch_chain(self, request: ChainRequest) -> ChainSnapshot: ...

class RateDataProvider(Protocol):
    def fetch_rates(self) -> RateBatch: ...

class DividendDataProvider(Protocol):
    def fetch_dividends(self, symbol: str) -> DividendFeedSnapshot: ...
```

Keep these synchronous. Do not add `get_spot`, `get_quote`, or `get_greeks` to any interface. Rate/dividend requests are new **reference** requests, not permission to replace the chain's spot.

Inject reference providers through keyword arguments to `create_app`, just as the chain provider is injectable. Select implementations in ordinary factory functions in `app.py`. A stub with an arbitrary provider ID must pass through resolution, analytics, persistence, and response generation without changing analytical or frontend code. Provider IDs in canonical/API records are strings; source selection is validated by the factory.

The Nasdaq chain and dividend adapters may share a small HTTP helper inside `nasdaq.py`; neither may call the other's fetch method. Analytics must never inspect a raw payload or branch on a provider name.

## 5. Canonical records and configuration

Validate untrusted source formats in adapters and cross-field financial invariants when constructing canonical inputs. Do not repeat schema-repair logic in routes, storage, analytics, and React.

All precise timestamps are UTC-aware. Dates remain dates. Preserve unknown source times as null. Monetary dividend amounts are decimal strings at serialization boundaries and positive `Decimal` values internally; convert to floats only for numerical pricing.

### 5.1 Required records

| Record | Required fields |
|---|---|
| `RateObservation` | `effective_date: date`, `percent_rate: finite float`, `rate_type: str`, `revision_indicator: str | None` |
| `RateBatch` | `provider_id`, `fetched_at`, `source_ref`, `observations: tuple[RateObservation, ...]`, `raw_payload_json` |
| `DividendRecord` | `provider_record_id: str | None`, `symbol`, `currency`, `ex_date: date`, `payment_date: date | None`, `declaration_date: date | None`, `amount: Decimal | None`, `kind: ordinary_cash | other | unknown`, `source_ref` |
| `DividendFeedSnapshot` | `provider_id`, `symbol`, `fetched_at`, `source_asof: datetime | None`, `records: tuple[DividendRecord, ...]`, `raw_payload_json`, `warnings` |
| `ScheduleReview` | `symbol`, `reviewed_at`, `coverage_start`, `coverage_end`, `no_other_events_expected: true`, `source_refs`, `expected_events` |
| `ExpectedDividend` | `event_id`, `ex_date`, `payment_date: date | None`, `amount: Decimal | None`, `amount_status: declared | estimated | pending_source`, `source_ref` |
| `ResolvedDividend` | `event_id`, `ex_date`, `payment_date`, `amount: Decimal`, `amount_status: source_reported | owner_declared | estimated`, `source_ref`, `source_provider_id` |
| `MarketInputs` | `input_schema_version=1`, `resolved_at`, `rate` with numeric result and provenance, `dividend_schedule` with resolved events and review, `warnings`, `reference_bundle_hash` |
| `ExpiryPricingContext` | `expiration`, `valuation_at`, `expiry_at`, `T`, `actual_spot`, `model_spot`, `r_cc`, `q_continuous`, `pv_dividends`, `forward`, `used_event_ids`, `warnings`, `status` |

`pending_source` means the owner identified an expected event date but has not supplied a usable amount. It must be resolved by a valid source record on that exact date or cause a reference-input error. It is never a zero-dollar dividend.

The resolved `MarketInputs.rate` object has exactly these required fields:

```text
source_provider_id: str
source_ref: str
effective_date: date
fetched_at: UTC datetime
raw_percent_rate: finite float | null
rate_cc: finite float
quote_convention: "percent_simple_act360" | "continuous_act365f"
normalization: "constant_daily_sofr_proxy" | "manual_already_continuous"
revision_indicator: str | null
manual_reason: str | null
```

`fetched_at` for a manual input is the actual time the local file was read, not an invented market timestamp; retain its `entered_at` in manual provenance. For fixture rates, use an explicitly synthetic observation and the synthetic economic clock for eligibility. For `rate_source="manual"`, the resolver reads `manual_rate` directly and makes zero `RateDataProvider` calls; do not force a continuously compounded manual rate into a fake `percent_rate` observation. A `ManualDividendProvider` can wrap the local schedule's event records, but the separate review/coverage validation still applies.

`source_ref` is a provenance label/URL, not a URL the backend automatically requests. Remote URLs are fixed inside adapters; the browser cannot submit arbitrary endpoints.

### 5.2 New configuration keys

Keep operational settings such as `source_mode`, database path, and cooldown. (The symbol allowlist was also a setting here until 2026-09-17; it now comes from `backend/instruments.py`, and `dividend_sources` must have one key per instrument there.) For the new pricing path, replace top-level `risk_free_rate` and `dividend_yields` with:

```json
{
  "pricing_model": "cash_pv_bsm_v2",
  "rate_source": "nyfed_sofr",
  "dividend_sources": {
    "SPY": "manual_schedule",
    "QQQ": "manual_schedule",
    "AAPL": "nasdaq_dividends"
  },
  "reference_inputs_path": "reference_inputs.json"
}
```

This is a **live configuration illustration**, not permission to enable an unverified adapter. Initially, AAPL's new live adapter is gated by M0. The SPY/QQQ entries deliberately select a local schedule until their Nasdaq dividend coverage is independently established. Do not copy the chain endpoint's stock/ETF asset-class mapping into the dividend adapter without verification.

For the shipped clean-checkout example, use `source_mode="fixture"`, `rate_source="fixture"`, and all dividend sources `"fixture"`. Synthetic providers supply the rate, schedule review, and events without requiring a local reference-input file. Fixture mode must never fall through to a live reference provider.

Live `reference_inputs.json` is local and git-ignored. Read and validate it once at the start of each permitted refresh; keep that in-memory version fixed for the entire attempt. Changes affect the next refresh only. A malformed file fails that attempt; do not keep silently using a prior file version. Save a content hash with resolved inputs.

A local schedule entry has this structure; **all dates and amounts below are synthetic**:

```json
{
  "input_schema_version": 1,
  "manual_rate": null,
  "schedules": {
    "SPY": {
      "symbol": "SPY",
      "reviewed_at": "2026-01-02T21:00:00Z",
      "coverage_start": "2026-01-02",
      "coverage_end": "2026-04-02",
      "no_other_events_expected": true,
      "source_refs": ["synthetic-example-not-market-data"],
      "expected_events": [
        {
          "event_id": "synthetic-spy-ordinary-1",
          "ex_date": "2026-01-07",
          "payment_date": "2026-02-11",
          "amount": "1.25",
          "amount_status": "estimated",
          "source_ref": "synthetic-example-not-market-data"
        }
      ]
    }
  }
}
```

Provide entries for every enabled live symbol. An empty `expected_events` list is allowed only with a valid explicit review stating no other events are expected over the covered interval. This is an owner's forecast assumption, not proof of future corporate actions.

For `rate_source="manual"`, require `manual_rate` with `rate_cc`, `effective_date`, `entered_at`, `source_ref`, and `reason`. `rate_cc` is already continuously compounded ACT/365F; do not convert it as though it were an API percentage. Zero is allowed as an explicit dated input, not as a missing-value default.

Old configuration must fail with a migration message identifying the new keys. Do not automatically treat an old zero dividend yield as a reviewed empty cash schedule. Do not overwrite the user's settings file or delete their database.

## 6. Rate acquisition and convention

### 6.1 Verified candidate response contract

Use this fixed HTTPS endpoint for the initial live rate adapter:

```text
GET https://markets.newyorkfed.org/api/rates/secured/sofr/last/5.json
```

The ADR author obtained a response containing `refRates[]` with `effectiveDate`, `type`, `percentRate`, and `revisionIndicator`. The coding agent must capture its own permitted verification evidence and test the parser. [E5]

Rules:

- Require a JSON object and a `refRates` list. Require each selected SOFR observation's effective date and finite numeric percentage. Reject booleans as numbers.
- Retain observations with `type == "SOFR"`; choose the most recent `effectiveDate <= New_York_date(valuation_at)`.
- Sort explicitly; do not assume response order.
- Equal-date exact duplicates collapse; conflicting equal-date rate observations reject the batch.
- If no eligible observation exists, return `RATE_UNAVAILABLE`. Do not use a future effective date, the Fed target rate, or a hardcoded zero as a substitute.
- Preserve the raw percentage, effective date, revision indicator, retrieval time, source, and normalized rate in the saved snapshot.

SOFR measures overnight Treasury-secured borrowing, not a maturity-specific option discount curve. The New York Fed's money-market methodology uses an actual/360 convention. [E6][E7]

### 6.2 Exact normalization used by this application

Let `p` be the API percentage, e.g. synthetic `4.0` means 4%, and `L = p / 100`.

```text
r_cc = 365 * log1p(L / 360)
D(t) = exp(-r_cc * t)
```

Here `t` is fractional ACT/365F years. This defines a **flat, constant daily-calendar financing proxy** from the observed overnight rate. It is not a reproduction of the NY Fed's realized SOFR Index, including its weekend/holiday compounding, and it is not an OIS curve or term-SOFR forecast.

Use the same `r_cc` for all expirations and dividend discounting in one snapshot. Always emit `FLAT_OVERNIGHT_RATE_PROXY` for this source. Never label the converted number a directly observed 30-day or 60-day zero rate.

Validate the normalized rate in `[-0.10, 0.50]`, consistent with the application's input guardrails. This is an application limit, not a statement about possible market rates.

Manual rates do not receive this conversion. Existing snapshot `parameters.r` values do not receive it retrospectively.

### 6.3 Freshness policy

The cache rules are in Section 8. Independently of cache age, reject a live rate whose effective date is more than **7 calendar days** before the snapshot's valuation date. Use calendar days deliberately; do not add a holiday-calendar service. A weekend/holiday observation within that range is not automatically stale.

The 7-day limit is a product policy, not a provider guarantee. A current retrieval timestamp cannot make an old effective date current. A manual rate follows the same effective-date limit for new live snapshots.

## 7. Dividend acquisition, review, and resolution

### 7.1 Nasdaq mapping gate

Before coding the raw-field mapping, record the following in `docs/dividend-source-contract.md` from an actual authorized response:

| Item to establish | Required evidence |
|---|---|
| Response envelope and failure shape | Success, missing data, and a mocked test matching verified structure. |
| Event row path and pagination | Exact path; meaning of totals/limits; whether the relevant record set can be truncated. |
| Ex-date | Exact field path, date format, and confirmation it is the ex-date rather than record/payment date. |
| Cash amount | Exact field path; currency; per-share versus annual total; treatment of missing values. |
| Payment/declaration dates | Exact paths and null semantics when supplied. |
| Distribution type | How ordinary cash, special, combined, preferred, and unknown records are represented. |
| Security identity | How records are matched to the requested common share/ETF, not a similarly named security. |
| Future coverage | Observed forward horizon; whether undeclared/estimated events are included. |
| Symbols | Separate verification for each instrument enabled against this dividend endpoint. |
| Access | Applicable permissions, usage limits, and private-sample handling. |

Until these are verified, the adapter must not guess fields such as an apparent annual-dividend summary, assume a 60-day horizon, or interpret an unavailable response as an empty valid schedule. Canonical fixture and manual-schedule work may proceed independently.

AAPL verification alone does not prove SPY or QQQ support. Nasdaq's public page has coverage qualifications; treat the ETF behavior as a separate integration question. [E4]

### 7.2 Normalization rules

Map only verified event fields to `DividendRecord`. Apply these rules:

- Preserve calendar ex-date, payment date, and declaration date independently.
- A row amount is usable only when verified to be USD cash **per current share** for the requested security.
- Do not divide an annual dividend by four, infer future dates by adding 90 days, or derive `q` from a trailing dividend total.
- Do not feed a display yield percentage into the cash model.
- Past events remain auditable but do not become future events because their payment date is still ahead.
- A source declaration date later than the live snapshot's valuation date cannot be treated as information known at valuation: reject with `INPUT_KNOWLEDGE_AFTER_VALUATION`. A missing declaration date stays unknown and the event stays `source_reported`, not `issuer_confirmed`. The diagnostic CLI may use later-known inputs only with its explicit counterfactual labels.
- Known special distributions, noncash distributions, adjusted deliverables, and security-identity ambiguity are unsupported for automatic live pricing in this increment. Do not repair them using an ordinary dividend.
- A missing event amount remains missing. A missing payment date remains null. Recognized missing sentinels are not zeros.
- Exact duplicates collapse. Conflicting ordinary records for the same ex-date fail as `DIVIDEND_CONFLICT`; do not sum them or choose the larger number.
- A malformed response is `DIVIDEND_SCHEMA_ERROR`, not a successful empty response.

These checks belong at the input boundary. Do not create generic source-schema inference or an extensive fallback cascade.

### 7.3 Reviewed forward coverage

An API history list alone does not establish that no more distributions are expected before every option expires. Require `ScheduleReview` for the selected live symbol:

```text
coverage_start <= New_York_date(valuation_at)
coverage_end >= latest returned in-scope expiration date
0 <= New_York_date(attempt_started_at) - New_York_date(reviewed_at) <= 7 days
no_other_events_expected == true
```

If there are no in-scope contracts, skip the end-coverage requirement; the existing insufficient-data behavior remains. For actual pricing, all applicable expected events require a usable amount. A **90-day review horizon is recommended operationally**, but the code checks the actual maximum in-scope expiry, not a mandatory 90-day value.

If coverage is missing, stale, or too short, fail refresh with `DIVIDEND_REVIEW_REQUIRED` or `DIVIDEND_COVERAGE_INCOMPLETE`. Preserve the previous analytical snapshot. Show the exact symbol and required end date in the error. Do not save a new apparently successful zero-dividend calculation.

Resolution is deterministic:

1. Begin with the review's expected ordinary event dates inside its coverage.
2. With `manual_schedule`, use each complete declared/estimated owner entry; `pending_source` is invalid in this mode.
3. With `nasdaq_dividends`, match a source ordinary event by **exact ex-date**. A usable source amount replaces a matching owner estimate or fills `pending_source`; retain provenance and emit `DIVIDEND_ESTIMATE_REPLACED` when an estimate changed.
4. If a source-reported amount conflicts with an owner entry marked declared, fail `DIVIDEND_CONFLICT`. Require review instead of a hidden precedence choice.
5. If a source event has missing amount, a complete owner estimate on the same date may supply it only with status `estimated` and `DIVIDEND_AMOUNT_ESTIMATED`.
6. A new source event inside the review interval that has no expected-date entry invalidates the review: `DIVIDEND_REVIEW_REQUIRED`. This prevents counting both an old estimated date and a newly announced changed date as two distributions.
7. Do not auto-match neighboring dates. The owner must correct a moved/cancelled event and re-review the schedule.
8. Do not infer that a missing source event was cancelled. A complete owner estimate remains an estimate; a pending amount remains unresolved.

For the same matched event, differing non-null payment dates are a conflict when both are declared/source-reported. A source payment date can replace a date attached to an estimated event; retain that change in provenance.

Manual entries are intentional assumptions, not automatic transport fallbacks. Switching a symbol from Nasdaq to `manual_schedule` is an explicit configuration choice.

### 7.4 Event eligibility and dates

Use the following **model timing conventions**, recorded with the snapshot:

```text
ex_at  = 09:30 America/New_York on ex_date
pay_at = 16:00 America/New_York on payment_date, when present
expiry_at = 16:00 America/New_York on option expiration date
```

Convert using `zoneinfo`, including DST. These are modeling timestamps, not observed timestamps or exact exchange settlement metadata. Early-close calendars remain excluded.

An event affects an expiry only when:

```text
valuation_at < ex_at <= expiry_at
```

If its payment date is after expiry, **include it anyway**: the inclusion boundary is the ex-date. If its ex-date is already past but payment is still future, **exclude it**. Payment date determines the discount horizon, not eligibility.

For known payment dates, discount the modeled cash amount to `pay_at`. A payment date before ex-date is invalid. If payment date is unknown, discount to `ex_at` and add `DIVIDEND_PAYMENT_TIME_ASSUMED_AT_EX`. Do not invent a payment date in the normalized source record.

### 7.5 Timestamp alignment around ex-date

Retain the PRD's chain-time-or-collection-start valuation rule and `VALUATION_TIME_ASSUMED` warning. Dividend data cannot establish when the chain's price was observed.

Preserve a date-only underlying as-of field as optional canonical `spot_asof_date` when the chain adapter can verify it. Do not synthesize `spot_asof` from that date. Old snapshots may lack the new date field.

Before adjusting spot:

- If precise chain/spot timestamps straddle a relevant ex-event, reject with `DIVIDEND_PRICE_TIME_MISMATCH`, even if their difference is less than the existing 120-second alignment threshold.
- If a date-only spot is definitely from before ex-date while the selected valuation is after `ex_at`, reject with the same error. Advancing valuation time cannot turn a cum-dividend observed price into an ex-dividend price.
- If source precision cannot determine the side of the event, keep the explicit collection-time assumption and add `DIVIDEND_ALIGNMENT_UNVERIFIED`. Results are conditional on that assumption; the UI must not claim synchronized or verified ex-dividend pricing.
- Do not fix a mismatch by subtracting the dividend twice, by guessing a quote timestamp, or by fetching a separate spot.

This increment does not reconstruct historical quote timestamps that the source never supplied.

## 8. Reference caching and refresh behavior

### 8.1 One cache table

Add this table through the existing idempotent schema initialization:

```sql
CREATE TABLE IF NOT EXISTS reference_cache (
    kind VARCHAR NOT NULL,
    provider_id VARCHAR NOT NULL,
    subject VARCHAR NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL,
    normalized_json JSON NOT NULL,
    raw_payload_json JSON NOT NULL,
    PRIMARY KEY (kind, provider_id, subject)
);
```

Allowed `kind` values are `rate` and `dividends`. Use `subject='USD'` for the rate batch and the actual ticker for dividends. Store only the latest successful entry per key. Do not create per-contract/per-expiry cache rows.

The cache is an optimization, not an audit dependency: the exact normalized observations and resolved schedule used by a calculation are copied into its immutable dashboard. A later cache replacement must not affect reproduction of an earlier snapshot.

### 8.2 Cache eligibility

| Input | Cache TTL | Additional requirement |
|---|---:|---|
| SOFR batch | 3,600 seconds | Contains an eligible effective date under Section 6. |
| Nasdaq dividend feed | 21,600 seconds | Selected symbol matches; schedule-review requirements still apply. |
| Local manual inputs | No response cache | Read once per permitted refresh; validate dates and coverage. |
| Fixture inputs | No external access | Use the fixture valuation clock for synthetic economic dates and review-age tests; keep real retrieval/collection timestamps distinct. |

Evaluate TTL of an existing cache entry against the attempt's real UTC start. Negative cache ages are ineligible. A newly fetched and validated response is consumed directly in that attempt; do not reject it because its retrieval occurred after the attempt started. An expired cache requires a new request; if it fails, reject refresh rather than silently using stale inputs. Do not erase the previous cache entry on failure.

Allow `POST /api/dashboard/{symbol}/refresh?force_reference_refresh=true`. It bypasses reference TTL only. It does not bypass the global lock, cooldown, permission gate, or data validation. Default is false. Provide a small `Refresh including reference inputs` action within the input-details panel; use the same endpoint and response handling, not a new workflow.

### 8.3 Network and failure budget

A normal manual refresh may make `N` option-chain page requests, at most one rate request on a cache miss, and at most one dividend-feed request on a cache miss for the selected symbol. An adapter requiring dividend pagination must document it at M0; the initial automatic adapter must fit one verified response or return `DIVIDEND_COVERAGE_UNVERIFIED`, not silently truncate.

Use the existing connect/read/write/pool timeout pattern for reference HTTP: 5-second connect and 10-second read/write/pool. Limit each reference response to 2 MiB of decoded content. No automatic retries and no fallback to another vendor. These are application resource limits, not provider entitlements.

Load/reload, symbol change, GEX mode change, unit change, timers, and tab focus issue **zero upstream calls**. GET routes read saved data/configuration only. Constructor/factory creation must not fetch.

All provider requests are inside the existing permitted refresh attempt. A failed permitted attempt consumes cooldown. Reference 429 responses extend the same cooldown using the existing rate-limit policy. Return the normal error envelope with the failing reference kind and provider in the message; never return raw upstream HTML or credentials.

Reference-cache upserts may survive a later failed chain/analysis save. They do not count as a successful analytical snapshot. The analytical snapshot and its quotes must still commit atomically, and an error must preserve the previous dashboard.

## 9. Cash-dividend pricing approximation

### 9.1 Model ID and assumptions

Use `model_id="cash_pv_bsm_v2"` and `algorithm_version="2"` for new calculations.

Treat dividends as deterministic USD cash amounts in this calculation, including amounts explicitly labeled estimated. Let `S` be the actual chain spot, not a historical adjusted-close value. Let `D_i` be an eligible cash event and `tau_i` its payment discount horizon from Section 7.4, in ACT/365F years.

For each expiry:

```text
T = (expiry_at - valuation_at).total_seconds() / 31_536_000
A(T) = sum(D_i * exp(-r_cc * tau_i)) over eligible ex-events
X(T) = S - A(T)
F(T) = X(T) * exp(r_cc * T)
q_continuous = 0
```

`X` is a **model pricing input**, not a replacement observed spot. The issuer may pay after option expiry; `tau_i > T` is valid when ex-date occurs before expiry.

This is an escrowed/present-value spot approximation. Related European dividend engines subtract discounted dividends before invoking a Black calculator; this ADR makes ex/payment-date handling explicit rather than assuming those dates are identical. It is not an exact solution for a stochastic stock with discrete cash jumps or early exercise. [E8]

### 9.2 Pricing, IV, and gamma must share the context

Calculate and cache one `ExpiryPricingContext` per returned in-scope expiration within the analysis call. No persistent pricing cache is needed.

Use the existing BSM kernels with `s=X`, `q=0`, and the resolved `r_cc`:

```text
d1 = [ln(X/K) + (r_cc + sigma^2/2)*T] / (sigma*sqrt(T))
d2 = d1 - sigma*sqrt(T)

C = X*N(d1) - K*exp(-r_cc*T)*N(d2)
P = K*exp(-r_cc*T)*N(-d2) - X*N(-d1)

Gamma_S = normal_pdf(d1) / (X*sigma*sqrt(T))
```

The gamma is with respect to the **actual share price S** under this approximation. The dividend amounts and discount factors are held fixed when differentiating, so `dX/dS=1`. IV is held fixed for the Greek derivative. When assumptions change in a reconciliation scenario, IV must first be re-solved from the unchanged midpoint.

**Do not replace this with an equivalent continuous `q` and reuse the continuous-yield gamma formula while holding that `q` fixed.** That would define a different spot derivative.

Use the actual `S` for strike-scope filtering and GEX dollar scaling. Use `X` only for the pricing model and the resulting forward. Replacing every occurrence of `spot` with `X` is a bug.

### 9.3 Bounds and existing quality rules

Retain the existing bid/ask, midpoint, relative-spread, nonstandard-contract, IV-bracket, solver-tolerance, and repricing checks. Change the model bounds and lower-bound time-value check consistently:

```text
Call bounds: max(0, X - K*exp(-r*T)) <= price <= X
Put bounds:  max(0, K*exp(-r*T) - X) <= price <= K*exp(-r*T)
```

Use the existing tolerances (`1e-8` model bounds, `0.01` minimum time value, midpoint at least `0.05`, relative spread at most `0.50`). Brent bracket remains `[0.0001, 5.0]`, with `xtol=rtol=1e-8`, 100 iterations, and final price residual at most `1e-5` dollars.

These are bounds of the **chosen European approximation**, not universal bounds for actual American market quotes. Exclusion can indicate a model mismatch, not bad market data. Do not relax filters merely to make another vendor's cells appear.

If `X <= 0`, return `INVALID_DIVIDEND_ADJUSTED_SPOT` for that expiry; its IV/gamma are unavailable. Other valid expiry contexts can still be processed. Do not clamp `X` to a small positive number. Unsupported contracts remain excluded even if their OI is zero.

Retain explicit zero-OI exposure for supported contracts, missing-OI independence of IV, and incomplete-cell nulls. Do not infer OI from volume.

### 9.4 IV surface

For each expiry use `F(T)` from the **same** context:

```text
k = ln(K / F(T))
Select put for K < F(T); otherwise select call.
w = IV^2 * T
```

Keep the existing fixed grid, minimum three strikes per slice, no extrapolation, maximum 0.05 log-moneyness bridge, and surface-readiness rules. Do not recompute a forward independently in `build_surface`.

The surface can change horizontally as well as vertically after correcting dividends. That is expected when the assumed forward changes. Show the model ID and dividend warnings with the surface.

### 9.5 Required model disclosures

Display these statements for version-2 cash-model snapshots:

```text
Cash-dividend PV BSM approximation.
European model applied to American-style equity/ETF options;
early exercise is not modeled.
```

Also display applicable rate, estimated-dividend, assumed-payment-time, and timestamp-alignment warnings. Flag any included ex-date within the next **7 calendar days** as `NEAR_EX_DIVIDEND`; this is a modeling caution, not a trade or exercise signal.

Do not claim that an explicit schedule resolves American exercise premiums, borrow effects, source delay, or every vendor difference.

## 10. GEX units and display contract

For each supported contract, using gamma from Section 9 and the **actual chain spot S**:

```text
GEX_per_1dollar = gamma * OI * multiplier * S
GEX_per_1pct    = gamma * OI * multiplier * S^2 * 0.01
GEX_per_1pct    = GEX_per_1dollar * S * 0.01
```

Both express the local gamma-driven change in delta-notional, marked at the snapshot spot. They are not option P&L, a finite-move revaluation, or the full derivative of marked hedge notional including its delta term.

Preserve the existing API fields `call_exposure`, `put_exposure`, `signed_proxy`, and `gross_exposure` in **unscaled dollars per 1% move**. In version 2 add:

```json
"gex": {
  "canonical_unit": "usd_delta_notional_per_1pct",
  "strikes": [],
  "expirations": [],
  "cells": []
}
```

React holds two independent choices:

```text
Exposure mode: Call-minus-put GEX proxy | Gross OI-weighted gamma
Move unit:     Per 1% move | Per $1 move
```

Default to per 1% to preserve current behavior. Unit choice is local component/application state, not part of a saved market snapshot. Do not store a second matrix or send a POST when it changes.

The only frontend financial conversion is:

```text
display_factor = 1                         for per_1pct
display_factor = 1 / (0.01 * snapshot.spot) for per_1dollar
```

Apply this factor to every displayed exposure magnitude, signed/gross cell, tooltip exposure, and legend bound. Do not scale gamma or OI. Preserve nulls. Use the same factor for bounds and values; switching units alone must not change relative colors. Do not multiply/divide by an arbitrary 10.

Every heatmap title includes the actual symbol. Show instrument class and `0DTE excluded` with scope metadata. No SPY-to-SPX strike rescaling, relabeling, or inferred index equivalence is allowed.

## 11. Snapshot schema, compatibility, and provenance

### 11.1 Write version 2, read versions 1 and 2

New snapshots use `schema_version=2`. Keep the existing two analytical tables and add only the cache table from Section 8. No destructive migration is authorized.

The version-2 dashboard adds:

```text
instrument:
  symbol, instrument_class (equity|etf), currency=USD,
  exercise_style=american, standard_multiplier=100

parameters:
  existing scope and multiplier-assumption fields
  algorithm_version="2"
  model_id="cash_pv_bsm_v2"
  r = normalized continuously compounded rate used
  q = 0.0
  dividend_model="cash_schedule"
  pricing_time_convention

market_inputs:
  exact resolved MarketInputs, including schedule review and source metadata

pricing_contexts:
  one serialized ExpiryPricingContext per in-scope expiry

gex.canonical_unit:
  "usd_delta_notional_per_1pct"

calculation_input_hash:
  SHA-256 of the canonical numerical inputs and algorithm identifiers
```

Version-2 `q=0.0` means **no additional continuous yield because cash events are explicit**. It must not be presented as an assertion that the security pays no dividends. Do not subtract cash distributions and apply a trailing annual dividend yield on top.

Use two explicit response variants in Python and a TypeScript discriminated union. Do not manufacture source dates or dividend schedules for old snapshots. A version-1 snapshot remains its saved continuous-yield result; label its reference-input provenance unavailable. Its GEX canonical unit is known from the legacy contract and can receive the same display-only conversion.

A normal GET returns the stored calculation unchanged. Do not upgrade old calculations on read, load current reference data for them, or recompute their surface. A new refresh creates a new snapshot ID and version.

`GET /api/config` adds `config_schema_version=2`, configured model/source choices, and the default move unit. Configured sources are not the actual pricing inputs for an old snapshot. React's pricing details come from the displayed dashboard.

### 11.2 Hashes and what must be saved

Canonicalize hash input with sorted JSON keys, sorted contract keys `(symbol, expiration, strike, option_type)`, sorted dividend events, ISO dates/UTC timestamps, stable decimal-string representation, compact separators, and no NaN/Infinity. Do not include UUIDs, report-generation time, or current cache timestamps in the numerical input hash.

The numerical hash includes actual spot, normalized quotes/OI/multiplier/flags, valuation time, scope/quality thresholds, resolved rate, applicable dividend amounts/dates/payment-time policy, and model/algorithm versions. `reference_bundle_hash` separately includes the frozen provenance/review metadata.

Save the full resolved normalized inputs, not only hashes or a cache key. A hash cannot reproduce missing inputs. Keep raw rate/dividend payloads private in `reference_cache`; do not expose them to React. Reproducing numerical results requires the saved normalized bundle, not indefinite retention of raw provider history.

Save exact assumptions for estimated dividends. Later replacement by a declared amount affects only new snapshots.

## 12. Offline reconciliation command

Implement `backend/reconcile.py`. It is a diagnostic, not an additional HTTP endpoint or backtesting system.

Example commands, run from `backend/`:

```text
python reconcile.py --db data/options.duckdb \
  --snapshot-id <uuid> \
  --scenario-inputs reference_inputs.scenario.json \
  --out exports/<uuid>-pricing-comparison
```

Stop the backend before this command opens the same DuckDB file from another process. Open the database with `read_only=True`. Do not acquire network providers, run schema creation, or mutate existing tables. Fail if the output directory already contains a report rather than overwriting it silently.

The scenario file contains a canonical `rate_cc` plus a reviewed canonical cash schedule in the formats above. It is supplied explicitly; do not read the current reference cache or fetch today's data. Validate coverage against the saved snapshot's valuation/expirations, but do not reject a historical scenario solely because it is old relative to the current wall clock.

Run these four scenarios on **identical saved normalized contracts, quotes, actual spot, OI, valuation time, and scope**:

| Scenario | Rate | Dividend model |
|---|---|---|
| `saved` | Original saved rate | Original saved model/schedule or legacy q |
| `rate_only` | Scenario rate | Original saved model/schedule or legacy q |
| `cash_only` | Original saved rate | Scenario cash schedule, continuous q=0 |
| `rate_and_cash` | Scenario rate | Scenario cash schedule, continuous q=0 |

Re-solve IV in every scenario. Holding the old IV fixed tests only a direct Greek effect, not this application's full inference pipeline.

First reproduce the `saved` scenario. Compare IV/gamma/GEX/surface and exclusion reasons with saved outputs. If it fails Section 14 tolerances, write the discrepancy and exit nonzero with `BASELINE_REPRODUCTION_FAILED`; do not attribute the difference to rates/dividends. Retain a small explicit legacy context path instead of copying an old backend or implementing a generic engine framework.

Write:

```text
summary.md
contracts.csv
cells.csv
inputs.json
```

Required report content:

- Snapshot ID, actual symbol/instrument, original source timestamps, collection time, fixed valuation time, model versions, both hashes, and reference-source/review metadata.
- For every contract/scenario: bid, ask, midpoint, OI, actual/model spot, r, continuous q, dividend PV, forward, IV, gamma, both GEX units, and exclusion reason.
- For every cell/scenario: call exposure, put exposure, signed proxy, gross exposure, completeness, both units, and differences from `saved`.
- Counts of contracts/cells that become eligible or ineligible. Do not sum different eligible populations and describe the result solely as a gamma change.
- Original and scenario dividend events, their status, and whether they fall before or after the relevant expiry.
- Percentage differences use `100 * (new - old) / abs(old)`. When `old == 0` or either value is missing, percentage difference is null with an explicit reason. Always report the dollar difference when both values exist.
- State that scenario effects interact; do not claim rate-only plus cash-only changes add exactly to the combined change.
- Label all override scenarios `counterfactual sensitivity, not a historical trading backtest`. If input knowledge postdates valuation, explicitly mark it `known_after_valuation`; a declaration date alone does not establish when this application knew an event.

Public examples and tests use synthetic inputs. Do not commit live provider exports, populated databases, credentials, or captured market-data screenshots as part of this work.

## 13. Frontend changes

Use the existing shadcn components; add no state or chart library.

- Add a shadcn `Select` for move unit, independently of exposure mode.
- Show the actual ticker in the heatmap and surface headings.
- Add a native collapsible details block inside the existing Snapshot card for **Pricing inputs**. Use existing shadcn `Badge`, `Alert`, and `Button` components inside it; no new UI framework.
- Show normalized rate, raw quoted percentage when available, source, effective date, retrieval time, and flat-proxy/manual designation.
- Show `Cash schedule` rather than a misleading zero-dividend-yield label. List ex-date, amount, payment date or assumed-payment-time warning, source, and declared/reported/estimated status.
- Show review coverage end and review date. Show future estimated events distinctly from source-reported events.
- Show each expiry's model spot, dividend PV, and forward in an expandable table; actual observed spot remains unchanged at the top of the dashboard and in the heatmap divider.
- Show the model and near-ex-date/timestamp warnings for both analytical panels.
- Include `Refresh including reference inputs` in this details block. It uses the same loading/cooldown/error state as normal Refresh and preserves old charts on failure.
- Version-1 snapshots display `Legacy continuous-yield model; reference provenance unavailable` and their saved r/q. Do not show a fabricated cash-event list.

Neither the unit selector nor expanding pricing details performs a network request. Switching move units changes every exposure label and legend consistently; refreshing replaces both charts and input metadata together from one response.

## 14. Required numerical and behavioral regression cases

All fixtures in this section are synthetic. They are not current rates, announced distributions, or extracted commercial market data.

### N1 - Existing no-dividend reference

Use `S=K=100`, `T=1`, `r=0.05`, `sigma=0.20`, and an explicitly reviewed empty schedule:

```text
Call price = 10.4505835722      absolute tolerance 1e-8
Gamma      = 0.01876201735      absolute tolerance 1e-10
Recovered IV = 0.20            absolute tolerance 1e-6
```

With no eligible cash events, the new kernel must equal the existing q=0 BSM kernel to floating-point tolerance. Do not require equality to legacy nonzero-q pricing.

### N2 - SOFR quote conversion

```text
Synthetic API percentRate = 4.0
L = 0.04
r_cc = 0.04055330263601719
```

Require absolute error at most `1e-12`. Assert `exp(r_cc/365) == 1 + 0.04/360` within `1e-14`. Test explicit zero and negative supported inputs, NaN/Infinity, booleans, missing dates, future effective dates, conflicting duplicate dates, and source-age boundaries.

### N3 - Cash event before expiry, payment after expiry

Use relative times so this case does not depend on actual market dates:

```text
Actual spot S = 100
Strike K = 100
T = 30 / 365
r_cc = 0.04
sigma = 0.25
One ordinary cash dividend D = 1.25
Ex-event time = 5 days after valuation
Payment time = 40 days after valuation
```

Expected values:

```text
PV dividend = 1.24453254017385
Model spot X = 98.75546745982615
Forward F = 99.08067726782153
Call price = 2.4015898917023364
Put price = 3.3178951559197145
Gamma_S = 0.056119740851123166
Call - put = -0.9163052642173852
```

Price, PV, model spot, and forward tolerances: `1e-8` absolute. Gamma tolerance: `1e-10` absolute. Solving the unrounded call and put prices must recover IV 0.25 within `1e-6`.

The ADR author calculated these values using standalone normal-CDF mathematics, not the repository's pricing functions. The coding agent must verify them independently and then pin them in tests. Do not generate expected values by calling the same production function under test.

### N4 - Derivative and horizon semantics

For N3, compute the central second price difference in **actual S**, holding rate, sigma, dividend cash amounts, and event times fixed. Use `h=0.001*S`; require gamma relative agreement within `1e-3`.

Also test:

```text
Past ex-date + future payment       -> exclude event
Future ex-date after option expiry  -> exclude event
Ex-date on expiry before expiry_at  -> include event
Valuation exactly at ex_at          -> exclude event
Unknown payment date               -> use ex_at only for discounting; warning required
Payment date before ex-date         -> reject input
Dividend PV >= actual spot          -> invalid context; no clamping
Two future events                  -> sum each eligible PV exactly once
```

Test winter and summer NY conversions and an interval crossing a DST transition. Check event inclusion using precise model instants, not rounded DTE or payment-date filtering.

### N5 - GEX scaling and actual spot

For `S=200`, gamma `0.02`, multiplier `100`, call OI `1000`, put OI `600`:

| Output | Per $1 move | Per 1% move |
|---|---:|---:|
| Call exposure | 400,000 | 800,000 |
| Put exposure | 240,000 | 480,000 |
| Signed proxy | 160,000 | 320,000 |
| Gross exposure | 640,000 | 1,280,000 |

Use absolute tolerance `1e-6`. Repeat with `model_spot != actual_spot` and assert the scaling still uses actual spot. Test nulls, explicit zero OI, unsupported contracts, and negative signed values. Changing units must leave gamma, OI, IV, spot, eligibility, and relative color intensity unchanged.

### N6 - Cash-aware surface

Generate synthetic quotes for at least two maturities, one expiring before an event and one after it, at known constant IV=25%, using their respective cash contexts. Re-solved surface observations and supported grid nodes must match 0.25 within `1e-6`.

Verify selected call/put sides and `k=ln(K/F)` against each expiry's saved forward. For the maturity before ex-date, dividend PV must be zero. Preserve no-extrapolation and no-wide-gap-bridging tests.

### N7 - Same-snapshot sensitivity and reproduction

For the unchanged `saved` scenario, require unchanged exclusion reasons and cell/surface readiness. Compare non-null IV/gamma with `abs_tol=1e-8, rel_tol=1e-7`; unscaled GEX with `abs_tol=0.01, rel_tol=1e-7`; surface IV with `abs_tol=1e-8, rel_tol=1e-7`. Do not turn null into zero to pass.

Changing only scenario inputs must leave the normalized quote/OI/spot/time hash component unchanged. Re-run the same scenario twice: numerical outputs and ordering must match; only report-generation metadata may differ.

### N8 - Source and schedule safety

Test history-only feeds, no upcoming rows, missing event amounts, reviewed empty schedules, too-short/stale reviews, duplicate rows, conflicting declared amounts, estimated-to-reported replacement, moved ex-dates, unsupported security/types, and unavailable ETF endpoints.

Test that an AAPL adapter error cannot cause an SPY schedule or a synthetic schedule to be used. Test that lookup of an existing snapshot performs zero chain/rate/dividend calls even after reference caches expire.

## 15. Milestones and acceptance criteria

Complete these in order, except M1 and synthetic portions of M2-M5 may proceed while a live-source verification is pending. Do not mark a live adapter complete based solely on synthetic fixtures.

### M0 - Establish contracts and amend the specification

**Deliverables:** This ADR, a PRD supersession note, `docs/dividend-source-contract.md`, and `docs/rate-source-contract.md`.

| ID | Acceptance criterion |
|---|---|
| M0.1 | Record the baseline commit and identify the exact PRD provisions superseded by Section 3; update project-agent instructions to recognize this ADR. |
| M0.2 | Record the owner-supplied AAPL dividend URL, actual response field paths, date/amount semantics, future coverage, and applicable access requirements. Missing attachment/JSON remains explicitly unverified. |
| M0.3 | Record a per-symbol matrix: Nasdaq-dividend verified, explicit manual schedule, or blocked. AAPL success cannot mark SPY/QQQ verified. |
| M0.4 | Verify the SOFR response paths in Section 6 and pin a synthetic response with the same verified shape. Record the normalization convention separately from the source quote convention. |
| M0.5 | Each enabled symbol has an explicit instrument identity; SPX/XSP cannot be selected through an SPY adapter or relabeled result. |
| M0.6 | Public fixtures contain no captured live option/dividend response bodies; private samples and local inputs are git-ignored. |

### M1 - Make exposure units explicit without changing analytics

**Deliverables:** Move-unit selector, correct labels/tooltips, version-1 compatibility tests.

| ID | Acceptance criterion |
|---|---|
| M1.1 | N5 passes for call, put, signed, and gross exposure, including an adjusted model spot distinct from actual spot. |
| M1.2 | One selector change produces zero HTTP calls and leaves stored JSON and all gamma/IV values unchanged. |
| M1.3 | Every exposure tooltip and legend changes units consistently; OI/gamma remain unscaled; nulls remain null. |
| M1.4 | Relative cell color does not change solely because units changed; exposure mode remains independent. |
| M1.5 | Both chart headings identify the actual ticker; scope metadata says 0DTE excluded. Version-1 snapshots remain viewable. |

### M2 - Acquire and resolve reference inputs

**Deliverables:** Two Protocols, selected adapters/manual path, canonical records, local schedule review, bounded DuckDB cache.

| ID | Acceptance criterion |
|---|---|
| M2.1 | N2 and N8 pass with mocked HTTP; no rate percentage is mistaken for a decimal or converted twice. |
| M2.2 | History with no future event cannot become a zero-dividend schedule without explicit reviewed coverage. Missing/invalid event amounts never become zero. |
| M2.3 | Review expiry, coverage end, exact-date matching, conflict rules, and estimate replacement behave exactly as Section 7. |
| M2.4 | A cache hit causes zero reference requests; a miss causes at most one request per required reference source; force refresh bypasses TTL but not cooldown. |
| M2.5 | Expired-cache fetch failure preserves the previous dashboard and cache entry and returns the common error envelope. There is no hidden stale/manual/fixture fallback. |
| M2.6 | A non-Nasdaq dividend stub and non-NY-Fed rate stub can resolve the same canonical inputs without concrete adapter imports in analytics/storage/frontend. |
| M2.7 | Clean-checkout fixture mode requires neither network access nor an untracked reference file. Synthetic economic timestamps remain fixed and collection timestamps remain actual collection timestamps. |
| M2.8 | Live reference inputs have effective/review dates and provenance; new API calls do not constitute or claim permission to access Nasdaq data. |

### M3 - Implement the cash-aware analytical path

**Deliverables:** Expiry contexts and cash-PV pricing, with unchanged transport and storage ownership.

| ID | Acceptance criterion |
|---|---|
| M3.1 | N1, N3, N4, and N6 pass. Expected numerical values are not obtained from the production implementation under test. |
| M3.2 | IV, gamma, model bounds, forward, and surface-side selection consume the same expiry context. There is one context per expiry, not one independent dividend calculation per function. |
| M3.3 | For changed rate/dividend assumptions, call and put IV are re-solved from unchanged midpoints before gamma is computed. |
| M3.4 | Actual spot controls strike filtering and GEX scaling; model spot controls pricing only. No double-counting with a nonzero continuous yield is possible in v2 configuration. |
| M3.5 | Ex-before-expiry/pay-after-expiry and past-ex/future-pay tests pass. Known cross-ex price-time mismatches fail; uncertain alignment carries the documented warning. |
| M3.6 | Invalid contexts and model-ineligible quotes remain explicit; no sigma, gamma, spot, or exposure clamping and no filter relaxation to match vendor output. |
| M3.7 | Existing pure analytics/provider-isolation tests still pass after signature updates. No network/database dependency enters analytics. |

### M4 - Persist and display the actual assumptions

**Deliverables:** Version-2 dashboard, backward-compatible reads, pricing-input details, atomic refresh integration.

| ID | Acceptance criterion |
|---|---|
| M4.1 | A new snapshot contains rate provenance, full resolved schedule/review, pricing contexts, both hashes, and algorithm/model identifiers; stored inputs suffice for numerical reproduction without cache access. |
| M4.2 | A version-1 database opens without destructive migration; GET preserves its saved values and metadata. A new refresh adds version 2 rather than mutating an old row. |
| M4.3 | Changing settings, a dividend estimate, or a cache entry leaves previously saved results unchanged. |
| M4.4 | Successful refresh replaces both charts and input details together. Any provider/input/transaction failure preserves the prior snapshot ID and collection time. |
| M4.5 | Frontend tests cover rates, cash events, estimated amounts, review coverage, model limitations, near-ex-date warning, unknown timestamps, and legacy provenance. |
| M4.6 | Input-details expansion, unit changes, clocks, page load, and symbol switching make no upstream calls. Manual force-reference refresh uses the same global gate. |
| M4.7 | No raw reference payload, credential, or local path is returned in the dashboard. Provider IDs appear only as provenance/source choices, never as pricing branches. |

### M5 - Isolate assumption changes on saved data

**Deliverables:** `reconcile.py`, four report outputs, and synthetic comparison evidence.

| ID | Acceptance criterion |
|---|---|
| M5.1 | N7 passes for one legacy snapshot and one version-2 snapshot; baseline disagreement exits nonzero and is reported before sensitivity conclusions. |
| M5.2 | All four scenarios use identical saved quotes, OI, actual spot, valuation time, scope, and contract population as inputs. Each scenario re-solves IV. |
| M5.3 | The CLI makes zero network calls and zero database writes; row counts, snapshot IDs, and saved JSON are unchanged afterward. |
| M5.4 | Reports show both GEX units, call/put inputs separately, changed eligibility counts, null-safe differences, and counterfactual/known-after-valuation labels. |
| M5.5 | Repeat runs produce identical numerical results and ordering. Cache deletion or updates do not affect reproduction. |
| M5.6 | README includes a copy-paste invocation and the requirement to stop the backend before opening its DuckDB file from this separate process. |

### M6 - Validate and hand off

**Deliverables:** Test/build logs, `docs/adr-0001-validation.md`, and updated README/setup examples.

| ID | Acceptance criterion |
|---|---|
| M6.1 | Full pytest, frontend tests, Python lint/type checks, frontend lint/type checks, and production build pass. Record commands, versions, machine, and commit. |
| M6.2 | All automated tests run with external HTTP disabled. Parser tests use synthetic source-shaped responses only. |
| M6.3 | Processing plus persistence for 2,000 contracts meets the existing 5-second p95 requirement over 20 warmed runs on a recorded 4-core/8-GB-or-better machine, excluding external network time. GET-latest meets the existing 500-ms p95 requirement. |
| M6.4 | For every live-enabled symbol, record one complete refresh and one cache-hit refresh, source IDs, input dates, review coverage, warnings, request counts, and resulting snapshot IDs. Manual-schedule symbols are labeled as such, not as verified Nasdaq-dividend integrations. |
| M6.5 | Hand-reproduce one live-enabled cell's IV/gamma/GEX from permitted private normalized inputs and reproduce its per-$1 conversion. Do not publish the private source data as test fixtures. |
| M6.6 | Reopen a saved dashboard with all providers unavailable. Displayed values and inputs are unchanged; unit switching still works without upstream access. |
| M6.7 | A vendor-comparison note confirms instrument, expiry, strike, exposure unit, scope, and available input timestamps. Any remaining unknown vendor inputs are stated; there is no arbitrary vendor-match tolerance. |
| M6.8 | Documentation does not call this an American pricing engine, a full risk-free curve, actual dealer positioning, guaranteed live data, or an authorized Nasdaq service merely because requests succeeded. |

## 16. Alternatives considered and rejected for this increment

| Alternative | Decision and reason |
|---|---|
| Keep undocumented zero yields and tune numbers until the vendor matches | Reject. Conceals the source of errors and cannot establish correct units or model inputs. |
| Use trailing annual dividend yield as the sole correction | Reject for the new path. It does not identify which cash events occur before a particular expiry; the source history also has adjustment/coverage qualifications. |
| Fetch a new spot to match a vendor | Reject. Changes the comparison input and breaks the chain-contained-price contract; it does not establish synchronized option quotes. |
| Assume a dividend-history endpoint is a complete forecast | Reject. Forward coverage and undeclared expected events must be handled explicitly. |
| Infer forwards from put-call parity automatically | Defer. Quote quality and American exercise make this a separate inference problem, not a transparent replacement for documented inputs. |
| Introduce QuantLib/American finite differences now | Defer. More complete exercise handling needs a separately validated numerical model; this change keeps the current deterministic BSM-based architecture. |
| Add a bootstrapped OIS/term curve now | Defer. The flat observed overnight proxy is explicitly labeled; a curve requires additional instruments, conventions, and calibration. |
| Add reference-data workers, queues, or a microservice | Reject. Reference inputs are resolved only on a manual refresh and cached in the existing embedded database. |
| Rewrite the backend to introduce a general pricing platform | Reject. One new context builder and isolated reference inputs address the identified causes. |

## 17. Consequences and definition of done

The application gains explainable units, updated rate observations, dated ordinary cash events, consistent cash-aware pricing/surface inputs, and reproducible sensitivity analysis. The cost is two narrow reference interfaces, a small cache table, versioned snapshot metadata, and a local schedule-review obligation where the source cannot supply a complete expected horizon.

It still has source-timestamp uncertainty, estimated future distributions, a flat-rate assumption, a European approximation for American contracts, and potentially different data/Greeks from another vendor. These limitations are not removed by changing the README or by obtaining visually similar cells.

Software completion requires M1-M5 and M6 automated/offline criteria. Live completion additionally requires M0 and applicable per-source/per-symbol M6 evidence. A blocked Nasdaq dividend adapter must be reported as blocked; an explicitly selected manual schedule can complete the cash-model feature without pretending that automatic Nasdaq coverage was verified.

**Do not merge a change that silently substitutes zero dividends, uses payment date as the ex-date filter, applies cash dividends and a yield twice, scales GEX with model spot, reprices old snapshots on GET, or reuses old IV after changing pricing assumptions.**

## 18. Evidence and references

### Repository evidence

- [R1] Current orchestration and pricing implementation, reviewed at `7137567b8b4faf79ad9a47f69517a731617b0290`: [app.py](https://github.com/zhuy9/GEX-lens/blob/7137567b8b4faf79ad9a47f69517a731617b0290/backend/app.py), [analytics.py](https://github.com/zhuy9/GEX-lens/blob/7137567b8b4faf79ad9a47f69517a731617b0290/backend/analytics.py).
- [R2] [Example settings](https://github.com/zhuy9/GEX-lens/blob/7137567b8b4faf79ad9a47f69517a731617b0290/backend/settings.example.json). These are repository examples, not the user's untracked running configuration.
- [R3] [Nasdaq chain adapter](https://github.com/zhuy9/GEX-lens/blob/7137567b8b4faf79ad9a47f69517a731617b0290/backend/nasdaq.py).
- [R4] [Original PRD](https://github.com/zhuy9/GEX-lens/blob/7137567b8b4faf79ad9a47f69517a731617b0290/docs/options_analytics_mvp_prd.md).

### Primary external references checked for this ADR

- [E1] QuantWheel, [How to read the GEX Heatmap](https://quantwheel.com/how-to/read-gex-heatmap). Supports existence of the formula selector; does not establish the selected setting or underlying numerical inputs in the owner's crop.
- [E2] Options Industry Council, [Black-Scholes Formula](https://www.optionseducation.org/advancedconcepts/black-scholes-formula). Pricing inputs and early-exercise limitation.
- [E3] Options Industry Council, [Options Exercise](https://www.optionseducation.org/referencelibrary/faq/options-exercise). Equity/ETF exercise and ex-dividend considerations.
- [E4] Nasdaq, [AAPL Dividend History](https://www.nasdaq.com/market-activity/stocks/aapl/dividend-history). Source's own historical adjustment, security/type, and forward-coverage qualifications. The page did not expose usable event rows to the ADR author.
- [E5] New York Fed, [SOFR last-five API response](https://markets.newyorkfed.org/api/rates/secured/sofr/last/5.json) and [Markets Data APIs](https://markets.newyorkfed.org/static/docs/markets-api.html). The response envelope was accessible during authoring; production source tests remain required.
- [E6] New York Fed, [SOFR description](https://www.newyorkfed.org/markets/reference-rates/sofr). Overnight nature of the benchmark.
- [E7] New York Fed, [Additional Information about Reference Rates](https://www.newyorkfed.org/markets/reference-rates/additional-information-about-reference-rates). Actual/360 and compounding conventions. This ADR's flat proxy is an application approximation, not the published SOFR Index algorithm.
- [E8] QuantLib, [AnalyticDividendEuropeanEngine implementation](https://github.com/lballabio/QuantLib/blob/master/ql/pricingengines/vanilla/analyticdividendeuropeanengine.cpp), inspected source blob `9e87c94ba114b3c0c0646d8ee0fb8549f0dfb91a`. Supports the discounted-dividend adjusted-spot approach; this ADR separately defines eligibility and payment discounting.
- [E9] Nasdaq, [Legal terms](https://www.nasdaq.com/legal), especially Sections 2 and 7. A successful public website request and personal use do not establish unrestricted access or redistribution rights. Apply the existing access gate to the new dividend adapter as well.

External sources support the stated source/model constraints. TTLs, review ages, schema choices, model timing conventions, module placement, and acceptance thresholds are **decisions of this ADR**, not assertions that a data provider or exchange mandates them.
