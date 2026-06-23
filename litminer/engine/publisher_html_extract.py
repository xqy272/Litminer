#!/usr/bin/env python3
"""Extract structured metadata from publisher HTML meta tags.

This module extracts ``<meta name="citation_*">`` tags and equivalent
JSON-LD ``schema.org/ScholarlyArticle`` structures from publisher landing
pages. It is a **Hard Boundary internal completion** — the Hard Boundary
prohibits parsing PDFs/OCR/SI, but HTML meta extraction is allowed because
the tags are publisher-provided structured data for search engines.

Design constraints (see iteration_plan.md §3.4):

- Only extract ``<meta name="citation_*">`` and JSON-LD
  ``schema.org/ScholarlyArticle``. No JavaScript execution, no PDF
  parsing, no paywall content, no SI content.
- Missing meta tags are explicitly marked
  ``html_meta_status="not_present_on_page"`` — never silently blank.
  Downstream must distinguish "this paper has no keywords" from "the
  publisher page didn't expose meta tags".
- Field-level provenance: ``source="publisher_html_meta"``. No new
  ``publisher_visible`` Trust Tier (use ``field_provenance``, not a new
  row-level tier).
- Priority fields: ``citation_keywords``, ``citation_online_date``,
  ``citation_funder_name`` (API sources rarely provide these).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from litminer.engine.common import read_csv_rows, utc_now, write_csv_atomic
from litminer.engine.publisher_probe import request_url


META_TAG_RE = re.compile(
    r"""<meta\b[^>]*(?:name|property)=["']([^"']+)["'][^>]*content=["']([^"']*)["']""",
    re.I | re.S,
)
JSONLD_BLOCK_RE = re.compile(
    r"""<script\b[^>]*type=["']application/ld\+json["'][^>]*>(.*?)</script>""",
    re.I | re.S,
)

CITATION_FIELDS = {
    "citation_keywords": "citation_keywords",
    "citation_online_date": "citation_online_date",
    "citation_funder_name": "citation_funder_name",
    "citation_author": "citation_author",
    "citation_author_institution": "citation_author_institution",
    "citation_author_orcid": "citation_author_orcid",
    "citation_abstract": "citation_abstract",
    "citation_journal_title": "citation_journal_title",
    "citation_publication_date": "citation_publication_date",
    "citation_doi": "citation_doi",
    "citation_reference": "citation_reference",
}

OUTPUT_COLUMNS = [
    "html_meta_status",
    "html_meta_keywords",
    "html_meta_online_date",
    "html_meta_funder_name",
    "html_meta_authors",
    "html_meta_affiliations",
    "html_meta_orcids",
    "html_meta_abstract",
    "html_meta_journal_title",
    "html_meta_publication_date",
    "html_meta_doi",
    "html_meta_reference_count",
    "html_meta_extracted_at",
]


def _extract_meta_tags(body: str) -> dict[str, list[str]]:
    """Extract all citation_* meta tags from HTML body."""
    found: dict[str, list[str]] = {}
    for match in META_TAG_RE.finditer(body or ""):
        name = match.group(1).strip().lower()
        content = match.group(2).strip()
        if name in CITATION_FIELDS and content:
            key = CITATION_FIELDS[name]
            found.setdefault(key, []).append(content)
    return found


def _extract_jsonld(body: str) -> dict[str, list[str]]:
    """Extract schema.org/ScholarlyArticle fields from JSON-LD blocks."""
    found: dict[str, list[str]] = {}
    for match in JSONLD_BLOCK_RE.finditer(body or ""):
        raw = match.group(1).strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            schema_type = (item.get("@type") or "").lower()
            if schema_type not in {"scholarlyarticle", "article"}:
                continue
            _collect_jsonld_fields(item, found)
    return found


def _collect_jsonld_fields(item: dict[str, Any], found: dict[str, list[str]]) -> None:
    """Map schema.org properties to citation_* field names."""
    field_map = {
        "keywords": "citation_keywords",
        "datePublished": "citation_online_date",
        "funder": "citation_funder_name",
        "author": "citation_author",
        "abstract": "citation_abstract",
        "journalName": "citation_journal_title",
        "publicationDate": "citation_publication_date",
        "doi": "citation_doi",
    }
    for schema_prop, citation_key in field_map.items():
        value = item.get(schema_prop)
        if value is None:
            continue
        if isinstance(value, list):
            for v in value:
                text = _jsonld_value_to_text(v)
                if text:
                    found.setdefault(citation_key, []).append(text)
        else:
            text = _jsonld_value_to_text(value)
            if text:
                found.setdefault(citation_key, []).append(text)


def _jsonld_value_to_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        name = value.get("name") or value.get("@id") or ""
        return str(name).strip() if name else ""
    return str(value).strip()


def _merge_meta_and_jsonld(meta: dict[str, list[str]], jsonld: dict[str, list[str]]) -> dict[str, list[str]]:
    """Merge meta tags and JSON-LD, preferring meta tags."""
    merged = dict(jsonld)
    merged.update(meta)
    return merged


def extract_fields(body: str) -> dict[str, str]:
    """Extract structured fields from publisher HTML body.

    Returns a dict with html_meta_* keys. If no citation meta tags or
    JSON-LD are found, ``html_meta_status`` is ``not_present_on_page``.
    """
    meta = _extract_meta_tags(body)
    jsonld = _extract_jsonld(body)
    combined = _merge_meta_and_jsonld(meta, jsonld)

    if not combined:
        return {"html_meta_status": "not_present_on_page", "html_meta_extracted_at": utc_now()}

    def _join(key: str) -> str:
        return "; ".join(combined.get(key, []))

    def _count(key: str) -> str:
        return str(len(combined.get(key, [])))

    return {
        "html_meta_status": "extracted",
        "html_meta_extracted_at": utc_now(),
        "html_meta_keywords": _join("citation_keywords"),
        "html_meta_online_date": _join("citation_online_date"),
        "html_meta_funder_name": _join("citation_funder_name"),
        "html_meta_authors": _join("citation_author"),
        "html_meta_affiliations": _join("citation_author_institution"),
        "html_meta_orcids": _join("citation_author_orcid"),
        "html_meta_abstract": _join("citation_abstract"),
        "html_meta_journal_title": _join("citation_journal_title"),
        "html_meta_publication_date": _join("citation_publication_date"),
        "html_meta_doi": _join("citation_doi"),
        "html_meta_reference_count": _count("citation_reference"),
    }


def extract_row(row: dict[str, str]) -> dict[str, str]:
    """Extract HTML meta fields for a single probed row."""
    out = dict(row)
    start_url = (
        row.get("resolved_url")
        or row.get("publisher_probe_start_url")
        or row.get("publisher_url")
        or ""
    )
    if not start_url or row.get("access_status") in ("blocked", "blocked_url", "missing_url"):
        out["html_meta_status"] = "skipped_no_accessible_url"
        out["html_meta_extracted_at"] = utc_now()
        for col in OUTPUT_COLUMNS:
            if col not in out:
                out[col] = ""
        return out

    result = request_url(start_url)
    body = result.get("body") or ""
    fields = extract_fields(body)
    out.update(fields)
    return out


def extract_csv(input_path: Path, output_path: Path, limit: int | None = None) -> dict[str, int]:
    """Extract HTML meta fields from a probed publisher queue CSV."""
    fieldnames, rows = read_csv_rows(input_path)
    if not fieldnames:
        raise SystemExit("Input CSV has no header")

    for col in OUTPUT_COLUMNS:
        if col not in fieldnames:
            fieldnames.append(col)

    output_rows: list[dict[str, str]] = []
    counts: dict[str, int] = {}
    for idx, row in enumerate(rows):
        if limit is not None and idx >= limit:
            output_rows.append(row)
            continue
        extracted = extract_row(row)
        status = extracted.get("html_meta_status", "unknown")
        counts[status] = counts.get(status, 0) + 1
        output_rows.append(extracted)

    write_csv_atomic(output_rows, output_path, fieldnames=fieldnames)
    print(f"HTML meta extraction: {sum(counts.values())} rows -> {output_path}", file=sys.stderr)
    for key, value in sorted(counts.items()):
        print(f"  {key}: {value}", file=sys.stderr)
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract citation_* meta tags from probed publisher pages.")
    parser.add_argument("--input", type=Path, required=True, help="Probed publisher queue CSV")
    parser.add_argument("--output", type=Path, required=True, help="Output CSV with html_meta_* columns")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    extract_csv(args.input, args.output, limit=args.limit)


if __name__ == "__main__":
    main()
