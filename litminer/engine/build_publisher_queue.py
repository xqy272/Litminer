#!/usr/bin/env python3
"""Build a DOI/publisher-page evidence queue from candidate CSV rows."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from litminer.engine import workspace
from litminer.engine.common import normalize_doi, read_csv_rows, write_csv_atomic


DEFAULT_QUEUE_PRIORITIES = {"high", "medium", "needs_review"}
DEFAULT_FIELDS_NEEDED = [
    "publisher_landing_page",
    "abstract_or_summary",
    "article_sections_available",
    "task_requested_fields",
    "evidence_pointer",
    "pdf_url",
    "si_url",
]
DEFAULT_PAGE_REQUIRED_FIELDS = [
    "publisher_visible_text",
    "abstract",
    "methods_or_evidence_section",
    "results_or_key_claim_section",
    "evidence_pointer",
]


def safe_name(value: str) -> str:
    value = normalize_doi(value) or value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    return value.strip("_") or "unknown"


def row_decision(row: dict[str, str]) -> str:
    return (row.get("screening_decision") or row.get("include_status") or "").strip()


def row_doi(row: dict[str, str]) -> str:
    return normalize_doi(row.get("crossref_doi") or row.get("doi") or "")


def _parse_number(value: str, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_set(value: str | list[str] | set[str] | None) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, set):
        return {str(item).strip() for item in value if str(item).strip()}
    if isinstance(value, list):
        raw = []
        for item in value:
            raw.extend(re.split(r"[,;]", str(item)))
        return {item.strip() for item in raw if item.strip()}
    return {item.strip() for item in re.split(r"[,;]", value) if item.strip()}


def _parse_fields(fields: list[str] | str | None, default: list[str]) -> list[str]:
    if fields is None:
        return list(default)
    if isinstance(fields, str):
        raw = re.split(r"[,;]", fields)
    else:
        raw = []
        for item in fields:
            raw.extend(re.split(r"[,;]", str(item)))
    parsed = []
    for item in raw:
        item = item.strip()
        if item and item not in parsed:
            parsed.append(item)
    return parsed or list(default)


def should_queue(row: dict[str, str], priorities: set[str] | None,
                 decisions: set[str] | None, statuses: set[str] | None) -> bool:
    if priorities:
        priority = (row.get("triage_priority") or "").strip()
        if priority:
            return priority in priorities

    if statuses:
        status = (row.get("candidate_status") or "").strip()
        if status:
            return status in statuses

    if decisions:
        decision = row_decision(row)
        if decision:
            return decision in decisions

    return True


def crossref_blocking_reason(row: dict[str, str]) -> str:
    status = (row.get("crossref_status") or "").strip().lower()
    mismatches = (row.get("crossref_mismatches") or "").strip()
    if mismatches:
        return mismatches
    if status in {"lookup_failed", "title_lookup_failed", "mismatch"}:
        return f"crossref_status={status}"
    return ""


def extraction_priority(row: dict[str, str]) -> str:
    priority = (row.get("triage_priority") or "").strip()
    if priority == "high":
        return "1"
    if priority == "medium":
        return "2"
    if priority == "needs_review":
        return "3"
    if priority == "low":
        return "4"

    decision = row_decision(row)
    if decision == "Include":
        return "1"
    if decision == "Backup":
        return "2"

    score = _parse_number(row.get("triage_score") or row.get("relevance_score") or "")
    if score >= 6:
        return "1"
    if score >= 3:
        return "2"
    return "3"


def build_queue(input_path: Path, output_path: Path,
                decisions: set[str] | None = None,
                screenshot_root: str = workspace.DEFAULT_SCREENSHOT_ROOT,
                require_doi: bool = True,
                priorities: set[str] | None = None,
                statuses: set[str] | None = None,
                include_metadata_blocked: bool = False,
                fields_needed: list[str] | str | None = None,
                page_required_fields: list[str] | str | None = None) -> dict[str, int]:
    fieldnames, rows = read_csv_rows(input_path)
    if not fieldnames:
        raise SystemExit("Input CSV has no header")
    if priorities and "triage_priority" not in fieldnames:
        raise SystemExit(
            "Input CSV has no triage_priority column; run semantic_triage first or omit priority filtering."
        )
    if statuses and "candidate_status" not in fieldnames:
        raise SystemExit(
            "Input CSV has no candidate_status column; run semantic_triage first or omit status filtering."
        )

    requested_fields = _parse_fields(fields_needed, DEFAULT_FIELDS_NEEDED)
    required_page_fields = _parse_fields(page_required_fields, DEFAULT_PAGE_REQUIRED_FIELDS)

    output_fields = [
        "queue_status",
        "extraction_priority",
        "title",
        "doi",
        "doi_url",
        "journal",
        "publication_year",
        "publisher_url",
        "discovery_source",
        "triage_priority",
        "triage_score",
        "candidate_status",
        "semantic_tags",
        "triage_reasons",
        "hard_filter_flags",
        "metadata_status",
        "crossref_status",
        "crossref_verified",
        "crossref_lookup_method",
        "crossref_mismatches",
        "crossref_title_similarity",
        "crossref_recovered_doi_confidence",
        "screening_decision",
        "relevance_score",
        "journal_metric",
        "journal_metric_source",
        "metric_filter_status",
        "access_status",
        "html_status",
        "is_oa",
        "oa_status",
        "best_oa_url",
        "best_oa_landing_url",
        "best_oa_pdf_url",
        "best_oa_host_type",
        "best_oa_version",
        "best_oa_license",
        "unpaywall_status",
        "pdf_status",
        "si_status",
        "resolved_url",
        "pdf_url",
        "si_url",
        "fields_needed",
        "page_required_fields",
        "full_text_required_fields",
        "screenshot_dir",
        "extraction_sop",
        "evidence_target_grade",
        "next_action",
        "notes",
    ]

    queued: list[dict[str, str]] = []
    skipped_missing_doi = 0
    skipped_filter = 0
    skipped_metadata_blocked = 0

    for row in rows:
        if not include_metadata_blocked and row.get("metadata_status") == "blocked":
            skipped_metadata_blocked += 1
            continue

        crossref_reason = crossref_blocking_reason(row)
        if crossref_reason and not include_metadata_blocked:
            skipped_metadata_blocked += 1
            continue

        if not should_queue(row, priorities, decisions, statuses):
            skipped_filter += 1
            continue

        doi = row_doi(row)
        if require_doi and not doi:
            skipped_missing_doi += 1
            continue

        doi_url = f"https://doi.org/{doi}" if doi else ""
        best_oa_pdf = row.get("best_oa_pdf_url", "")
        best_oa_url = row.get("best_oa_landing_url") or row.get("best_oa_url", "")
        title = row.get("crossref_title") or row.get("title") or (f"DOI {doi}" if doi else "")
        screenshot_dir = str(Path(screenshot_root) / safe_name(doi or row.get("title", "")))
        queued.append({
            "queue_status": "pending",
            "extraction_priority": extraction_priority(row),
            "title": title,
            "doi": doi,
            "doi_url": doi_url,
            "journal": row.get("crossref_container") or row.get("journal", ""),
            "publication_year": row.get("crossref_year") or row.get("publication_year") or row.get("year", ""),
            "publisher_url": doi_url or row.get("landing_page_url") or row.get("url") or row.get("crossref_url", ""),
            "discovery_source": row.get("discovery_source", ""),
            "triage_priority": row.get("triage_priority", ""),
            "triage_score": row.get("triage_score", ""),
            "candidate_status": row.get("candidate_status", ""),
            "semantic_tags": row.get("semantic_tags", ""),
            "triage_reasons": row.get("triage_reasons", ""),
            "hard_filter_flags": row.get("hard_filter_flags", ""),
            "metadata_status": row.get("metadata_status", ""),
            "crossref_status": row.get("crossref_status", ""),
            "crossref_verified": row.get("crossref_verified", ""),
            "crossref_lookup_method": row.get("crossref_lookup_method", ""),
            "crossref_mismatches": row.get("crossref_mismatches", ""),
            "crossref_title_similarity": row.get("crossref_title_similarity", ""),
            "crossref_recovered_doi_confidence": row.get("crossref_recovered_doi_confidence", ""),
            "screening_decision": row_decision(row),
            "relevance_score": row.get("relevance_score", ""),
            "journal_metric": row.get("journal_metric", ""),
            "journal_metric_source": row.get("journal_metric_source", ""),
            "metric_filter_status": row.get("metric_filter_status", ""),
            "access_status": "pending",
            "html_status": "pending",
            "is_oa": row.get("is_oa", ""),
            "oa_status": row.get("oa_status", ""),
            "best_oa_url": row.get("best_oa_url", ""),
            "best_oa_landing_url": row.get("best_oa_landing_url", ""),
            "best_oa_pdf_url": best_oa_pdf,
            "best_oa_host_type": row.get("best_oa_host_type", ""),
            "best_oa_version": row.get("best_oa_version", ""),
            "best_oa_license": row.get("best_oa_license", ""),
            "unpaywall_status": row.get("unpaywall_status", ""),
            "pdf_status": "found" if best_oa_pdf else "unknown",
            "si_status": "unknown",
            "resolved_url": best_oa_url,
            "pdf_url": best_oa_pdf,
            "si_url": "",
            "fields_needed": "; ".join(requested_fields),
            "page_required_fields": "; ".join(required_page_fields),
            "full_text_required_fields": "; ".join(required_page_fields),
            "screenshot_dir": screenshot_dir,
            "extraction_sop": "litminer/sources/extraction/publisher-page-extraction.md",
            "evidence_target_grade": "publisher_page_or_better",
            "next_action": (
                "Resolve DOI landing page and inspect publisher-visible article page. "
                "Use Unpaywall OA links as structured access hints when present. "
                "Record PDF/SI URLs if offered, but do not infer unavailable fields."
            ),
            "notes": (
                "Agent-facing evidence queue; requested fields are task-specific. "
                "Publisher probe fields are planning hints, not extracted evidence."
            ),
        })

    queued.sort(key=lambda row: (
        int(row["extraction_priority"]) if row["extraction_priority"].isdigit() else 99,
        -_parse_number(row.get("triage_score") or row.get("relevance_score") or ""),
        row.get("title", "").lower(),
    ))

    write_csv_atomic(queued, output_path, fieldnames=output_fields)

    print(f"Publisher evidence queue: {len(queued)} rows -> {output_path}", file=sys.stderr)
    if skipped_filter:
        print(f"Skipped {skipped_filter} row(s) outside queue selection.", file=sys.stderr)
    if skipped_metadata_blocked:
        print(f"Skipped {skipped_metadata_blocked} metadata-blocked row(s).", file=sys.stderr)
    if skipped_missing_doi:
        print(
            f"Skipped {skipped_missing_doi} selected row(s) without DOI. "
            "Run Crossref title lookup first, or pass --allow-missing-doi for manual triage.",
            file=sys.stderr,
        )
    return {
        "queued": len(queued),
        "skipped_filter": skipped_filter,
        "skipped_metadata_blocked": skipped_metadata_blocked,
        "skipped_missing_doi": skipped_missing_doi,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a DOI/publisher evidence queue.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--priorities", default="high,medium,needs_review",
                        help="Comma-separated triage priorities to queue")
    parser.add_argument("--decisions", default="",
                        help="Legacy comma-separated screening decisions to queue")
    parser.add_argument("--statuses", default="",
                        help="Comma-separated candidate_status values to queue")
    parser.add_argument("--include-metadata-blocked", action="store_true",
                        help="Also queue rows marked metadata_status=blocked")
    parser.add_argument("--fields-needed", action="append", default=None,
                        help="Task-specific field needed from publisher page; repeat or comma-separate")
    parser.add_argument("--page-required-field", action="append", default=None,
                        help="Generic publisher-page evidence field; repeat or comma-separate")
    parser.add_argument("--screenshot-root", default=None)
    parser.add_argument("--allow-missing-doi", action="store_true",
                        help="Queue selected rows even when DOI is missing")
    args = parser.parse_args()

    build_queue(
        args.input,
        args.output,
        decisions=_parse_set(args.decisions),
        priorities=_parse_set(args.priorities) or DEFAULT_QUEUE_PRIORITIES,
        statuses=_parse_set(args.statuses),
        include_metadata_blocked=args.include_metadata_blocked,
        screenshot_root=args.screenshot_root or str(workspace.resolve_workspace_path(workspace.DEFAULT_SCREENSHOT_ROOT)),
        require_doi=not args.allow_missing_doi,
        fields_needed=args.fields_needed,
        page_required_fields=args.page_required_field,
    )


if __name__ == "__main__":
    main()
