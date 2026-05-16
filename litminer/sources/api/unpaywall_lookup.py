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
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from litminer.engine.common import normalize_doi, read_csv_rows, write_csv_atomic


UNPAYWALL_BASE = "https://api.unpaywall.org/v2"
REQUEST_TIMEOUT = 30
MAX_RETRIES = 3
USER_AGENT = "litminer/1.0"

OUTPUT_COLUMNS = [
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


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def resolve_email(email: str | None = None) -> str:
    return (
        (email or "").strip()
        or os.environ.get("UNPAYWALL_EMAIL", "").strip()
        or os.environ.get("LITMINER_CONTACT_EMAIL", "").strip()
    )


class UnpaywallRateLimitError(RuntimeError):
    def __init__(self, message: str, retry_after_seconds: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


def _retry_after_seconds(exc: urllib.error.HTTPError, attempt: int) -> float:
    retry_after = exc.headers.get("Retry-After") if exc.headers else None
    if retry_after:
        try:
            return max(0.0, min(float(retry_after), 120.0))
        except ValueError:
            pass
    return float(2 ** attempt)


def _request_json(url: str) -> dict[str, Any]:
    last_error: Exception | None = None
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(MAX_RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise
            last_error = exc
            if exc.code == 429:
                wait = _retry_after_seconds(exc, attempt)
                if attempt < MAX_RETRIES - 1:
                    print(
                        f"  Rate limited by Unpaywall (429). Retry {attempt + 1}/{MAX_RETRIES} after {wait:g}s",
                        file=sys.stderr,
                    )
                    time.sleep(wait)
                    continue
                raise UnpaywallRateLimitError(
                    f"Unpaywall rate limit persisted after {MAX_RETRIES} attempts",
                    retry_after_seconds=wait,
                ) from exc
            if 500 <= exc.code < 600 and attempt < MAX_RETRIES - 1:
                wait = _retry_after_seconds(exc, attempt)
                print(f"  Retry {attempt + 1}/{MAX_RETRIES} after {wait:g}s: {exc}", file=sys.stderr)
                time.sleep(wait)
                continue
            raise
        except (urllib.error.URLError, json.JSONDecodeError, OSError) as exc:
            last_error = exc
            if attempt < MAX_RETRIES - 1:
                wait = float(2 ** attempt)
                print(f"  Retry {attempt + 1}/{MAX_RETRIES} after {wait:g}s: {exc}", file=sys.stderr)
                time.sleep(wait)
                continue
    raise RuntimeError(f"Unpaywall request failed after {MAX_RETRIES} attempts: {last_error}")


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
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {"status": "not_found", "error": "DOI not found in Unpaywall", "data": None}
        return {"status": "error", "error": f"HTTP {exc.code}: {exc.reason}", "data": None}
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
                 checked_at: str | None = None) -> dict[str, str]:
    out = dict(row)
    doi = normalize_doi(row.get("crossref_doi") or row.get("doi") or "")
    out.update(flatten_response(lookup_doi(doi, email=email), checked_at=checked_at))
    return out


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
        if status in {"ok", "not_found", "skipped_missing_email", "missing_doi"}:
            existing[_row_identity(row)] = row
    return existing


def annotate_csv(input_path: Path, output_path: Path,
                 email: str | None = None,
                 sleep_s: float = 0.1,
                 checkpoint_interval: int = 25,
                 max_rows: int | None = None) -> dict[str, int]:
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
            annotated = annotate_row(row, email=email, checked_at=checked_at)
        status = annotated.get("unpaywall_status", "unknown")
        counts[status] = counts.get(status, 0) + 1
        output_rows.append(annotated)
        checkpoint(index)
        if sleep_s and status != "skipped_budget":
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
    )


if __name__ == "__main__":
    main()
