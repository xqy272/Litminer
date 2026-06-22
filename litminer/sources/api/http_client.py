#!/usr/bin/env python3
"""Shared HTTP client for provider API requests.

This module handles **single-request** retry/backoff/429/timeout logic that
was previously duplicated across six provider files. It deliberately does
**not** absorb cross-request concerns: circuit breakers, failure counting,
failure caching, and polite-pause-every-N-requests stay in
``api_discovery.py`` or the caller. Mixing the two layers would make the
single-request logic harder to test and the cross-request logic harder to
reason about.

Callers configure a ``RetryPolicy`` and pass it to ``fetch_json`` or
``fetch_bytes``. On failure, ``ProviderSearchError`` is raised with
``status``, ``http_status``, ``retry_after_seconds``, and ``transient``
attributes set. Providers that have their own error types (Crossref,
Unpaywall, Semantic Scholar) catch ``ProviderSearchError`` and convert.

Behavior preservation notes (vs. the per-provider implementations that
existed before extraction):

- ``raise_on_status`` replaces provider-specific "raise immediately"
  sets (OpenAlex ``{403, 409}``, Crossref ``{400, 404, 410}``,
  Unpaywall ``{404}``).
- ``RetryPolicy.rate_limit_retries`` overrides ``max_retries`` for 429
  responses only, preserving Semantic Scholar's longer rate-limit budget.
- ``RetryPolicy.backoff_floor`` preserves arXiv's ``max(3s, 2^attempt)``
  minimum sleep.
- ``RetryPolicy.max_wait_seconds`` preserves per-provider caps (60s for
  Crossref, 120s for most others).
- XML parse errors (``ET.ParseError``) are retried by ``fetch_bytes``
  to preserve arXiv's behavior.
"""

from __future__ import annotations

import http.client
import json
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any
from xml.etree import ElementTree as ET

from litminer.sources.api.errors import ProviderSearchError


_NETWORK_ERROR_MARKERS = (
    "ssl", "certificate", "cert", "dns", "name resolution", "network",
)


@dataclass(frozen=True)
class RetryPolicy:
    """Configuration for a single-request retry loop.

    Attributes:
        max_retries: Maximum attempts for non-rate-limit errors.
        max_wait_seconds: Cap on any single sleep.
        backoff_base: Exponential backoff base (``backoff_base ** attempt``).
        backoff_floor: Minimum sleep seconds (preserves arXiv's 3s floor).
        rate_limit_retries: If set, use this instead of ``max_retries``
            for HTTP 429 responses. Preserves Semantic Scholar's longer
            rate-limit budget.
        user_agent: User-Agent header value.
    """

    max_retries: int = 3
    max_wait_seconds: float = 120.0
    backoff_base: float = 2.0
    backoff_floor: float = 0.0
    rate_limit_retries: int | None = None
    user_agent: str = "litminer/1.0"


def retry_after_seconds(exc: urllib.error.HTTPError, attempt: int,
                        policy: RetryPolicy) -> float:
    """Extract Retry-After from an HTTPError, falling back to backoff."""
    retry_after = exc.headers.get("Retry-After") if exc.headers else None
    if retry_after:
        try:
            return max(0.0, min(float(retry_after), policy.max_wait_seconds))
        except ValueError:
            pass
    wait = max(policy.backoff_floor, policy.backoff_base ** attempt)
    return min(wait, policy.max_wait_seconds)


def status_for_exception(exc: Exception | None) -> str:
    """Classify an exception into a status string for ProviderSearchError."""
    text = str(exc or "").lower()
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code in {401, 403}:
            return "auth_error"
        return f"http_{exc.code}"
    if isinstance(exc, urllib.error.URLError) or any(
        marker in text for marker in _NETWORK_ERROR_MARKERS
    ):
        return "network_error"
    if isinstance(exc, json.JSONDecodeError):
        return "response_parse_error"
    if isinstance(exc, ET.ParseError):
        return "response_parse_error"
    return "error"


def _is_transient(status: str) -> bool:
    return (
        status in {"network_error", "response_parse_error"}
        or status.startswith("http_5")
    )


def _do_fetch(
    url: str,
    *,
    headers: dict[str, str] | None,
    retry: RetryPolicy,
    timeout: float,
    raise_on_status: frozenset[int],
    parse: bool,
) -> Any:
    """Single-request fetch with retries. Returns parsed JSON or raw bytes."""
    max_attempts = retry.max_retries
    rate_limit_max = retry.rate_limit_retries if retry.rate_limit_retries is not None else max_attempts
    last_error: Exception | None = None
    req_headers = {"User-Agent": retry.user_agent}
    if headers:
        req_headers.update(headers)

    for attempt in range(max(max_attempts, rate_limit_max)):
        try:
            req = urllib.request.Request(url, headers=req_headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                if parse:
                    return json.loads(raw.decode("utf-8"))
                return raw
        except urllib.error.HTTPError as exc:
            if exc.code in raise_on_status:
                raise ProviderSearchError(
                    f"HTTP {exc.code}: {exc.reason}",
                    status="auth_error" if exc.code in {401, 403} else f"http_{exc.code}",
                    http_status=exc.code,
                    transient=False,
                ) from exc
            last_error = exc
            is_rate_limited = exc.code == 429
            is_server_error = 500 <= exc.code < 600
            if is_rate_limited and attempt < rate_limit_max - 1:
                wait = retry_after_seconds(exc, attempt, retry)
                print(f"  Rate limited (429). Retry {attempt + 1}/{rate_limit_max} after {wait:g}s", file=sys.stderr)
                time.sleep(wait)
                continue
            if is_server_error and attempt < max_attempts - 1:
                wait = retry_after_seconds(exc, attempt, retry)
                print(f"  Retry {attempt + 1}/{max_attempts} after {wait:g}s: {exc}", file=sys.stderr)
                time.sleep(wait)
                continue
            if not is_rate_limited and not is_server_error and attempt < max_attempts - 1:
                wait = max(retry.backoff_floor, retry.backoff_base ** attempt)
                wait = min(wait, retry.max_wait_seconds)
                print(f"  Retry {attempt + 1}/{max_attempts} after {wait:g}s: {exc}", file=sys.stderr)
                time.sleep(wait)
                continue
            break
        except (urllib.error.URLError, json.JSONDecodeError, ET.ParseError,
                OSError, http.client.IncompleteRead) as exc:
            last_error = exc
            if attempt < max_attempts - 1:
                wait = max(retry.backoff_floor, retry.backoff_base ** attempt)
                wait = min(wait, retry.max_wait_seconds)
                print(f"  Retry {attempt + 1}/{max_attempts} after {wait:g}s: {exc}", file=sys.stderr)
                time.sleep(wait)
                continue
            break

    if isinstance(last_error, urllib.error.HTTPError) and last_error.code == 429:
        raise ProviderSearchError(
            f"Rate limit persisted after {rate_limit_max} attempts",
            status="rate_limited",
            retry_after_seconds=retry_after_seconds(last_error, max(rate_limit_max - 1, 0), retry),
            http_status=429,
            transient=True,
        ) from last_error

    status = status_for_exception(last_error)
    raise ProviderSearchError(
        f"Request failed after {max_attempts} attempts: {last_error}",
        status=status,
        http_status=last_error.code if isinstance(last_error, urllib.error.HTTPError) else None,
        transient=_is_transient(status),
    ) from last_error


def fetch_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    retry: RetryPolicy = RetryPolicy(),
    timeout: float = 30.0,
    raise_on_status: frozenset[int] = frozenset(),
) -> dict:
    """Fetch URL with retries and return parsed JSON dict."""
    return _do_fetch(url, headers=headers, retry=retry, timeout=timeout,
                     raise_on_status=raise_on_status, parse=True)


def fetch_bytes(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    retry: RetryPolicy = RetryPolicy(),
    timeout: float = 30.0,
    raise_on_status: frozenset[int] = frozenset(),
) -> bytes:
    """Fetch URL with retries and return raw bytes."""
    return _do_fetch(url, headers=headers, retry=retry, timeout=timeout,
                     raise_on_status=raise_on_status, parse=False)
