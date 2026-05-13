#!/usr/bin/env python3
"""Search Semantic Scholar for candidate papers and output uniform CSV.

Usage:
    python semantic_scholar_search.py --query "your literature query" --year-from 2020 --output candidates.csv
    python semantic_scholar_search.py --query "..." --citation-expand "seed_doi" --output expanded.csv

Handles:
- Graph API search with pagination
- Citation graph expansion (forward + backward)
- Rate limiting with backoff
- Field mapping to uniform extraction schema
"""

from __future__ import annotations

import argparse
import csv
import http.client
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# ── Configuration ──────────────────────────────────────────────────────────

S2_BASE = "https://api.semanticscholar.org/graph/v1"
REQUEST_TIMEOUT = 30
MAX_RETRIES = 3
RESULTS_PER_QUERY = 100

# Fields to request from S2 API
S2_FIELDS = (
    "title,year,venue,publicationTypes,externalIds,abstract,"
    "citationCount,url,authors,publicationDate,journal"
)
OUTPUT_FIELDS = [
    "title",
    "doi",
    "publication_year",
    "journal",
    "abstract",
    "article_type",
    "cited_by_count",
    "authors",
    "s2_id",
    "url",
    "discovery_source",
    "discovery_query",
    "source_note",
]


class ProviderSearchError(RuntimeError):
    """Raised when a provider query fails, preserving any rows already fetched."""

    def __init__(
        self,
        message: str,
        partial_results: list[dict[str, str]] | None = None,
        status: str = "error",
    ) -> None:
        super().__init__(message)
        self.partial_results = partial_results or []
        self.status = status


def _build_headers() -> dict[str, str]:
    return {"Accept": "application/json"}


def _fetch_json(url: str) -> dict:
    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            req = urllib.request.Request(url, headers=_build_headers())
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, json.JSONDecodeError, OSError,
                http.client.IncompleteRead) as e:
            last_error = e
            if attempt < MAX_RETRIES - 1:
                wait = 2 ** attempt
                print(f"  Retry {attempt + 1}/{MAX_RETRIES} after {wait}s: {e}", file=sys.stderr)
                time.sleep(wait)
    raise RuntimeError(f"Failed after {MAX_RETRIES} attempts: {last_error}")


# ── Field mapping ──────────────────────────────────────────────────────────

def _extract_doi(paper: dict) -> str:
    ext = paper.get("externalIds", {}) or {}
    doi = ext.get("DOI", "")
    if doi and isinstance(doi, str):
        return doi.lower().strip()
    return ""


def _extract_journal(paper: dict) -> str:
    venue = paper.get("venue", "") or ""
    if venue and isinstance(venue, str) and venue.strip():
        return venue.strip()
    journal = paper.get("journal", {}) or {}
    name = journal.get("name", "")
    return name.strip() if name else ""


def _extract_authors(paper: dict) -> str:
    authors = paper.get("authors", []) or []
    return "; ".join(a.get("name", "") for a in authors if a.get("name"))


def _extract_year(paper: dict) -> str:
    year = paper.get("year")
    if year is not None:
        return str(year)
    pub_date = paper.get("publicationDate", "")
    if pub_date and isinstance(pub_date, str) and len(pub_date) >= 4:
        return pub_date[:4]
    return ""


def _paper_to_row(paper: dict, source_query: str = "", source_note: str = "") -> dict[str, str]:
    pub_types = paper.get("publicationTypes", []) or []
    return {
        "title": paper.get("title", "").strip() if paper.get("title") else "",
        "doi": _extract_doi(paper),
        "publication_year": _extract_year(paper),
        "journal": _extract_journal(paper),
        "abstract": paper.get("abstract", "") or "",
        "article_type": ";".join(pub_types) if pub_types else "",
        "cited_by_count": str(paper.get("citationCount", "")),
        "authors": _extract_authors(paper),
        "s2_id": paper.get("paperId", ""),
        "url": paper.get("url", ""),
        "discovery_source": "semantic_scholar",
        "discovery_query": source_query,
        "source_note": source_note,
    }


# ── Search ─────────────────────────────────────────────────────────────────

def search(
    query: str,
    year_from: int | None = None,
    max_results: int = 200,
) -> list[dict[str, str]]:
    """Search Semantic Scholar and return uniform-schema rows."""
    results: list[dict[str, str]] = []
    seen_dois: set[str] = set()
    offset = 0
    total_hits: int | None = None

    print(f"Searching Semantic Scholar: {query!r}", file=sys.stderr)

    try:
        while len(results) < max_results:
            params: dict[str, str] = {
                "query": query,
                "limit": str(min(RESULTS_PER_QUERY, max_results - len(results))),
                "offset": str(offset),
                "fields": S2_FIELDS,
            }
            if year_from:
                params["year"] = f"{year_from}-"

            url = f"{S2_BASE}/paper/search?{urllib.parse.urlencode(params)}"
            data = _fetch_json(url)

            if total_hits is None:
                total_hits = data.get("total", 0)
                print(f"  Total hits: {total_hits}", file=sys.stderr)

            papers = data.get("data", [])
            if not papers:
                break

            for paper in papers:
                row = _paper_to_row(paper, source_query=query)
                doi = row["doi"]
                if doi and doi in seen_dois:
                    continue
                if doi:
                    seen_dois.add(doi)
                results.append(row)

            offset += len(papers)
            print(f"  Offset {offset}: {len(papers)} papers, {len(results)} collected", file=sys.stderr)

            if len(papers) < RESULTS_PER_QUERY:
                break
    except Exception as e:
        status = "partial_error" if results else "error"
        message = f"Semantic Scholar search failed at offset {offset}: {e}"
        print(
            f"  ERROR: {message}. Partial rows={len(results)}.",
            file=sys.stderr,
        )
        raise ProviderSearchError(message, partial_results=results, status=status) from e

    print(f"  Collected: {len(results)} candidates.", file=sys.stderr)
    return results


# ── Citation expansion ─────────────────────────────────────────────────────

def get_citations(doi: str, max_results: int = 100) -> list[dict[str, str]]:
    """Get papers that cite the given DOI (forward search)."""
    # First resolve DOI to S2 paper ID
    resolve_url = f"{S2_BASE}/paper/DOI:{urllib.parse.quote(doi)}?fields=paperId"
    try:
        data = _fetch_json(resolve_url)
    except RuntimeError:
        print(f"  Could not resolve DOI {doi} in Semantic Scholar.", file=sys.stderr)
        return []

    paper_id = data.get("paperId", "")
    if not paper_id:
        return []

    # Get citations
    citations_url = (
        f"{S2_BASE}/paper/{paper_id}/citations"
        f"?fields={S2_FIELDS}&limit={min(max_results, 500)}"
    )
    data = _fetch_json(citations_url)
    citing_papers = data.get("data", [])

    results = []
    for entry in citing_papers:
        paper = entry.get("citingPaper", {})
        if paper:
            row = _paper_to_row(paper, source_note=f"cites {doi}")
            row["discovery_query"] = f"citation_of:{doi}"
            results.append(row)

    print(f"  Citation expansion for {doi}: {len(results)} citing papers.", file=sys.stderr)
    return results


def get_references(doi: str, max_results: int = 100) -> list[dict[str, str]]:
    """Get papers referenced by the given DOI (backward search)."""
    resolve_url = f"{S2_BASE}/paper/DOI:{urllib.parse.quote(doi)}?fields=paperId"
    try:
        data = _fetch_json(resolve_url)
    except RuntimeError:
        print(f"  Could not resolve DOI {doi} in Semantic Scholar.", file=sys.stderr)
        return []

    paper_id = data.get("paperId", "")
    if not paper_id:
        return []

    refs_url = (
        f"{S2_BASE}/paper/{paper_id}/references"
        f"?fields={S2_FIELDS}&limit={min(max_results, 500)}"
    )
    data = _fetch_json(refs_url)
    ref_papers = data.get("data", [])

    results = []
    for entry in ref_papers:
        paper = entry.get("citedPaper", {})
        if paper:
            row = _paper_to_row(paper, source_note=f"cited by {doi}")
            row["discovery_query"] = f"reference_of:{doi}"
            results.append(row)

    print(f"  Reference expansion for {doi}: {len(results)} referenced papers.", file=sys.stderr)
    return results


# ── CSV output ─────────────────────────────────────────────────────────────

def to_csv(results: list[dict[str, str]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(results[0].keys()) if results else OUTPUT_FIELDS
    with output_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(results)
    print(f"Wrote {len(results)} rows to {output_path}", file=sys.stderr)


# ── CLI ────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Search Semantic Scholar for literature candidates."
    )
    parser.add_argument("--query", type=str, help="Search query string")
    parser.add_argument("--year-from", type=int, default=None, help="Minimum publication year")
    parser.add_argument("--max-results", type=int, default=200, help="Max results (default: 200)")
    parser.add_argument("--citation-expand", type=str, default=None,
                        help="DOI of seed paper for forward citation expansion")
    parser.add_argument("--reference-expand", type=str, default=None,
                        help="DOI of seed paper for backward reference expansion")
    parser.add_argument("--output", type=Path, required=True, help="Output CSV path")
    args = parser.parse_args()

    results: list[dict[str, str]] = []

    try:
        if args.query:
            results.extend(search(args.query, args.year_from, args.max_results))

        if args.citation_expand:
            results.extend(get_citations(args.citation_expand, args.max_results))

        if args.reference_expand:
            results.extend(get_references(args.reference_expand, args.max_results))
    except ProviderSearchError as exc:
        if exc.partial_results:
            to_csv(exc.partial_results, args.output)
            print(f"Provider error after partial results: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    if not results and not args.query and not args.citation_expand and not args.reference_expand:
        parser.error("At least one of --query, --citation-expand, --reference-expand is required.")

    to_csv(results, args.output)
    print(f"Done: {len(results)} candidates -> {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
