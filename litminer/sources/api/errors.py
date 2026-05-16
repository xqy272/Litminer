"""Shared source-provider exceptions."""

from __future__ import annotations


class ProviderSearchError(RuntimeError):
    """Raised when a provider query fails, preserving rows already fetched."""

    def __init__(
        self,
        message: str,
        partial_results: list[dict[str, str]] | None = None,
        status: str = "error",
        *,
        retry_after_seconds: float | None = None,
        http_status: int | None = None,
        transient: bool | None = None,
    ) -> None:
        super().__init__(message)
        self.partial_results = partial_results or []
        self.status = status
        self.retry_after_seconds = retry_after_seconds
        self.http_status = http_status
        self.transient = transient
