# GEX Lens: ADR-0001 implementation review

**Reviewed revision:** `8575da60e6c2c5eb03c3b0de5a694b342db048b5`  
**Scope:** ADR-0001 reference inputs, cash-PV pricing, persistence/orchestration, reconciliation, relevant tests, and frontend presentation. This is a focused implementation review, not a claim that every repository path was exhaustively re-audited.  
**Repository changes made:** None.  
**Verdict:** Keep the architecture. Correct the input and time invariants before expanding the analytics. The cash-PV mathematical path is coherent with the selected ADR approximation, but passing individual helper tests has not established that all protections are reached from the real caller.

## 1. Evidence and verification limits

The GitHub connector reported a successful [CI run for the reviewed commit](https://github.com/zhuy9/GEX-lens/actions/runs/35301967309). The [validation document](https://github.com/zhuy9/GEX-lens/blob/8575da60e6c2c5eb03c3b0de5a694b342db048b5/docs/adr-0001-validation.md) records 247 backend and 41 frontend tests in its final addendum. Those are repository/CI results, not a claim that I reran those suites locally.

I read the current implementation through the connector and executed 10 isolated probes using the retrieved function bodies and relevant canonical model definitions. Comments/docstrings were abbreviated in the probe bundle; the exercised executable logic was retained. One probe is a passing ADR N3 numerical control. The remaining probes demonstrate acceptance or control-flow behaviors described below. The operational-clock probe stubs the cache read and supplies the same arguments used by the actual caller; it is not a DuckDB integration test.

Local test environment: Python 3.13.5, Pydantic 2.13.4. This differs from the repository's pinned Python 3.12/Pydantic 2.9.2 environment. Local Git cloning failed because GitHub DNS resolution was unavailable; DuckDB was not installed. I did not run the complete pytest/Vitest suites, the production build, a browser session, or live market-data requests. Add the proposed regressions to the real repository and run its pinned environment before merging fixes.

The supplied ADR artifact has the same Git blob hash as the repository ADR: `5f11673722840f8339bfa232749be2d745ed910d`. Its requirements were used as the comparison basis; the cash-PV model was not silently replaced with a different pricing model.

## 2. What is working and should be preserved

The following are grounded in the inspected code:

- `build_expiry_pricing_context` supplies model spot, rate, dividend PV, forward, and time to the cash-aware quote-pricing path. IV is solved again from the midpoint, rather than retaining IV from the old assumptions. Actual spot remains separate for strike scope and GEX scaling.
- The surface consumes the same contexts' forwards instead of maintaining another independent cash-dividend adjustment.
- The heatmap uses one conversion factor for per-$1 display and keeps gamma and OI unchanged. Its legend bound and exposure tooltips are scaled consistently.
- Rate conversion is a named operation with preserved raw percentage and effective date. The pricing-input panel discloses the cash model and the absence of early-exercise modeling.
- The implementation does not pretend SPY's missing reviewed schedule is a zero-dividend schedule. The validation document explicitly leaves that live path incomplete.

Sources: [analytics.py](https://github.com/zhuy9/GEX-lens/blob/8575da60e6c2c5eb03c3b0de5a694b342db048b5/backend/analytics.py), [rates.py](https://github.com/zhuy9/GEX-lens/blob/8575da60e6c2c5eb03c3b0de5a694b342db048b5/backend/rates.py), [GexHeatmap.tsx](https://github.com/zhuy9/GEX-lens/blob/8575da60e6c2c5eb03c3b0de5a694b342db048b5/frontend/src/components/GexHeatmap.tsx), [PricingInputsPanel.tsx](https://github.com/zhuy9/GEX-lens/blob/8575da60e6c2c5eb03c3b0de5a694b342db048b5/frontend/src/components/PricingInputsPanel.tsx).

No rewrite, new service, scheduler, pricing-engine framework, or database replacement is justified by this review.

## 3. Prioritized findings

P1 means a path can produce materially misleading financial inputs or bypass an expressly required guard. P2 means a reproducibility, operational, or configuration defect that should be corrected in the next maintenance increment. These are engineering priorities, not assertions that a real user snapshot has already been corrupted.

| ID | Priority | Finding | Evidence level |
|---|---|---|---|
| C01 | P1 | Ex-date mismatch checks are skipped for the post-ex valuation case | Executed pricing-context/helper probes plus caller inspection |
| C02 | P1 | Duplicate owner events can deduct one dividend twice | Canonical-input acceptance and pricing-context probe; resolver iteration inspected |
| C03 | P1 | Reference symbol/provider/currency boundaries are not fully enforced | Canonical/normalizer probes and orchestration inspection |
| C04 | P2 | Operational attempt time is replaced with valuation time | Caller inspection plus cache-eligibility probe |
| C05 | P2 | Next-day counterfactual inputs fail before hindsight labeling | Executed review-policy probe using diagnostic caller arguments |
| C06 | P2 | Future-effective manual rates are accepted for live pricing | Executed manual-rate resolver probe |
| C07 | P2 | Equal-amount source records with different payment dates depend on input order | Executed source-normalization probe |
| C08 | P2 | `pricing_model` is advertised as configurable but is not used to select or validate execution | Static inspection |

### C01. Validate price/event alignment before filtering events for PV

**Code:** `analytics.py::build_expiry_pricing_context`, `check_price_time_alignment`; `nasdaq.py::NasdaqProvider.fetch_chain`.

Current ordering:

```python
ex_at = dividend_ex_at(event.ex_date)
if not (valuation_at < ex_at <= expiry_at):
    continue
# Alignment check occurs only below this point.
```

The helper contains a check for a date-only spot from before ex-date paired with a valuation after the ex-event. That case can never reach the helper through this loop: the event has already been excluded by `valuation_at < ex_at`.

**Isolated reproduction:** synthetic ex-date September 18, 2026; spot as-of date September 17; valuation September 18 at 10:00 New York; option expiry September 25. Calling the helper directly raises `DIVIDEND_PRICE_TIME_MISMATCH`. Calling `build_expiry_pricing_context` returns `status="OK"`, zero dividend PV, and no context warnings. A precise pre-ex spot paired with a precise post-ex chain timestamp is also skipped in this direction.

There is a second wiring gap: `ChainSnapshot` defines `spot_asof_date`, but the current Nasdaq chain adapter does not populate it in its returned snapshot. Its last-trade response contains a date that the existing source contract discusses. Adding a canonical field did not make the live adapter supply that evidence.

**Required fix:** make snapshot/event alignment validation a distinct operation, performed before PV eligibility filtering. Its event set must include known events that can fall between the observed spot time/date and valuation, including events already ex at valuation. Keep the PV rule unchanged: an already-ex dividend is not deducted again. Populate the verified date-only source field without inventing a time of day. Preserve uncertainty when the available evidence cannot establish which side of the event the spot represents.

Do not simply validate every historical event forever. Use the price/valuation interval and available reviewed/recent source events to determine relevant boundaries. Retain enough event evidence to check the boundary even when it no longer belongs to the forward PV sum.

**Acceptance criteria:**

- A pre-ex date-only spot with post-ex valuation fails from the normal pricing/refresh path, not only from a direct helper call.
- Precise timestamps straddling the ex-event fail in either direction; include a difference smaller than 120 seconds.
- Two confirmed post-ex observations succeed, and that event contributes zero PV.
- A future eligible event is still deducted exactly once; payment after option expiry remains allowed.
- Unknown same-day timing produces the documented uncertainty warning rather than a fabricated timestamp.
- The verified last-trade date appears as `spot_asof_date`; precise `spot_asof` remains null when unavailable.

Source: [pricing/guard implementation](https://github.com/zhuy9/GEX-lens/blob/8575da60e6c2c5eb03c3b0de5a694b342db048b5/backend/analytics.py), [chain snapshot construction](https://github.com/zhuy9/GEX-lens/blob/8575da60e6c2c5eb03c3b0de5a694b342db048b5/backend/nasdaq.py). ADR sections 7.4 and 7.5 intentionally express different responsibilities.

### C02. Reject duplicate events at the schedule boundary

**Code:** `models.py::ScheduleReview`, `market_inputs.py::resolve_dividend_schedule`.

`ScheduleReview` checks coverage-date ordering, but not event-ID or ordinary-event ex-date uniqueness. Resolution iterates every expected event, and the pricing context sums every resolved event.

**Isolated reproduction:** the review accepts the same event twice. At synthetic spot 100 and rate zero, one future $2 event gives model spot 98; two copies give model spot 96, with repeated `used_event_ids`. Zero interest is an explicit test input here, not a recommended live default.

**Required fix:** in the canonical local-review boundary, require unique, nonempty event IDs and one ordinary event per ex-date for this supported model. Reject duplicated local entries with a readable configuration error instead of silently summing them. Reject events outside the review interval. Keep source exact-duplicate collapse as a separate, documented normalization rule.

**Acceptance criteria:** duplicate IDs, identical duplicated owner entries, same ex-date under different IDs, and out-of-coverage events are rejected. A single reviewed event contributes once even when the source returns duplicate rows. Tests must exercise review -> resolution -> context construction.

Sources: [models.py](https://github.com/zhuy9/GEX-lens/blob/8575da60e6c2c5eb03c3b0de5a694b342db048b5/backend/models.py), [resolver](https://github.com/zhuy9/GEX-lens/blob/8575da60e6c2c5eb03c3b0de5a694b342db048b5/backend/market_inputs.py).

### C03. Keep requested identity through reference resolution

**Code:** `LocalReferenceInputs`, `_collect_and_save`, `_fetch_dividend_feed_cached`, `_normalize_source_records`.

The chain's identity is checked, but the analogous reference boundaries are incomplete:

- `schedules["AAPL"]` may contain `ScheduleReview(symbol="QQQ")`; the local model accepts it.
- The app retrieves the review under the selected symbol, then the resolver calls the dividend provider with `review.symbol`. A copy/paste error can therefore select another instrument's reference feed.
- The dividend feed's `symbol` and `provider_id` are not checked against the requested cache key before reuse/upsert.
- Ordinary records are indexed by ex-date without checking their symbol or USD currency. The Nasdaq adapter itself preserves a nonempty non-USD currency string, so this is not solely a hypothetical custom-adapter issue.

**Probe evidence:** the local model accepts an AAPL key with a QQQ review; the source normalizer accepts an ordinary QQQ/EUR record. I did not claim that the real Nasdaq feed returned that record or that I executed a full live refresh with it.

**Required fix:** validate the local key/review symbol equality once. Pass the requested symbol explicitly into reference resolution. Validate batch/feed identity and each usable record's symbol/currency before caching or converting it to a resolved cash event. Apply the same configured-identity consistency rule to rates. An arbitrary injected provider ID is permitted, but it must agree with its configured identity; do not reintroduce a fixed Nasdaq-only enum.

**Acceptance criteria:** AAPL never calls a reference adapter for QQQ. Wrong-symbol/wrong-provider batches fail before cache writes. Known non-USD ordinary records fail rather than becoming USD amounts. Valid injected providers still traverse resolution and persistence without Nasdaq-specific logic. Test the adapter/resolver/application integration, not only Pydantic parsing.

Sources: [app.py](https://github.com/zhuy9/GEX-lens/blob/8575da60e6c2c5eb03c3b0de5a694b342db048b5/backend/app.py), [market_inputs.py](https://github.com/zhuy9/GEX-lens/blob/8575da60e6c2c5eb03c3b0de5a694b342db048b5/backend/market_inputs.py), [NasdaqDividendProvider](https://github.com/zhuy9/GEX-lens/blob/8575da60e6c2c5eb03c3b0de5a694b342db048b5/backend/nasdaq.py).

### C04. Separate economic time from operational time

**Code:** `app.py::_collect_and_save` passes `attempt_started_at=valuation_at` to both reference resolvers. It sets `resolved_at` to the chain's collection-start timestamp.

The ADR distinguishes the economic valuation instant from the attempt's real start. TTL is an operational measure; eligibility of a rate/event is an economic measure. The current caller makes them equal even when the chain has an earlier verified pricing timestamp or fixture valuation clock.

**Isolated reproduction:** valuation 15:00 UTC, cached fetch 15:05, real attempt 15:10. The same cache is a hit under its true 300-second age and a miss under the supplied negative 300-second age. An old valuation can similarly make a current owner review appear to come from the future. Manually resolved rate retrieval times inherit the same incorrect caller value.

**Required fix:** capture real UTC attempt start once after acquiring the refresh gate and pass it explicitly. Use valuation time only for economic selection/pricing. Record real completion time for reference resolution. Preserve fixture economic/review-age conventions explicitly; do not fix all live operational timestamps to the fixture clock merely to make tests pass. TTL must use real retrieval/attempt time even for fixtures.

**Acceptance criteria:** cache eligibility uses real attempt time when chain time differs; rates are still selected against valuation time; a current valid live review is not rejected due to a delayed quote; metadata satisfies actual attempt <= reference resolution, without rewriting collection/valuation timestamps. Include a test with distinct clocks rather than `ATTEMPT_AT = VALUATION_AT` in every fixture.

Sources: [app.py](https://github.com/zhuy9/GEX-lens/blob/8575da60e6c2c5eb03c3b0de5a694b342db048b5/backend/app.py), [cache/review logic](https://github.com/zhuy9/GEX-lens/blob/8575da60e6c2c5eb03c3b0de5a694b342db048b5/backend/market_inputs.py).

### C05. Counterfactual diagnostics should not pretend to be live refreshes

**Code:** `reconcile.py::_resolve_scenario` calls the live review resolver with `attempt_started_at=valuation_at`, then computes `known_after_valuation` only after resolution succeeds.

**Isolated reproduction:** September 17 snapshot, corrected review entered September 18, valid coverage spanning both dates and the option horizon. `_validate_review` rejects with `DIVIDEND_REVIEW_REQUIRED` because the review age is -1. The diagnostic cannot reach its later-known-input label. A same-day hindsight test will not expose this.

**Required fix:** separate structural/event resolution from the live operational freshness policy. The diagnostic must still validate currency, event uniqueness, payment order, coverage of the saved horizon, and numerical bounds. It must not require a later-authored counterfactual review to have existed before the historical snapshot. Keep the explicit `known_after_valuation` label and zero-network/read-only behavior.

Avoid a generic `ignore_validation=True` switch. Reuse a pure resolver and invoke a small, specifically named live freshness validator only in live refreshes. Define the counterfactual policy explicitly in the ADR if the implementation contract needs clarification.

**Acceptance criteria:** next-day and later-month scenario reviews covering the saved interval succeed with hindsight labels. Same-as-saved inputs reproduce the baseline. Insufficient coverage and invalid amounts still fail. Database contents and normalized quote/OI/spot/time inputs remain unchanged. No source requests are made.

Source: [diagnostic resolver](https://github.com/zhuy9/GEX-lens/blob/8575da60e6c2c5eb03c3b0de5a694b342db048b5/backend/reconcile.py). Compare ADR section 12 with the live-only freshness policy.

### C06. A live manual rate must not have a future effective date

**Code:** `market_inputs.py::resolve_rate`, manual branch.

The staleness check is only `(valuation_date - effective_date).days > 7`. A negative age passes. In contrast, automatic rate selection explicitly filters out future effective dates.

**Isolated reproduction:** a September 18 effective rate is accepted for September 17 valuation.

**Required fix:** require `0 <= age_days <= 7` for live manual-rate resolution and report a specific input error for a future effective date. Apply the rate's numerical bounds during local input validation so user input errors do not first surface as an internal response-construction failure. Keep intentionally later-known diagnostic scenarios subject to their distinct explicit policy, rather than weakening the live rule.

**Acceptance criteria:** valuation-day and seven-day-old manual rates pass; eight-day-old and future-effective live rates fail before analytics. Manual zero remains a valid explicit, dated assumption within the permitted bounds, never a fallback.

Source: [manual/automatic rate selection](https://github.com/zhuy9/GEX-lens/blob/8575da60e6c2c5eb03c3b0de5a694b342db048b5/backend/market_inputs.py).

### C07. Payment-date conflicts must not be resolved by response order

**Code:** `market_inputs.py::_normalize_source_records`.

Two records on one ex-date fail only when both amounts are present and unequal. If amounts match, the first record with a known amount wins even when non-null payment dates disagree. Payment date changes the cash discount horizon under this ADR.

**Isolated reproduction:** two synthetic $2 records with payment dates September 25 and October 2. Normalizing A,B chooses September 25; reversing the rows chooses October 2. Neither ordering raises a conflict.

**Required fix:** define event equality/conflict over the relevant economic fields, not amount alone. Conflicting non-null payment dates fail. For incomplete compatible records, use one explicitly specified merge rule or reject ambiguity; do not silently discard complementary known fields. Keep exact duplicate normalization order-invariant.

**Acceptance criteria:** permuting source rows cannot change a resolved schedule. Conflicting non-null payment dates raise `DIVIDEND_CONFLICT`. An owner-declared payment conflict continues to fail. The same ex-date with genuinely conflicting cash amounts continues to fail.

Source: [source normalization](https://github.com/zhuy9/GEX-lens/blob/8575da60e6c2c5eb03c3b0de5a694b342db048b5/backend/market_inputs.py).

### C08. Do not expose a configuration choice that execution ignores

**Code:** `models.py::Settings.pricing_model`, `app.py::get_config`, `_collect_and_save`.

`pricing_model` is an arbitrary string accepted by settings and echoed by config. Execution always calls the cash-aware v2 path and records the fixed model ID. No inspected factory or startup check validates that the requested pricing-model string is supported.

Provider IDs being extensible does not require pricing algorithms to be arbitrarily selectable. These are different boundaries.

**Required fix:** for this increment, constrain the setting to `Literal["cash_pv_bsm_v2"]`, or remove the setting and publish the one supported model ID as application metadata. Do not add a model registry merely to justify the current string.

**Acceptance criteria:** a misspelled or unsupported model is rejected at startup; configuration, saved parameters, and the executed pricing path agree. Legacy snapshots remain readable and offline legacy reproduction remains available.

Sources: [settings](https://github.com/zhuy9/GEX-lens/blob/8575da60e6c2c5eb03c3b0de5a694b342db048b5/backend/models.py), [orchestrator](https://github.com/zhuy9/GEX-lens/blob/8575da60e6c2c5eb03c3b0de5a694b342db048b5/backend/app.py).

## 4. Refactor recommendations after the correctness fixes

### 4.1 Keep one input boundary, not more defensive layers

Put event uniqueness, review identity, amount/currency, and local-field validation in their canonical/input boundary. Put live freshness in its explicitly live policy. Once `MarketInputs` is constructed, analytics should receive a consistent bundle, not repair provider data or search for another default.

Most new checks proposed above are not arbitrary defensive coding. They protect invariants that change the financial answer. Conversely, broad catches such as `except Exception` around Pydantic parsing obscure implementation bugs; catch `ValidationError` at that boundary instead.

### 4.2 Shorten orchestration without adding services

Move the reference-input orchestration block from `_collect_and_save` into a function in the existing `market_inputs.py`, with selected providers, requested symbol, economic valuation, and operational attempt supplied explicitly. Do not make that module instantiate a concrete Nasdaq adapter. Preserve the startup factory as the only source selection point.

Target flow:

```text
capture real attempt time
collect canonical chain
validate requested identity
resolve/validate reference inputs
validate snapshot/event alignment
build expiry contexts and analyze
build immutable dashboard and save atomically
```

That gives each function one state transition. No `ReferenceDataService`, `MarketDataRepository`, plugin registry, or additional process is necessary.

### 4.3 Stop duplicating calculation definitions between app and diagnostic

`app.py::_calculation_input_hash` and `reconcile.py::_quote_time_hash` separately construct contract/threshold representations. Avoid a second source of truth: share a small pure canonical-input-payload helper in an existing appropriate module, and derive the hashes from it. Store/identify the numerical policy needed for reproduction; a hash detects a change but cannot reconstruct a missing historical policy by itself.

Do not introduce a generic serialization framework. Use one concrete representation for this application's supported inputs. Keep numeric equivalence/order tests, including Decimal storage round trips, as the acceptance gate.

### 4.4 Make the pricing summary understandable without opening every warning

The compact summary still prints `r / q`, with `q=0` under the cash model. That is mathematically intentional, but users investigating a dividend discrepancy can read it as "dividends are still zero." Show a cash-model summary such as:

```text
Rate: dated flat proxy
Dividends: cash schedule; 1 estimated event
Model: cash-PV BSM; early exercise not modeled
```

Keep the exact legacy r/q display for v1 snapshots. Derive estimated-event warnings from the resolved schedule so manual and provider-backed estimates are presented consistently. The current manual branch returns estimated event status without adding `DIVIDEND_AMOUNT_ESTIMATED`, while the provider-backed estimate fallback adds it. The expanded table already exposes the status; this recommendation makes the compact disclosure consistent too.

Sources: [SnapshotMeta.tsx](https://github.com/zhuy9/GEX-lens/blob/8575da60e6c2c5eb03c3b0de5a694b342db048b5/frontend/src/components/SnapshotMeta.tsx), [PricingInputsPanel.tsx](https://github.com/zhuy9/GEX-lens/blob/8575da60e6c2c5eb03c3b0de5a694b342db048b5/frontend/src/components/PricingInputsPanel.tsx), [event resolver](https://github.com/zhuy9/GEX-lens/blob/8575da60e6c2c5eb03c3b0de5a694b342db048b5/backend/market_inputs.py).

## 5. Operational completion: SPY is still a separate gap

The repository validation document explicitly says SPY has no supplied reviewed schedule and remains incomplete for the cash-model live path. This is not a request to synthesize a dividend or loosen validation. Supply and review an actual supported schedule, with estimates explicitly labeled, before claiming the intended SPY use case is complete.

A source's dividend-history rows are evidence of past events, not proof that the next 60 days contain no events. Do not eliminate the forward-review requirement simply to make a successful empty API response produce a usable dashboard.

Once the correctness items are fixed and the schedule exists, use the read-only diagnostic on one saved SPY chain. Align instrument, expiration, exposure unit, quote/valuation times, OI, and per-side gamma before comparing another vendor. Vendor agreement is not a replacement for the ADR regression tests, and an American engine is not an automatic fix for unresolved inputs.

## 6. Recommended implementation milestones

### R1 - Fix time semantics and the ex-event guard

Scope: C01 and C04.

Acceptance: context/refresh regressions cover both sides of the ex-event; verified date-only source evidence is wired through; cache time differs deliberately from economic time in tests; prior successful snapshots are retained after rejection; no separate quote endpoint or new network behavior is added.

### R2 - Seal canonical reference inputs

Scope: C02, C03, C06, C07, and C08.

Acceptance: duplicate-event, mismatched-symbol/provider, non-USD, future-manual-rate, conflicting-payment-date, and unsupported-model cases fail at the appropriate boundary. Valid schedules remain order-invariant. A valid stub provider traverses the full application. No repeated downstream schema-repair code is introduced.

### R3 - Make diagnostics reusable and complete the target use case

Scope: C05, shared input-payload representation, compact disclosure, and SPY operational validation.

Acceptance: next-day counterfactual analysis succeeds with correct hindsight labels and no writes/HTTP; same-input numerical reproduction passes; valid pre-existing snapshots remain unchanged; a reviewed SPY schedule produces a new live result when authorized source access is available; tests/build run in the pinned project environment and the hand-off report states any remaining gap explicitly.

## 7. Verification commands for the coding agent

Use the repository's existing setup and lockfiles, not the isolated review environment. Run its full checks after adding the proposed regressions. Suggested new test targets are behavioral names, not requests to create another testing framework:

```text
test_refresh_rejects_pre_ex_spot_after_ex_valuation
test_nasdaq_preserves_verified_spot_asof_date
test_cache_uses_attempt_clock_not_chain_valuation
test_review_rejects_duplicate_ordinary_event_dates
test_reference_schedule_key_matches_review_symbol
test_dividend_feed_identity_and_currency_match_request
test_live_manual_rate_rejects_future_effective_date
test_source_payment_date_conflict_is_order_invariant
test_counterfactual_next_day_review_is_labeled_not_rejected
test_unsupported_pricing_model_fails_startup
```

The existing BSM/IV/gamma regression tolerances, cash-event eligibility, actual-spot GEX scaling, unit conversion, null semantics, and atomic snapshot behavior must remain unchanged except where the finding expressly corrects an error.

**Bottom line:** the useful simplification is stronger canonical inputs and clearer time ownership, not removing safeguards or starting over. Fix the few financial invariants that are currently bypassed, then reduce the code that exists only to shuttle or re-create the same state.
