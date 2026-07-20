#!/usr/bin/env python3
"""Look up open-access locations for DOI records through Unpaywall.

Unpaywall is used as a structured OA/link-discovery layer. It does not parse
PDFs and does not bypass access controls.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
from email.message import Message
from pathlib import Path
from typing import Any

from litminer.engine import cache as cache_helpers
from litminer.engine.common import normalize_doi, read_csv_rows, utc_now, write_csv_atomic
from litminer.sources.api.errors import ProviderSearchError
from litminer.sources.api.http_client import RetryPolicy, fetch_json
from litminer.contracts.errors import ProviderCooldownError
from litminer.runtime.provider_runtime import ProviderRuntime


UNPAYWALL_BASE = "https://api.unpaywall.org/v2"
REQUEST_TIMEOUT = 30
MAX_RETRIES = 3
USER_AGENT = "litminer/1.0"

UNPAYWALL_RETRY = RetryPolicy(
    max_retries=MAX_RETRIES,
    max_wait_seconds=120.0,
    backoff_base=2.0,
    user_agent=USER_AGENT,
)

OUTPUT_COLUMNS = [
    "unpaywall_cache_status",
    "unpaywall_cache_key",
    "unpaywall_status",
    "unpaywall_error",
    "unpaywall_retry_after_seconds",
    "unpaywall_checked_at",
    "is_oa",
    "oa_status",
    "oa_locations_count",
    "best_oa_url",
    "best_oa_landing_url",
    "best_oa_pdf_url",
    "best_oa_host_type",
    "best_oa_version",
    "best_oa_license",
    "best_oa_evidence",
    "best_oa_repository_institution",
    "unpaywall_doi_url",
]

CACHEABLE_STATUSES = {"ok"}


def resolve_email(email: str | None = None) -> str:
    return (
        (email or "").strip()
        or os.environ.get("UNPAYWALL_EMAIL", "").strip()
        or os.environ.get("LITMINER_CONTACT_EMAIL", "").strip()
    )


class UnpaywallRateLimitError(RuntimeError):
    def __init__(self, message: str, retry_after_seconds: float | None = None,
                 attempts: int | None = None, request_count: int | None = None) -> None:
        super().__init__(message)
        self.status = "rate_limited"
        self.provider = "unpaywall"
        self.http_status = 429
        self.transient = True
        self.retry_after_seconds = retry_after_seconds
        self.attempts = attempts
        self.request_count = request_count


class UnpaywallRequestError(RuntimeError):
    def __init__(self, message: str, status: str = "error", *,
                 http_status: int | None = None, transient: bool | None = None,
                 attempts: int | None = None, request_count: int | None = None) -> None:
        super().__init__(message)
        self.provider = "unpaywall"
        self.status = status
        self.http_status = http_status
        self.transient = transient
        self.attempts = attempts
        self.request_count = request_count


def _request_json(url: str) -> dict[str, Any]:
    """Fetch via shared HTTP client, converting to Unpaywall error types."""
    try:
        return fetch_json(
            url,
            retry=UNPAYWALL_RETRY,
            timeout=REQUEST_TIMEOUT,
            raise_on_status=frozenset({404}),
        )
    except ProviderSearchError as exc:
        if exc.http_status == 404:
            raise urllib.error.HTTPError(
                url=url,
                code=404,
                msg="Not Found",
                hdrs=Message(),
                fp=None,
            ) from exc
        if exc.status == "rate_limited":
            raise UnpaywallRateLimitError(
                f"Unpaywall rate limit persisted after {MAX_RETRIES} attempts",
                retry_after_seconds=exc.retry_after_seconds,
                attempts=exc.attempts,
                request_count=exc.request_count,
            ) from exc
        raise UnpaywallRequestError(
            f"Unpaywall request failed after {MAX_RETRIES} attempts: {exc}",
            status=exc.status or "error", http_status=exc.http_status,
            transient=exc.transient, attempts=exc.attempts,
            request_count=exc.request_count,
        ) from exc


def lookup_doi(doi: str, email: str | None = None) -> dict[str, Any]:
    doi_clean = normalize_doi(doi)
    if not doi_clean:
        return {"status": "missing_doi", "error": "DOI is missing", "data": None}

    email_value = resolve_email(email)
    if not email_value:
        return {
            "status": "skipped_missing_email",
            "error": "Set UNPAYWALL_EMAIL or LITMINER_CONTACT_EMAIL to use Unpaywall",
            "data": None,
        }

    url = (
        f"{UNPAYWALL_BASE}/{urllib.parse.quote(doi_clean, safe='')}"
        f"?{urllib.parse.urlencode({'email': email_value})}"
    )
    try:
        return {"status": "ok", "error": "", "data": _request_json(url)}
    except UnpaywallRateLimitError as exc:
        return {
            "status": "rate_limited",
            "error": str(exc),
            "retry_after_seconds": exc.retry_after_seconds,
            "data": None,
        }
    except UnpaywallRequestError as exc:
        return {"status": exc.status, "error": str(exc), "data": None}
    except urllib.error.HTTPError as exc:
        try:
            if exc.code == 404:
                return {
                    "status": "not_found",
                    "error": "DOI not found in Unpaywall",
                    "data": None,
                }
            return {
                "status": "error",
                "error": f"HTTP {exc.code}: {exc.reason}",
                "data": None,
            }
        finally:
            exc.close()
    except Exception as exc:
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}", "data": None}


def _location_value(location: dict[str, Any] | None, key: str) -> str:
    if not isinstance(location, dict):
        return ""
    value = location.get(key)
    return "" if value is None else str(value)


def flatten_response(result: dict[str, Any], checked_at: str | None = None) -> dict[str, str]:
    data = result.get("data")
    location = data.get("best_oa_location") if isinstance(data, dict) else None
    oa_locations = data.get("oa_locations") if isinstance(data, dict) else []
    if not isinstance(oa_locations, list):
        oa_locations = []

    retry_after = result.get("retry_after_seconds")
    return {
        "unpaywall_status": str(result.get("status") or ""),
        "unpaywall_error": str(result.get("error") or ""),
        "unpaywall_retry_after_seconds": "" if retry_after is None else str(retry_after),
        "unpaywall_checked_at": checked_at or utc_now(),
        "is_oa": str(bool(data.get("is_oa"))).lower() if isinstance(data, dict) else "",
        "oa_status": str(data.get("oa_status") or "") if isinstance(data, dict) else "",
        "oa_locations_count": str(len(oa_locations)) if isinstance(data, dict) else "",
        "best_oa_url": _location_value(location, "url"),
        "best_oa_landing_url": _location_value(location, "url_for_landing_page"),
        "best_oa_pdf_url": _location_value(location, "url_for_pdf"),
        "best_oa_host_type": _location_value(location, "host_type"),
        "best_oa_version": _location_value(location, "version"),
        "best_oa_license": _location_value(location, "license"),
        "best_oa_evidence": _location_value(location, "evidence"),
        "best_oa_repository_institution": _location_value(location, "repository_institution"),
        "unpaywall_doi_url": str(data.get("doi_url") or "") if isinstance(data, dict) else "",
    }


def annotate_row(row: dict[str, str], email: str | None = None,
                 checked_at: str | None = None,
                 provider_runtime: ProviderRuntime | None = None) -> dict[str, str]:
    out = dict(row)
    doi = normalize_doi(row.get("crossref_doi") or row.get("doi") or "")
    if provider_runtime is not None and doi and resolve_email(email):
        try:
            result = provider_runtime.execute(
                "unpaywall",
                "doi_lookup",
                doi,
                lambda: lookup_doi(doi, email=email),
            )
        except ProviderCooldownError as exc:
            result = {
                "status": "rate_limited",
                "error": exc.envelope.message,
                "retry_after_seconds": exc.envelope.retry_after_seconds,
                "data": None,
            }
    else:
        result = lookup_doi(doi, email=email)
    out.update(flatten_response(result, checked_at=checked_at))
    return out


def _cache_identity(doi: str) -> str:
    return f"doi:{cache_helpers.cache_key(normalize_doi(doi))}"


def _cache_lookup(cache_obj: cache_helpers.JsonCache | None, key: str) -> dict[str, str] | None:
    if cache_obj is None:
        return None
    hit = cache_obj.get(key)
    if hit is None or not isinstance(hit.value, dict):
        return None
    cached = {str(k): str(v) for k, v in hit.value.items()}
    if cached.get("unpaywall_status") != "ok":
        return None
    return cached


def _cache_store(
    cache_obj: cache_helpers.JsonCache | None,
    key: str,
    flattened: dict[str, str],
) -> bool:
    if cache_obj is None:
        return False
    status = (flattened.get("unpaywall_status") or "").strip()
    if status not in CACHEABLE_STATUSES:
        return False
    cache_obj.set(key, flattened, status=status)
    return True


def _row_identity(row: dict[str, str]) -> str:
    doi = normalize_doi(row.get("crossref_doi") or row.get("doi") or "")
    if doi:
        return f"doi:{doi}"
    title = " ".join((row.get("crossref_title") or row.get("title") or "").strip().lower().split())
    year = (row.get("crossref_year") or row.get("publication_year") or row.get("year") or "").strip()
    return f"title:{title}|year:{year}"


def _existing_annotated_rows(output_path: Path) -> dict[str, dict[str, str]]:
    if not output_path.exists() or not output_path.is_file():
        return {}
    try:
        _fieldnames, rows = read_csv_rows(output_path)
    except Exception:
        return {}
    existing = {}
    for row in rows:
        status = (row.get("unpaywall_status") or "").strip()
        if status in {"ok", "not_found", "missing_doi"}:
            existing[_row_identity(row)] = row
    return existing


def annotate_csv(input_path: Path, output_path: Path,
                 email: str | None = None,
                 sleep_s: float = 0.1,
                 checkpoint_interval: int = 25,
                 max_rows: int | None = None,
                 cache_dir: Path | None = None,
                 cache_ttl_days: float | None = None,
                 cache_enabled: bool = True,
                 provider_runtime: ProviderRuntime | None = None) -> dict[str, int]:
    fieldnames, rows = read_csv_rows(input_path)
    if not fieldnames:
        raise SystemExit("Input CSV has no header")

    for col in OUTPUT_COLUMNS:
        if col not in fieldnames:
            fieldnames.append(col)

    counts: dict[str, int] = {}
    checked_at = utc_now()
    output_rows: list[dict[str, str]] = []
    existing_rows = _existing_annotated_rows(output_path)
    cache_obj = (
        cache_helpers.JsonCache(
            cache_dir,
            "unpaywall",
            enabled=cache_enabled,
            ttl_seconds=cache_helpers.ttl_days_to_seconds(cache_ttl_days),
        )
        if cache_dir
        else None
    )

    def checkpoint(index: int) -> None:
        if checkpoint_interval and checkpoint_interval > 0 and (index + 1) % checkpoint_interval == 0:
            write_csv_atomic(output_rows + rows[index + 1:], output_path, fieldnames=fieldnames)

    for index, row in enumerate(rows):
        existing = existing_rows.get(_row_identity(row))
        if existing is not None:
            annotated = dict(row)
            for col in OUTPUT_COLUMNS:
                annotated[col] = existing.get(col, "")
            status = annotated.get("unpaywall_status", "unknown")
            counts[status] = counts.get(status, 0) + 1
            counts["reused"] = counts.get("reused", 0) + 1
            output_rows.append(annotated)
            checkpoint(index)
            continue

        if max_rows is not None and max_rows >= 0 and index >= max_rows:
            annotated = dict(row)
            for col in OUTPUT_COLUMNS:
                annotated.setdefault(col, "")
            annotated["unpaywall_status"] = "skipped_budget"
            annotated["unpaywall_checked_at"] = checked_at
        else:
            annotated = dict(row)
            doi = normalize_doi(row.get("crossref_doi") or row.get("doi") or "")
            cache_key = _cache_identity(doi) if doi else ""
            cached = _cache_lookup(cache_obj, cache_key) if cache_key else None
            if cached is not None:
                counts["cache_hit"] = counts.get("cache_hit", 0) + 1
                for col in OUTPUT_COLUMNS:
                    annotated[col] = cached.get(col, "")
                annotated["unpaywall_cache_status"] = "hit"
                annotated["unpaywall_cache_key"] = cache_key
                annotated["unpaywall_checked_at"] = checked_at
            else:
                if cache_obj is not None and cache_key:
                    counts["cache_miss"] = counts.get("cache_miss", 0) + 1
                annotated = annotate_row(
                    row,
                    email=email,
                    checked_at=checked_at,
                    provider_runtime=provider_runtime,
                )
                annotated["unpaywall_cache_status"] = "miss" if cache_obj is not None and cache_key else "disabled"
                annotated["unpaywall_cache_key"] = cache_key if cache_obj is not None else ""
                cache_value = {col: annotated.get(col, "") for col in OUTPUT_COLUMNS}
                if cache_key and _cache_store(cache_obj, cache_key, cache_value):
                    counts["cache_store"] = counts.get("cache_store", 0) + 1
                    annotated["unpaywall_cache_status"] = "store"
        status = annotated.get("unpaywall_status", "unknown")
        counts[status] = counts.get(status, 0) + 1
        output_rows.append(annotated)
        checkpoint(index)
        if sleep_s and status != "skipped_budget" and annotated.get("unpaywall_cache_status") != "hit":
            time.sleep(sleep_s)

    write_csv_atomic(output_rows, output_path, fieldnames=fieldnames)

    print(f"Unpaywall annotation: {len(output_rows)} rows -> {output_path}", file=sys.stderr)
    for key, value in sorted(counts.items()):
        print(f"  {key}: {value}", file=sys.stderr)
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="Annotate DOI rows with Unpaywall OA locations.")
    parser.add_argument("--doi", default=None, help="Lookup one DOI and print JSON")
    parser.add_argument("--input", type=Path, default=None, help="Input CSV for batch annotation")
    parser.add_argument("--output", type=Path, default=None, help="Output CSV for batch annotation")
    parser.add_argument("--email", default=None, help="Unpaywall email; falls back to UNPAYWALL_EMAIL or LITMINER_CONTACT_EMAIL")
    parser.add_argument("--sleep", type=float, default=0.1, help="Delay between batch requests")
    parser.add_argument("--checkpoint-interval", type=int, default=25,
                        help="Write batch progress every N rows; 0 disables checkpoints")
    parser.add_argument("--max-rows", type=int, default=None,
                        help="Only annotate the first N CSV rows; remaining rows are marked skipped_budget")
    parser.add_argument("--cache-dir", type=Path, default=None,
                        help="Optional JSON cache directory for Unpaywall DOI metadata")
    parser.add_argument("--cache-ttl-days", type=float, default=None,
                        help="Cache TTL in days; omitted means no TTL for this cache invocation")
    parser.add_argument("--no-cache", action="store_true",
                        help="Disable Unpaywall cache even when --cache-dir is set")
    args = parser.parse_args()

    if args.doi:
        print(json.dumps(flatten_response(lookup_doi(args.doi, email=args.email)), indent=2))
        return
    if not args.input or not args.output:
        parser.error("Provide either --doi or both --input and --output")
    annotate_csv(
        args.input,
        args.output,
        email=args.email,
        sleep_s=args.sleep,
        checkpoint_interval=args.checkpoint_interval,
        max_rows=args.max_rows,
        cache_dir=args.cache_dir,
        cache_ttl_days=args.cache_ttl_days,
        cache_enabled=not args.no_cache,
    )


if __name__ == "__main__":
    main()
