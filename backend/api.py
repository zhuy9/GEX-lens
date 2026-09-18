"""External API endpoints, timeouts, and size limits (ADR-0001 Section 8.3).

One place for "what third-party APIs do we call and how" -- previously
split across nasdaq.py and rates.py.
"""

# Shared by every adapter (chain, dividend, SOFR): Section 8.3's "existing
# connect/read/write/pool timeout pattern," reused rather than reinvented
# per source.
CONNECT_TIMEOUT_SECONDS = 5.0
RW_POOL_TIMEOUT_SECONDS = 10.0

# --- Nasdaq (unofficial; see README's "Data Sources and Usage Rights") ----

NASDAQ_HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
NASDAQ_CHAIN_URL = "https://api.nasdaq.com/api/quote/{symbol}/option-chain"
NASDAQ_DIVIDENDS_URL = "https://api.nasdaq.com/api/quote/{symbol}/dividends"

# Chain-specific: a multi-page fetch, so its own (larger) cumulative cap
# (docs/source-contract.md) -- distinct from a single reference response.
CHAIN_PAGE_LIMIT = 2000
CHAIN_MAX_REQUESTS = 10
CHAIN_MAX_RESPONSE_BYTES = 10 * 1024 * 1024
CHAIN_MAX_CONTRACTS = 10_000
CHAIN_COLLECTION_WINDOW_SECONDS = 30

# --- NY Fed SOFR -----------------------------------------------------------

NYFED_SOFR_URL = "https://markets.newyorkfed.org/api/rates/secured/sofr/last/5.json"

# --- Shared reference-data limit (Section 8.3): SOFR and Nasdaq dividends
# are both single-shot, bounded requests, unlike the chain's multi-page fetch.
REFERENCE_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
