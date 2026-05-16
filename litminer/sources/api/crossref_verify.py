#!/usr/bin/env python3
"""Verify bibliographic metadata against the Crossref API.

Usage:
    # Verify a single DOI
    python -m litminer.sources.api.crossref_verify --doi "10.1016/j.apcatb.2024.123456"

    # Verify all DOIs in a CSV
    python -m litminer.sources.api.crossref_verify --input candidates.csv --output verified.csv

    # Search Crossref by title to find a DOI
    python -m litminer.sources.api.crossref_verify --title-search "Machine learning accelerates enzyme stability screening"

    # Verify and add mismatch warnings
    python -m litminer.sources.api.crossref_verify --input candidates.csv --output verified.csv --strict

The script returns the canonical Crossref metadata for each paper and flags
mismatches between the input metadata and Crossref's authoritative record.
Crossref is treated as the ground truth for DOI, title, journal, year, and type.
"""

from __future__ import annotations

import argparse
import difflib
import http.client
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from litminer.engine.common import normalize_doi, read_csv_rows, write_csv_atomic

# Configuration

CROSSREF_BASE = "https://api.crossref.org"
REQUEST_TIMEOUT = 30
MAX_RETRIES = 3
USER_AGENT = "litminer/1.0"


def _user_agent() -> str:
    contact = os.environ.get("CROSSREF_MAILTO") or os.environ.get("LITMINER_CONTACT_EMAIL") or ""
    if contact:
        if contact.startswith("mailto:"):
            contact = contact[len("mailto:"):]
        return f"litminer/1.0 (mailto:{contact})"
    return USER_AGENT


def _retry_wait_seconds(exc: urllib.error.HTTPError, attempt: int) -> float:
    retry_after = exc.headers.get("Retry-After") if exc.headers else None
    if retry_after:
        try:
            return max(0.0, min(float(retry_after), 60.0))
        except ValueError:
            pass
    return float(2 ** attempt)


def _fetch_json(url: str) -> dict:
    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _user_agent()})
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in {400, 404, 410}:
                raise RuntimeError(f"HTTP {e.code}: {e.reason}") from e
            last_error = e
            if attempt < MAX_RETRIES - 1:
                wait = _retry_wait_seconds(e, attempt)
                print(f"  Retry {attempt + 1}/{MAX_RETRIES} after {wait}s: {e}", file=sys.stderr)
                time.sleep(wait)
        except (urllib.error.URLError, json.JSONDecodeError, OSError,
                http.client.IncompleteRead) as e:
            last_error = e
            if attempt < MAX_RETRIES - 1:
                wait = 2 ** attempt
                print(f"  Retry {attempt + 1}/{MAX_RETRIES} after {wait}s: {e}", file=sys.stderr)
                time.sleep(wait)
    raise RuntimeError(f"Crossref request failed: {last_error}")


_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    """Strip HTML/XML tags from text for comparison."""
    return _HTML_TAG_RE.sub("", text)


def _title_similarity(a: str, b: str) -> float:
    """Return a similarity score between two titles (0-1)."""
    a = _strip_html(a).strip().lower().rstrip(".")
    b = _strip_html(b).strip().lower().rstrip(".")
    a = re.sub(r"\s+", " ", a)
    b = re.sub(r"\s+", " ", b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


# Metadata extraction from Crossref response

def _extract_crossref_metadata(message: dict) -> dict[str, str]:
    """Extract uniform fields from a Crossref API message."""
    return {
        "crossref_doi": normalize_doi(message.get("DOI", "")),
        "crossref_title": (message.get("title", [""]) or [""])[0].strip(),
        "crossref_container": (message.get("container-title", [""]) or [""])[0].strip(),
        "crossref_publisher": message.get("publisher", ""),
        "crossref_type": message.get("type", ""),
        "crossref_year": str(_extract_year(message)),
        "crossref_issn": (message.get("ISSN", [""]) or [""])[0],
        "crossref_url": message.get("URL", ""),
        "crossref_created": _extract_date_part(message.get("created", {})),
        "crossref_published": _extract_date_part(message.get("published-print", {}) or message.get("published-online", {})),
    }


def _safe_first_date_part(date_dict: dict) -> list:
    """Safely extract the first date-parts entry, handling None, empty lists, and missing keys."""
    if not date_dict or not isinstance(date_dict, dict):
        return []
    dps = date_dict.get("date-parts", None)
    if not dps or not isinstance(dps, list) or len(dps) == 0:
        return []
    first = dps[0]
    return first if isinstance(first, list) else []


def _extract_year(message: dict) -> int | str:
    """Extract publication year from Crossref message."""
    # Try issued date first
    parts = _safe_first_date_part(message.get("issued", {}))
    if parts and parts[0]:
        return parts[0]

    # Try created date
    parts = _safe_first_date_part(message.get("created", {}))
    if parts and parts[0]:
        return parts[0]

    # Try published-print or published-online
    for key in ("published-print", "published-online"):
        parts = _safe_first_date_part(message.get(key, {}))
        if parts and parts[0]:
            return parts[0]

    return ""


def _extract_date_part(date_dict: dict) -> str:
    if not date_dict:
        return ""
    parts = _safe_first_date_part(date_dict)
    if not parts:
        return ""
    return "-".join(str(p) for p in parts if p is not None)


# Core verification

def verify_doi(doi: str) -> dict[str, str] | None:
    """Verify a single DOI against Crossref. Returns metadata dict or None."""
    doi_clean = normalize_doi(doi)
    if not doi_clean:
        return None

    url = f"{CROSSREF_BASE}/works/{urllib.parse.quote(doi_clean)}"
    try:
        data = _fetch_json(url)
    except RuntimeError as e:
        print(f"  Crossref lookup failed for {doi_clean}: {e}", file=sys.stderr)
        return None

    message = data.get("message")
    if not message:
        print(f"  Crossref returned no message for {doi_clean}", file=sys.stderr)
        return None

    return _extract_crossref_metadata(message)


def search_by_title(title: str, max_results: int = 5) -> list[dict[str, str]]:
    """Search Crossref by title and return candidate metadata."""
    if not title or len(title.strip()) < 5:
        return []

    url = (
        f"{CROSSREF_BASE}/works"
        f"?query.title={urllib.parse.quote(title)}"
        f"&rows={max_results}"
    )
    try:
        data = _fetch_json(url)
    except RuntimeError as e:
        print(f"  Crossref title search failed: {e}", file=sys.stderr)
        return []

    items = data.get("message", {}).get("items", [])
    return [_extract_crossref_metadata(item) for item in items]


def _row_identity(row: dict[str, str]) -> str:
    doi = normalize_doi(row.get("crossref_doi") or row.get("doi") or "")
    if doi:
        return f"doi:{doi}"
    return _row_title_identity(row)


def _row_title_identity(row: dict[str, str]) -> str:
    title = re.sub(r"\s+", " ", (row.get("crossref_title") or row.get("title") or "").strip().lower())
    year = (row.get("crossref_year") or row.get("publication_year") or row.get("year") or "").strip()
    journal = re.sub(r"\s+", " ", (row.get("crossref_container") or row.get("journal") or "").strip().lower())
    return f"title:{title}|year:{year}|journal:{journal}"


def _existing_verified_rows(output_path: Path) -> dict[str, dict[str, str]]:
    if not output_path.exists() or not output_path.is_file():
        return {}
    try:
        _fieldnames, rows = read_csv_rows(output_path)
    except Exception:
        return {}
    existing = {}
    for row in rows:
        if (row.get("crossref_status") or "").strip():
            existing[_row_identity(row)] = row
            existing[_row_title_identity(row)] = row
    return existing


# Mismatch detection

def detect_mismatches(input_row: dict[str, str], crossref_meta: dict[str, str],
                      strict: bool = False) -> list[str]:
    """Compare input metadata with Crossref and return mismatch warnings."""
    warnings: list[str] = []

    # Title check
    input_title = (input_row.get("title") or "").strip()
    xref_title = crossref_meta.get("crossref_title", "")
    if input_title and xref_title:
        sim = _title_similarity(input_title, xref_title)
        if sim < 0.85:
            warnings.append(f"TITLE_MISMATCH: input='{input_title[:80]}' vs crossref='{xref_title[:80]}' (sim={sim:.2f})")
        elif sim < 0.95 and strict:
            warnings.append(f"TITLE_MINOR_DIFF: input vs crossref (sim={sim:.2f})")

    # Year check
    input_year = (input_row.get("publication_year") or "").strip()
    xref_year = crossref_meta.get("crossref_year", "")
    if input_year and xref_year and input_year != xref_year:
        warnings.append(f"YEAR_MISMATCH: input={input_year} vs crossref={xref_year}")

    # Journal check
    input_journal = (input_row.get("journal") or "").strip().lower()
    xref_journal = crossref_meta.get("crossref_container", "").lower()
    if input_journal and xref_journal and input_journal != xref_journal:
        sim = _title_similarity(input_journal, xref_journal)
        if sim < 0.8:
            warnings.append(f"JOURNAL_MISMATCH: input='{input_journal[:60]}' vs crossref='{xref_journal[:60]}'")

    return warnings


# Batch verification

def _best_title_match(title: str, input_row: dict[str, str] | None = None,
                      max_results: int = 5,
                      min_similarity: float = 0.90) -> dict[str, str] | None:
    """Return the best Crossref title-search match above a similarity threshold."""
    candidates = search_by_title(title, max_results=max_results)
    best: dict[str, str] | None = None
    best_score = 0.0
    input_row = input_row or {}
    input_year = (input_row.get("publication_year") or input_row.get("year") or "").strip()
    input_journal = (input_row.get("journal") or input_row.get("container") or "").strip()
    has_context = bool(input_year or input_journal)
    threshold = min_similarity if has_context else max(min_similarity, 0.95)

    for candidate in candidates:
        score = _title_similarity(title, candidate.get("crossref_title", ""))
        if score < threshold:
            continue
        candidate_year = candidate.get("crossref_year", "").strip()
        if input_year and candidate_year and input_year != candidate_year:
            continue
        candidate_journal = candidate.get("crossref_container", "").strip()
        if input_journal and candidate_journal:
            journal_score = _title_similarity(input_journal, candidate_journal)
            if journal_score < 0.80:
                continue
        if score > best_score:
            best = candidate
            best_score = score
    if best:
        best["crossref_title_similarity"] = f"{best_score:.3f}"
        best["crossref_recovered_doi_confidence"] = "high" if best_score >= 0.97 and has_context else "medium"
        return best
    return None


def verify_csv(input_path: Path, output_path: Path, strict: bool = False,
               title_lookup: bool = False,
               checkpoint_interval: int = 25) -> dict[str, int]:
    """Verify all DOIs in a CSV file and write augmented output."""
    fieldnames, rows = read_csv_rows(input_path)
    if not fieldnames:
        raise SystemExit("Input CSV has no header")

    if "doi" not in fieldnames:
        fieldnames.append("doi")

    # Add Crossref columns
    xref_cols = [
        "crossref_doi", "crossref_title", "crossref_container", "crossref_publisher",
        "crossref_type", "crossref_year", "crossref_issn", "crossref_url",
        "crossref_created", "crossref_published", "crossref_mismatches",
        "crossref_lookup_method", "crossref_title_similarity",
        "crossref_recovered_doi_confidence", "crossref_status", "crossref_verified",
    ]
    for col in xref_cols:
        if col not in fieldnames:
            fieldnames.append(col)

    counts = {
        "rows": len(rows),
        "verified": 0,
        "title_recovered": 0,
        "mismatch": 0,
        "lookup_failed": 0,
        "missing_doi": 0,
        "title_lookup_failed": 0,
        "reused": 0,
    }
    request_count = 0
    existing_rows = _existing_verified_rows(output_path)

    def polite_pause() -> None:
        nonlocal request_count
        request_count += 1
        if request_count % 10 == 0:
            time.sleep(0.5)

    def checkpoint(index: int) -> None:
        if checkpoint_interval and checkpoint_interval > 0 and (index + 1) % checkpoint_interval == 0:
            write_csv_atomic(rows, output_path, fieldnames=fieldnames)

    def count_status(row: dict[str, str]) -> None:
        status = (row.get("crossref_status") or "").strip()
        if status in counts:
            counts[status] += 1
        elif status == "title_recovered":
            counts["title_recovered"] += 1

    for i, row in enumerate(rows):
        existing = existing_rows.get(_row_identity(row))
        if existing is not None:
            for col in ["doi", *xref_cols]:
                if existing.get(col):
                    row[col] = existing[col]
            counts["reused"] += 1
            count_status(row)
            checkpoint(i)
            continue

        doi = row.get("doi", "").strip()
        if not doi:
            if title_lookup:
                title = row.get("title", "").strip()
                meta = _best_title_match(title, input_row=row)
                polite_pause()
                if meta is None:
                    row["crossref_mismatches"] = "NO_DOI_TITLE_LOOKUP_FAILED"
                    row["crossref_status"] = "title_lookup_failed"
                    row["crossref_verified"] = "false"
                    counts["title_lookup_failed"] += 1
                    checkpoint(i)
                    continue
                row["doi"] = meta.get("crossref_doi", "")
                row["crossref_lookup_method"] = "title_search"
                for key, value in meta.items():
                    row[key] = value
                row["crossref_mismatches"] = ""
                row["crossref_status"] = "title_recovered"
                row["crossref_verified"] = "true"
                counts["title_recovered"] += 1
                checkpoint(i)
                continue
            row["crossref_mismatches"] = "NO_DOI"
            row["crossref_status"] = "missing_doi"
            row["crossref_verified"] = "false"
            counts["missing_doi"] += 1
            checkpoint(i)
            continue

        meta = verify_doi(doi)
        polite_pause()
        if meta is None:
            row["crossref_mismatches"] = "CROSSREF_LOOKUP_FAILED"
            row["crossref_status"] = "lookup_failed"
            row["crossref_verified"] = "false"
            counts["lookup_failed"] += 1
            checkpoint(i)
            continue

        row["crossref_lookup_method"] = "doi"

        # Write Crossref fields
        for key, value in meta.items():
            row[key] = value

        # Detect mismatches
        warnings = detect_mismatches(row, meta, strict=strict)
        row["crossref_mismatches"] = "; ".join(warnings) if warnings else ""
        if warnings:
            row["crossref_status"] = "mismatch"
            row["crossref_verified"] = "false"
            counts["mismatch"] += 1
            print(f"  Row {i + 1}: {len(warnings)} mismatch(es) for {doi}", file=sys.stderr)
            for w in warnings:
                print(f"    {w}", file=sys.stderr)
        else:
            row["crossref_status"] = "verified"
            row["crossref_verified"] = "true"
            counts["verified"] += 1
        checkpoint(i)
    trusted_count = counts["verified"] + counts["title_recovered"]
    print(
        f"Verified: {trusted_count} rows "
        f"(doi={counts['verified']}, title_recovered={counts['title_recovered']}), "
        f"mismatch={counts['mismatch']}, failed={counts['lookup_failed']}, "
        f"missing_doi={counts['missing_doi']}, title_lookup_failed={counts['title_lookup_failed']}.",
        file=sys.stderr,
    )

    write_csv_atomic(rows, output_path, fieldnames=fieldnames)

    print(f"Wrote verified results to {output_path}", file=sys.stderr)
    return counts


# CLI

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify bibliographic metadata against Crossref API."
    )
    doi_group = parser.add_mutually_exclusive_group()
    doi_group.add_argument("--doi", type=str, help="Single DOI to verify")
    doi_group.add_argument("--input", type=Path, help="CSV file with a 'doi' column to verify")
    doi_group.add_argument("--title-search", type=str, help="Search Crossref by title")

    parser.add_argument("--output", type=Path, help="Output CSV path (required with --input)")
    parser.add_argument("--strict", action="store_true",
                        help="Flag minor title differences as mismatches")
    parser.add_argument("--title-lookup", action="store_true",
                        help="For CSV rows without DOI, search Crossref by title and fill high-confidence matches")
    parser.add_argument("--checkpoint-interval", type=int, default=25,
                        help="Write batch progress every N rows; 0 disables checkpoints")
    parser.add_argument("--json", action="store_true",
                        help="Output JSON for single --doi lookups instead of table")
    args = parser.parse_args()

    if not any([args.doi, args.input, args.title_search]):
        parser.error("One of --doi, --input, or --title-search is required.")

    # Single DOI mode
    if args.doi:
        meta = verify_doi(args.doi)
        if meta is None:
            print(json.dumps({"error": "DOI not found or lookup failed"}))
            sys.exit(1)
        if args.json:
            print(json.dumps(meta, indent=2))
        else:
            for k, v in meta.items():
                print(f"{k}: {v}")
        return

    # Title search mode
    if args.title_search:
        results = search_by_title(args.title_search)
        if args.json:
            print(json.dumps(results, indent=2))
        else:
            for i, r in enumerate(results):
                print(f"\n--- Result {i + 1} ---")
                for k, v in r.items():
                    print(f"  {k}: {v}")
        return

    # CSV batch mode
    if args.input:
        if not args.output:
            parser.error("--output is required with --input")
        verify_csv(
            args.input,
            args.output,
            strict=args.strict,
            title_lookup=args.title_lookup,
            checkpoint_interval=args.checkpoint_interval,
        )
        print(f"Done: verified -> {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
