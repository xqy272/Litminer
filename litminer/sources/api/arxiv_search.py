#!/usr/bin/env python3
"""Search arXiv through the official Atom API and return uniform rows."""

from __future__ import annotations

import argparse
import http.client
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from litminer.engine.common import normalize_doi, write_csv_atomic
from litminer.sources.api.errors import ProviderSearchError


ARXIV_BASE = "https://export.arxiv.org/api/query"
REQUEST_TIMEOUT = 30
MAX_RETRIES = 3
RESULTS_PER_PAGE = 100
SLEEP_BETWEEN_REQUESTS = 3.0
USER_AGENT = "litminer/1.0"

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
}

OUTPUT_FIELDS = [
    "title",
    "doi",
    "publication_year",
    "journal",
    "abstract",
    "article_type",
    "cited_by_count",
    "authors",
    "landing_page_url",
    "url",
    "pdf_url",
    "arxiv_id",
    "arxiv_categories",
    "discovery_source",
    "discovery_query",
    "source_note",
]


def _clean_text(value: str | None) -> str:
    return " ".join((value or "").split())


def _text(entry: ET.Element, path: str) -> str:
    elem = entry.find(path, NS)
    return _clean_text(elem.text if elem is not None else "")


def _entry_id_to_arxiv_id(entry_id: str) -> str:
    if not entry_id:
        return ""
    return entry_id.rstrip("/").split("/")[-1]


def _entry_links(entry: ET.Element) -> tuple[str, str]:
    landing = ""
    pdf = ""
    for link in entry.findall("atom:link", NS):
        href = link.attrib.get("href", "")
        rel = link.attrib.get("rel", "")
        title = link.attrib.get("title", "")
        link_type = link.attrib.get("type", "")
        if rel == "alternate" and href:
            landing = href
        if title == "pdf" or link_type == "application/pdf":
            pdf = href
    return landing, pdf


def _entry_categories(entry: ET.Element) -> str:
    terms = [
        category.attrib.get("term", "")
        for category in entry.findall("atom:category", NS)
        if category.attrib.get("term")
    ]
    return ";".join(terms)


def _entry_authors(entry: ET.Element) -> str:
    names = []
    for author in entry.findall("atom:author", NS):
        name = _text(author, "atom:name")
        if name:
            names.append(name)
    return "; ".join(names)


def entry_to_row(entry: ET.Element, source_query: str = "") -> dict[str, str]:
    entry_id = _text(entry, "atom:id")
    landing, pdf = _entry_links(entry)
    published = _text(entry, "atom:published")
    updated = _text(entry, "atom:updated")
    doi = _text(entry, "arxiv:doi")
    journal_ref = _text(entry, "arxiv:journal_ref")
    categories = _entry_categories(entry)
    return {
        "title": _text(entry, "atom:title"),
        "doi": normalize_doi(doi),
        "publication_year": published[:4] if len(published) >= 4 else "",
        "journal": journal_ref,
        "abstract": _text(entry, "atom:summary"),
        "article_type": "preprint",
        "cited_by_count": "",
        "authors": _entry_authors(entry),
        "landing_page_url": landing or entry_id,
        "url": landing or entry_id,
        "pdf_url": pdf,
        "arxiv_id": _entry_id_to_arxiv_id(entry_id),
        "arxiv_categories": categories,
        "discovery_source": "arxiv",
        "discovery_query": source_query,
        "source_note": f"updated={updated}; categories={categories}",
    }


def _with_year_filter(query: str, year_from: int | None, year_to: int | None = None) -> str:
    if not year_from and not year_to:
        return query
    start = f"{year_from}01010000" if year_from else "000001010000"
    end = f"{year_to}12312359" if year_to else "999912312359"
    return f"({query}) AND submittedDate:[{start} TO {end}]"


def _build_url(query: str, year_from: int | None, year_to: int | None, start: int, max_results: int) -> str:
    params = {
        "search_query": _with_year_filter(query, year_from, year_to),
        "start": str(start),
        "max_results": str(max_results),
        "sortBy": "relevance",
        "sortOrder": "descending",
    }
    return f"{ARXIV_BASE}?{urllib.parse.urlencode(params)}"


def _retry_after_seconds(exc: urllib.error.HTTPError, attempt: int) -> float:
    retry_after = exc.headers.get("Retry-After") if exc.headers else None
    if retry_after:
        try:
            return max(0.0, min(float(retry_after), 120.0))
        except ValueError:
            pass
    return float(max(SLEEP_BETWEEN_REQUESTS, 2 ** attempt))


def _status_for_fetch_exception(exc: Exception | None) -> str:
    text = str(exc or "").lower()
    if isinstance(exc, urllib.error.HTTPError):
        return f"http_{exc.code}"
    if isinstance(exc, urllib.error.URLError) or any(
        marker in text for marker in ("ssl", "certificate", "cert", "dns", "name resolution", "network")
    ):
        return "network_error"
    if isinstance(exc, ET.ParseError):
        return "response_parse_error"
    return "error"


def _fetch_xml(url: str) -> ET.Element:
    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                return ET.fromstring(resp.read())
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code == 429 and attempt < MAX_RETRIES - 1:
                wait = _retry_after_seconds(exc, attempt)
                print(
                    f"  Rate limited by arXiv (429). Retry {attempt + 1}/{MAX_RETRIES} after {wait:g}s",
                    file=sys.stderr,
                )
                time.sleep(wait)
                continue
            if 500 <= exc.code < 600 and attempt < MAX_RETRIES - 1:
                wait = _retry_after_seconds(exc, attempt)
                print(f"  Retry {attempt + 1}/{MAX_RETRIES} after {wait:g}s: {exc}", file=sys.stderr)
                time.sleep(wait)
                continue
            break
        except (urllib.error.URLError, ET.ParseError, OSError,
                http.client.IncompleteRead) as exc:
            last_error = exc
            if attempt < MAX_RETRIES - 1:
                wait = 2 ** attempt
                print(f"  Retry {attempt + 1}/{MAX_RETRIES} after {wait}s: {exc}", file=sys.stderr)
                time.sleep(wait)
    if isinstance(last_error, urllib.error.HTTPError) and last_error.code == 429:
        raise ProviderSearchError(
            f"arXiv rate limit persisted after {MAX_RETRIES} attempts",
            status="rate_limited",
            retry_after_seconds=_retry_after_seconds(last_error, MAX_RETRIES - 1),
            http_status=429,
            transient=True,
        ) from last_error
    status = _status_for_fetch_exception(last_error)
    raise ProviderSearchError(
        f"arXiv request failed after {MAX_RETRIES} attempts: {last_error}",
        status=status,
        http_status=last_error.code if isinstance(last_error, urllib.error.HTTPError) else None,
        transient=status in {"network_error", "response_parse_error"} or status.startswith("http_5"),
    ) from last_error


def _year_ok(row: dict[str, str], year_from: int | None, year_to: int | None = None) -> bool:
    if not year_from and not year_to:
        return True
    try:
        year = int(row.get("publication_year", "0"))
    except ValueError:
        return True
    if year_from and year < year_from:
        return False
    if year_to and year > year_to:
        return False
    return True


def search(query: str, year_from: int | None = None,
           year_to: int | None = None,
           max_results: int = 100) -> list[dict[str, str]]:
    """Search arXiv and return uniform-schema rows.

    Pass advanced arXiv syntax directly, for example ``all:graphene AND cat:cs.LG``.
    """
    results: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    start = 0

    print(f"Searching arXiv: {query!r}", file=sys.stderr)
    try:
        while len(results) < max_results:
            page_size = min(RESULTS_PER_PAGE, max_results - len(results))
            root = _fetch_xml(_build_url(query, year_from, year_to, start, page_size))
            entries = root.findall("atom:entry", NS)
            if not entries:
                break
            for entry in entries:
                row = entry_to_row(entry, source_query=query)
                arxiv_id = row.get("arxiv_id", "")
                if arxiv_id and arxiv_id in seen_ids:
                    continue
                if not _year_ok(row, year_from, year_to):
                    continue
                if arxiv_id:
                    seen_ids.add(arxiv_id)
                results.append(row)
                if len(results) >= max_results:
                    break
            start += len(entries)
            print(f"  Start {start}: {len(entries)} entries, {len(results)} collected", file=sys.stderr)
            if len(entries) < page_size:
                break
            if len(results) < max_results:
                time.sleep(SLEEP_BETWEEN_REQUESTS)
    except Exception as exc:
        if isinstance(exc, ProviderSearchError):
            status = exc.status
            if results and not str(status).startswith("partial"):
                status = f"partial_{status}"
            retry_after = exc.retry_after_seconds
            http_status = exc.http_status
            transient = exc.transient
        else:
            status = "partial_error" if results else "error"
            retry_after = getattr(exc, "retry_after_seconds", None)
            http_status = None
            transient = None
        message = f"arXiv search failed at start={start}: {exc}"
        raise ProviderSearchError(
            message,
            partial_results=results,
            status=status,
            retry_after_seconds=retry_after,
            http_status=http_status,
            transient=transient,
        ) from exc

    print(f"  Collected: {len(results)} candidates.", file=sys.stderr)
    return results


def to_csv(results: list[dict[str, str]], output_path: Path) -> None:
    fieldnames = list(results[0].keys()) if results else OUTPUT_FIELDS
    write_csv_atomic(results, output_path, fieldnames=fieldnames)
    print(f"Wrote {len(results)} rows to {output_path}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description="Search arXiv for literature candidates.")
    parser.add_argument("--query", required=True, help="arXiv search query")
    parser.add_argument("--year-from", type=int, default=None, help="Minimum publication year")
    parser.add_argument("--year-to", type=int, default=None, help="Maximum publication year")
    parser.add_argument("--max-results", type=int, default=100)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    to_csv(search(args.query, year_from=args.year_from, year_to=args.year_to, max_results=args.max_results), args.output)


if __name__ == "__main__":
    main()
