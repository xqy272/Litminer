#!/usr/bin/env python3
"""Incremental research-session lineage and delta artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from litminer.engine import dedupe_papers
from litminer.engine.common import read_csv_rows, utc_now, write_text_atomic


SESSION_NAME = "research_session_manifest.json"
DELTA_NAME = "delta_profile.json"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _read_rows(path: Path | None) -> list[dict[str, str]]:
    if path is None or not path.exists():
        return []
    _fields, rows = read_csv_rows(path)
    return rows


def _identity(row: dict[str, str]) -> str:
    dedupe_key = (row.get("dedupe_key") or "").strip()
    if dedupe_key:
        return dedupe_key
    kind, value = dedupe_papers.row_key(row, "doi", "title")
    if value:
        return f"{kind}:{value}"
    return "row:" + json.dumps(row, sort_keys=True, ensure_ascii=False)


def _journal(row: dict[str, str]) -> str:
    status = (row.get("crossref_status") or "").strip()
    if status in {"verified", "title_recovered"}:
        value = (row.get("crossref_container") or "").strip()
        if value:
            return value
    return (row.get("journal") or "").strip()


def next_iteration_id(output_dir: Path) -> str:
    session = _read_json(output_dir / SESSION_NAME)
    iterations = session.get("iterations", [])
    count = len(iterations) if isinstance(iterations, list) else 0
    return f"iteration_{count + 1:03d}"


def session_id(output_dir: Path) -> str:
    session = _read_json(output_dir / SESSION_NAME)
    existing = str(session.get('session_id') or '').strip()
    if existing:
        return existing
    identity = str(output_dir.resolve(strict=False)).lower()
    return 'litminer_session_' + hashlib.sha256(identity.encode('utf-8')).hexdigest()[:20]


def resume_iteration_id(output_dir: Path) -> str:
    """Return the active iteration id without turning resume into a new iteration."""
    plan = _read_json(output_dir / "query_plan.json")
    controls = plan.get("run_controls") if isinstance(plan.get("run_controls"), dict) else {}
    planned = str((controls or {}).get("session_iteration_id") or "").strip()
    if planned:
        return planned

    session = _read_json(output_dir / SESSION_NAME)
    iterations = session.get("iterations", [])
    if isinstance(iterations, list):
        for item in reversed(iterations):
            if isinstance(item, dict):
                iteration_id = str(item.get("iteration_id") or "").strip()
                if iteration_id:
                    return iteration_id
    return next_iteration_id(output_dir)


def build_delta(
    before_path: Path | None,
    after_path: Path,
    *,
    iteration_id: str,
    queries: list[str],
) -> dict[str, Any]:
    before_rows = _read_rows(before_path)
    after_rows = _read_rows(after_path)
    before_ids = {_identity(row) for row in before_rows}
    new_rows = [row for row in after_rows if _identity(row) not in before_ids]

    priority_counts = Counter(
        (row.get("triage_priority") or "<blank>").strip() or "<blank>"
        for row in new_rows
    )
    source_counts = Counter(
        (
            row.get("discovery_provider")
            or row.get("discovery_source")
            or "<unknown>"
        ).strip()
        for row in new_rows
    )
    journal_counts = Counter(_journal(row) for row in new_rows if _journal(row))
    bibliographically_verified = sum(
        1
        for row in new_rows
        if (row.get("crossref_status") or "").strip() in {"verified", "title_recovered"}
    )
    return {
        "schema_version": 1,
        "iteration_id": iteration_id,
        "generated_at": utc_now(),
        "queries": queries,
        "previous_rows": len(before_rows),
        "current_rows": len(after_rows),
        "new_rows": len(new_rows),
        "new_bibliographically_verified": bibliographically_verified,
        "new_priority_distribution": dict(sorted(priority_counts.items())),
        "new_source_distribution": dict(sorted(source_counts.items())),
        "new_top_journals": journal_counts.most_common(15),
        "boundary": (
            "Delta statistics describe this retrieved collection only; "
            "they do not measure field-level recall or scientific importance."
        ),
    }


def write_delta(
    before_path: Path | None,
    after_path: Path,
    *,
    iteration_id: str,
    queries: list[str],
    output_path: Path | None = None,
) -> Path:
    output = output_path or after_path.parent / DELTA_NAME
    delta = build_delta(
        before_path,
        after_path,
        iteration_id=iteration_id,
        queries=queries,
    )
    write_text_atomic(output, json.dumps(delta, indent=2, ensure_ascii=False) + "\n")
    return output


def append_iteration(
    output_dir: Path,
    *,
    iteration_id: str,
    queries: list[str],
    concepts: dict[str, list[str]],
    delta: dict[str, Any],
    run_status: str,
    merge_mode: bool,
) -> Path:
    path = output_dir / SESSION_NAME
    session = _read_json(path)
    if not session:
        session = {
            "schema_version": 1,
            "session_id": session_id(output_dir),
            "created_at": utc_now(),
            "iterations": [],
        }
    iterations = session.setdefault("iterations", [])
    if not isinstance(iterations, list):
        iterations = []
        session["iterations"] = iterations
    record = {
        "iteration_id": iteration_id,
        "completed_at": utc_now(),
        "merge_mode": merge_mode,
        "run_status": run_status,
        "queries": queries,
        "concepts": concepts,
        "delta": {
            "previous_rows": delta.get("previous_rows", 0),
            "current_rows": delta.get("current_rows", 0),
            "new_rows": delta.get("new_rows", 0),
            "new_bibliographically_verified": delta.get(
                "new_bibliographically_verified", 0
            ),
        },
    }
    iterations[:] = [
        item for item in iterations
        if not isinstance(item, dict) or item.get("iteration_id") != iteration_id
    ]
    iterations.append(record)
    session["updated_at"] = utc_now()
    write_text_atomic(path, json.dumps(session, indent=2, ensure_ascii=False) + "\n")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a Litminer delta profile.")
    parser.add_argument("--before", type=Path, default=None)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--iteration-id", required=True)
    parser.add_argument("--query", action="append", default=[])
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    path = write_delta(
        args.before,
        args.after,
        iteration_id=args.iteration_id,
        queries=args.query,
        output_path=args.output,
    )
    print(f"Delta profile: {path}")


if __name__ == "__main__":
    main()
