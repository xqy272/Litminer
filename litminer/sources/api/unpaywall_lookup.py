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
USER_AGENT = "litminer/1.0"

OUTPUT_COLUMNS = [
    "unpaywall_status",
    "unpaywall_error",
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


def _request_json(url: str) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


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

    return {
        "unpaywall_status": str(result.get("status") or ""),
        "unpaywall_error": str(result.get("error") or ""),
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


def annotate_csv(input_path: Path, output_path: Path,
                 email: str | None = None,
                 sleep_s: float = 0.1) -> dict[str, int]:
    fieldnames, rows = read_csv_rows(input_path)
    if not fieldnames:
        raise SystemExit("Input CSV has no header")

    for col in OUTPUT_COLUMNS:
        if col not in fieldnames:
            fieldnames.append(col)

    counts: dict[str, int] = {}
    checked_at = utc_now()
    output_rows = []
    for row in rows:
        annotated = annotate_row(row, email=email, checked_at=checked_at)
        status = annotated.get("unpaywall_status", "unknown")
        counts[status] = counts.get(status, 0) + 1
        output_rows.append(annotated)
        if sleep_s:
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
    args = parser.parse_args()

    if args.doi:
        print(json.dumps(flatten_response(lookup_doi(args.doi, email=args.email)), indent=2))
        return
    if not args.input or not args.output:
        parser.error("Provide either --doi or both --input and --output")
    annotate_csv(args.input, args.output, email=args.email, sleep_s=args.sleep)


if __name__ == "__main__":
    main()
