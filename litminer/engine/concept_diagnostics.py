#!/usr/bin/env python3
"""Mechanical diagnostics for caller-supplied semantic concept selectivity."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from litminer.engine.common import read_csv_rows, write_text_atomic


DIAGNOSTICS_NAME = "concept_diagnostics.json"
CONCEPT_FIELDS = {
    "required": "matched_required",
    "optional": "matched_optional",
    "negative": "matched_negative",
}


def _items(value: str) -> list[str]:
    return [item.strip() for item in (value or "").split(";") if item.strip()]


def build_diagnostics(triaged_path: Path) -> dict[str, Any]:
    if not triaged_path.exists():
        rows: list[dict[str, str]] = []
    else:
        _fields, rows = read_csv_rows(triaged_path)
    total = len(rows)
    high_rows = [
        row for row in rows
        if (row.get("triage_priority") or "").strip() == "high"
    ]
    high_total = len(high_rows)

    required_names: set[str] = set()
    for row in rows:
        required_names.update(_items(row.get("matched_required") or ""))
        required_names.update(_items(row.get("missing_required") or ""))

    concepts: dict[str, list[dict[str, Any]]] = {}
    warnings: list[str] = []
    for kind, field in CONCEPT_FIELDS.items():
        counts: Counter[str] = Counter()
        high_counts: Counter[str] = Counter()
        by_source: dict[str, Counter[str]] = defaultdict(Counter)
        for row in rows:
            source = (
                row.get("discovery_provider")
                or row.get("discovery_source")
                or "<unknown>"
            ).strip()
            for name in _items(row.get(field) or ""):
                counts[name] += 1
                by_source[name][source] += 1
                if (row.get("triage_priority") or "").strip() == "high":
                    high_counts[name] += 1
        names = set(counts)
        if kind == "required":
            names.update(required_names)
        entries: list[dict[str, Any]] = []
        for name in sorted(names):
            count = counts.get(name, 0)
            rate = round(count / total, 4) if total else 0.0
            high_count = high_counts.get(name, 0)
            high_rate = round(high_count / high_total, 4) if high_total else 0.0
            entries.append({
                "name": name,
                "matched_rows": count,
                "match_rate": rate,
                "high_priority_rows": high_count,
                "high_priority_match_rate": high_rate,
                "by_source": dict(sorted(by_source[name].items())),
            })
            if kind == "required" and total >= 10 and rate >= 0.8:
                warnings.append(
                    f"Required concept '{name}' matched {rate:.0%} of rows; "
                    "it has low selectivity."
                )
            if kind == "required" and count == 0:
                warnings.append(f"Required concept '{name}' matched no rows.")
        concepts[kind] = entries

    return {
        "schema_version": 1,
        "total_rows": total,
        "high_priority_rows": high_total,
        "concepts": concepts,
        "warnings": warnings,
        "boundary": (
            "Mechanical match-rate diagnostics only. Litminer does not infer "
            "which concepts should be added, removed, or used for final inclusion."
        ),
    }


def write_diagnostics(
    triaged_path: Path,
    output_path: Path | None = None,
) -> Path:
    output = output_path or triaged_path.parent / DIAGNOSTICS_NAME
    diagnostics = build_diagnostics(triaged_path)
    write_text_atomic(
        output,
        json.dumps(diagnostics, indent=2, ensure_ascii=False) + "\n",
    )
    return output


def to_summary(diagnostics: dict[str, Any]) -> dict[str, Any]:
    return {
        "total_rows": diagnostics.get("total_rows", 0),
        "high_priority_rows": diagnostics.get("high_priority_rows", 0),
        "warnings": diagnostics.get("warnings", []),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Write mechanical semantic-concept match diagnostics."
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    path = write_diagnostics(args.input, args.output)
    print(f"Concept diagnostics: {path}")


if __name__ == "__main__":
    main()
