"""Shared source-provider exceptions."""

from __future__ import annotations


class ProviderSearchError(RuntimeError):
    """Raised when a provider query fails, preserving rows already fetched."""

    def __init__(
        self,
        message: str,
        partial_results: list[dict[str, str]] | None = None,
        status: str = "error",
    ) -> None:
        super().__init__(message)
        self.partial_results = partial_results or []
        self.status = status
