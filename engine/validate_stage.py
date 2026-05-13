#!/usr/bin/env python3
"""Stage-aware validation for Agent-facing Litminer outputs."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path


STAGE_REQUIRED = {
    "candidate": ["title", "publication_year", "journal"],
    "triage": ["triage_priority", "triage_score", "triage_reasons"],
    "metadata": ["title", "doi", "journal", "publication_year"],
    "queue": ["title", "doi", "doi_url", "publisher_url", "fields_needed", "next_action"],
    "preliminary": ["title", "doi", "journal", "publication_year", "evidence_grade", "evidence_pointer"],
}


def empty(value: str) -> bool:
    return not value or value.strip() in {"", "Unknown", "Not verified", "Not available"}


def validate_stage(input_path: Path, output_path: Path, stage: str) -> int:
    if stage not in STAGE_REQUIRED:
        raise SystemExit(f"Unknown stage: {stage}. Expected one of {sorted(STAGE_REQUIRED)}")

    with input_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise SystemExit("Input CSV has no header")
        fieldnames = set(reader.fieldnames)
        rows = list(reader)

    issues: list[tuple[int | str, str, str, str]] = []
    for field in STAGE_REQUIRED[stage]:
        if field not in fieldnames:
            issues.append(("HEADER", "FAIL", field, "Required column missing"))

    for idx, row in enumerate(rows, start=2):
        for field in STAGE_REQUIRED[stage]:
            if field in fieldnames and empty(row.get(field, "")):
                issues.append((idx, "FAIL", field, "Required stage field is empty/Unknown"))

        if stage == "queue":
            if row.get("access_status", "") == "pending" and row.get("queue_status", "") != "pending":
                issues.append((idx, "WARN", "access_status", "Queue status advanced while access remains pending"))
        if stage == "preliminary":
            if empty(row.get("evidence_pointer", "")):
                issues.append((idx, "WARN", "evidence_pointer", "Task-specific values still require source evidence"))

    fails = [item for item in issues if item[1] == "FAIL"]
    warns = [item for item in issues if item[1] == "WARN"]
    lines = [
        "# Stage Validation Report",
        "",
        f"File: `{input_path.name}`",
        f"Stage: `{stage}`",
        f"Rows: {len(rows)}",
        f"FAILs: {len(fails)}",
        f"WARNs: {len(warns)}",
        f"Overall: {'PASS' if not fails else 'FAIL'}",
        "",
    ]
    if issues:
        lines.extend(["| Row | Level | Field | Issue |", "|-----|-------|-------|-------|"])
        for row_num, level, field, issue in issues:
            lines.append(f"| {row_num} | {level} | `{field}` | {issue} |")
    else:
        lines.append("No issues found.")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(fails)


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate a Litminer CSV at a workflow stage.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--stage", choices=sorted(STAGE_REQUIRED), required=True)
    args = parser.parse_args()
    failures = validate_stage(args.input, args.output, args.stage)
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
