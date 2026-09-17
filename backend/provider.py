"""OptionsDataProvider Protocol and ProviderError only."""

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
