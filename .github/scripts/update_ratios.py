"""Refresh frontend/src/conversion_ratios.json (ADR-0002).

Display-only SPX/SPY and NDX/QQQ ratios from the last 1-minute bar where all
four Yahoo tickers have a close. On any bad value it exits non-zero and leaves
the file untouched, so the last committed ratios remain the fallback.
"""

import json
import math
import sys
from pathlib import Path

import yfinance as yf

OUT = Path(__file__).resolve().parents[2] / "frontend/src/conversion_ratios.json"
# etf -> (index label, Yahoo index ticker, sane ratio range). The ranges only
# catch garbage data; the real ratios drift ~0.1-0.2%/yr.
PAIRS = {"SPY": ("SPX", "^GSPC", (9, 11)), "QQQ": ("NDX", "^NDX", (38, 44))}


def main() -> None:
    tickers = [t for etf, (_, index_ticker, _) in PAIRS.items() for t in (etf, index_ticker)]
    # threads=False: parallel downloads race on yfinance's sqlite tz cache
    # ("database is locked") on fresh runners.
    closes = yf.download(
        tickers, period="1d", interval="1m", progress=False, auto_adjust=False, threads=False
    )
    closes = closes["Close"].dropna()
    if closes.empty:
        sys.exit("no minute bar with all four closes; keeping previous ratios")
    last = closes.iloc[-1]
    ratios = {}
    for etf, (index, index_ticker, (lo, hi)) in PAIRS.items():
        ratio = float(last[index_ticker] / last[etf])
        if not (math.isfinite(ratio) and lo < ratio < hi):
            sys.exit(f"{index}/{etf} ratio {ratio} outside {lo}-{hi}; keeping previous ratios")
        ratios[etf] = {"index": index, "ratio": round(ratio, 5)}
    out = {
        "asof": last.name.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "yahoo_1m_close",
        "ratios": ratios,
    }
    OUT.write_text(json.dumps(out, indent="\t") + "\n")  # tabs: match biome, no churn
    print(json.dumps(out))


if __name__ == "__main__":
    main()
