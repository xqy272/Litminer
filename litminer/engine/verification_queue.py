#!/usr/bin/env python3
"""Build a deterministic, budget-aware bibliographic verification queue."""

from __future__ import annotations

import argparse
from pathlib import Path

from litminer.engine.common import normalize_doi, read_csv_rows, write_csv_atomic


OUTPUT_COLUMNS = [
    "verification_queue_rank",
    "verification_lane",
    "verification_reason",
]

PRIORITY_ORDER = {
    "high": 0,
    "medium": 1,
    "needs_review": 2,
    "low": 3,
}


def _number(value: str, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _lane(row: dict[str, str]) -> tuple[int, str]:
    priority = (row.get("triage_priority") or "needs_review").strip()
    priority_rank = PRIORITY_ORDER.get(priority, PRIORITY_ORDER["needs_review"])
    has_doi = bool(normalize_doi(row.get("crossref_doi") or row.get("doi") or ""))
    blocked = (row.get("metadata_status") or "").strip() == "blocked"

    # DOI lookups are cheaper and more reliable than title recovery. Within
    # each relevance tier they therefore receive the earlier lane.
    lane = priority_rank * 2 + (0 if has_doi else 1) + 1
    if blocked:
        lane += 20
    reason = (
        f"triage={priority}; "
        f"identifier={'doi' if has_doi else 'title_lookup'}; "
        f"metadata={'blocked' if blocked else 'eligible'}"
    )
    return lane, reason


def verification_sort_key(row: dict[str, str]) -> tuple[int, float, float, str]:
    lane, _reason = _lane(row)
    score = _number(row.get("triage_score") or "")
    cited = _number(row.get("cited_by_count") or "")
    title = (row.get("crossref_title") or row.get("title") or "").strip().lower()
    return lane, -score, -cited, title


def build_queue(input_path: Path, output_path: Path) -> dict[str, int]:
    fieldnames, rows = read_csv_rows(input_path)
    if not fieldnames:
        raise SystemExit("Input CSV has no header")
    if "triage_priority" not in fieldnames:
        raise SystemExit("Verification queue input must be semantically triaged first")
    for column in OUTPUT_COLUMNS:
        if column not in fieldnames:
            fieldnames.append(column)

    rows.sort(key=verification_sort_key)
    counts = {
        "rows": len(rows),
        "doi_first": 0,
        "title_lookup": 0,
        "metadata_blocked": 0,
    }
    for index, row in enumerate(rows, start=1):
        lane, reason = _lane(row)
        row["verification_queue_rank"] = str(index)
        row["verification_lane"] = str(lane)
        row["verification_reason"] = reason
        if normalize_doi(row.get("crossref_doi") or row.get("doi") or ""):
            counts["doi_first"] += 1
        else:
            counts["title_lookup"] += 1
        if (row.get("metadata_status") or "").strip() == "blocked":
            counts["metadata_blocked"] += 1

    write_csv_atomic(rows, output_path, fieldnames=fieldnames)
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Order triaged candidates for budget-aware Crossref verification."
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    counts = build_queue(args.input, args.output)
    print(
        "Verification queue: "
        f"{counts['rows']} rows; doi_first={counts['doi_first']}; "
        f"title_lookup={counts['title_lookup']}; metadata_blocked={counts['metadata_blocked']}"
    )


if __name__ == "__main__":
    main()
