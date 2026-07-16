"""Canonical bibliography projection with direct field provenance."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from litminer.engine.common import read_csv_rows, write_csv_atomic, write_text_atomic
from litminer.runtime.state_store import StateStore


CANONICAL_NAME = "canonical_papers.csv"
PROVENANCE_NAME = "canonical_provenance.json"
TRUSTED_CROSSREF = {"verified", "title_recovered"}

CANONICAL_FIELDS = [
    "paper_id", "entry_type", "title", "authors", "publication_year",
    "journal", "doi", "url", "volume", "issue", "pages", "publisher",
    "abstract", "bibliographic_status", "trusted_bibliography",
    "retraction_status", "export_eligible", "triage_priority",
    "scientific_review_needed", "workflow_status", "discovery_sources",
    "field_provenance_json",
]


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalize_doi(value: Any) -> str:
    text = _clean(value).lower()
    text = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", text)
    return text.removeprefix("doi:").strip()


def _paper_id(row: dict[str, str]) -> str:
    doi = normalize_doi(row.get("crossref_doi") or row.get("doi"))
    if doi:
        return f"doi:{doi}"
    title = _clean(row.get("crossref_title") or row.get("title")).lower()
    year = _clean(row.get("crossref_year") or row.get("publication_year") or row.get("year"))
    authors = _clean(row.get("crossref_authors") or row.get("authors") or row.get("author")).lower()
    digest = hashlib.sha256(f"{title}|{year}|{authors}".encode("utf-8")).hexdigest()[:24]
    return f"candidate:{digest}"


def _entry_type(value: str) -> str:
    normalized = _clean(value).lower()
    if normalized in {"journal-article", "article", "research-article"}:
        return "article"
    if "proceedings" in normalized or "conference" in normalized:
        return "conference"
    if "preprint" in normalized or normalized in {"posted-content", "arxiv"}:
        return "preprint"
    if normalized in {"book", "monograph"}:
        return "book"
    if "chapter" in normalized:
        return "book_chapter"
    return "generic"


def _choose(
    row: dict[str, str],
    field_name: str,
    candidates: list[tuple[str, str, str, str]],
) -> tuple[str, dict[str, str], list[dict[str, Any]]]:
    all_values: list[dict[str, Any]] = []
    for source, row_field, trust, reason in candidates:
        value = _clean(row.get(row_field))
        if not value:
            continue
        item = {
            "field_name": field_name,
            "field_value": value,
            "source": source,
            "source_field": row_field,
            "trust_class": trust,
            "selected": False,
            "reason": reason,
        }
        all_values.append(item)
    if not all_values:
        return "", {"source": "", "trust_class": "missing", "reason": "no candidate value"}, []
    all_values[0]["selected"] = True
    selected = all_values[0]
    provenance = {
        "source": str(selected["source"]),
        "source_field": str(selected["source_field"]),
        "trust_class": str(selected["trust_class"]),
        "reason": str(selected["reason"]),
    }
    return str(selected["field_value"]), provenance, all_values


def canonicalize_row(row: dict[str, str]) -> tuple[dict[str, str], dict[str, Any], list[dict[str, Any]]]:
    crossref_status = _clean(row.get("crossref_status"))
    trusted = crossref_status in TRUSTED_CROSSREF
    provenance: dict[str, Any] = {}
    field_values: list[dict[str, Any]] = []

    policies = {
        "title": [
            ("crossref", "crossref_title", "verified", "Crossref trusted title"),
            ("publisher_html_meta", "citation_title", "publisher", "Publisher citation meta title"),
            ("discovery", "title", "discovery", "Discovery/import fallback"),
        ] if trusted else [
            ("publisher_html_meta", "citation_title", "publisher", "Publisher citation meta title"),
            ("discovery", "title", "discovery", "Discovery/import value"),
            ("crossref_untrusted", "crossref_title", "unverified", "Untrusted Crossref fallback"),
        ],
        "authors": [
            ("crossref", "crossref_authors", "verified", "Crossref structured authors"),
            ("publisher_html_meta", "citation_authors", "publisher", "Publisher citation meta authors"),
            ("discovery", "authors", "discovery", "Discovery authors"),
            ("discovery", "author", "discovery", "Discovery author fallback"),
        ] if trusted else [
            ("publisher_html_meta", "citation_authors", "publisher", "Publisher citation meta authors"),
            ("discovery", "authors", "discovery", "Discovery authors"),
            ("discovery", "author", "discovery", "Discovery author fallback"),
        ],
        "publication_year": [
            ("crossref", "crossref_year", "verified", "Crossref publication year"),
            ("publisher_html_meta", "citation_online_date", "publisher", "Publisher online date"),
            ("discovery", "publication_year", "discovery", "Discovery year"),
            ("discovery", "year", "discovery", "Discovery year fallback"),
        ] if trusted else [
            ("publisher_html_meta", "citation_online_date", "publisher", "Publisher online date"),
            ("discovery", "publication_year", "discovery", "Discovery year"),
            ("discovery", "year", "discovery", "Discovery year fallback"),
        ],
        "journal": [
            ("crossref", "crossref_container", "verified", "Crossref container"),
            ("publisher_html_meta", "citation_journal_title", "publisher", "Publisher citation meta journal"),
            ("discovery", "journal", "discovery", "Discovery journal"),
        ] if trusted else [
            ("publisher_html_meta", "citation_journal_title", "publisher", "Publisher citation meta journal"),
            ("discovery", "journal", "discovery", "Discovery journal"),
        ],
        "publisher": [
            ("crossref", "crossref_publisher", "verified", "Crossref publisher"),
            ("discovery", "publisher", "discovery", "Discovery publisher"),
        ],
        "volume": [("crossref", "crossref_volume", "verified", "Crossref volume"), ("discovery", "volume", "discovery", "Discovery volume")],
        "issue": [("crossref", "crossref_issue", "verified", "Crossref issue"), ("discovery", "issue", "discovery", "Discovery issue")],
        "pages": [("crossref", "crossref_pages", "verified", "Crossref page range"), ("discovery", "pages", "discovery", "Discovery pages")],
        "abstract": [
            ("publisher_html_meta", "citation_abstract", "publisher", "Publisher citation meta abstract"),
            ("crossref", "crossref_abstract", "verified", "Crossref abstract"),
            ("discovery", "abstract", "discovery", "Discovery abstract"),
        ],
    }

    values: dict[str, str] = {}
    for field_name, candidates in policies.items():
        value, field_provenance, candidates_used = _choose(row, field_name, candidates)
        if field_name == "publication_year" and len(value) >= 4:
            year_match = re.search(r"\b(18|19|20|21)\d{2}\b", value)
            value = year_match.group(0) if year_match else value
        values[field_name] = value
        provenance[field_name] = field_provenance
        field_values.extend(candidates_used)

    doi = normalize_doi(row.get("crossref_doi") if trusted else row.get("doi") or row.get("crossref_doi"))
    if not doi:
        doi = normalize_doi(row.get("doi"))
    doi_source = "crossref" if trusted and row.get("crossref_doi") else "discovery"
    provenance["doi"] = {
        "source": doi_source if doi else "",
        "source_field": "crossref_doi" if doi_source == "crossref" else "doi",
        "trust_class": "verified" if doi_source == "crossref" else "discovery",
        "reason": "Crossref normalized DOI" if doi_source == "crossref" else "Discovery/import DOI",
    }
    if doi:
        field_values.append({
            "field_name": "doi", "field_value": doi, "source": doi_source,
            "trust_class": provenance["doi"]["trust_class"], "selected": True,
            "reason": provenance["doi"]["reason"],
        })

    url = f"https://doi.org/{doi}" if doi else _clean(
        row.get("publisher_url") or row.get("landing_page_url") or row.get("best_oa_landing_url") or row.get("url")
    )
    provenance["url"] = {
        "source": "doi_resolver" if doi else "discovery_or_access",
        "trust_class": "verified_pointer" if doi and trusted else "queue_pointer",
        "reason": "Canonical DOI resolver" if doi else "Best available page pointer",
    }
    bibliographic_status = _clean(row.get("bibliographic_status")) or (
        "verified" if trusted else crossref_status or "not_checked"
    )
    retraction_status = _clean(row.get("retraction_status")) or "unknown"
    eligible = trusted and retraction_status.lower() != "retracted" and bool(values["title"])
    source_value = _clean(row.get("merged_discovery_sources") or row.get("discovery_provider") or row.get("discovery_source"))
    canonical = {
        "paper_id": _paper_id(row),
        "entry_type": _entry_type(row.get("crossref_type") or row.get("article_type") or ""),
        "title": values["title"],
        "authors": values["authors"],
        "publication_year": values["publication_year"],
        "journal": values["journal"],
        "doi": doi,
        "url": url,
        "volume": values["volume"],
        "issue": values["issue"],
        "pages": values["pages"],
        "publisher": values["publisher"],
        "abstract": values["abstract"],
        "bibliographic_status": bibliographic_status,
        "trusted_bibliography": str(trusted).lower(),
        "retraction_status": retraction_status,
        "export_eligible": str(eligible).lower(),
        "triage_priority": _clean(row.get("triage_priority")),
        "scientific_review_needed": _clean(row.get("scientific_review_needed") or row.get("llm_review_needed")),
        "workflow_status": _clean(row.get("workflow_status")),
        "discovery_sources": source_value,
        "field_provenance_json": json.dumps(provenance, ensure_ascii=False, sort_keys=True),
    }
    return canonical, provenance, field_values


def build_canonical_artifacts(
    input_csv: Path,
    output_dir: Path,
    *,
    state_store: StateStore | None = None,
) -> tuple[Path, Path, dict[str, int]]:
    fields, rows = read_csv_rows(input_csv) if input_csv.exists() else ([], [])
    del fields
    canonical_rows: list[dict[str, str]] = []
    provenance_records: list[dict[str, Any]] = []
    trusted = 0
    eligible = 0
    for row in rows:
        canonical, provenance, field_values = canonicalize_row(row)
        canonical_rows.append(canonical)
        provenance_records.append({"paper_id": canonical["paper_id"], "fields": provenance})
        if canonical["trusted_bibliography"] == "true":
            trusted += 1
        if canonical["export_eligible"] == "true":
            eligible += 1
        if state_store is not None:
            identifiers = []
            if canonical["doi"]:
                identifiers.append(("doi", canonical["doi"], provenance["doi"].get("source", "")))
            for field_name in ("pmid", "pmcid", "arxiv_id"):
                value = _clean(row.get(field_name))
                if value:
                    identifiers.append((field_name, value, "discovery"))
            state_store.upsert_canonical_paper(
                canonical,
                identifiers=identifiers,
                field_values=field_values,
            )

    canonical_path = output_dir / CANONICAL_NAME
    provenance_path = output_dir / PROVENANCE_NAME
    write_csv_atomic(canonical_rows, canonical_path, fallback_fields=CANONICAL_FIELDS)
    write_text_atomic(provenance_path, json.dumps({
        "schema_version": 1,
        "input_csv": str(input_csv),
        "paper_count": len(canonical_rows),
        "records": provenance_records,
        "boundary": "Canonical fields are bibliographic projections; scientific annotations remain separate.",
    }, indent=2, ensure_ascii=False) + "\n")
    return canonical_path, provenance_path, {
        "rows": len(canonical_rows),
        "trusted": trusted,
        "export_eligible": eligible,
    }
