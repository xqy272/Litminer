#!/usr/bin/env python3
"""Normalize WebSearch results into unverified Litminer candidate rows.

WebSearch is a lead generator, not an authority. Rows produced here are marked
as requiring Crossref and publisher-page verification before promotion.
"""

from __future__ import annotations

import argparse
import csv
import re
from datetime import datetime, timezone
from pathlib import Path

from litminer.engine.common import normalize_doi, write_csv_atomic


DOI_RE = re.compile(r"\b10\.\d{4,9}/[^\s\"'<>]+", re.I)
YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")

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
    "discovery_source",
    "discovery_provider",
    "discovery_query",
    "discovery_query_id",
    "discovery_rank",
    "source_trace",
    "retrieved_at",
    "source_note",
    "websearch_url",
    "websearch_snippet",
    "websearch_status",
    "websearch_verification_needed",
]

TITLE_FIELDS = ["title", "result_title", "name", "paper_title"]
URL_FIELDS = ["url", "link", "result_url", "href", "websearch_url"]
SNIPPET_FIELDS = ["snippet", "description", "summary", "abstract", "text"]
QUERY_FIELDS = ["query", "search_query", "discovery_query", "websearch_query"]
JOURNAL_FIELDS = ["journal", "venue", "source", "container"]
YEAR_FIELDS = ["publication_year", "year", "date"]
DOI_FIELDS = ["doi", "DOI"]


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def first_value(row: dict[str, str], fields: list[str]) -> str:
    for field in fields:
        value = (row.get(field) or "").strip()
        if value:
            return value
    return ""


def clean_doi(value: str) -> str:
    return normalize_doi(value)


def extract_doi(*values: str) -> str:
    for value in values:
        explicit = clean_doi(value)
        if explicit.startswith("10."):
            return explicit
        match = DOI_RE.search(value or "")
        if match:
            return clean_doi(match.group(0))
    return ""


def extract_year(*values: str) -> str:
    for value in values:
        match = YEAR_RE.search(value or "")
        if match:
            return match.group(0)
    return ""


def fieldnames_for(rows: list[dict[str, str]]) -> list[str]:
    fields = list(OUTPUT_FIELDS)
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    return fields


def normalize_row(row: dict[str, str], index: int,
                  default_query: str, retrieved_at: str) -> dict[str, str]:
    title = first_value(row, TITLE_FIELDS)
    url = first_value(row, URL_FIELDS)
    snippet = first_value(row, SNIPPET_FIELDS)
    query = first_value(row, QUERY_FIELDS) or default_query
    doi = extract_doi(first_value(row, DOI_FIELDS), title, url, snippet)
    year = first_value(row, YEAR_FIELDS) or extract_year(title, snippet, url)
    journal = first_value(row, JOURNAL_FIELDS)
    query_id = "webq001" if query else ""
    source_trace = f"websearch:{query_id}:rank{index}:doi={doi or 'none'}:url={url[:80]}"

    return {
        "title": title,
        "doi": doi,
        "publication_year": year,
        "journal": journal,
        "abstract": snippet,
        "article_type": "",
        "cited_by_count": "",
        "authors": "",
        "landing_page_url": url,
        "url": url,
        "discovery_source": "websearch",
        "discovery_provider": "websearch",
        "discovery_query": query,
        "discovery_query_id": query_id,
        "discovery_rank": str(index),
        "source_trace": source_trace,
        "retrieved_at": retrieved_at,
        "source_note": "websearch_lead_unverified",
        "websearch_url": url,
        "websearch_snippet": snippet,
        "websearch_status": "lead_unverified",
        "websearch_verification_needed": "crossref; publisher_page",
    }


def import_websearch(input_csv: Path, output_csv: Path,
                     default_query: str = "") -> dict[str, int]:
    with input_csv.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise SystemExit("Input CSV has no header")
        source_rows = list(reader)

    retrieved_at = utc_now()
    rows = [
        normalize_row(row, index, default_query, retrieved_at)
        for index, row in enumerate(source_rows, start=1)
    ]

    write_csv_atomic(rows, output_csv, fieldnames=fieldnames_for(rows))

    with_doi = sum(1 for row in rows if row.get("doi"))
    return {"rows": len(rows), "with_doi": with_doi, "without_doi": len(rows) - with_doi}


def main() -> None:
    parser = argparse.ArgumentParser(description="Normalize WebSearch leads into candidate rows.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--query", default="", help="Default query if input has no query column")
    args = parser.parse_args()
    counts = import_websearch(args.input, args.output, default_query=args.query)
    print(
        "WebSearch import: "
        f"{counts['rows']} rows, with DOI={counts['with_doi']}, "
        f"without DOI={counts['without_doi']} -> {args.output}"
    )


if __name__ == "__main__":
    main()
