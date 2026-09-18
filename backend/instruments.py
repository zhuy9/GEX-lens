"""Per-symbol verified instrument facts (ADR-0001 M0).

One centralized table instead of separate ASSET_CLASS-style dicts scattered
across nasdaq.py and app.py. Adding a symbol here requires re-running M0
verification for it first (docs/source-contract.md,
docs/dividend-source-contract.md) -- this is a record of what each source
actually supports, not a runtime preference.
"""

from typing import Literal, NamedTuple

from models import InstrumentClass

NasdaqAssetClass = Literal["etf", "stocks"]  # Nasdaq's own "assetclass" query param values


class InstrumentInfo(NamedTuple):
    instrument_class: InstrumentClass
    chain_asset_class: NasdaqAssetClass  # option-chain endpoint's assetclass param
    dividend_asset_class: NasdaqAssetClass | None  # dividend endpoint's assetclass param; None if unsupported


INSTRUMENTS: dict[str, InstrumentInfo] = {
    "SPY": InstrumentInfo(
        instrument_class="etf",
        chain_asset_class="etf",
        # Confirmed live 2026-09-17 (docs/dividend-source-contract.md): SPY
        # is NYSE Arca-listed; Nasdaq's dividend-history feature returns a
        # "successful" envelope with a null payload for any non-Nasdaq-listed
        # symbol. Not a placeholder pending verification -- confirmed absent.
        dividend_asset_class=None,
    ),
    "QQQ": InstrumentInfo(instrument_class="etf", chain_asset_class="etf", dividend_asset_class="etf"),
    "AAPL": InstrumentInfo(
        instrument_class="equity", chain_asset_class="stocks", dividend_asset_class="stocks"
    ),
}
