#!/usr/bin/env python3
"""Artifact index for Agent-facing Litminer runs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from litminer.engine.common import write_text_atomic
from litminer.engine import workflow_state


INDEX_NAME = "artifacts_index.json"


ARTIFACTS: list[tuple[str, str, str, str]] = [
    ("agent_summary", "primary", "agent_summary.json", "Machine-readable run state and next actions."),
    ("processing_report", "primary", "processing_report.md", "Human-readable status and trust-tier summary."),
    ("artifacts_index", "primary", INDEX_NAME, "Compact artifact map grouped by Agent reading tier."),
    ("query_plan", "primary", "query_plan.json", "Queries, concepts, source strategy, and run controls."),
    ("run_manifest", "primary", "run_manifest.json", "Stage status, fingerprints, resume metadata, and run signature."),
    ("triaged_candidates", "primary", "triaged_candidates.csv", "Semantic review surface; not final inclusion."),
    ("publisher_queue", "primary", "publisher_queue.csv", "Publisher-page inspection queue."),
    (
        "api_discovery_trace",
        "primary",
        "api_discovery_trace.csv",
        "Provider/query/status trace for discovery failures.",
    ),
    ("feasibility_report", "supporting", "feasibility_report.md", "Constraint feasibility and blocking reasons."),
    ("field_provenance", "supporting", "field_provenance.json", "Field-level source/trust map."),
    ("api_candidates", "supporting", "api_candidates.csv", "Raw API discovery candidates."),
    ("deduped_candidates", "supporting", "deduped_candidates.csv", "Deduplicated candidate set."),
    ("verified_candidates", "supporting", "verified_candidates.csv", "Crossref verification output."),
    ("selected_candidates", "supporting", "selected_candidates.csv", "Priority-selected rows for enrichment."),
    ("oa_annotated_candidates", "supporting", "oa_annotated_candidates.csv", "Unpaywall OA/access hints."),
    ("metrics_annotated_candidates", "supporting", "metrics_annotated_candidates.csv", "Journal metric annotations."),
    ("publisher_queue_probed", "supporting", "publisher_queue_probed.csv", "Optional publisher probe output."),
    ("api_discovery_report", "debug", "api_discovery_report.md", "Discovery provider status report."),
    ("publisher_adapters", "debug", "publisher_adapters.json", "Publisher adapter capability registry."),
    ("strict_candidates", "debug", "strict_candidates.csv", "Metric-pass table when metric filtering is active."),
    ("backup_candidates", "debug", "backup_candidates.csv", "Metric-fail or metric-unverified backup table."),
]


def _artifact_record(
    output_dir: Path,
    name: str,
    tier: str,
    filename: str,
    description: str,
    read_order: int,
) -> dict[str, Any]:
    path = output_dir / filename
    return {
        "name": name,
        "tier": tier,
        "role": tier,
        "read_order": read_order,
        "path": str(path),
        "exists": path.exists(),
        "rows": workflow_state.row_count(path) if path.suffix == ".csv" else 0,
        "sha256": workflow_state.file_sha256(path),
        "description": description,
    }


def build_index(output_dir: Path) -> dict[str, Any]:
    records = [
        _artifact_record(output_dir, name, tier, filename, description, read_order)
        for read_order, (name, tier, filename, description) in enumerate(ARTIFACTS, start=1)
    ]
    by_tier: dict[str, list[str]] = {}
    for record in records:
        if record["exists"]:
            by_tier.setdefault(str(record["tier"]), []).append(str(record["name"]))
    existing = [record for record in records if record["exists"]]
    existing_by_name = {str(record["name"]): record for record in existing}
    return {
        "schema_version": 1,
        "output_dir": str(output_dir),
        "tiers": {
            "primary": "Read these first; they are the Agent default surface.",
            "supporting": "Use when a primary artifact points to a specific table or evidence need.",
            "debug": "Use for diagnosis, audits, and advanced/manual continuation.",
        },
        "by_tier": by_tier,
        "read_order": [str(record["name"]) for record in sorted(existing, key=lambda item: int(item["read_order"]))],
        "primary_artifacts": [record for record in existing if record["tier"] == "primary"],
        "supporting_artifacts": [record for record in existing if record["tier"] == "supporting"],
        "debug_artifacts": [record for record in existing if record["tier"] == "debug"],
        "artifacts_by_name": existing_by_name,
        "artifacts": records,
    }


def write_index(output_dir: Path, output_path: Path | None = None) -> Path:
    path = output_path or output_dir / INDEX_NAME
    write_text_atomic(path, json.dumps(build_index(output_dir), indent=2, ensure_ascii=False) + "\n")
    return path
