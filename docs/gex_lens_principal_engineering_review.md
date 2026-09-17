# GEX Lens: Principal Engineering Review

**Repository:** `zhuy9/GEX-lens`  
**Reviewed commit:** `07898ef0621350c42cffc82dc8158ce1ab08bc94`  
**Review date:** September 17, 2026  
**Purpose:** Identify correctness problems and simplify the implementation while preserving the existing local, manual-refresh product.  
**Disposition:** Keep the architecture and numerical engine. Repair the data and UI state boundaries. Do not start a wholesale rewrite.

## 1. Executive assessment

This is **not an application that needs to be replaced because it was written by an AI model**. The important architectural decisions are already appropriate: one Python process, one embedded database, a small synchronous provider interface, pure analytical functions, and a React client. The implemented HTML heatmap is also a reasonable choice for the sticky headers, strike window, and spot divider the product now requires. Its departure from the original Plotly heatmap requirement is explicitly recorded as approved. [Source: application structure and approved deviation][src-agent]

The main problem is **uneven treatment of uncertainty**. Some code is very particular about module names, extra model fields, and fallback exception responses, while more important invariants are not enforced: a complete chain must actually be complete, counts must be nonnegative integers, timestamps must have known precision, the newest response must win, and time must continue advancing in the browser.

The highest-value change is not to reduce the number of lines indiscriminately. It is to reduce the number of places that independently interpret the same fact.

The recommended sequence is:

1. Fix the frozen browser clock and the paths that accept malformed or incomplete market data.
2. Make loading, refresh, failure, and response ordering explicit.
3. Complete the provider abstraction through the public API, not just the Python Protocol.
4. Remove redundant conversions and exception handling without removing genuine financial-data safeguards.
5. Strengthen behavioral tests and correct the documentation before adding features.

**No new server, queue, ORM, state-management framework, database engine, or numerical model is justified by this review.**

### Priority definitions

| Priority | Meaning |
|---|---|
| P1 | Fix before relying on the dashboard for ongoing research. A core interaction or data result can be wrong. |
| P2 | Correctness, maintainability, or observability work for the next focused refactor. |
| P3 | Lower-risk cleanup or release hygiene; do not let it delay P1 fixes. |

## 2. Scope and verification limits

The review is pinned to the commit above, not to a moving `main` branch. The repository tree contains 62 file entries. All seven backend application modules, all backend tests, all frontend application/test/component source, configuration files, the three documentation files, licensing text, agent instructions, and the SVG asset were inspected. The generated npm lockfile was inspected at its root dependency metadata and initial package entries; its entire transitive dependency graph was **not** audited. The supplied screenshot was also inspected. [Repository tree][src-tree]

Source access was through the connected GitHub tool. Direct cloning and archive download were unavailable in this environment. For executable checks, four backend source files and the frontend time helper were reconstructed locally from the retrieved text and verified against their Git blob SHA values before execution.

### What was executed

- Twelve backend probe observations, including a known-value BSM/gamma control, against the actual `models.py`, `provider.py`, `nasdaq.py`, and `analytics.py` source. HTTP was replaced with `httpx.MockTransport`; there were no live Nasdaq requests.
- A Node check importing the actual `time.ts` helper and evaluating the same render-time offset expression used in `App.tsx`.
- A check of the heatmap's UTC-based DTE algorithm against the backend's New York calendar convention.

The probe environment was Python 3.13.5, Pydantic 2.13.4, HTTPX 0.28.1, SciPy 1.17.0, and Node 22.16.0. These are **not** the repository's pinned Python environment. The observed control-flow and arithmetic failures are documented below, but the proposed fixes still need regression tests under the repository's supported versions.

### What was not executed

The complete repository `pytest` suite, Vitest suite, production build, lint/type checks, actual DuckDB transaction tests, browser rendering, and live-provider integration were not rerun. DuckDB was not installed locally, and a complete installable checkout was unavailable. Performance and browser-interaction concerns are therefore identified as static findings or measurement tasks, not presented as measured runtime regressions.

`docs/m5-validation.md` records 77 passing backend tests and eight passing frontend tests from an earlier run. Those are the repository's recorded results, not results independently reproduced in this review. [Recorded validation][src-validation]

**Terminology:** "Reproduced" means a focused executable check confirmed the behavior. "Static" means the behavior follows from the inspected code but was not exercised in the full application. "Refactor" denotes a proposed design improvement rather than a demonstrated bug.

## 3. Findings at a glance

| ID | Priority | Finding | Evidence level |
|---|---|---|---|
| R01 | P1 | Clock offset is recalculated every render, freezing cooldown and snapshot age | Reproduced |
| R02 | P1 | Malformed and truncated chains can be accepted as successful snapshots | Reproduced |
| R03 | P1 | Count parsing truncates fractional OI and permits negative gross exposure | Reproduced |
| R04 | P1 | A pending saved-data GET can overwrite a successful manual refresh | Static |
| R05 | P2 | Read/config failures are hidden as empty data or indefinite loading | Static |
| R06 | P2 | Timestamp parsing accepts date-only/naive values as pricing timestamps | Reproduced; documentation mismatch also confirmed |
| R07 | P2 | Provider identity is still hardcoded into public Python and TypeScript models | Reproduced |
| R08 | P2 | The one-second display clock invalidates the entire dashboard render tree | Static; performance impact not measured |
| R09 | P2 | Error translation is duplicated and unexpected failures lose useful diagnostics | Static/refactor |
| R10 | P2 | DTE, chart inspection, units, and chart-error recovery need correction | DTE reproduced; other items static/visual |
| R11 | P2 | Fixture collection time is frozen, undermining freshness and latest-snapshot ordering | Static |
| R12 | P2 | Domain facts are duplicated while typed records are converted back into loose dictionaries | Refactor; multiplier discrepancy reproduced |
| R13 | P3 | Persistence has avoidable encoding/schema duplication; retain its bulk path until measured | Refactor; replacement performance unverified |
| R14 | P2 | Tests emphasize prescribed structure more than critical behavioral sequences | Static |
| R15 | P3 | Setup instructions, status claims, and repository hygiene need reconciliation | Static |

## 4. Correctness findings and suggested fixes

### R01 - The refresh countdown and snapshot age freeze

**Priority:** P1. **Status:** Reproduced with the actual time helper.

**Location:** [`App.tsx`, calculation of `offsetMs` and `cooldownRemaining`][src-app-tsx]; [`lib/time.ts`][src-time].

`clockOffsetMs(serverTime)` returns:

```text
serverTime - Date.now()
```

`App` calls it again on every render. `secondsUntil` and `ageSeconds` then compute:

```text
estimatedServerNow = Date.now() + offsetMs
                  = Date.now() + serverTime - Date.now()
                  = the old serverTime
```

Apart from negligible timing between calls, estimated server time remains the time of the last config response. The one-second timer causes renders but does not make this clock advance.

**Reproduction:** With a server time of 12:00:00 and a deadline of 12:01:00:

| Actual elapsed seconds | Displayed cooldown | Displayed snapshot age |
|---:|---:|---:|
| 0 | 60 | 0 |
| 30 | 60 | 0 |
| 60 | 60 | 0 |
| 120 | 60 | 0 |
| 600 | 60 | 0 |

After a refresh and config reconciliation, the Refresh button can remain disabled after the real cooldown expires. A snapshot that was fresh when config loaded also does not naturally transition to stale.

**Required fix:** Capture the offset **once when a config response is accepted**, and retain that offset until another accepted config response replaces it. Do not recalculate it from an old `server_time` on render.

A sufficient implementation is a single config-state value containing:

```ts
type ConfigSnapshot = {
  value: ConfigResponse;
  serverOffsetMs: number;
};

// In the successful config-fetch handler, not in App's render:
setConfigSnapshot({
  value: result,
  serverOffsetMs: Date.parse(result.server_time) - Date.now(),
});
```

Then use `Date.now() + serverOffsetMs` to derive age and remaining time. This is enough for a local MVP. A custom time-synchronization service is unnecessary.

**Preserve:** Server-side monotonic cooldown enforcement, no polling, no automatic POST, and the existing server timestamp/deadline fields.

**Acceptance tests:** Freeze time; receive config once; advance 30 seconds and assert the countdown decreased by 30; advance past the deadline and assert Refresh becomes enabled; advance snapshot age past 300 seconds and assert the stale warning appears. Repeat with the browser clock five minutes ahead and five minutes behind. Assert that timer advancement makes zero network requests.

### R02 - Invalid envelopes and incomplete pagination can become valid snapshots

**Priority:** P1. **Status:** Reproduced at the adapter boundary. Persistence of the accepted result follows from the inspected orchestration path; it was not tested against DuckDB here.

**Location:** [`nasdaq.py:79-192`, `fetch_chain`][src-nasdaq-fetch]; [`nasdaq.py:202-219`, `_parse_body`][src-nasdaq-body]; [`app.py`, `_collect_and_save`][src-app]; [recorded `totalRecord` semantics][src-source-contract].

These fallbacks erase the distinction between absent data and a valid empty response:

```python
data = body.get("data") or {}
rows = (data.get("table") or {}).get("rows") or []
```

A response with a valid `lastTrade` and `rCode=200`, but a missing `table` or `rows=null`, returns a successful snapshot with zero contracts. The adapter treats the resulting short page as successful completion.

It also ignores `data.totalRecord`, even though the source-contract document records it as the total number of filtered rows including expiration headers. A response claiming 1,906 rows but containing only two rows was accepted after one request.

There is another symptom of the same design problem: contract identity is parsed in both the pagination-progress check and `_normalize_rows`. The latter contains a header-based fallback when the drill-down URL is absent. The former calls `_drilldown_expiration_and_strike(r["drillDownURL"])` first. A missing URL raises `KeyError`; an explicit null raises `TypeError`. The supposed fallback is not consistently reachable.

**Required fix:** Make one adapter-level page parser own the envelope and row shape. It should return a small typed page result containing the parsed spot, nullable verified timestamp, total row count, and parsed rows. Do not validate every unused Nasdaq display field; allow unrelated upstream fields to exist.

The required rules are:

- `data` and `table` must have the expected object shape; `rows` must be a list. Missing or incorrectly typed required structure is `SCHEMA_ERROR`, not an empty success.
- A valid empty result is allowed only when the source explicitly describes an empty complete collection. `rows=[]` and `totalRecord=0` is different from a missing table.
- Use the verified `totalRecord` semantics to check completion. Reject a premature short/empty page or a changing total that prevents a consistent collection. Preserve request, size, contract-count, and collection-window caps.
- Parse row identity once. Use that identity for progress detection, duplicate checks, and normalized contracts. For an absent URL, use the carried expiration header and validated strike when both are available; otherwise raise `SCHEMA_ERROR`. Do not accidentally throw generic Python exceptions before the existing header-based fallback can run.
- Raise `ProviderError` for invalid external data. Do not catch unrelated programming errors and relabel them as harmless missing rows.

Update the pagination test helper as part of this fix: `test_nasdaq.py::_body` currently sets `totalRecord=len(rows)` separately for each page. Supply the same verified collection-wide total to every page of a multipage fixture. [Parser tests][src-nasdaq-tests]

**Preserve:** First-page underlying price, chain-only requests, all-page collection before publication, exact duplicate handling, conflicting-duplicate rejection, and the prior successful snapshot on failure.

**Acceptance tests:** Missing table, missing rows, null rows, array-valued root, malformed row, missing/null drill-down URL, premature final page, empty intermediate page, inconsistent `totalRecord`, exact-multiple page counts, legitimate empty complete result, and a normal multipage result. Every rejected collection must leave the previous snapshot ID and both chart datasets unchanged.

### R03 - Open-interest parsing can create false zeros and negative gross exposure

**Priority:** P1. **Status:** Reproduced.

**Location:** [`nasdaq.py:40-59`, numeric parsers][src-nasdaq-numbers]; [`models.py:46-70`, `OptionQuote`][src-optionquote]; [`analytics.py:141-149`, `_exposure_for`][src-exposure].

The current count parser converts through a float and then calls `int`:

```python
value = _parse_num(raw)
return None if value is None else int(value)
```

The observed behavior is:

```text
"1.9"      -> 1
"0.9"      -> 0
"-1000"    -> -1000
"NaN"      -> ValueError
"Infinity" -> OverflowError
```

`OptionQuote` does not reject negative OI. With call OI -1,000, put OI 600, gamma 0.02 on both sides, and spot 100, `build_gex` returns:

```text
Call exposure:  -$200,000
Put exposure:   $120,000
Gross exposure: -$80,000
Status:         COMPLETE
```

A particularly misleading path is `"0.9" -> 0`: the GEX engine intentionally treats explicitly reported zero OI as zero exposure even when gamma is unavailable. Truncation therefore turns an invalid count into a confident zero.

**Required fix:** Parse counts as finite, nonnegative integers without truncation. Recognized missing sentinels remain null. For a malformed quote-level OI or volume field, retain the identifiable contract, set the invalid field to null, and attach a quality flag such as `INVALID_OPEN_INTEREST`; do not quietly treat it as ordinary missing data. Invalid identity or envelope structure remains a collection failure.

Enforce nonnegative integer counts in the canonical model as well, so another provider cannot inject negative OI. An adapter should produce a valid canonical value once; downstream analytical code should not repeatedly repair it.

Use a separate parser for HTTP `Retry-After`. It is not an option-volume field. Handle its accepted forms deliberately and apply the PRD's minimum/default cooldown rule. In particular, a valid short numeric delay should be clamped to the configured minimum rather than automatically treated as a missing value. Test this under the intended HTTP contract before changing the rate-limit path.

**Preserve:** Real zero OI, unknown OI, midpoint-based IV, and the rule that missing OI does not prevent IV calculation.

**Acceptance tests:** Zero, positive integers, comma-separated integers, missing sentinels, negative values, fractional values, NaN, Infinity, and malformed strings. Assert that no valid complete gross-exposure cell is negative and that invalid OI cannot generate a complete zero cell.

### R04 - A stale GET can overwrite a newer POST result

**Priority:** P1. **Status:** Static; the full React race was not executed in this environment.

**Location:** [`hooks/useDashboard.ts`][src-use-dashboard]; [`App.tsx`, Refresh enablement][src-app-tsx].

The initial/symbol-change GET and manual POST each write `dashboard` and `status`. Starting a refresh neither invalidates nor aborts a saved-dashboard GET for the same symbol. The button is enabled once config exists, even while that GET remains pending.

A permitted ordering is:

```text
GET saved SPY starts and is delayed
User clicks Refresh
POST returns a new SPY snapshot; UI displays it
The old GET resolves; UI replaces the new snapshot with the old one
```

The existing symbol-switch test covers aborting a GET for a different symbol. It does not cover this same-symbol GET/POST ordering.

**Required fix:** Give the hook one request-generation value shared by loads and refreshes. Starting a new load or refresh invalidates earlier work; a response may update state only when its generation and symbol still match. Abort obsolete GETs as an optimization, but do not rely on cancellation alone as the state-consistency contract.

As a small additional UI safeguard, disable Refresh until the initial saved-data request settles. This should complement, not replace, response-ordering correctness.

Also keep Refresh pending through the one post-refresh config reconciliation. Currently `finally` clears `refreshing` and invokes a callback that discards the reconciliation promise. This opens a window for another POST before the updated cooldown arrives. Make the reconciliation callback awaitable; clear the pending state after it succeeds or fails, without automatically retrying either request.

**Preserve:** One accepted snapshot object drives both charts; old data stays visible during a refresh of the same symbol; changing symbols never displays the previous symbol's charts.

**Acceptance tests:** Deferred GET followed by successful POST followed by late GET; deferred GET followed by failed POST; rapid symbol switches; double-clicks; a slow reconciliation GET; failed reconciliation; and unmount during a request. Assert snapshot IDs, not just displayed prices.

### R05 - Failures are disguised as empty data or permanent loading

**Priority:** P2. **Status:** Static.

**Location:** [`hooks/useConfig.ts`][src-use-config]; [`hooks/useDashboard.ts`][src-use-dashboard]; [`api.ts`][src-api-ts]; [`App.tsx`][src-app-tsx].

`useConfig` catches every failure and exposes neither an error nor a retry state. On the first load, `config` remains null, selectors stay disabled, and the user has no in-app recovery action.

`useDashboard` catches every non-abort GET failure and sets `status="empty"`. A server failure or corrupt saved response is therefore presented as "No saved snapshot yet ... Click Refresh to fetch one." That is not what happened and can encourage an unnecessary provider request.

The request helper also assumes every failed HTTP response contains the expected JSON error envelope. A proxy HTML response or connection-related failure can lose the HTTP context and become an unrelated JSON/shape error.

**Required fix:** Use explicit load states: `loading`, `empty`, `ready`, and `error`. Only the API's `NO_SNAPSHOT` outcome means empty. Give config loading the same distinction. Retain an existing config/dashboard on a subsequent failed read, but expose the failure.

Add a **Retry loading** button that retries the failed GET only. Keep **Refresh market data** as the separate POST action. In the HTTP helper, preserve the response status and supply a bounded generic error when an error body is not the expected JSON envelope. Do not add a second full runtime schema system for every successful response merely to solve this small error-path problem.

**Acceptance tests:** Initial config failure and manual recovery; saved-dashboard 500; malformed error body; true `NO_SNAPSHOT`; and failed later config reconciliation with a previously valid config. Verify the recovery button sends no POST.

### R06 - Timestamp precision and timezone handling are not enforced

**Priority:** P2. **Status:** Reproduced for date-only input. The normal sample's `asOf=null` path is not the crashing case.

**Location:** [`nasdaq.py:221-227`, `_parse_chain_asof`][src-nasdaq-time]; [`models.py`, datetime fields and `year_fraction`][src-models]; [`app.py`, valuation selection][src-app]; [README limitations][src-readme].

`datetime.fromisoformat("2026-09-17")` yields a naive midnight datetime. The adapter accepts it as `chain_asof`, does not add `VALUATION_TIME_ASSUMED`, and the pricing code later subtracts it from an aware expiry timestamp. The focused probe raises:

```text
TypeError: can't subtract offset-naive and offset-aware datetimes
```

An ISO-looking string is also not sufficient evidence that an upstream field represents the option-chain pricing time. The source-contract document says the populated form of `data.table.asOf` is unverified.

**Required fix:** Accept a source pricing timestamp only after its field meaning and format are documented. Require timezone-aware values and normalize to UTC. Date-only values must remain date precision, not become midnight. Unknown or unverified timing must use the documented fallback with its explicit warning.

Canonical snapshot datetime fields must enforce aware timestamps. Put valuation selection and its warning in one provider-independent helper at the orchestration boundary. A newly added provider should not need to remember an undocumented warning convention to make fallback pricing honest.

**Important correction to the earlier preliminary review:** The README says valuation uses assumed 16:00 New York time. The inspected code actually uses collection start when `chain_asof` is missing; **16:00 is the expiry convention**, not its fallback valuation clock. Correct the README rather than changing working code to match that sentence.

Keep the current explicitly disclosed fallback for this MVP unless the product owner approves a different pricing-time policy. Do not invent a historical close timestamp from the date-only underlying-price field. It is reasonable to preserve that source date separately in a future metadata addition, but it must not masquerade as an exact timestamp.

**Acceptance tests:** Null, date-only, naive full datetime, aware UTC, aware non-UTC, malformed values, DST boundaries, and source/spot timestamp disagreement where both are actually known. Reopening a saved snapshot must not use the current time to recalculate its IV.

### R07 - The provider abstraction stops before the public API

**Priority:** P2. **Status:** Reproduced at response-model validation; full substitute-provider API/DB test remains to be added.

**Location:** [`models.py`, `ConfigResponse` and `DashboardResponse`][src-models]; [`types.ts`, `SourceMode`][src-types]; [`test_provider_boundary.py`][src-boundary-tests]; [README provider-extension claim][src-readme].

The Protocol itself is small and appropriate. The remaining coupling is here:

```python
source_mode: Literal["fixture", "nasdaq"]
```

and:

```ts
export type SourceMode = "fixture" | "nasdaq";
```

A public config response with `source_mode="review-stub"` fails validation. Therefore adding a provider requires more than an adapter and factory/config choice: public response models and frontend types also need editing.

The boundary test using `totally-different-test-stub` bypasses the application and calls `analyze_snapshot` with contracts. The application-level stub identifies itself as `fixture`. Consequently those tests do not prove the advertised end-to-end replaceability.

**Required fix:** Use a string for provider identity in `Settings`, `ConfigResponse`, `DashboardResponse`, and the frontend metadata type. Validate available configured implementations in `build_provider`; an unknown production implementation still fails startup. An explicitly injected test provider can carry a distinct configured identity, subject to the existing snapshot-identity check. The existing `source_mode` field name can remain to avoid a gratuitous API rename. React should display the provider ID and only special-case the existing fixture banner.

Keep `OptionsDataProvider.fetch_chain` as the single operation. Do not introduce a registry, abstract adapter hierarchy, capability negotiation, or a second live provider as part of this change. Keep the user's chain-contained-price requirement.

**Acceptance test:** Register/inject a test implementation with a distinct configured identity, collect a valid two-expiration synthetic chain, POST a refresh, GET the saved result, and verify both chart payloads and provider metadata. Fail the test on HTTP/Nasdaq access. The test must not change analytics, storage schema, chart components, or public model definitions to accommodate that identity.

## 5. Design and simplification findings

### R08 - A display timer reaches the heavy chart subtree

**Priority:** P2. **Status:** Static; no claim of measured frame-rate or bundle-size impact.

**Location:** [`App.tsx`][src-app-tsx]; [`useTick.ts`][src-tick]; [`IvSurface.tsx`][src-surface-ui]; [`GexHeatmap.tsx`][src-gex-ui].

`App` owns a one-second tick. Every tick rerenders the full dashboard. `IvSurface` rebuilds its grid-percent array, Plotly data array, layout, and config objects on each render, even though the immutable analytical snapshot has not changed. Only the observed-point arrays are memoized. The heatmap also recomputes its visible rows and renders its cells each second.

**Recommended refactor:** Place the ticking display in small clock/status components, or put the immutable chart pair behind a memoized component receiving only snapshot data and GEX mode. Do not pass a `tick` prop to charts or a shared chart container. Keep Plotly data/layout/config references stable between actual snapshot changes. Preserve camera state through ordinary age updates and mode changes.

The surface module is imported eagerly from the application. Consider a lazy import for that panel so controls and the HTML heatmap do not wait for the 3D chart module. First inspect the actual production build; do not assume a particular bundle size. Do not change Plotly distributions until the required surface/scatter3d traces and build are verified.

**Acceptance tests:** Advance a display clock 60 seconds without changing the snapshot. Age/countdown update, network call count remains unchanged, and the chart-data transformation is not rebuilt 60 times. A manual refresh and GEX-mode change still update the appropriate chart. Confirm camera preservation in a real browser.

### R09 - Error handling hides failure instead of explaining it

**Priority:** P2. **Status:** Static/refactor.

**Location:** [`app.py`, exception handlers and `refresh`][src-app]; [`provider.py`][src-provider]; [`nasdaq.py`, `_get` and `_parse_body`][src-nasdaq].

The refresh route catches `ProviderError`, catches and rethrows `HTTPException`, and catches every other exception to construct another `HTTPException`. There is also a global generic exception handler. The route-level wrapping of unexpected exceptions prevents the global handler from being a useful diagnostic boundary, and neither location records a traceback.

The HTTP exception handler then reconstructs an error from an untyped `detail` dictionary using `.get` defaults and a cast. These are multiple representations of the same small error contract.

**Recommended refactor:** Retain one JSON error-response builder. Map expected provider errors in one location. Keep explicit HTTP errors for unsupported symbols, missing snapshots, contention, and cooldown. Let unexpected exceptions reach one handler that logs a traceback and returns the sanitized generic 500 envelope.

Use standard-library logging with the symbol, provider ID, operation, and exception. Do not log complete raw payloads, secrets, or authorization headers. A hosted observability service is unnecessary.

Keep lock release in `finally`. Do not remove the provider/transport error distinction or expose exception internals to the browser. Replace exception swallowing with a meaningful error state, not a growing exception-class hierarchy.

**Acceptance tests:** A deliberate unexpected analytics/storage exception emits a diagnostic log and returns the common 500 envelope, releases the refresh lock, and preserves old data. Provider rate limits still update the deadline and emit the documented status and retry header. No raw HTML or traceback is returned to React.

### R10 - Presentation must preserve the meaning of the analytical data

**Priority:** P2. **Status:** DTE mismatch reproduced; other findings are static or visible in the supplied screenshot.

**Locations:** [`GexHeatmap.tsx`][src-gex-ui], [`IvSurface.tsx`][src-surface-ui], [`SnapshotMeta.tsx`][src-meta], [`ChartErrorBoundary.tsx`][src-chart-error].

**Calendar DTE is inconsistent.** The backend uses the New York calendar date; the heatmap helper deliberately uses UTC. At valuation time `2026-09-17T02:00:00Z`, New York is still September 16. September 18 is two calendar days away under the backend convention, but the UI displays one. Correct the existing display helper using `Intl.DateTimeFormat` with `timeZone: "America/New_York"`; this does not require another API or a date library. Use the saved valuation time, not the current clock.

**The surface cannot be inspected at interpolated grid points.** Its trace sets `hoverinfo: "skip"`, while only the observed markers have hover text. Supply a grid hover template containing expiration, moneyness, fractional DTE, and IV, labeled as surface-grid/interpolated data rather than raw observed points. Preserve `connectgaps=false`.

**Units are not sufficiently visible.** Dollar-valued heatmap cells need a nearby label such as `USD delta-notional change per 1% underlying move`, plus the selected scope. Add numerical endpoints to the color legend so "High" has a quantitative meaning. Do not rename the signed proxy as observed dealer positioning.

**No usable GEX is not the same as no axes.** The heatmap checks for empty strike/expiry arrays, not for zero complete cells. A populated but entirely invalid grid can therefore look blank without a clear explanation. Show an explicit unavailable/insufficient-data message and exclusion counts while retaining any useful per-cell inspection. Genuine zero exposure must remain a visible zero, not unavailable.

**Chart failure recovery is incomplete.** The React boundary's `hasError` is never reset for a same-symbol new snapshot. Give it an explicit reset action or reset it when snapshot identity changes. Its message also speculates about WebGL even for the HTML table; use a panel-appropriate error. Test real Plotly/WebGL initialization failure, not only a thrown React render error, and connect the wrapper's supported error callback as needed.

**Screenshot observation:** The 3D scene occupies a relatively small central area and dense black markers obscure parts of the surface. Tune panel height, camera, and marker size/opacity in a browser. Do not smooth away steep sections or remove observations to make a chart look cleaner without changing and documenting the analytical model.

**Acceptance tests:** UTC/New York date-boundary examples, DST examples, all-zero versus all-null GEX, required grid/point hover content, legend units, and recovery from a failed chart after a subsequent valid snapshot. At 1280x800 and a wider desktop width, axis labels and controls remain readable without horizontal page overflow.

### R11 - Fixture data freezes collection time, not just valuation time

**Priority:** P2. **Status:** Static.

**Location:** [`fixtures.py`, `FIXED_VALUATION_AT` and returned snapshot][src-fixtures]; [`storage.py`, latest ordering and retention][src-storage].

The fixed synthetic valuation date is a good idea: it keeps fixtures inside the DTE window. However, both `collection_started_at` and `collected_at` are also set to that fixed date.

This has two unintended effects. Newly generated fixture data appears collected months ago. Also, all fixture snapshots have identical collection times, so "latest" and retention are determined by random UUID ordering rather than refresh order. A GET after a successful fixture refresh can select a different saved snapshot; after retention fills, a just-created snapshot can fail to be among the retained UUIDs.

**Required fix:** Keep synthetic quote timestamps and `chain_asof` fixed. Use the actual collection start/end instants for the collection fields. With `chain_asof` populated by the fixed synthetic clock, analytical values remain deterministic while collection metadata reflects reality.

The fixture generator also uses nonzero per-symbol dividend yields while the example configuration supplies zero. This is not itself proof of a pricing defect, but it makes the default demo a model-input mismatch rather than a controlled known-volatility example. Align the default synthetic generation inputs with the example configuration, or document the mismatch explicitly and keep independent numerical reference tests separate.

**Acceptance tests:** Two fixture refreshes preserve the same analytical values while collection times advance. GET latest returns the ID from the most recent POST. Repeat through retention pruning. Synthetic valuation/quote times remain fixed and the synthetic banner remains present.

### R12 - Use typed facts consistently; do not build a larger abstraction layer

**Priority:** P2. **Status:** Refactor, with a reproduced multiplier inconsistency.

**Location:** [`models.py`][src-models]; [`analytics.py`, GEX and surface assembly][src-analytics]; [`app.py`, scope constants][src-app].

Several opportunities are small but worthwhile:

**One scope definition.** Strike bounds are defined in both `app.py` and `analytics.py`; the former reports the policy and the latter executes it. Keep one frozen scope record or a single shared constant definition, used by config output, snapshot parameters, and analytics. Do not create a configurable rules engine.

**One multiplier contract.** `OptionQuote.multiplier` accepts arbitrary integers, but `_exposure_for` always multiplies by 100. A canonical quote with multiplier 50 is accepted and still produces the 100-share result. This MVP supports standard 100-share contracts, so enforce that scope rather than quietly ignoring the supplied value. Either constrain supported normalized records to 100, or ensure an explicitly nonstandard record is excluded before exposure calculation. Do not expand to adjusted deliverables as part of this refactor.

**Keep typed quotes through grouping.** `build_gex` converts a `PricedQuote` into loose nested dictionaries containing `oi` and `gamma`, then rebuilds a typed cell. Group the existing typed call/put records instead. A small typed pair or a dictionary keyed by `Literal["C", "P"]` is sufficient. The surface's `dict[date, dict]` can similarly use a small local typed slice record. Neither needs a service class or separate repository.

**Preserve straightforward early exits.** `price_quote` is a readable sequence of eligibility checks. It is not a good candidate for a configurable pipeline of validator objects. A small result-construction helper is optional; the ordering and exact exclusion reason must remain obvious.

**Do not confuse typing with validation.** Repeating `ConfigDict(frozen=True, extra="forbid")` is mostly harmless boilerplate. A common frozen model base can reduce repetition, but that is lower value than enforcing count and timestamp invariants. Do not replace all models just to reduce line count.

**Acceptance tests:** Changing the single scope definition changes both reported parameters and actual eligibility. Unsupported multipliers cannot produce supported GEX cells. Existing deterministic analytics, exclusion ordering, null semantics, sorted axes, and output units remain unchanged for valid inputs.

### R13 - Simplify persistence carefully; its bulk-loading choice is not automatically wrong

**Priority:** P3. **Status:** Refactor candidate; alternative performance not benchmarked here.

**Location:** [`storage.py`][src-storage]; [`schema.sql`][src-schema]; [`test_performance.py`][src-performance-tests].

The current path writes quotes to a temporary CSV and lets DuckDB bulk-load it. It adds a filesystem round trip, a manual CSV representation, a second column/type map, and cleanup handling. However, the author explicitly introduced it to avoid slow repeated parameter binding. Removing it in favor of per-row `execute` or `executemany` without measurement would be a poor review recommendation.

**Immediate, behavior-preserving cleanup:**

- Remove `default=str` from serialization of an already JSON-compatible dashboard. It can conceal an unexpected object instead of exposing a broken serialization contract. Serialize strictly and reject nonfinite values before saving.
- Keep the schema, CSV column order, and INSERT projection aligned through one small explicit mapping or a test. Do not introduce an ORM or runtime schema-generation framework.
- Use explicit UTF-8 for the temporary text file and ensure cleanup failure does not hide the primary database failure.
- Retain one transaction for snapshot insertion, quote insertion, and pruning. Retain the database lock and source/symbol filtering.
- Replace the health endpoint's full latest-dashboard load with a small database check; health does not need to decode an analytical snapshot.

**Potential later simplification:** Benchmark one in-memory bulk-insert approach supported by the pinned DuckDB version against the existing CSV path, using realistic row counts and null/date/decimal data. Adopt it only when it demonstrably simplifies the code without violating the measured latency and round-trip requirements. Do not add Pandas or Arrow solely because they are fashionable choices for an otherwise small application.

The 20-snapshot limit makes the current pruning loop small. A new retention subsystem or migration framework is unnecessary.

**Acceptance tests:** Null versus zero, decimal strike identity, timezone-bearing timestamps, empty strings versus missing optional metadata, JSON flags, transaction rollback, and retention across providers/symbols all round-trip correctly. Compare actual saved dashboard payloads, not only row counts. Record before/after performance before replacing the bulk path.

## 6. Test strategy and repository maintenance

### R14 - Test the behavior that can fail, not only the prescribed file layout

**Priority:** P2. **Status:** Static.

**Location:** [backend boundary tests][src-boundary-tests], [analytics tests][src-analytics-tests], [API tests][src-app-tests], [performance tests][src-performance-tests], [frontend tests][src-ui-tests], [frontend test fixtures][src-ui-fixtures].

The existing tests provide useful foundations: known-value BSM/gamma checks, parity and finite differences, missing-OI behavior, transaction rollback, provider-call counts, and manual-only refresh checks. Preserve them.

The important omissions are specific:

- The fake-timer UI test verifies no network traffic but does **not** assert that age or cooldown text advances. It therefore misses R01 even though that assertion is required by the PRD.
- The different-provider test exercises pure analytics, not the public response model and persistence path. It misses R07.
- Frontend fixtures default to `surface.status="INSUFFICIENT_DATA"`; the main suite therefore does not establish that a READY surface's grid, observed markers, hover data, and units render correctly.
- There are no focused table tests for the 17-strike window, exact spot divider, expansion, all-null versus zero cells, and the two color modes.
- Parser tests cover malformed JSON text, but not enough structurally valid malformed envelopes, count corruption, or a short page inconsistent with `totalRecord`.
- The concurrency test uses `sleep(0.2)` to guess when a provider call is blocked. Use an explicit "entered provider" event before launching the competing request.
- The performance test persists `{"ok": true}` rather than a full dashboard response, and uses the same bid/ask across many strikes, causing numerous early exclusions. It is a limited processing/storage benchmark, not a measurement of the complete refresh path.
- There is no checked-in CI workflow in the reviewed tree.

**Recommended changes:** Add regression tests for R01-R07 before restructuring production code. Add one meaningful READY-surface component fixture and direct heatmap tests. Strengthen the provider replacement test so it reaches a saved public dashboard under a distinct provider identity.

Keep a simple import-boundary check if useful, but reduce the custom AST visitor's dependence on an exact factory function name/location. Test the architectural dependency direction and actual injected-provider behavior. Otherwise a harmless rename can fail a structural test while a public provider coupling passes it.

The fixed seven-module prescription and detailed AST policing came from the PRD/agent instructions, not from an unavoidable engineering requirement. Relax those instructions when adopting this review. Keep the architectural boundaries, not an arbitrary file count.

Add one CI workflow for the existing tools: supported Python installation, backend tests/lint/type check, supported Node installation, `npm ci`, frontend tests, lint, and build. Keep hardware-sensitive benchmarks separate from functional pass/fail checks and document the machine and workload used for reported timings. No external market-data access should occur in CI.

### R15 - Correct the setup and evidence trail

**Priority:** P3. **Status:** Static.

**Locations:** [README][src-readme], [source contract][src-source-contract], [M5 validation][src-validation], [requirements][src-requirements], [npm lockfile metadata][src-lock], [ignore rules][src-ignore], [agent instructions][src-agent].

Make the following concrete changes:

1. **Correct the Node prerequisite.** README says Node 18+. The PRD specifies Node 22.x at least 22.12, and inspected lockfile packages already declare engine ranges excluding Node 18. State the runtime actually validated by CI. Use `npm ci` for the reproducible checkout path.
2. **Correct valuation terminology.** Document collection-start fallback separately from the 16:00 New York expiry convention, as described in R06.
3. **Separate current contract from historical investigation.** `source-contract.md` begins with PASS, then says only AAPL is verified, and later resolves that status. Put the current verified contract at the top and move historical observations into a clearly dated appendix. Preserve genuine unknowns rather than making the story sound more certain.
4. **Separate technical verification from access authorization.** The PRD says authorization is required; the source-contract record says the owner accepted personal-use risk and explicitly says permission was not granted. Do not use one undifferentiated PASS label to imply both conditions were established. This is an evidence/documentation issue; this review does not establish legal permission.
5. **Do not attribute unexplained numeric changes to market movement.** The M5 writeup attributes different valid-IV counts to live quotes moving, but collection-time fallback itself changes T between refreshes. Record the observed difference; identify quote changes only when raw-input comparisons establish them.
6. **Use precise execution evidence.** Link a commit and actual CI results for future completion claims. Passing the current tests is not evidence that every PRD acceptance condition was exercised.
7. **Tighten ignore rules.** Add explicit `.env.*` exclusions with an `.env.example` exception, DuckDB WAL files outside the ignored data directory, and private exports. Check tracked files separately; an ignore pattern is not an audit of existing history.
8. **Review copied-component notices and unused dependencies.** Preserve applicable notices for copied UI source. `pytz` is not imported by the inspected application; the code uses `zoneinfo`. Do not remove `shadcn` blindly: `index.css` imports its Tailwind stylesheet. Do not mass-upgrade or remove numerical dependencies during correctness fixes.
9. **Own the HTTP client lifecycle.** The provider creates an HTTPX client without an application shutdown close path. Close an owned client at shutdown, while keeping injected test clients under test ownership. This does not require a generic resource-management framework or a new public provider operation.

## 7. What to keep, simplify, and avoid

| Keep | Why |
|---|---|
| One synchronous FastAPI process | Fits one local user and a manually triggered collection. |
| DuckDB and the existing two-table design | Provides snapshot replay and quote-level audit without a database service. |
| The one-method provider Protocol | A useful, small seam; finish it through the public contract. |
| Pure analytical functions | Easy to verify without networking or storage. |
| Ordered quote-eligibility checks and Brent solving | These protect numerical meaning; they are not gratuitous defensiveness. |
| Missing versus zero semantics | Essential to avoid fabricated exposure. |
| Snapshot JSON plus normalized quotes and raw audit payload | These serve distinct replay, audit, and diagnostic purposes. |
| Atomic save and the two locks | The refresh lock and database lock protect different invariants. |
| The HTML/CSS heatmap | Supports the approved sticky headers, windowing, and underlying-price divider directly. |
| shadcn/Radix primitives | Their generated markup and interaction machinery are not a reason to rewrite controls. |
| Manual-only collection | No need to change product scope to fix code quality. |

| Simplify or replace | Replacement |
|---|---|
| `get(... ) or {}` / `or []` for required source structure | One explicit source page contract; honest schema failures |
| Count parsing through floating-point truncation | Strict count normalization plus canonical invariants |
| Several competing async state writers | One load/refresh state owner with response generations |
| Recomputed clock offset and app-wide tick | One accepted clock sample; narrow display-only updates |
| Public fixture/Nasdaq enums | Opaque provider identity metadata |
| Duplicate exception translations | One expected-error mapping and one logged unexpected-error boundary |
| Typed records converted to untyped internal dictionaries | Small typed group/slice structures |
| `default=str` serialization fallback | Explicit JSON-compatible serialization |
| Tests policing incidental file/function names | Behavioral invariants plus a small dependency-direction check |

**Avoid:** A full backend rewrite; switching to async HTTP for this workload; Celery/Redis/Kafka; an ORM; a provider plugin registry; Redux or a new server-state library solely for three requests; replacing SciPy; adding an American-option engine; SVI/SABR calibration; automatic refresh; new GEX signals; and deleting quality flags to make the UI look cleaner.

## 8. Target architecture after the refactor

The target remains a small modular application:

```text
React controls and load/refresh state
  |                                  Small clock/status display
  | GET saved / POST refresh         (no network work)
  v
FastAPI request/error boundary
  |
  +-- RefreshCoordinator: admission and cooldown only
  |
  +-- collect-and-save workflow
        |
        +-- OptionsDataProvider.fetch_chain
        |      |
        |      +-- Nasdaq adapter: HTTP + one page parser + normalization
        |      +-- Fixture adapter: canonical synthetic data
        |
        +-- Canonical snapshot: identity, counts, timing, provenance
        |
        +-- Pure analytics: eligibility -> IV/gamma -> GEX/surface
        |
        +-- One dashboard serialization
        |
        +-- DuckDB transaction: snapshot + quotes + prune
                |
                +-- saved immutable dashboard returned to both charts
```

There is no requirement to introduce an eighth file. `app.py` can keep orchestration while it remains readable. Conversely, a genuinely useful small module should not be forbidden just because a previous PRD named seven files. The boundary matters more than the count.

### Single-owner rules

| Fact or responsibility | Sole owner |
|---|---|
| Nasdaq keys, formatting, pagination | Nasdaq adapter |
| Required normalized field invariants | Canonical input models / one validation boundary |
| Scope and pricing convention | One shared domain policy |
| Valuation fallback and resulting warnings | Orchestration policy |
| IV, gamma, GEX, surface calculations | Pure analytics |
| Persistence encoding and transaction | Storage |
| Which in-flight request may update the page | One dashboard state owner |
| Accepted server-clock offset | Config-response state |
| Formatting, windowing, hover, camera | Frontend view components |

## 9. Refactor milestones and acceptance gates

Do not combine all of this into one large patch. Separate observable bug fixes from behavior-preserving cleanup.

### M1 - Restore correct time and request ordering

**Scope:** R01, R04, R05.

Deliver the captured clock offset, explicit load-error states, request-generation protection, and awaited post-refresh reconciliation. Keep manual refresh and old-data preservation.

**Gate:** Fake-timer countdown/staleness tests pass; delayed GET cannot replace a newer POST; failed GET is not labeled empty; initial config failure has a GET-only retry; one click creates one POST; no timer/focus/reconnect collection occurs.

### M2 - Establish a trustworthy provider boundary

**Scope:** R02, R03, R06 and the multiplier invariant from R12.

Deliver one page parser, verified completion checks, strict count handling, consistent identity parsing, and aware-time validation. Preserve raw data for audit and per-contract quality flags.

**Gate:** Malformed/truncated responses cannot become saved successful snapshots. Invalid OI cannot become a confident zero or negative gross value. Date-only/naive source times cannot enter pricing as exact timestamps. Existing valid-chain math remains within the current numerical tolerances.

### M3 - Complete provider independence and simplify errors

**Scope:** R07, R09, remaining R12 cleanup.

Deliver provider-neutral public metadata, a full-path substitute-provider test, one scope definition, typed grouping, and centralized logged error handling.

**Gate:** A distinct test provider reaches saved API output without Nasdaq imports or chart/schema changes. Valid-input analytical arrays remain unchanged. All documented error codes, lock behavior, and prior-snapshot preservation remain intact.

### M4 - Make views efficient and inspectable

**Scope:** R08, R10, R11.

Deliver isolated clock updates, stable chart props, correct displayed DTE, grid hover content, unit/scale labels, unavailable-data states, chart recovery, and realistic fixture collection metadata.

**Gate:** The clock updates without rebuilding analytical chart data. READY surface and heatmap component tests pass. Fixture latest-snapshot behavior matches the most recent POST through retention. Manual browser checks confirm camera behavior, readable labels, marker density, and panel recovery.

### M5 - Clean persistence and make the handoff reproducible

**Scope:** R13, R14, R15.

Deliver strict serialization, persistence round-trip tests, a representative refresh benchmark, CI, runtime/setup corrections, current source documentation, and owned HTTP-client cleanup.

**Gate:** Supported clean-checkout commands pass in CI. Existing saved snapshots remain readable without analytical recalculation. Bulk-storage replacement, if attempted, has recorded before/after timings and complete round-trip coverage. No new infrastructure or market-data requests are introduced into tests.

## 10. Behavior that must remain invariant

Treat this as the review's regression contract:

- The user selects one configured symbol and manually requests one collection. GETs, elapsed time, tab focus, and reconnect do not collect market data.
- The chain response supplies the underlying last price. No quote endpoint or previous-snapshot spot is used as a repair mechanism.
- Both charts and all metadata describe one snapshot ID, one price, one valuation time, and one parameter set.
- A collection or persistence failure preserves the previous successful snapshot. No partial result is published as complete.
- Missing prices/OI/gamma remain distinguishable from real zero values. Existing OI-zero and incomplete-cell semantics remain explicit.
- BSM includes the dividend adjustment, IV stays decimal in the API, displayed IV is percent, and GEX keeps its documented dollar/1%-move unit and signed-proxy label.
- Current quote filters, expiry/strike scope, OTM surface selection, no-extrapolation behavior, and no gap bridging remain unchanged unless a separate product decision explicitly changes them.
- GET latest returns saved analytical arrays without recalculating them using current time or current settings.
- Retention is scoped by provider identity and symbol. Source data is not relabeled when switching providers.
- Synthetic data stays visibly synthetic.
- Existing surface/heatmap functionality remains available. No new signal, gamma-flip, broker, or auto-refresh feature is smuggled into the cleanup.

When a bug fix intentionally changes results, state the changed condition and bump the relevant algorithm/schema version when needed. Do not silently reprice historical snapshots. Do not add a generic migration framework for a small compatible metadata change; define that change explicitly and test an old saved payload.

## Appendix A - Executable observations

The backend probes used synthetic HTTP bodies, not live market data. Their expected assertions described the current bug so the review could confirm its existence; they are not claims that the desired behavior passes.

| Probe | Observed current behavior |
|---|---|
| Missing `data.table` with valid spot/status | Successful snapshot, zero contracts |
| `rows=null` with valid spot/status | Successful snapshot, zero contracts |
| `totalRecord=1906`, only two rows | Successful snapshot, two contracts, one request |
| JSON root is an array | Unmapped `AttributeError` |
| Count strings `1.9`, `0.9`, `-1000` | Parsed as 1, 0, -1000 |
| Count strings NaN/Infinity | Unmapped ValueError/OverflowError |
| Negative call OI in canonical input | COMPLETE cell with negative gross exposure |
| Canonical multiplier 50 | Accepted; exposure still uses 100 |
| Date-only chain `asOf` | Naive midnight accepted; pricing subtraction raises TypeError |
| New public provider ID | Pydantic literal validation error |
| Missing/null drill-down URL | KeyError/TypeError in progress parsing |
| Reference BSM/gamma control | Call `10.450583572185565`; gamma `0.018762017345846895`, matching the PRD tolerance |

The saved backend result log contains 12 observations: NaN/Infinity share the count-parser observation, and missing/null drill-down URLs are two separate observations. This table groups some cases for readability.

### Minimal clock reproduction

The important part is not a new time algorithm. It is the lifetime of the offset:

```ts
// Current render behavior, repeated on each tick:
const offset = clockOffsetMs(config.server_time);
const remaining = secondsUntil(config.refresh_not_before, offset);

// Required behavior:
// Capture offset when config arrives; reuse it while actual time advances.
```

### Source-copy verification

These local probe inputs matched the reviewed Git blobs exactly:

| Source | Git blob SHA |
|---|---|
| `backend/models.py` | `23c7e00278fa31c279bf21e0f93d686209f02381` |
| `backend/provider.py` | `6f5b29481ca752c77911aa406285ac19097f5493` |
| `backend/nasdaq.py` | `c1cadaf544e5c692189d463e58505ba96f2010be` |
| `backend/analytics.py` | `7dd7fb39c7008340d346f5a5f2cfb5e3439c9c44` |
| `frontend/src/lib/time.ts` | `057a73551f4508b1bbf0fb3b7c3a8418ccae7307` |

## Appendix B - Repository coverage

| Area | Inspected files |
|---|---|
| Root | `.gitignore`, `AGENTS.md` symlink target, `CLAUDE.md`, `LICENSE`, `README.md` |
| Backend application | `analytics.py`, `app.py`, `fixtures.py`, `models.py`, `nasdaq.py`, `provider.py`, `storage.py` |
| Backend configuration | `pyproject.toml`, `requirements.txt`, `schema.sql`, `settings.example.json` |
| Backend tests | `conftest.py`, `test_analytics.py`, `test_app.py`, `test_fixtures.py`, `test_models.py`, `test_nasdaq.py`, `test_performance.py`, `test_provider_boundary.py`, `test_storage.py` |
| Documentation | `options_analytics_mvp_prd.md`, `source-contract.md`, `m5-validation.md` |
| Frontend entry/API/types | `App.tsx`, `api.ts`, `types.ts`, `main.tsx`, `index.css` |
| Frontend hooks/helpers | `useConfig.ts`, `useDashboard.ts`, `useTick.ts`, `lib/time.ts`, `lib/utils.ts` |
| Frontend views | `ChartErrorBoundary.tsx`, `GexHeatmap.tsx`, `IvSurface.tsx`, `SnapshotMeta.tsx` |
| UI primitives | `alert.tsx`, `badge.tsx`, `button.tsx`, `card.tsx`, `select.tsx`, `skeleton.tsx` |
| Frontend tests | `App.test.tsx`, `test-fixtures.ts`, `test-setup.ts` |
| Frontend tooling/assets | `.gitignore`, `biome.json`, `components.json`, `index.html`, `package.json`, three `tsconfig` files, `vite.config.ts`, `public/favicon.svg` |
| Limited inspection | `package-lock.json`: root metadata and initial dependency/engine entries only; not an exhaustive dependency security/license audit |

## Final recommendation

Keep the product and its stack. Make a targeted redesign of **data admission, time ownership, and asynchronous UI state**, not a rewrite of the option-pricing engine or application framework.

The implementation's most concerning code is not its visible collection of financial checks. It is the code that tries to keep running by turning malformed input into empty success, malformed counts into apparently valid numbers, and failed reads into an empty page. Replace those behaviors with a small number of precise contracts, then let the rest of the application stay simple.

<!-- All code references are pinned to the reviewed commit. -->

[src-tree]: https://github.com/zhuy9/GEX-lens/tree/07898ef0621350c42cffc82dc8158ce1ab08bc94
[src-agent]: https://github.com/zhuy9/GEX-lens/blob/07898ef0621350c42cffc82dc8158ce1ab08bc94/CLAUDE.md
[src-app]: https://github.com/zhuy9/GEX-lens/blob/07898ef0621350c42cffc82dc8158ce1ab08bc94/backend/app.py
[src-app-tsx]: https://github.com/zhuy9/GEX-lens/blob/07898ef0621350c42cffc82dc8158ce1ab08bc94/frontend/src/App.tsx
[src-time]: https://github.com/zhuy9/GEX-lens/blob/07898ef0621350c42cffc82dc8158ce1ab08bc94/frontend/src/lib/time.ts
[src-nasdaq]: https://github.com/zhuy9/GEX-lens/blob/07898ef0621350c42cffc82dc8158ce1ab08bc94/backend/nasdaq.py
[src-nasdaq-fetch]: https://github.com/zhuy9/GEX-lens/blob/07898ef0621350c42cffc82dc8158ce1ab08bc94/backend/nasdaq.py#L79-L192
[src-nasdaq-body]: https://github.com/zhuy9/GEX-lens/blob/07898ef0621350c42cffc82dc8158ce1ab08bc94/backend/nasdaq.py#L202-L219
[src-nasdaq-numbers]: https://github.com/zhuy9/GEX-lens/blob/07898ef0621350c42cffc82dc8158ce1ab08bc94/backend/nasdaq.py#L40-L59
[src-nasdaq-time]: https://github.com/zhuy9/GEX-lens/blob/07898ef0621350c42cffc82dc8158ce1ab08bc94/backend/nasdaq.py#L221-L227
[src-optionquote]: https://github.com/zhuy9/GEX-lens/blob/07898ef0621350c42cffc82dc8158ce1ab08bc94/backend/models.py#L46-L70
[src-exposure]: https://github.com/zhuy9/GEX-lens/blob/07898ef0621350c42cffc82dc8158ce1ab08bc94/backend/analytics.py#L141-L149
[src-source-contract]: https://github.com/zhuy9/GEX-lens/blob/07898ef0621350c42cffc82dc8158ce1ab08bc94/docs/source-contract.md
[src-validation]: https://github.com/zhuy9/GEX-lens/blob/07898ef0621350c42cffc82dc8158ce1ab08bc94/docs/m5-validation.md
[src-use-dashboard]: https://github.com/zhuy9/GEX-lens/blob/07898ef0621350c42cffc82dc8158ce1ab08bc94/frontend/src/hooks/useDashboard.ts
[src-use-config]: https://github.com/zhuy9/GEX-lens/blob/07898ef0621350c42cffc82dc8158ce1ab08bc94/frontend/src/hooks/useConfig.ts
[src-api-ts]: https://github.com/zhuy9/GEX-lens/blob/07898ef0621350c42cffc82dc8158ce1ab08bc94/frontend/src/api.ts
[src-models]: https://github.com/zhuy9/GEX-lens/blob/07898ef0621350c42cffc82dc8158ce1ab08bc94/backend/models.py
[src-readme]: https://github.com/zhuy9/GEX-lens/blob/07898ef0621350c42cffc82dc8158ce1ab08bc94/README.md
[src-types]: https://github.com/zhuy9/GEX-lens/blob/07898ef0621350c42cffc82dc8158ce1ab08bc94/frontend/src/types.ts
[src-boundary-tests]: https://github.com/zhuy9/GEX-lens/blob/07898ef0621350c42cffc82dc8158ce1ab08bc94/backend/tests/test_provider_boundary.py
[src-tick]: https://github.com/zhuy9/GEX-lens/blob/07898ef0621350c42cffc82dc8158ce1ab08bc94/frontend/src/hooks/useTick.ts
[src-surface-ui]: https://github.com/zhuy9/GEX-lens/blob/07898ef0621350c42cffc82dc8158ce1ab08bc94/frontend/src/components/IvSurface.tsx
[src-gex-ui]: https://github.com/zhuy9/GEX-lens/blob/07898ef0621350c42cffc82dc8158ce1ab08bc94/frontend/src/components/GexHeatmap.tsx
[src-provider]: https://github.com/zhuy9/GEX-lens/blob/07898ef0621350c42cffc82dc8158ce1ab08bc94/backend/provider.py
[src-meta]: https://github.com/zhuy9/GEX-lens/blob/07898ef0621350c42cffc82dc8158ce1ab08bc94/frontend/src/components/SnapshotMeta.tsx
[src-chart-error]: https://github.com/zhuy9/GEX-lens/blob/07898ef0621350c42cffc82dc8158ce1ab08bc94/frontend/src/components/ChartErrorBoundary.tsx
[src-fixtures]: https://github.com/zhuy9/GEX-lens/blob/07898ef0621350c42cffc82dc8158ce1ab08bc94/backend/fixtures.py
[src-storage]: https://github.com/zhuy9/GEX-lens/blob/07898ef0621350c42cffc82dc8158ce1ab08bc94/backend/storage.py
[src-analytics]: https://github.com/zhuy9/GEX-lens/blob/07898ef0621350c42cffc82dc8158ce1ab08bc94/backend/analytics.py
[src-schema]: https://github.com/zhuy9/GEX-lens/blob/07898ef0621350c42cffc82dc8158ce1ab08bc94/backend/schema.sql
[src-performance-tests]: https://github.com/zhuy9/GEX-lens/blob/07898ef0621350c42cffc82dc8158ce1ab08bc94/backend/tests/test_performance.py
[src-analytics-tests]: https://github.com/zhuy9/GEX-lens/blob/07898ef0621350c42cffc82dc8158ce1ab08bc94/backend/tests/test_analytics.py
[src-app-tests]: https://github.com/zhuy9/GEX-lens/blob/07898ef0621350c42cffc82dc8158ce1ab08bc94/backend/tests/test_app.py
[src-ui-tests]: https://github.com/zhuy9/GEX-lens/blob/07898ef0621350c42cffc82dc8158ce1ab08bc94/frontend/src/App.test.tsx
[src-ui-fixtures]: https://github.com/zhuy9/GEX-lens/blob/07898ef0621350c42cffc82dc8158ce1ab08bc94/frontend/src/test-fixtures.ts
[src-requirements]: https://github.com/zhuy9/GEX-lens/blob/07898ef0621350c42cffc82dc8158ce1ab08bc94/backend/requirements.txt
[src-lock]: https://github.com/zhuy9/GEX-lens/blob/07898ef0621350c42cffc82dc8158ce1ab08bc94/frontend/package-lock.json#L1-L85
[src-ignore]: https://github.com/zhuy9/GEX-lens/blob/07898ef0621350c42cffc82dc8158ce1ab08bc94/.gitignore
[src-nasdaq-tests]: https://github.com/zhuy9/GEX-lens/blob/07898ef0621350c42cffc82dc8158ce1ab08bc94/backend/tests/test_nasdaq.py
