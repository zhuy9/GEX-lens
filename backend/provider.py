"""OptionsDataProvider/RateDataProvider/DividendDataProvider Protocols, ProviderError."""

from typing import Protocol

from models import ChainRequest, ChainSnapshot, DividendFeedSnapshot, RateBatch


class OptionsDataProvider(Protocol):
    def fetch_chain(self, request: ChainRequest) -> ChainSnapshot:
        """Return a complete normalized snapshot, or raise ProviderError."""
        ...


class RateDataProvider(Protocol):
    def fetch_rates(self) -> RateBatch:
        """Return a batch of rate observations, or raise ProviderError."""
        ...


class DividendDataProvider(Protocol):
    def fetch_dividends(self, symbol: str) -> DividendFeedSnapshot:
        """Return a symbol's dividend feed, or raise ProviderError."""
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
