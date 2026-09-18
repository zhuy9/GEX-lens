# PRD: Personal GEX Heatmap and Options Volatility Surface

Version: 1.1  
Date: September 16, 2026  
Amended: September 17, 2026 (post-MVP): the symbol allowlist moved from `settings.json` to `backend/instruments.py`, and the one-to-three symbol cap was removed. Sections 1, 3, 3.1, and 5.1 reflect this.  
Audience: coding agent  
Delivery format: local application; React + shadcn/ui frontend; Python backend; DuckDB storage

## Revision decisions

This version replaces v1.0. Keep the numerical methods and two-chart MVP scope. Apply these changes throughout the implementation:

- Use a code-defined symbol allowlist (`backend/instruments.py`), initially `SPY`, `QQQ`, and `AAPL`; load only the selected symbol.
- Use React + TypeScript + shadcn/ui + Tailwind CSS for the application UI. Keep Plotly for analytical charts.
- Require one synchronous `OptionsDataProvider` interface. Nasdaq and synthetic fixtures implement that same interface in separate modules.
- Obtain the underlying last-trade price from the option-chain response. Do not call a separate quote endpoint.
- Retain manual refresh only. Loading saved snapshots is not a market-data refresh.

Provider isolation, chain-contained price extraction, and no-automatic-refresh behavior are acceptance gates, not implementation suggestions.

## 1. Objective and implementation boundary

Build one local dashboard that retrieves an option-chain snapshot, calculates implied volatility (IV) and gamma, and displays exactly two analytical views:

1. A strike-by-expiration gamma-exposure (GEX) heatmap.
2. A three-dimensional implied-volatility surface.

The user selects a symbol, clicks **Refresh**, and inspects both views from the same saved snapshot. Restarting the application restores the last successful snapshot without contacting any data provider.

This is a snapshot-based research application, not a real-time trading feed or execution system. The requirements below are implementation decisions, not claims about data quality or predictive performance.

**Do not implement:** automatic refresh, background collection, WebSockets, gamma-flip/profile calculations, trading signals, order execution, news integration, historical replay, user accounts, cloud deployment, or additional live data providers. The provider interface and separate synthetic fixture implementation are required, not excluded.

This list is an MVP scope-discipline decision, not a judgment that these calculations are infeasible or unsafe to compute from the two analytical views already in scope. Revisit case by case in a future revision of this PRD; do not add them by inferring intent from an in-chat request alone.

## 2. Source-access gate

The Nasdaq webpage is not an established API contract. Nasdaq's current website terms restrict data capture and extraction, including use in data-analysis software. Personal use must not be treated as blanket permission for automated collection. Its documented authenticated options API is a separate service. [S1][S2]

The website-backend URL discussed previously is a candidate to verify, not a verified integration:

```text
https://api.nasdaq.com/api/quote/{symbol}/option-chain
```

Version 1.0 did not verify a valid live JSON response. The owner reports that their sample option-chain response includes the underlying last price. The sample bytes were not accessible while preparing this revision, so its exact JSON field path has not been verified here. Treat chain-contained underlying pricing as a required integration contract; do not guess its mapping.

Before implementing the live parser, inspect the owner's sample, record the exact field paths in `docs/source-contract.md`, and add a parser regression test. Do not invent current parameters, field names, pagination behavior, supported symbols, timestamps, or entitlements. A recorded sample can establish a parser example; it does not by itself establish authorization, live availability, or full-chain pagination.

**Milestone M0 must establish authorized access and the actual response contract before enabling live mode.** Record permission/access prerequisites and provider-supported request limits. The owner supplies any necessary permission or entitlement. Do not substitute a paid service or add another provider without changing this PRD.

Provide `fixture` mode for development using synthetic data. If authorized live access is unavailable, deliver the fixture implementation and mark live integration **BLOCKED**. Fixture success does not satisfy the live-data acceptance criteria. Never switch from live data to synthetic data silently.

## 3. Fixed MVP scope

| Item | Requirement |
|---|---|
| User/runtime | One user, one local machine, desktop browser |
| Symbol allowlist | Every verified instrument in `backend/instruments.py`; ships `SPY`, `QQQ`, `AAPL`; default is the first entry (`SPY`) |
| Instruments | Standard US equity/ETF options; USD prices |
| Index/futures support | Excluded; do not relabel SPY/QQQ data as SPX/NDX/NQ options |
| Expiration scope | Calendar DTE from 1 through 60, inclusive |
| Strike scope | `0.80 * spot <= strike <= 1.20 * spot` |
| Refresh | Manual only; one global refresh at a time |
| Request cooldown | At least 60 seconds between refresh attempts across all symbols |
| History storage | Latest 20 successful snapshots per `(source_mode, symbol)` pair |
| History interface | None; display latest successful snapshot only |
| GEX modes | `Call-minus-put proxy` and `Gross OI-weighted gamma` |
| Surface | OTM-selected, BSM-derived IV; interpolation specified in Section 8 |
| Default source | `fixture`; enable `nasdaq` only after M0 passes |
| Underlying price | Last-trade price embedded in the chain response; no separate quote endpoint |
| Active symbol | Exactly one selected symbol; never prefetch or refresh the remaining allowlist |

Exclude 0DTE to avoid adding same-day expiration and near-expiry numerical behavior to this MVP. All displayed metrics cover the stated DTE/strike scope, not the entire options market.

### 3.1 Ticker boundaries

The initial release must validate `SPY`, `QQQ`, and `AAPL`. The allowlist limits the validated product scope; it must not introduce symbol-specific branches into analytics or charts.

The backend derives `symbols` and `default_symbol` from `backend/instruments.py`, in its order; the first entry is the default. `settings.json` has no symbol list. The frontend obtains the exact same ordered list from `GET /api/config`; do not hardcode ticker strings in React. Use a dropdown, not free-text ticker entry. Every dashboard route validates membership before database or provider work.

Adding a symbol requires passing the source-contract checks for it, adding its entry to `backend/instruments.py`, adding its `dividend_sources` entry to `settings.json`, and restarting. It does not require changing analytics, API models, or chart code. There is no add/remove-ticker UI or symbol-discovery endpoint.

Keep standard USD equity/ETF options as the only instrument class. Do not implement index, futures-option, adjusted-contract, or cross-instrument conversion logic. SPY/QQQ snapshots must never be labeled as SPX/NDX/NQ snapshots.

## 4. Stack and backend structure

| Component | Required choice |
|---|---|
| Frontend | React, TypeScript, Vite `react-ts` template |
| Charts | Plotly.js: `heatmap`, `surface`, and observed-point `scatter3d` traces |
| Frontend state/network | React hooks and native `fetch`; no Redux or query library |
| UI and styling | shadcn/ui components using Radix primitives; Tailwind CSS v4 with the Vite plugin; no second UI kit |
| API | FastAPI and Uvicorn |
| Provider HTTP | Synchronous `httpx.Client` |
| Calculations | NumPy and SciPy; implement the BSM functions in this repository |
| Storage | DuckDB Python package and parameterized SQL |
| Validation | Pydantic request/response models |
| Tests | pytest, Vitest, React Testing Library; mocked upstream HTTP |
| Runtimes | Python 3.12; Node 22.x at least 22.12 [S11] |

Plotly supports the required heatmap and 3D-surface trace types. Use its complete distribution containing both trace types, not a reduced build without surfaces. [S6][S7]

Use seven application Python modules. The two additions to v1.0 isolate the provider contract and synthetic fixtures; they do not add services or background processes:

```text
backend/
  app.py             # Configuration, provider factory, four routes, orchestration
  provider.py        # OptionsDataProvider Protocol and ProviderError only
  nasdaq.py          # NasdaqProvider: HTTP, pagination, Nasdaq-to-canonical parsing
  fixtures.py        # FixtureProvider: synthetic canonical snapshots; zero HTTP
  analytics.py       # Pure quote filters, BSM, IV, gamma, GEX, surface
  storage.py         # DuckDB initialization, latest/save/prune; no provider parsing
  models.py          # Canonical typed records, configuration and API models
  schema.sql
  settings.example.json
  requirements.txt
  tests/
  fixtures/
frontend/
  src/
    App.tsx
    api.ts
    types.ts
    components/
      GexHeatmap.tsx
      IvSurface.tsx
      ui/            # Generated shadcn/ui component source
    lib/
      utils.ts       # shadcn cn helper
    index.css        # Tailwind import and shadcn theme variables
  components.json    # Committed shadcn configuration
  package.json
  package-lock.json
docs/
  source-contract.md
README.md
```

Do not add service/repository class hierarchies, provider plugins, a scheduler, Celery, Redis, SQLAlchemy, database migrations framework, Docker, or Kubernetes. Use functions and typed records except for the single provider Protocol, its two concrete implementations, and the common provider exception. Do not introduce abstract service/repository layers. Pin tested dependency versions in the delivered dependency files.

### 4.1 Execution model

```text
React + shadcn/ui --GET latest--> FastAPI --read saved JSON--> DuckDB
React + shadcn/ui --POST refresh--> OptionsDataProvider.fetch_chain(request)
                                      | selected once at startup
                                NasdaqProvider OR FixtureProvider
                                      |
                           canonical ChainSnapshot, including spot
                                      |
                           analytics functions -> DuckDB transaction
                                      |
                          one saved dashboard -> both Plotly charts
```

Run Uvicorn with exactly one worker. Use ordinary synchronous `def` routes; FastAPI runs these routes in its thread pool. [S8]

Use a process-wide `threading.Lock` for refreshes, acquired without waiting. A concurrent refresh returns `409 REFRESH_IN_PROGRESS` rather than entering a queue.

Use a separate lock around DuckDB operations. Open and close a DuckDB connection within each locked storage operation. Do not share an unguarded connection between request threads. Hold the database lock only during database work, never during HTTP requests or analytics. This is deliberately a single-process DuckDB design. [S3]

Bind both development servers to `127.0.0.1`. Proxy frontend `/api` requests to the backend through Vite. Do not enable wildcard CORS or expose the application to the LAN.

### 4.2 Mandatory provider interface

Define one Python `typing.Protocol` in `provider.py`. A Protocol provides structural typing; concrete providers do not need to inherit from each other. [S12]

```python
# backend/provider.py
from typing import Protocol
from models import ChainRequest, ChainSnapshot


class OptionsDataProvider(Protocol):
    def fetch_chain(self, request: ChainRequest) -> ChainSnapshot:
        """Return a complete normalized snapshot, or raise ProviderError."""
        ...


class ProviderError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retry_after_seconds = retry_after_seconds
```

`fetch_chain` is synchronous. It is the only provider operation exposed to the application. Do not add `get_spot`, `get_quote`, `get_greeks`, per-expiration routes, or a streaming method. Pagination is internal to the provider; one method invocation can make several chain-page requests.

Define the canonical records in `models.py` as validated, frozen Pydantic models. Reject extra fields; use tuples for contract/warning collections. No model name or canonical field may encode Nasdaq's JSON layout. Use the following mandatory fields:

| Record | Required fields |
|---|---|
| `ChainRequest` | `symbol: str`, `min_calendar_dte: int`, `max_calendar_dte: int`; this app always supplies 1 and 60 |
| `OptionQuote` | All normalized fields from Section 5.4; no provider-specific row dictionaries |
| `ChainSnapshot` | `provider_id: str`, `symbol: str`, `underlying_price: float`, `underlying_price_kind: Literal["last_trade"]`, `underlying_price_origin: Literal["chain_payload"]`, `collection_started_at: datetime`, `collected_at: datetime`, `chain_asof: datetime | None`, `spot_asof: datetime | None`, `oi_asof: date | None`, `contracts: tuple[OptionQuote, ...]`, `warnings: tuple[str, ...]`, `source_row_count: int`, `provider_response_count: int`, `raw_payload_json: str` |

All datetimes must be timezone-aware UTC. `underlying_price` must be finite and positive. In fixture mode, source timestamps and underlying prices are explicitly synthetic. Nullable timestamps remain null when absent; a date-only source value must not be converted into an invented midnight timestamp.

Only a complete, normalized collection may be returned. A valid complete response containing zero in-scope records is allowed and yields insufficient-data panels. HTTP, access, schema, invalid-price, and incomplete-pagination failures must raise `ProviderError`; they must not return an empty successful snapshot. Translate HTTPX exceptions inside `nasdaq.py`, not in the route.

`raw_payload_json` is the sanitized JSON serialization of all response pages plus nonsecret request parameters. Pass it directly to storage as an opaque audit field. Analytics and route logic must never inspect it. It is not returned to React. Fixture raw data uses a synthetic canonical format, not an imitation of Nasdaq's JSON.

### 4.3 Provider selection and dependency rules

Expose `create_app(settings: Settings, provider: OptionsDataProvider | None = None)` in `app.py`. Select the implementation once during application creation. When a provider is explicitly injected, use it without constructing or importing the Nasdaq implementation.

Use one ordinary `build_provider(settings)` factory function in `app.py`: `fixture` creates `FixtureProvider`; `nasdaq` creates `NasdaqProvider`; unknown configured values fail startup. Keep concrete adapter imports inside the matching factory branches so fixture mode does not import `nasdaq.py`. Do not use a plugin registry or dependency-injection framework.

The refresh handler calls the selected object's `fetch_chain(ChainRequest(...))`. Before analytics or storage, verify that the returned symbol equals the requested symbol and `provider_id` equals configured `source_mode`; reject a mismatch with HTTP 502 and `SNAPSHOT_IDENTITY_MISMATCH`. Test stubs used through the app must return the configured identity. Then pass only canonical records, rates, and timestamps to analytical functions. It does not pass settings containing transport credentials into analytics. The provider does not receive a database connection or calculate IV/GEX.

Allowed dependencies:

```text
app.py           -> provider.py, models.py, analytics.py, storage.py
build_provider() -> nasdaq.py OR fixtures.py       [only concrete selection point]
nasdaq.py        -> provider.py, models.py, httpx  [all Nasdaq parsing is here]
fixtures.py      -> provider.py, models.py        [canonical files; no Nasdaq import]
analytics.py     -> models.py, numpy, scipy       [no HTTP or storage]
storage.py       -> models.py, duckdb             [no HTTP or Nasdaq imports]
provider.py      -> models.py, typing             [no concrete implementations]
React            -> /api only                    [no upstream market-data calls]
```

Routes must not branch on `nasdaq`, inspect its field names, or type-check for `NasdaqProvider`. The only permitted source-specific UI behavior is the explicit `fixture` synthetic-data warning; other source IDs are displayed as metadata. Provider identity in API/storage is a string, not a Nasdaq-shaped schema. Retain the existing `source_mode` field/column name for the provider identifier; copy its value from `ChainSnapshot.provider_id` when saving a snapshot.

Future replacement must require only a new adapter module, one factory branch/configuration choice, and adapter tests. It must not require modifications to analytics, chart components, public dashboard payload shape, or DuckDB schema. Do not implement another live provider now.

### 4.4 Boundary acceptance tests

Add `backend/tests/test_provider_boundary.py` with these tests:

1. Inject a test `StubProvider` returning a canonical snapshot. One refresh produces saved GEX/surface output, with exactly one `fetch_chain` call. Run with all external HTTP disabled and fail the test on any attempted Nasdaq import or construction.
2. Feed equal canonical quotes, rates, and timestamps under two different provider IDs. Analytical arrays are identical; only provenance/snapshot metadata differs. The second provider is a test stub, not another production integration.
3. Inspect Python imports with the standard-library `ast` module. Fail if `provider.py`, `models.py`, `analytics.py`, `storage.py`, or `fixtures.py` imports `nasdaq`; fail if the concrete import in `app.py` occurs outside `build_provider`. Fail if analytics/storage import HTTPX or requests.
4. Run pure analytics tests against canonical fixtures without importing the Nasdaq parser. Test the Nasdaq parser separately against the actual verified sample.
5. Make a stub raise `ProviderError`. Assert the documented API error and unchanged saved snapshot, with no provider-specific route logic.

These tests are required even if the initial live adapter works. A nominal interface that is bypassed by routes does not pass this PRD.

## 5. Configuration and collection

### 5.1 Configuration

Read one local `settings.json`, excluded from version control. Commit `settings.example.json` and validate configuration at startup.

Required configuration fields are `source_mode`, `db_path`, `refresh_min_interval_seconds`, `risk_free_rate`, and `dividend_yields` containing exactly one value per instrument in `backend/instruments.py`. Live configuration must explicitly provide both rate inputs; do not retrieve them from another service.

Commit this synthetic example; do not represent its rates as current market data:

```json
{
  "source_mode": "fixture",
  "db_path": "data/options.fixture.duckdb",
  "refresh_min_interval_seconds": 60,
  "risk_free_rate": 0.04,
  "dividend_yields": {"SPY": 0.0, "QQQ": 0.0, "AAPL": 0.0}
}
```

Validate exact per-instrument dividend key coverage at startup. `refresh_min_interval_seconds` must be an integer at least 60; increase it if M0 establishes a stricter permitted refresh interval. No hardcoded frontend ticker list or symbol-specific analytical code is permitted; configured per-symbol dividend yields remain required. The configured source identifies one provider for the entire app, not a separate provider per chart or price field.

Use annual continuously compounded decimal inputs: `0.04` means 4%, not 0.04%. The fixture example uses `r=0.04` and `q=0.00`; these are synthetic model assumptions, not current market observations. Fixture data includes a fixed valuation clock so its expirations do not become unusable as the real date changes. Validate `-0.10 <= r <= 0.50` and `0 <= q <= 0.50`.

Display the exact `r` and selected symbol's `q` with every snapshot. Changing the configuration affects the next refresh only; saved snapshots retain their original parameters.

### 5.2 Collection rules

The selected provider must fetch all chain pages required to cover the complete 1-60 DTE scope and both call/put sides. Pagination, request parameter names, provider symbol formatting, and payload parsing belong inside its adapter. Apply the strike filter in Python analytics using the normalized underlying price. Do not accept the provider's default expiration filter without verification.

Configure `httpx` connect timeout to 5 seconds and read/write/pool timeouts to 10 seconds. Do not retry automatically. Limit one refresh to 10 provider requests, 10 MiB of decoded response bodies in total, and 10,000 normalized contracts. These are safety caps, not provider entitlements; apply stricter verified provider limits.

If pagination is unfinished at a cap, a page fails, a page repeats without progress, or completeness cannot be established, reject the refresh as `INCOMPLETE_CHAIN`. Never publish a silently truncated chain.

Measure collection duration. If it exceeds 30 seconds, reject the snapshot as `COLLECTION_WINDOW_EXCEEDED`. This is a snapshot-validity check, not a claim that synchronous HTTP has an exact 30-second wall-clock cancellation mechanism.

**The underlying last-trade price must come from the same option-chain collection. A separate quote/spot endpoint is prohibited in this MVP.** The reported sample is expected to provide that field; M0 must verify the exact location and meaning.

The Nasdaq adapter maps the response's underlying-level last-trade field into `ChainSnapshot.underlying_price`. An option row's last premium maps only to `OptionQuote.last`. Never use a call/put premium, the selected strike, a previous snapshot's spot, or a client-supplied price as the underlying price. Never make a second-service or separate-quote request to repair a missing price.

For paginated chains, use the first page's verified underlying-level last-trade value as the single snapshot price. Missing or invalid underlying price on that page fails with `INVALID_UNDERLYING_PRICE`; do not search later pages for a fallback. Do not overwrite the chosen price when later pages repeat it. If later pages repeat different underlying prices, keep the first and add `UNDERLYING_PRICE_CHANGED_DURING_COLLECTION`; retain the raw pages and the existing collection-window limit.

Set `underlying_price_kind="last_trade"` and `underlying_price_origin="chain_payload"`. Preserve its actual timestamp when supplied. A shared response does not prove simultaneous timestamps or real-time pricing. Keep unknown timestamp/delay metadata unknown. Multiple requests for pagination are allowed; requests to a different quote endpoint are not.

If both chain and spot timestamps exist and differ by more than 120 seconds, reject the refresh as `MISALIGNED_TIMESTAMPS`. If their alignment cannot be established, retain the snapshot with an explicit `TIMESTAMP_ALIGNMENT_UNKNOWN` warning. Missing source timestamps must remain null.

A missing, nonfinite, or nonpositive underlying price rejects the refresh as `INVALID_UNDERLYING_PRICE`. The source's contract quotes, spot, and OI must not be copied from different saved snapshots to fill gaps.

### 5.3 Collection failure behavior

Failed refreshes leave the last successful snapshot unchanged. The UI shows the error and keeps displaying that snapshot with its original collection time.

Start the global cooldown when a permitted refresh begins, including failed attempts. Use `refresh_min_interval_seconds` from settings; it defaults to 60 and must satisfy any stricter interval verified in M0. If the provider returns `429`, use a valid `Retry-After` value, subject to a minimum of 60 seconds; otherwise use 300 seconds. Do not implement proxy rotation, CAPTCHA solving, browser-impersonation workarounds, or access-control bypasses.

### 5.4 Normalization

All source-format normalization is owned by `NasdaqProvider` before returning `ChainSnapshot`. `FixtureProvider` reads canonical synthetic records and must not call the Nasdaq parser.

Normalize a side-by-side call/put row into separate contract records. Remove expiration-heading rows. Parse currency symbols and thousands separators. Map empty strings, `--`, and `N/A` to null; preserve numeric zero.

Normalize expiration to ISO date and strike to `DECIMAL(18,6)` for identity/storage. Use `(symbol, expiration, strike, option_type)` as the supported-contract key. Collapse exact duplicates; reject conflicting duplicates as `CONFLICTING_CONTRACTS`.

Required contract fields:

| Field | Type/rule |
|---|---|
| `symbol` | Supported symbol |
| `expiration` | ISO date |
| `strike` | Positive decimal |
| `option_type` | `C` or `P` |
| `bid`, `ask`, `last` | Nullable finite prices |
| `volume`, `open_interest` | Nullable nonnegative integers |
| `multiplier` | 100 for supported standard contracts |
| `provider_contract_id` | Nullable provider identifier |
| `quote_asof` | Nullable UTC timestamp |
| `flags` | Explicit quality/model-assumption codes |

Never infer OI from volume. Missing OI does not prevent IV calculation; it does prevent nonzero GEX calculation.

Standard equity options typically represent 100 shares, but corporate actions can change deliverables. Exclude explicitly identified nonstandard/adjusted contracts. If the source lacks deliverable metadata, attach `MULTIPLIER_ASSUMED` and display the 100-share assumption; do not claim every adjustment was detected. [S9]

## 6. Pricing inputs, quality filters, and IV

### 6.1 Time and model

Use dividend-adjusted Black-Scholes-Merton (BSM) for all included contracts. This is a European-model approximation for equity/ETF options, not an American-option pricing engine. Do not add a binomial tree, discrete-dividend service, or automatic forward inference. Display this model limitation. [S5]

Set `valuation_at` to the chain-level pricing timestamp when its semantics are verified. Otherwise use collection-start time and add `VALUATION_TIME_ASSUMED`. Do not substitute a spot-only timestamp for a chain pricing timestamp.

Set the model expiration time to 16:00 in `America/New_York` on the source expiration date. This is an explicit pricing convention, not verified settlement metadata. Early-close schedules are not modeled. Use timezone-aware conversion with DST; include `tzdata` for Windows.

```text
calendar_dte = expiration_date - New_York_date(valuation_at)
T = (model_expiry_at_utc - valuation_at).total_seconds() / 31_536_000
F = S * exp((r - q) * T)
```

DTE determines scope; fractional `T` determines pricing. Do not round `T` to whole days. Saved snapshots must not be repriced using the current clock when reopened.

### 6.2 Quote eligibility

Apply the following checks in order and retain the first exclusion reason. Retain all records in storage for audit.

| Check | Failure code |
|---|---|
| Within DTE/strike scope and `T > 0` | `OUT_OF_SCOPE` |
| Not explicitly identified as nonstandard | `NONSTANDARD_CONTRACT` |
| Finite `bid`/`ask`, `bid > 0`, `ask >= bid` | `INVALID_BID_ASK` |
| `mid = (bid + ask) / 2 >= 0.05` | `LOW_MID` |
| `(ask - bid) / mid <= 0.50` | `WIDE_SPREAD` |
| Mid within BSM price bounds, tolerance `1e-8` | `MODEL_PRICE_BOUNDS` |
| Mid minus model lower bound greater than `0.01` | `LOW_TIME_VALUE` |

Bounds:

```text
Call: max(0, S*exp(-q*T) - K*exp(-r*T)) <= price <= S*exp(-q*T)
Put:  max(0, K*exp(-r*T) - S*exp(-q*T)) <= price <= K*exp(-r*T)
```

These are model-eligibility checks, not proof that an excluded market quote is erroneous. Never use `last` in place of a failed midpoint, replace missing IV with an average, or substitute yesterday's quote.

### 6.3 Calculation algorithm

Implement these functions in `analytics.py`: `bsm_price`, `solve_iv`, `bsm_gamma`, `build_gex`, and `build_surface`. Keep them deterministic; pass timestamps and configuration as arguments.

```text
d1 = [ln(S/K) + (r - q + sigma^2/2)*T] / (sigma*sqrt(T))
d2 = d1 - sigma*sqrt(T)

call = S*exp(-q*T)*N(d1) - K*exp(-r*T)*N(d2)
put  = K*exp(-r*T)*N(-d2) - S*exp(-q*T)*N(-d1)

gamma = exp(-q*T)*normal_pdf(d1) / (S*sigma*sqrt(T))
```

Solve `bsm_price(sigma) - mid = 0` with `scipy.optimize.brentq`. Use bracket `[0.0001, 5.0]`, `xtol=1e-8`, `rtol=1e-8`, and `maxiter=100`. Brent's method requires a bracket containing a sign change. [S4]

An endpoint with absolute residual at most `1e-8` is an accepted root. Otherwise require opposite endpoint signs. Return null IV/gamma with `IV_NOT_BRACKETED` or `IV_SOLVER_FAILED` on failure. Require final repricing error at most `1e-5` dollars; otherwise reject the solution.

IV is an annualized decimal. Gamma is per-share option delta change per $1 underlying move. Calculate only IV and gamma; delta, theta, vega, and rho are out of scope.

## 7. GEX heatmap

For each supported contract with valid gamma and known OI:

```text
exposure = gamma * open_interest * 100 * S^2 * 0.01
```

Unit: **USD delta-notional change for a 1% underlying move**. This is not option premium, expected P&L, or a forecast hedge order.

Both call and put exposure magnitudes are nonnegative. At each `(expiration, strike)`:

```text
signed_proxy = call_exposure - put_exposure
gross_exposure = call_exposure + put_exposure
```

The signed convention does not identify actual dealer positions. OI counts outstanding contracts, each involving both a long and a short; it does not identify which participant holds which side. Label the signed view **Call-minus-put GEX proxy**, never **Actual dealer GEX**. Do not treat intraday volume as updated OI. [S10]

A supported contract with explicitly reported `OI=0` has zero exposure even when IV cannot be calculated. Missing OI remains unknown. For any cell with either side absent or unknown, return null signed/gross values and mark it `INCOMPLETE`; do not substitute zero or show a partial sum as net exposure.

Heatmap axes are ascending numeric strike and ascending expiration date. Use the union of returned in-scope strikes and expirations; absent combinations remain null. Signed mode uses a symmetric color scale around zero. Gross mode uses a nonnegative scale. If every valid value is zero, use a nondegenerate display range while preserving zero values.

Hover must show strike, expiration, call/put OI, call/put gamma, call/put exposure magnitudes, signed proxy, gross exposure, and completeness. Use USD millions for display; keep unscaled dollar values in the API. Render unknown cells as gaps, not zero-colored cells.

Do not display a whole-market total GEX or infer support/resistance levels. As with section 1, this is an MVP scope boundary, not a claim that call/put walls or a gamma-flip level cannot be derived from the per-cell data already computed here.

## 8. IV surface

Use valid IV observations independently of OI availability.

For each expiration, compute `F` from Section 6 and select exactly one side per strike: use the put for `K < F`, otherwise use the call. Do not fall back to the other side when the selected side is invalid.

Transform observations:

```text
k = ln(K/F)
w = IV^2 * T
```

Require at least three valid distinct strikes for a usable expiration slice. Interpolate each slice independently on the fixed 41-point grid `k_j = -0.20 + 0.01*j`, where `j=0..40`.

Use piecewise-linear interpolation of total variance `w` in `k`. Do not extrapolate. Do not bridge adjacent observed points separated by more than `0.05` log-moneyness; exact observed grid points remain valid. Set unsupported grid cells to null. Convert interpolated variance back with `IV = sqrt(w/T)`.

Do not interpolate additional expiration rows, fit SVI/SABR, apply cubic smoothing, or claim an arbitrage-free calibration. Plotly's connections between actual maturity rows are visualization, not a pricing model calibrated at intermediate maturities.

Require at least two usable expiration slices and at least one adjacent-expiration pair sharing two adjacent finite grid columns before rendering a surface. Otherwise return `INSUFFICIENT_DATA` for this panel without failing the GEX panel.

Render a Plotly surface with `connectgaps=false` and overlay the selected observed points as `scatter3d` markers. Axes: log-forward-moneyness, fractional days to expiry (`365*T`), annualized IV percent. Hover identifies expiration, moneyness, IV, and whether the point is observed or interpolated; observed points also show strike.

## 9. DuckDB persistence

Use `data/options.duckdb` by default. Fixture mode uses `data/options.fixture.duckdb` unless explicitly overridden; do not mix fixture and Nasdaq snapshots in one default database. Two tables only:

```sql
CREATE TABLE IF NOT EXISTS snapshots (
    snapshot_id UUID PRIMARY KEY,
    symbol VARCHAR NOT NULL,
    collected_at TIMESTAMPTZ NOT NULL,
    valuation_at TIMESTAMPTZ NOT NULL,
    source_mode VARCHAR NOT NULL,
    raw_payload JSON NOT NULL,
    dashboard_json JSON NOT NULL
);

CREATE TABLE IF NOT EXISTS option_quotes (
    snapshot_id UUID NOT NULL,
    symbol VARCHAR NOT NULL,
    expiration DATE NOT NULL,
    strike DECIMAL(18,6) NOT NULL,
    option_type VARCHAR NOT NULL,
    bid DOUBLE,
    ask DOUBLE,
    last DOUBLE,
    volume BIGINT,
    open_interest BIGINT,
    multiplier INTEGER NOT NULL,
    provider_contract_id VARCHAR,
    quote_asof TIMESTAMPTZ,
    mid DOUBLE,
    iv DOUBLE,
    gamma DOUBLE,
    exclusion_reason VARCHAR,
    flags JSON NOT NULL,
    PRIMARY KEY (snapshot_id, symbol, expiration, strike, option_type)
);
```

`raw_payload` stores the provider's opaque `raw_payload_json` without parsing its source-specific keys. `dashboard_json` contains the complete successful API snapshot, including input rates, source timestamps, underlying-price provenance, quality counts, chart arrays, and `schema_version=1`. Storage code never extracts quotes or prices from raw JSON.

Compute the complete result before opening a write transaction. Insert the snapshot and all normalized contracts in one transaction using bound parameters. In the same transaction, remove contracts and snapshots older than the newest 20 for that `(source_mode, symbol)` pair. Do not prune another provider's snapshots or another symbol's data. Roll back everything on failure. Do not create a separate aggregation table or external cache.

Serve the saved `dashboard_json` on GET; do not recalculate its analytical arrays. Filter latest reads by both configured source mode and symbol. Even when the database path is explicitly shared, a Nasdaq session must not receive an old fixture snapshot relabeled as Nasdaq. Order latest snapshots by collection time, with snapshot ID as a deterministic tie-breaker. Never expose credentials or request authorization headers in stored raw data.

## 10. API contract

| Method/path | Behavior |
|---|---|
| `GET /api/health` | Return `{"status":"ok","schema_version":1}` after a successful database check; no upstream request |
| `GET /api/config` | Return enabled symbols/default (from `backend/instruments.py`), source mode, model inputs, fixed scope, `refresh_mode="manual"`, `refresh_min_interval_seconds`, current `refresh_in_progress`, `server_time`, and nullable `refresh_not_before` |
| `GET /api/dashboard/{symbol}` | Return latest stored dashboard; `404 NO_SNAPSHOT` when absent |
| `POST /api/dashboard/{symbol}/refresh` | Fetch, calculate, atomically save, and return one new dashboard; empty request body |

Normalize symbols to uppercase and reject values outside the allowlist with `422 UNSUPPORTED_SYMBOL`. No endpoint accepts a provider URL or arbitrary database path.

The dashboard response contains these fields; no NaN or Infinity is permitted:

| Field | Shape/meaning |
|---|---|
| `schema_version`, `snapshot_id`, `symbol`, `source_mode` | Version 1 and snapshot identity |
| `collected_at`, `valuation_at` | UTC ISO timestamps |
| `chain_asof`, `spot_asof`, `oi_asof` | Nullable source timestamps/date; never inferred from collection time |
| `spot`, `spot_kind`, `spot_origin` | Map canonical `underlying_price` to positive `spot`; `spot_kind="last_trade"`, `spot_origin="chain_payload"` |
| `parameters` | `r`, `q`, multiplier assumption, DTE/strike scope, pricing-time convention, algorithm version |
| `warnings` | Array of distinct warning codes |
| `quality` | Counts: source rows, normalized contracts, in-scope contracts, valid IVs, known-OI contracts, complete GEX cells, exclusion counts by reason |
| `gex.strikes`, `gex.expirations` | Ascending axes |
| `gex.cells` | Matrix indexed `[expiration_index][strike_index]` |
| `surface.status` | `READY` or `INSUFFICIENT_DATA` |
| `surface.k`, `surface.expirations`, `surface.dte` | Fixed moneyness grid, usable maturities, fractional DTE |
| `surface.iv` | Nullable matrix indexed `[expiration_index][k_index]` |
| `surface.observations` | Records containing expiration, strike, k, fractional DTE, IV |

Each GEX cell contains `call_oi`, `put_oi`, `call_gamma`, `put_gamma`, `call_exposure`, `put_exposure`, `signed_proxy`, `gross_exposure`, and `status` (`COMPLETE` or `INCOMPLETE`). Unknown numerical fields are null.

Errors use this common shape:

```json
{
  "error": {
    "code": "UPSTREAM_UNAVAILABLE",
    "message": "Refresh failed. The previous snapshot was not changed.",
    "retry_after_seconds": null
  }
}
```

Use HTTP 409 for refresh contention; 429 for the local cooldown; 502 for upstream/schema/collection failures, including `INVALID_UNDERLYING_PRICE`; 503 for upstream rate limiting; and 500 for storage/internal failures. Include appropriate `Retry-After` headers for 429/503. The adapter raises provider-neutral error codes; routes apply this shared mapping. Do not return raw upstream HTML or stack traces to the browser. Normalize framework validation errors into the same envelope.

## 11. Frontend behavior

### 11.1 Required shadcn/ui implementation

Use the existing-project React/Vite setup documented by shadcn/ui, with Tailwind CSS v4, `@tailwindcss/vite`, and the `@/*` alias pointing to `src/*`. Use the Radix-based shadcn components consistently; do not mix primitive families. Commit `components.json`, generated component source, `lib/utils.ts`, theme CSS variables, and the npm lockfile. [S13]

Use these components for their specified roles:

| shadcn/ui component | Required role |
|---|---|
| `Select` | Symbol and GEX-mode selectors, with visible associated labels |
| `Button` | Refresh control with disabled and pending states |
| `Card` | Snapshot metadata container and each analytical chart panel |
| `Badge` | Provider, age, and quality status; never a misleading `Live` label |
| `Alert` | Synthetic-data notice, collection failures, and methodology warnings |
| `Skeleton` | Initial saved-snapshot loading; do not hide old charts during refresh |

Use shadcn theme tokens and Tailwind utilities for page layout. Keep a single light theme for the MVP; no theme selector. Use `lucide-react` only for needed control/status icons. No custom design-system layer, component marketplace, Next.js migration, Material UI, or Ant Design.

**Keep Plotly for the two analytical charts.** shadcn's Chart component is based on Recharts; it is not the required renderer for this project. Do not install Recharts or generate shadcn's `chart` component. Render `heatmap` and `surface`/`scatter3d` inside shadcn `Card` components. [S14][S6][S7]

### 11.2 Page and data states

Render a single page containing a symbol selector, Refresh button, snapshot metadata, a GEX-mode selector, and the two chart panels. On load and symbol change, GET the saved snapshot only. Do not initiate provider collection automatically.

Loading, empty, refreshing, ready, and refresh-failed-with-old-data states must be explicit. Disable the Refresh button and symbol selector during a refresh. Changing GEX mode must not fetch data or calculate Greeks in JavaScript.

When switching symbols, clear the prior symbol's chart data immediately. Cancel or ignore obsolete GET responses so they cannot replace the selected symbol's view.

Always display source mode, collection time, source timestamps or `Unknown`, `Underlying last (from chain)`, r/q, scope, and quality counts. The value under that underlying-price label is `spot`; do not call it a live quote or an option last price. Display `Snapshot older than 5 minutes` when collection age exceeds 300 seconds. This describes snapshot age, not exchange-feed latency. Do not show a `Live` badge based on collection time.

Display permanent methodology notes: `BSM approximation`, `OI does not identify dealer positions`, and `Surface interpolation is not arbitrage-free calibration`. Display multiplier and timestamp-assumption warnings when applicable. Fixture mode has a persistent **SYNTHETIC DATA** banner.

A successful source snapshot with no usable IV/GEX is still saved with explicit insufficient-data panel states and rejection counts; it must not render fabricated zeros. A failed collection or malformed response is not a successful snapshot.

All provider parsing and financial calculations stay in Python. React handles selection, request state, chart-array mapping, and unit formatting only. At 1280x800, controls must be visible without horizontal page scrolling; vertical scrolling is allowed. Show a panel error if WebGL is unavailable, without crashing the rest of the page.

### 11.3 Manual refresh state machine

Manual refresh is the only market-data trigger. Implement the following behavior exactly:

| Event | Required behavior |
|---|---|
| Open/reload app | GET config, then GET selected symbol's saved dashboard; zero provider calls |
| Change symbol | Cancel/ignore obsolete GET; clear old symbol; GET config and selected saved dashboard; zero provider calls |
| Click Refresh | One POST for the selected symbol only; disable Refresh and symbol selector until it settles |
| Refresh succeeds | Replace the single dashboard state object; both charts and all metadata change to the returned snapshot together |
| Refresh fails | Keep the selected symbol's previous snapshot and original timestamps; show an Alert; do not retry |
| Request settles | Fetch config once to reconcile the server's cooldown; this GET never contacts the provider |
| Cooldown active | Show `Refresh available in Ns`; disable the button until the reported deadline |
| Tab focus, reconnect, elapsed interval | No GET-driven collection and no automatic POST |
| GEX-mode change | Update displayed saved arrays only; zero network requests |

Use a local one-second timer only to update snapshot age and cooldown text. It must not make network requests. Stop the timer on unmount. A 60-second cooldown is a rate limit, not an instruction to refresh every 60 seconds. Do not add an Auto toggle, scheduler, polling hook, visibility/focus refresh, or automatic retries.

The backend is authoritative: validate the symbol first; try the refresh lock without waiting; return 409 if busy; otherwise check cooldown, return 429 when active, or record the permitted start time and collect. Update provider rate-limit deadlines before releasing the lock. Release the lock in `finally`. Use a monotonic clock for elapsed-time enforcement and UTC only for the API deadline. Cooldown state is in process memory; it does not survive restart in this MVP.

`GET /api/config` must expose the server clock and deadline even when no snapshot exists. After a POST response, disable Refresh until the one config reconciliation completes; if it fails, retain the existing local cooldown and allow a later manual attempt, with the backend still enforcing limits. Do not persist refresh state inside immutable `dashboard_json`.

Use `ceil(max(0, refresh_not_before - estimated_server_now))` for countdown display. Derive the clock offset from `server_time`; do not assume the browser and server clocks are identical. A stale countdown from another tab never bypasses server enforcement. Do not synchronize tabs or poll them in the MVP.

Automatic refresh is deferred, not declared useless. It would require a verified permitted polling interval and explicit requirements for active/hidden-tab behavior. No such feature is part of this delivery.

## 12. Milestones and acceptance criteria

Complete milestones in order. M1-M4 may proceed in fixture mode while M0 is blocked, but live completion remains blocked.

### M0 - Verify live-source feasibility

**Deliverable:** `docs/source-contract.md` and permitted sample responses, kept private unless redistribution is allowed.

| ID | Acceptance criterion |
|---|---|
| M0.1 | Record access authorization/prerequisites, verified endpoint, request parameters, limits, and verification date. |
| M0.2 | Verify all three symbols; document actual stock/ETF parameter handling rather than assuming it. |
| M0.3 | Record exact field paths and timestamp meanings; demonstrate all required expirations and complete pagination. |
| M0.4 | Inspect the owner-provided sample and record the exact JSON paths for the underlying last-trade price, its timestamp, and option-row last premiums. Prove they are distinct; do not infer paths from field-name guesses. Record OI-date and multiplier limitations. |
| M0.5 | Assign PASS or BLOCKED with evidence. HTTP 403/429, unknown permission, or unavailable data cannot be represented as PASS. |
| M0.6 | Verify the first chain page supplies a finite positive underlying price for every enabled symbol. The live adapter must need no separate quote endpoint. A missing field blocks this integration; it does not authorize a fallback. |

### M1 - Fetch, normalize, and persist

**Deliverable:** seven-module scaffold, mandatory provider contract and isolated adapters, DuckDB schema, config, health, and snapshot loading.

| ID | Acceptance criterion |
|---|---|
| M1.1 | A clean checkout starts in fixture mode without external credentials; startup creates the DuckDB schema. |
| M1.2 | Parser tests cover grouped expirations, call/put expansion, comma-formatted values, nulls, zero OI, and duplicates. |
| M1.3 | A multipage fixture proves every contract is collected once. A failed/missing/repeated page rejects the refresh. |
| M1.4 | A saved snapshot survives restart. GET latest performs zero provider HTTP calls. |
| M1.5 | Simulated mid-transaction failure leaves no partial snapshot or orphan contract rows. |
| M1.6 | Saving snapshot 21 leaves exactly 20 for that `(source_mode, symbol)` pair. Another source's and another symbol's snapshots remain unchanged. Latest reads are source-filtered. |
| M1.7 | All five provider-boundary tests in Section 4.4 pass. A StubProvider reaches both charts' saved API arrays without importing or constructing Nasdaq. |
| M1.8 | Settings with missing/extra per-instrument dividend entries, or a leftover `symbols` key, fail startup. React receives its ordered selector options from the backend; no ticker array is hardcoded there. |
| M1.9 | In a sample-derived adapter fixture, expected `underlying_price` exactly matches the response's underlying field. Give option-row premiums deliberately different values and assert none is used as spot. |
| M1.10 | Missing, zero, negative, or nonfinite first-page underlying price raises `INVALID_UNDERLYING_PRICE`, preserves old data, and makes zero separate-quote requests. A single-page chain requires one upstream request; N paginated chain pages require N, not N+1. |
| M1.11 | If later pages repeat changed underlying prices, the snapshot retains the first-page price and adds `UNDERLYING_PRICE_CHANGED_DURING_COLLECTION`. It never reprices different contracts using different page spots. |

### M2 - Implement and verify analytics

**Deliverable:** deterministic IV, gamma, GEX, and surface functions with numerical tests.

| ID | Acceptance criterion |
|---|---|
| M2.1 | For `S=K=100, T=1, r=0.05, q=0, sigma=0.20`, call price is `10.4505835722` within `1e-8`; gamma is `0.01876201735` within `1e-10`. Solving the unrounded call price recovers IV within `1e-6`. |
| M2.2 | For `S=100, K=105, T=0.5, r=0.04, q=0.02, sigma=0.25`, call/put parity error is below `1e-8`, and analytic gamma matches the second central difference `[V(S+h)-2*V(S)+V(S-h)]/h^2` within relative error `1e-4`, using `h=0.001*S`. |
| M2.3 | Tests cover every quote-exclusion reason, missing OI, explicit zero OI, 0DTE exclusion, DST conversion, and source-time fallback. |
| M2.4 | With gamma `0.02`, `S=100`, call OI `1000`, put OI `600`, multiplier `100`: call exposure is `$200,000`, put exposure `$120,000`, signed proxy `$80,000`, and gross `$320,000`. |
| M2.5 | Missing call or put exposure yields a null heatmap cell, not a zero or partial net. |
| M2.6 | A synthetic constant-IV chain at 25% produces surface IV within `1e-6` of `0.25` at all supported nodes. No extrapolated or gap-bridged cells appear. |
| M2.7 | Reprocessing the same normalized input, valuation time, and parameters produces identical analytical values and ordering. |

### M3 - Complete the API and failure handling

**Deliverable:** all four endpoints and immutable dashboard responses.

| ID | Acceptance criterion |
|---|---|
| M3.1 | GET and POST responses validate against Pydantic models and contain finite numbers or null, never NaN/Infinity. |
| M3.2 | Two overlapping refreshes produce one provider collection and one HTTP 409. Requests inside cooldown receive HTTP 429 without contacting the provider. |
| M3.3 | Timeout, HTTP 403/429, malformed JSON, pagination failure, and database failure preserve the old snapshot and return the documented error shape. |
| M3.4 | Every returned chart uses the same snapshot ID, spot, valuation time, and parameters. Spot provenance is `last_trade` / `chain_payload`. |
| M3.5 | Synthetic processing plus persistence for 2,000 contracts takes at most 5 seconds; GET latest takes at most 500 ms. Measure p95 over 20 warmed runs, excluding provider/network time, on a recorded 4-core/8-GB-or-better test machine. |
| M3.6 | Every GET endpoint and every rejected unsupported-symbol/cooldown request makes zero provider calls. A successful selected-symbol refresh does not collect other configured symbols. |
| M3.7 | Config reports the manual policy, server clock, in-progress state, and cooldown deadline independently of snapshots. A failed permitted attempt still consumes cooldown; a provider 429 extends it according to Section 5.3. |

### M4 - Build the React dashboard

**Deliverable:** shadcn/ui controls and panels, both Plotly charts, and all documented page states.

| ID | Acceptance criterion |
|---|---|
| M4.1 | Initial load and symbol change show saved data without a provider request. A symbol with no snapshot shows an explicit empty state. |
| M4.2 | One Refresh click issues one POST; successful completion replaces both chart panels together. |
| M4.3 | Both GEX modes have correct scales, units, null gaps, and hover fields. Mode changes issue no HTTP requests. |
| M4.4 | Surface plots use the specified axes and observed markers. Sparse inputs show insufficient-data messages, not invented surfaces. |
| M4.5 | Failed refresh keeps old data visible with its original timestamp and error message. Obsolete responses cannot cross-contaminate symbols. |
| M4.6 | Fixture, age, timestamp, pricing, and multiplier warnings appear under their exact conditions. No live-data claims appear. |
| M4.7 | Frontend tests cover loading, empty, successful refresh, stale snapshot, failed refresh, and rapid symbol switching. Type checking and production build pass. |
| M4.8 | Symbol/GEX selectors, Refresh, panels, status indicators, alerts, and loading placeholders use the specified shadcn/ui components. `components.json` and generated source are committed. No Recharts dependency or shadcn chart component is present. |
| M4.9 | With fake timers, advance ten minutes and dispatch focus/online/visibility events. Assert zero automatic POSTs and zero timer-triggered network requests; age/cooldown labels still update. |
| M4.10 | One click produces exactly one POST. Repeated clicks while pending/cooling down produce none. POST settlement produces exactly one config reconciliation GET. A failed refresh never retries itself. |
| M4.11 | Keyboard users can operate both selectors and Refresh; controls have visible labels and focus indicators. At 1280x800 there is no horizontal page overflow. |
| M4.12 | A snapshot displays `Underlying last (from chain)` and source timestamp or `Unknown`. It does not label recent collection as a live price. |

### M5 - Validate live operation and hand off

**Deliverable:** tested source code, dependency locks, setup instructions, and acceptance-test results.

| ID | Acceptance criterion |
|---|---|
| M5.1 | All automated tests pass without internet access or live Nasdaq requests. |
| M5.2 | README provides copy-paste Windows PowerShell setup/start/test commands; Docker and external database servers are not required. |
| M5.3 | After M0 passes, perform one complete live refresh for every supported symbol and a second refresh of SPY outside cooldown. Record snapshot IDs and completeness checks. |
| M5.4 | Hand-check five source contracts against normalized data and independently reproduce at least one GEX cell. Record exact inputs and results. |
| M5.5 | Restart with provider access disabled; the last saved snapshot still renders unchanged. |
| M5.6 | Document model limitations and remaining source gaps. Do not describe fixture-only operation as a completed Nasdaq integration. |
| M5.7 | README explains that replacing Nasdaq requires an adapter plus a factory/config choice, not changes to analytics/storage/frontend. Include the test-stub demonstration, the manual refresh policy, and the chain-only underlying-price rule. |

## 13. Definition of done

The live MVP is complete only when M0-M5 pass: the user can run the app locally, select any supported symbol, manually obtain an authorized complete snapshot, inspect both requested views, identify missing data/model assumptions, and reopen saved results after restarting.

Provider isolation tests, shadcn/ui usage, chain-contained underlying pricing, and manual-only collection must all pass before the release is accepted. A working Nasdaq-specific script without the interface does not satisfy the MVP.

The coding agent must stop at this scope. Do not add features to compensate for source-access problems or sparse data; report those conditions explicitly.

## Sources and verification notes

These references support external constraints and library/model behavior. Numerical thresholds, scope choices, and acceptance criteria are product decisions in this PRD.

- [S1] Nasdaq, Legal, especially current Sections 2 and 7. `https://www.nasdaq.com/legal`
- [S2] Nasdaq, authenticated Options Chain API documentation. `https://github.com/Nasdaq/NasdaqCloudDataService-REST-API/blob/main/restapi/chain.md`
- [S3] DuckDB, Concurrency. `https://duckdb.org/docs/current/connect/concurrency`
- [S4] SciPy, brentq. `https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.brentq.html`
- [S5] Options Industry Council, Black-Scholes Formula. `https://www.optionseducation.org/advancedconcepts/black-scholes-formula`
- [S6] Plotly, Heatmaps. `https://plotly.com/javascript/heatmaps/`
- [S7] Plotly, 3D Surface Plots. `https://plotly.com/javascript/3d-surface-plots/`
- [S8] FastAPI, Concurrency and async/await. `https://fastapi.tiangolo.com/async/`
- [S9] Options Industry Council, Options Basics. `https://www.optionseducation.org/optionsoverview/options-basics`
- [S10] Options Industry Council, Open Interest: Why It Matters. `https://www.optionseducation.org/news/open-interest-why-it-matters`
- [S11] Vite, Getting Started and Node runtime requirements. `https://vite.dev/guide/`
- [S12] Python 3.12, typing.Protocol and structural subtyping. `https://docs.python.org/3.12/library/typing.html`
- [S13] shadcn/ui, Vite installation. `https://ui.shadcn.com/docs/installation/vite`
- [S14] shadcn/ui, Radix Chart documentation (Recharts implementation). `https://ui.shadcn.com/docs/components/radix/chart`

Version 1.0 source notes are retained. Python/shadcn documentation was checked for this revision on September 16, 2026 (America/Chicago). The owner reports chain-contained underlying last-price data; its sample JSON was not accessible in this revision. The exact live schema and access permission remain M0 verification items. No live endpoint or sample-derived parser was verified as part of this document update.
