#!/usr/bin/env python3
"""Search Europe PMC through its REST API and return uniform rows."""

from __future__ import annotations

import argparse
import html
import http.client
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from litminer.engine.common import normalize_doi, write_csv_atomic
from litminer.sources.api.errors import ProviderSearchError


EUROPE_PMC_BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
REQUEST_TIMEOUT = 30
MAX_RETRIES = 3
RESULTS_PER_PAGE = 100
USER_AGENT = "litminer/1.0"
HTML_TAG_RE = re.compile(r"<[^>]+>")

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
    "best_full_text_url",
    "pmid",
    "pmcid",
    "europe_pmc_id",
    "discovery_source",
    "discovery_query",
    "source_note",
]


def _clean_text(value: Any) -> str:
    text = "" if value is None else str(value)
    text = html.unescape(HTML_TAG_RE.sub("", text))
    return " ".join(text.split())


def _value(record: dict[str, Any], key: str) -> str:
    return _clean_text(record.get(key))


def _article_type(record: dict[str, Any]) -> str:
    value = record.get("pubType") or record.get("pubTypeList")
    if isinstance(value, dict):
        value = value.get("pubType")
    if isinstance(value, list):
        return ";".join(_clean_text(item) for item in value if _clean_text(item))
    return _clean_text(value)


def _best_full_text_url(record: dict[str, Any]) -> str:
    url_list = record.get("fullTextUrlList")
    urls = url_list.get("fullTextUrl") if isinstance(url_list, dict) else []
    if isinstance(urls, dict):
        urls = [urls]
    if not isinstance(urls, list):
        return ""
    for item in urls:
        if not isinstance(item, dict):
            continue
        if item.get("availabilityCode") == "OA" and item.get("url"):
            return str(item["url"])
    for item in urls:
        if isinstance(item, dict) and item.get("url"):
            return str(item["url"])
    return ""


def _landing_page(record: dict[str, Any]) -> str:
    source = _value(record, "source")
    record_id = _value(record, "id")
    if source and record_id:
        return f"https://europepmc.org/article/{urllib.parse.quote(source)}/{urllib.parse.quote(record_id)}"
    doi = normalize_doi(_value(record, "doi"))
    if doi:
        return f"https://doi.org/{doi}"
    return ""


def _year(record: dict[str, Any]) -> str:
    for key in ("pubYear", "yearOfPublication"):
        value = _value(record, key)
        if len(value) >= 4:
            return value[:4]
    for key in ("firstPublicationDate", "electronicPublicationDate", "journalInfo"):
        raw_value = record.get(key)
        if isinstance(raw_value, dict):
            raw_value = raw_value.get("printPublicationDate") or raw_value.get("dateOfPublication")
        value_text = _clean_text(raw_value)
        if len(value_text) >= 4:
            return value_text[:4]
    return ""


def record_to_row(record: dict[str, Any], source_query: str = "") -> dict[str, str]:
    source = _value(record, "source")
    record_id = _value(record, "id")
    pmid = _value(record, "pmid")
    if not pmid and source.upper() == "MED":
        pmid = record_id
    pmcid = _value(record, "pmcid")
    landing = _landing_page(record)
    return {
        "title": _value(record, "title"),
        "doi": normalize_doi(_value(record, "doi")),
        "publication_year": _year(record),
        "journal": _value(record, "journalTitle"),
        "abstract": _value(record, "abstractText"),
        "article_type": _article_type(record),
        "cited_by_count": _value(record, "citedByCount"),
        "authors": _value(record, "authorString").rstrip("."),
        "landing_page_url": landing,
        "url": landing,
        "best_full_text_url": _best_full_text_url(record),
        "pmid": pmid,
        "pmcid": pmcid,
        "europe_pmc_id": f"{source}:{record_id}" if source and record_id else record_id,
        "discovery_source": "europe_pmc",
        "discovery_query": source_query,
        "source_note": (
            f"source={source}; isOpenAccess={_value(record, 'isOpenAccess')}; "
            f"hasFullText={_value(record, 'hasFullText')}; inEPMC={_value(record, 'inEPMC')}"
        ),
    }


def _with_year_filter(query: str, year_from: int | None, year_to: int | None = None) -> str:
    if not year_from and not year_to:
        return query
    start = f"{year_from}-01-01" if year_from else "0000-01-01"
    end = f"{year_to}-12-31" if year_to else "9999-12-31"
    return f"({query}) AND FIRST_PDATE:[{start} TO {end}]"


def _build_url(query: str, year_from: int | None, year_to: int | None, page_size: int,
               cursor_mark: str) -> str:
    params = {
        "query": _with_year_filter(query, year_from, year_to),
        "format": "json",
        "resultType": "core",
        "pageSize": str(page_size),
        "cursorMark": cursor_mark,
    }
    return f"{EUROPE_PMC_BASE}?{urllib.parse.urlencode(params)}"


def _retry_after_seconds(exc: urllib.error.HTTPError, attempt: int) -> float:
    retry_after = exc.headers.get("Retry-After") if exc.headers else None
    if retry_after:
        try:
            return max(0.0, min(float(retry_after), 120.0))
        except ValueError:
            pass
    return float(2 ** attempt)


def _status_for_fetch_exception(exc: Exception | None) -> str:
    text = str(exc or "").lower()
    if isinstance(exc, urllib.error.HTTPError):
        return f"http_{exc.code}"
    if isinstance(exc, urllib.error.URLError) or any(
        marker in text for marker in ("ssl", "certificate", "cert", "dns", "name resolution", "network")
    ):
        return "network_error"
    if isinstance(exc, json.JSONDecodeError):
        return "response_parse_error"
    return "error"


def _fetch_json(url: str) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code == 429 and attempt < MAX_RETRIES - 1:
                wait = _retry_after_seconds(exc, attempt)
                print(
                    f"  Rate limited by Europe PMC (429). Retry {attempt + 1}/{MAX_RETRIES} after {wait:g}s",
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
        except (urllib.error.URLError, json.JSONDecodeError, OSError,
                http.client.IncompleteRead) as exc:
            last_error = exc
            if attempt < MAX_RETRIES - 1:
                wait = 2 ** attempt
                print(f"  Retry {attempt + 1}/{MAX_RETRIES} after {wait}s: {exc}", file=sys.stderr)
                time.sleep(wait)
    if isinstance(last_error, urllib.error.HTTPError) and last_error.code == 429:
        raise ProviderSearchError(
            f"Europe PMC rate limit persisted after {MAX_RETRIES} attempts",
            status="rate_limited",
            retry_after_seconds=_retry_after_seconds(last_error, MAX_RETRIES - 1),
            http_status=429,
            transient=True,
        ) from last_error
    status = _status_for_fetch_exception(last_error)
    raise ProviderSearchError(
        f"Europe PMC request failed after {MAX_RETRIES} attempts: {last_error}",
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
    """Search Europe PMC and return uniform-schema rows."""
    results: list[dict[str, str]] = []
    seen_keys: set[str] = set()
    cursor = "*"

    print(f"Searching Europe PMC: {query!r}", file=sys.stderr)
    try:
        while len(results) < max_results:
            page_size = min(RESULTS_PER_PAGE, max_results - len(results))
            data = _fetch_json(_build_url(query, year_from, year_to, page_size, cursor))
            records = data.get("resultList", {}).get("result", [])
            if not isinstance(records, list) or not records:
                break
            for record in records:
                row = record_to_row(record, source_query=query)
                key = row.get("doi") or row.get("europe_pmc_id") or row.get("title")
                if key and key in seen_keys:
                    continue
                if not _year_ok(row, year_from, year_to):
                    continue
                if key:
                    seen_keys.add(key)
                results.append(row)
                if len(results) >= max_results:
                    break
            next_cursor = str(data.get("nextCursorMark") or "")
            print(f"  Cursor page: {len(records)} records, {len(results)} collected", file=sys.stderr)
            if not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor
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
        message = f"Europe PMC search failed at cursor={cursor}: {exc}"
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
    parser = argparse.ArgumentParser(description="Search Europe PMC for literature candidates.")
    parser.add_argument("--query", required=True, help="Europe PMC search query")
    parser.add_argument("--year-from", type=int, default=None, help="Minimum publication year")
    parser.add_argument("--year-to", type=int, default=None, help="Maximum publication year")
    parser.add_argument("--max-results", type=int, default=100)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    to_csv(search(args.query, year_from=args.year_from, year_to=args.year_to, max_results=args.max_results), args.output)


if __name__ == "__main__":
    main()
