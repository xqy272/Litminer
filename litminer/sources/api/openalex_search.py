#!/usr/bin/env python3
"""Search OpenAlex for candidate papers and output uniform CSV.

Usage:
    python -m litminer.sources.api.openalex_search --query "your literature query" --year-from 2020 --max-results 200 --output candidates.csv
    python -m litminer.sources.api.openalex_search --query-file queries.txt --year-from 2020 --output candidates.csv

The script handles:
- API pagination and cursor-based traversal
- Rate limiting with exponential backoff
- Field mapping from OpenAlex schema to the uniform extraction schema
- Error handling (timeouts, rate limits, empty results)
- Duplicate detection within a single search session
"""

from __future__ import annotations

import argparse
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
from typing import Any

from litminer.engine.common import write_csv_atomic
from litminer.sources.api.errors import ProviderSearchError

# Configuration

OPENALEX_BASE = "https://api.openalex.org/works"
REQUEST_TIMEOUT = 30  # seconds
MAX_RETRIES = 3
PER_PAGE = 200  # max allowed by OpenAlex
USER_AGENT = "litminer/1.0"
DEFAULT_MAILTO = os.environ.get("OPENALEX_MAILTO") or os.environ.get("LITMINER_CONTACT_EMAIL") or ""
OPENALEX_SELECT_FIELDS = ",".join([
    "id",
    "doi",
    "title",
    "publication_year",
    "primary_location",
    "abstract_inverted_index",
    "type",
    "cited_by_count",
    "authorships",
])


# Field mapping: OpenAlex JSON path to uniform CSV column
# Each value is a callable that extracts the field from the work dict.
OUTPUT_FIELDS = [
    "title",
    "doi",
    "publication_year",
    "journal",
    "abstract",
    "article_type",
    "cited_by_count",
    "authors",
    "openalex_id",
    "landing_page_url",
    "discovery_source",
    "discovery_query",
]


def _extract_doi(work: dict) -> str:
    doi = work.get("doi", "")
    if doi and isinstance(doi, str):
        return doi.lower().replace("https://doi.org/", "").strip()
    return ""


def _normalize_work_types(value: str | list[str] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        if value.strip().lower() in {"", "*", "all", "none"}:
            return []
        raw = re.split(r"[,;|]", value)
    else:
        raw = []
        for item in value:
            raw.extend(re.split(r"[,;|]", str(item)))
    return [item.strip().lower().replace("_", "-") for item in raw if item.strip()]


def _extract_landing_page_url(work: dict) -> str:
    """Return a usable article landing URL, preferring DOI resolution."""
    doi = _extract_doi(work)
    if doi:
        return f"https://doi.org/{doi}"

    loc = work.get("primary_location", {}) or {}
    landing = loc.get("landing_page_url", "")
    return landing if isinstance(landing, str) else ""


def _extract_journal(work: dict) -> str:
    loc = work.get("primary_location", {}) or {}
    source = loc.get("source", {}) or {}
    return source.get("display_name", "")


def _extract_abstract(work: dict) -> str:
    # OpenAlex returns inverted abstracts (list of {word, score})
    ai = work.get("abstract_inverted_index")
    if ai and isinstance(ai, dict):
        return _reconstruct_inverted_abstract(ai)
    return ""


def _reconstruct_inverted_abstract(ai: dict) -> str:
    """Reconstruct text from OpenAlex inverted abstract index."""
    max_pos = 0
    word_positions: dict[int, str] = {}
    for word, positions in ai.items():
        for pos in positions:
            word_positions[pos] = word
            if pos > max_pos:
                max_pos = pos
    words = [word_positions.get(i, "") for i in range(max_pos + 1)]
    return " ".join(words)


def _extract_authors(work: dict) -> str:
    authorships = work.get("authorships", []) or []
    names = []
    for a in authorships:
        author = a.get("author", {}) or {}
        name = author.get("display_name", "")
        if name:
            names.append(name)
    return "; ".join(names)


FIELD_MAP: dict[str, Any] = {
    "title": lambda w: w.get("title", ""),
    "doi": lambda w: _extract_doi(w),
    "publication_year": lambda w: str(w.get("publication_year", "")),
    "journal": lambda w: _extract_journal(w),
    "abstract": lambda w: _extract_abstract(w),
    "article_type": lambda w: w.get("type", ""),
    "cited_by_count": lambda w: str(w.get("cited_by_count", "")),
    "authors": lambda w: _extract_authors(w),
    "openalex_id": lambda w: w.get("id", ""),
    "landing_page_url": lambda w: _extract_landing_page_url(w),
    "discovery_source": lambda w: "openalex",
    "discovery_query": lambda w: "",
}


# HTTP helpers

def _build_url(query: str, year_from: int | None, year_to: int | None, page: int, per_page: int,
               api_key: str | None = None, mailto: str | None = None,
               work_types: str | list[str] | None = "article") -> str:
    params: dict[str, str] = {
        "search": query,
        "per-page": str(per_page),
        "page": str(page),
        "select": OPENALEX_SELECT_FIELDS,
    }
    filters = []
    types = _normalize_work_types(work_types)
    if types:
        filters.append(f"type:{'|'.join(types)}")
    if year_from:
        filters.append(f"from_publication_date:{year_from}-01-01")
    if year_to:
        filters.append(f"to_publication_date:{year_to}-12-31")
    if filters:
        params["filter"] = ",".join(filters)
    if api_key:
        params["api_key"] = api_key
    if mailto:
        params["mailto"] = mailto
    return f"{OPENALEX_BASE}?{urllib.parse.urlencode(params)}"


def _retry_after_seconds(exc: urllib.error.HTTPError, attempt: int) -> float:
    retry_after = exc.headers.get("Retry-After") if exc.headers else None
    if retry_after:
        try:
            return max(0.0, min(float(retry_after), 120.0))
        except ValueError:
            pass
    return float(2 ** attempt)


def _fetch_json(url: str) -> dict:
    """Fetch URL with retries and backoff.
    Distinguishes transient errors (429 rate-limit) from permanent ones (403 auth, 409 credit exhaustion).
    """
    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (403, 409):
                raise ProviderSearchError(
                    f"HTTP {e.code}: API key required, invalid, or credits exhausted. "
                    f"Configure --api-key if your OpenAlex access policy requires it.",
                    status="auth_error",
                    http_status=e.code,
                    transient=False,
                ) from e
            if e.code == 429:
                last_error = e
                wait = _retry_after_seconds(e, attempt)
                if attempt < MAX_RETRIES - 1:
                    print(f"  Rate limited (429). Retry {attempt + 1}/{MAX_RETRIES} after {wait}s", file=sys.stderr)
                    time.sleep(wait)
                continue
            last_error = e
        except (urllib.error.URLError, json.JSONDecodeError, OSError,
                http.client.IncompleteRead) as e:
            last_error = e
            if attempt < MAX_RETRIES - 1:
                wait = 2 ** attempt
                print(f"  Retry {attempt + 1}/{MAX_RETRIES} after {wait}s: {e}", file=sys.stderr)
                time.sleep(wait)
    if isinstance(last_error, urllib.error.HTTPError) and last_error.code == 429:
        raise ProviderSearchError(
            f"OpenAlex rate limit persisted after {MAX_RETRIES} attempts",
            status="rate_limited",
            retry_after_seconds=_retry_after_seconds(last_error, MAX_RETRIES - 1),
            http_status=429,
            transient=True,
        ) from last_error
    raise RuntimeError(f"Failed after {MAX_RETRIES} attempts: {last_error}")


# Core search

def search(query: str, year_from: int | None = None, year_to: int | None = None, max_results: int = 200,
           api_key: str | None = None, mailto: str | None = None,
           work_types: str | list[str] | None = "article") -> list[dict[str, str]]:
    """Run a single query against OpenAlex and return uniform-schema rows.

    Handles pagination automatically. Stops when max_results is reached
    or when no more results are available.
    """
    results: list[dict[str, str]] = []
    seen_dois: set[str] = set()
    page = 1
    total_hits: int | None = None
    mailto = mailto if mailto is not None else DEFAULT_MAILTO

    print(f"Searching OpenAlex: {query!r}", file=sys.stderr)
    if year_from or year_to:
        print(f"  Year filter: {year_from or '*'}..{year_to or '*'}", file=sys.stderr)

    try:
        while len(results) < max_results:
            url = _build_url(
                query,
                year_from,
                year_to,
                page,
                min(PER_PAGE, max_results - len(results)),
                api_key=api_key,
                mailto=mailto,
                work_types=work_types,
            )
            data = _fetch_json(url)

            if total_hits is None:
                meta = data.get("meta", {})
                total_hits = meta.get("count", 0)
                print(f"  Total hits: {total_hits}", file=sys.stderr)

            works = data.get("results", [])
            if not works:
                print(f"  No more results at page {page}.", file=sys.stderr)
                break

            new_count = 0
            for work in works:
                if len(results) >= max_results:
                    break
                row = {}
                for col, extractor in FIELD_MAP.items():
                    row[col] = extractor(work)
                doi = row["doi"]
                if doi and doi in seen_dois:
                    continue
                if doi:
                    seen_dois.add(doi)
                row["discovery_query"] = query
                results.append(row)
                new_count += 1

            print(f"  Page {page}: {new_count} new, {len(works)} total on page", file=sys.stderr)

            if len(results) >= max_results:
                break

            page += 1

            # Safety: don't paginate beyond reasonable limits
            if page > 50:
                print(f"  Stopping at page {page} (limit 50 pages).", file=sys.stderr)
                break
    except ProviderSearchError as e:
        if e.status == "rate_limited":
            status = "partial_rate_limited" if results else "rate_limited"
        elif results and e.status == "error":
            status = "partial_error"
        elif results and not str(e.status).startswith("partial"):
            status = f"partial_{e.status}"
        else:
            status = e.status
        message = f"OpenAlex search failed at page {page}: {e}"
        print(
            f"  ERROR: {message}. Partial rows={len(results)}.",
            file=sys.stderr,
        )
        raise ProviderSearchError(
            message,
            partial_results=results or e.partial_results,
            status=status,
            retry_after_seconds=e.retry_after_seconds,
            http_status=e.http_status,
            transient=e.transient,
        ) from e
    except Exception as e:
        status = "partial_error" if results else "error"
        message = f"OpenAlex search failed at page {page}: {e}"
        print(
            f"  ERROR: {message}. Partial rows={len(results)}.",
            file=sys.stderr,
        )
        raise ProviderSearchError(message, partial_results=results, status=status) from e

    print(f"  Collected: {len(results)} candidates.", file=sys.stderr)
    return results


# Batch search from file

def search_from_file(query_file: Path, year_from: int | None, year_to: int | None, max_results: int,
                     api_key: str | None = None, mailto: str | None = None,
                     work_types: str | list[str] | None = "article") -> list[dict[str, str]]:
    """Run multiple queries from a file, one per line."""
    queries = [line.strip() for line in query_file.read_text(encoding="utf-8").splitlines()
               if line.strip() and not line.strip().startswith("#")]
    all_results: list[dict[str, str]] = []
    seen_dois: set[str] = set()

    for query in queries:
        try:
            batch = search(query, year_from=year_from, year_to=year_to, max_results=max_results,
                           api_key=api_key, mailto=mailto, work_types=work_types)
        except ProviderSearchError as exc:
            for row in exc.partial_results:
                doi = row["doi"]
                if doi and doi in seen_dois:
                    continue
                if doi:
                    seen_dois.add(doi)
                all_results.append(row)
            raise ProviderSearchError(
                f"OpenAlex batch search failed for query {query!r}: {exc}",
                partial_results=all_results,
                status="partial_error" if all_results else exc.status,
            ) from exc
        for row in batch:
            doi = row["doi"]
            if doi and doi in seen_dois:
                continue
            if doi:
                seen_dois.add(doi)
            all_results.append(row)

    print(f"Total unique candidates across {len(queries)} queries: {len(all_results)}", file=sys.stderr)
    return all_results


# CSV output

def to_csv(results: list[dict[str, str]], output_path: Path) -> None:
    fieldnames = list(results[0].keys()) if results else OUTPUT_FIELDS
    write_csv_atomic(results, output_path, fieldnames=fieldnames)
    print(f"Wrote {len(results)} rows to {output_path}", file=sys.stderr)


# CLI

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Search OpenAlex for literature candidates."
    )
    parser.add_argument("--query", type=str, help="Single search query string")
    parser.add_argument("--query-file", type=Path, help="File with one query per line")
    parser.add_argument("--year-from", type=int, default=None, help="Minimum publication year")
    parser.add_argument("--year-to", type=int, default=None, help="Maximum publication year")
    parser.add_argument("--max-results", type=int, default=200, help="Max results per query (default: 200)")
    parser.add_argument("--output", type=Path, required=True, help="Output CSV path")
    parser.add_argument("--api-key", type=str, default=None,
                        help="OpenAlex API key, if required by your OpenAlex access policy")
    parser.add_argument("--mailto", type=str, default=DEFAULT_MAILTO,
                        help="Contact email for OpenAlex polite pool; also reads OPENALEX_MAILTO or LITMINER_CONTACT_EMAIL")
    parser.add_argument("--work-types", type=str, default="article",
                        help="OpenAlex work type filter (comma/pipe-separated); use 'all' to disable")
    args = parser.parse_args()

    if not args.query and not args.query_file:
        parser.error("Either --query or --query-file is required.")

    common = {"year_from": args.year_from, "year_to": args.year_to, "max_results": args.max_results,
              "api_key": args.api_key, "mailto": args.mailto, "work_types": args.work_types}
    try:
        if args.query_file:
            results = search_from_file(args.query_file, **common)
        else:
            results = search(args.query, **common)
    except ProviderSearchError as exc:
        if exc.partial_results:
            to_csv(exc.partial_results, args.output)
            print(f"Provider error after partial results: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    to_csv(results, args.output)
    print(f"Done: {len(results)} candidates -> {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
