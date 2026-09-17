"""FixtureProvider: synthetic canonical snapshots; zero HTTP."""

import json
import math
import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from models import ChainRequest, ChainSnapshot, OptionQuote, ny_local_date, year_fraction
from provider import ProviderError

# Fixed synthetic "now" so fixture expirations never age out of the 1-60 DTE
# window, regardless of when the app actually runs (PRD 5.1).
FIXED_VALUATION_AT = datetime(2026, 1, 2, 21, 0, tzinfo=timezone.utc)

# spot price, flat-vol assumption, and dividend yield used only to synthesize
# plausible bid/ask around a BSM mid. Not read from settings.json.
_SYMBOL_DATA = {
    "SPY": {"spot": 550.00, "sigma": 0.16, "q": 0.012},
    "QQQ": {"spot": 480.00, "sigma": 0.20, "q": 0.006},
    "AAPL": {"spot": 225.00, "sigma": 0.28, "q": 0.004},
}
_RISK_FREE_RATE = 0.04
_DTE_OFFSETS = (2, 9, 16, 30, 44, 58)
_NUM_STRIKES = 25


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _bsm_mid(is_call: bool, s: float, k: float, t: float, r: float, q: float, sigma: float) -> float:
    d1 = (math.log(s / k) + (r - q + 0.5 * sigma * sigma) * t) / (sigma * math.sqrt(t))
    d2 = d1 - sigma * math.sqrt(t)
    if is_call:
        return s * math.exp(-q * t) * _norm_cdf(d1) - k * math.exp(-r * t) * _norm_cdf(d2)
    return k * math.exp(-r * t) * _norm_cdf(-d2) - s * math.exp(-q * t) * _norm_cdf(-d1)


def _synthesize_symbol(symbol: str, min_dte: int, max_dte: int, rng: random.Random) -> list[dict]:
    """Build synthetic canonical contract rows for one symbol as plain dicts."""
    info = _SYMBOL_DATA[symbol]
    spot = info["spot"]
    q = info["q"]
    today = ny_local_date(FIXED_VALUATION_AT)
    step = spot * 0.40 / (_NUM_STRIKES - 1)

    rows = []
    for offset in _DTE_OFFSETS:
        if not (min_dte <= offset <= max_dte):
            continue
        expiration = today + timedelta(days=offset)
        t = year_fraction(expiration, FIXED_VALUATION_AT)
        sigma = info["sigma"] + 0.02 * (offset / _DTE_OFFSETS[-1])
        for i in range(_NUM_STRIKES):
            strike = round((spot * 0.80 + i * step) / 0.5) * 0.5
            moneyness_weight = max(0.05, 1.0 - abs(strike - spot) / (spot * 0.20))
            for option_type, is_call in (("C", True), ("P", False)):
                mid = max(0.01, _bsm_mid(is_call, spot, strike, t, _RISK_FREE_RATE, q, sigma))
                spread = max(0.01, mid * 0.02)
                bid = round(mid - spread / 2, 2)
                ask = round(mid + spread / 2, 2)
                volume = rng.randint(0, 500)
                oi_roll = rng.random()
                if oi_roll < 0.05:
                    open_interest = None
                elif oi_roll < 0.10:
                    open_interest = 0
                else:
                    open_interest = int(rng.randint(10, 5000) * moneyness_weight)
                rows.append(
                    {
                        "symbol": symbol,
                        "expiration": expiration.isoformat(),
                        "strike": str(Decimal(str(strike))),
                        "option_type": option_type,
                        "bid": bid,
                        "ask": ask,
                        "last": round(mid, 2),
                        "volume": volume,
                        "open_interest": open_interest,
                        "multiplier": 100,
                        "provider_contract_id": (
                            f"FIXTURE-{symbol}-{expiration.isoformat()}-{option_type}-{strike}"
                        ),
                        "quote_asof": FIXED_VALUATION_AT.isoformat(),
                    }
                )
    return rows


class FixtureProvider:
    """Synthetic OptionsDataProvider. Makes zero network calls."""

    def fetch_chain(self, request: ChainRequest) -> ChainSnapshot:
        if request.symbol not in _SYMBOL_DATA:
            raise ProviderError("UNSUPPORTED_SYMBOL", f"No fixture data for {request.symbol!r}")

        rng = random.Random(f"fixture-{request.symbol}")
        rows = _synthesize_symbol(request.symbol, request.min_calendar_dte, request.max_calendar_dte, rng)

        contracts = tuple(
            OptionQuote(
                symbol=row["symbol"],
                expiration=row["expiration"],
                strike=Decimal(row["strike"]),
                option_type=row["option_type"],
                bid=row["bid"],
                ask=row["ask"],
                last=row["last"],
                volume=row["volume"],
                open_interest=row["open_interest"],
                multiplier=row["multiplier"],
                provider_contract_id=row["provider_contract_id"],
                quote_asof=row["quote_asof"],
                flags=(),
            )
            for row in rows
        )

        raw_payload = json.dumps(
            {
                "provider_id": "fixture",
                "request": {
                    "symbol": request.symbol,
                    "min_calendar_dte": request.min_calendar_dte,
                    "max_calendar_dte": request.max_calendar_dte,
                },
                "rows": rows,
            }
        )

        return ChainSnapshot(
            provider_id="fixture",
            symbol=request.symbol,
            underlying_price=_SYMBOL_DATA[request.symbol]["spot"],
            underlying_price_kind="last_trade",
            underlying_price_origin="chain_payload",
            collection_started_at=FIXED_VALUATION_AT,
            collected_at=FIXED_VALUATION_AT,
            chain_asof=FIXED_VALUATION_AT,
            spot_asof=FIXED_VALUATION_AT,
            oi_asof=None,
            contracts=contracts,
            warnings=(),
            source_row_count=len(rows) // 2,
            provider_response_count=1,
            raw_payload_json=raw_payload,
        )
