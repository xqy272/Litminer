#!/usr/bin/env python3
"""Deduplicate literature candidate CSV files by DOI, then title.
Moved from scripts/dedupe_papers.py — this is now the canonical location.

Usage:
    python engine/dedupe_papers.py candidates.csv deduped.csv
    python engine/dedupe_papers.py candidates.csv deduped.csv --doi-field doi --title-field title
"""

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path


DOI_PREFIX_RE = re.compile(r"^(https?://(dx\.)?doi\.org/|doi:\s*)", re.I)
NON_WORD_RE = re.compile(r"[^a-z0-9]+")

MERGED_LIST_COLUMNS = {
    "discovery_source": "merged_discovery_sources",
    "discovery_provider": "merged_discovery_providers",
    "discovery_query": "merged_discovery_queries",
    "source_trace": "merged_source_traces",
    "source_file": "merged_source_files",
}

ALTERNATE_VALUE_COLUMNS = {
    "title": "alternate_titles",
    "abstract": "alternate_abstracts",
    "journal": "alternate_journals",
    "publication_year": "alternate_years",
    "landing_page_url": "alternate_landing_page_urls",
    "url": "alternate_urls",
    "pdf_url": "alternate_pdf_urls",
    "best_full_text_url": "alternate_full_text_urls",
    "authors": "alternate_authors",
}


def normalize_doi(value: str) -> str:
    value = (value or "").strip().lower()
    value = DOI_PREFIX_RE.sub("", value)
    return value.strip().rstrip(".")


def normalize_title(value: str) -> str:
    value = (value or "").strip().lower()
    value = NON_WORD_RE.sub(" ", value)
    return " ".join(value.split())


def cell_text(value: object) -> str:
    """Normalize csv cells, including DictReader overflow lists, to text."""
    if value is None:
        return ""
    if isinstance(value, list):
        return " ".join(str(item) for item in value if item is not None)
    return str(value)


def row_key(row: dict[str, str], doi_field: str, title_field: str) -> tuple[str, str]:
    doi = normalize_doi(cell_text(row.get(doi_field, "")))
    if doi:
        return ("doi", doi)
    return ("title", normalize_title(cell_text(row.get(title_field, ""))))


def row_quality(row: dict[str, str]) -> int:
    score = 0
    for key, value in row.items():
        text = cell_text(value).strip()
        if text:
            score += 1
        key_text = cell_text(key).lower()
        if key_text in {"abstract", "summary"} and len(text) > 200:
            score += 3
    return score


def append_unique(values: list[str], value: object) -> None:
    text = cell_text(value).strip()
    if text and text not in values:
        values.append(text)


def merge_group(rows: list[dict[str, str]]) -> dict[str, str]:
    best = rows[0]
    for row in rows[1:]:
        if row_quality(row) > row_quality(best):
            best = row
    base = dict(best)

    merged_lists: dict[str, list[str]] = {column: [] for column in MERGED_LIST_COLUMNS.values()}
    alternates: dict[str, list[str]] = {column: [] for column in ALTERNATE_VALUE_COLUMNS.values()}

    for row in rows:
        for source_field, merged_field in MERGED_LIST_COLUMNS.items():
            append_unique(merged_lists[merged_field], row.get(source_field, ""))

        for field, value in row.items():
            if field is None:
                continue
            text = cell_text(value).strip()
            if not text:
                continue
            if not cell_text(base.get(field, "")).strip():
                base[field] = text
                continue
            base_value = cell_text(base.get(field, "")).strip()
            if field in ALTERNATE_VALUE_COLUMNS and text != base_value:
                append_unique(alternates[ALTERNATE_VALUE_COLUMNS[field]], text)

    for field, values in merged_lists.items():
        base[field] = "; ".join(values)
    for field, values in alternates.items():
        base[field] = " || ".join(values)
    return base


def dedupe(input_path: Path, output_path: Path, doi_field: str, title_field: str) -> None:
    with input_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise SystemExit("Input CSV has no header")
        fieldnames = list(reader.fieldnames)
        rows = list(reader)

    if "duplicate_count" not in fieldnames:
        fieldnames.append("duplicate_count")
    for col in [*MERGED_LIST_COLUMNS.values(), *ALTERNATE_VALUE_COLUMNS.values()]:
        if col not in fieldnames:
            fieldnames.append(col)

    grouped: dict[tuple[str, str], list[dict[str, str]]] = {}

    for index, row in enumerate(rows):
        key = row_key(row, doi_field, title_field)
        if not key[1]:
            key = ("row", str(index))
        grouped.setdefault(key, []).append(row)

    output_rows = []
    for _key, group_rows in grouped.items():
        row = merge_group(group_rows)
        row["duplicate_count"] = str(len(group_rows))
        output_rows.append(row)

    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(output_rows)

    import sys as _sys
    print(f"Deduplication: {len(rows)} -> {len(output_rows)} (removed {len(rows) - len(output_rows)} duplicates)", file=_sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description="Deduplicate paper candidates by DOI/title.")
    parser.add_argument("input_csv", type=Path)
    parser.add_argument("output_csv", type=Path)
    parser.add_argument("--doi-field", default="doi")
    parser.add_argument("--title-field", default="title")
    args = parser.parse_args()
    dedupe(args.input_csv, args.output_csv, args.doi_field, args.title_field)


if __name__ == "__main__":
    main()
