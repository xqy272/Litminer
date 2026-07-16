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
    ("run_outcome", "primary", "run_outcome.json", "Stable execution status, retrieval quality, artifacts, and next actions."),
    ("agent_summary", "primary", "agent_summary.json", "Machine-readable run state and next actions."),
    ("coverage_report", "primary", "coverage_report.json", "Provider/query/verification coverage and healthy, degraded, or inconclusive quality."),
    ("canonical_papers", "primary", "canonical_papers.csv", "Canonical bibliography projection with trusted-field selection."),
    ("processing_report", "primary", "processing_report.md", "Human-readable status and trust-tier summary."),
    ("artifacts_index", "primary", INDEX_NAME, "Compact artifact map grouped by Agent reading tier."),
    ("query_plan", "primary", "query_plan.json", "Queries, concepts, source strategy, and run controls."),
    ("run_manifest", "primary", "run_manifest.json", "Stage status, fingerprints, resume metadata, and run signature."),
    (
        "research_session_manifest",
        "primary",
        "research_session_manifest.json",
        "Cross-iteration query, concept, and delta lineage for an incremental research session.",
    ),
    ("triaged_candidates", "primary", "triaged_candidates.csv", "Semantic review surface; not final inclusion."),
    ("publisher_queue", "primary", "publisher_queue.csv", "Publisher-page inspection queue."),
    (
        "api_discovery_trace",
        "primary",
        "api_discovery_trace.csv",
        "Provider/query/status trace for discovery failures.",
    ),
    (
        "result_profile",
        "primary",
        "result_profile.json",
        "Stratified descriptive statistics and search-process completeness caveats for the retrieved collection.",
    ),
    (
        "concept_diagnostics",
        "supporting",
        "concept_diagnostics.json",
        "Mechanical concept match rates, source distribution, and low-selectivity warnings.",
    ),
    (
        "delta_profile",
        "supporting",
        "delta_profile.json",
        "Current-iteration additions and their priority, source, journal, and bibliographic-verification counts.",
    ),
    (
        "search_audit_report",
        "primary",
        "search_audit_report.md",
        "Human-readable audit report for research reproducibility; same information as Agent artifacts.",
    ),
    ("feasibility_report", "supporting", "feasibility_report.md", "Constraint feasibility and blocking reasons."),
    ("field_provenance", "supporting", "field_provenance.json", "Field-level source/trust map."),
    ("canonical_provenance", "supporting", "canonical_provenance.json", "Direct source, trust class, and selection reason for canonical bibliography fields."),
    ("run_spec", "supporting", "run_spec.json", "Normalized typed input contract shared by CLI and MCP."),
    ("export_manifest", "supporting", "export_manifest.json", "Audited RIS/BibTeX export inputs, exclusions, conflicts, and hashes."),
    ("export_ris", "supporting", "litminer_export.ris", "RIS bibliography export when requested."),
    ("export_bibtex", "supporting", "litminer_export.bib", "BibTeX bibliography export when requested."),
    ("api_candidates", "supporting", "api_candidates.csv", "Raw API discovery candidates."),
    ("deduped_candidates", "supporting", "deduped_candidates.csv", "Deduplicated candidate set."),
    (
        "pretriaged_candidates",
        "supporting",
        "pretriaged_candidates.csv",
        "Pre-verification semantic ranking used to allocate bibliographic verification budget.",
    ),
    (
        "verification_queue",
        "supporting",
        "verification_queue.csv",
        "Deterministic DOI-first queue ordered before Crossref verification.",
    ),
    ("verified_candidates", "supporting", "verified_candidates.csv", "Crossref verification output."),
    ("selected_candidates", "supporting", "selected_candidates.csv", "Priority-selected rows for enrichment."),
    ("oa_annotated_candidates", "supporting", "oa_annotated_candidates.csv", "Unpaywall OA/access hints."),
    ("metrics_annotated_candidates", "supporting", "metrics_annotated_candidates.csv", "Journal metric annotations."),
    ("publisher_queue_probed", "supporting", "publisher_queue_probed.csv", "Optional publisher probe output."),
    ("publisher_queue_html_meta", "supporting", "publisher_queue_html_meta.csv", "Publisher HTML meta tag extraction (citation_keywords, citation_online_date, etc.)."),
    ("citation_expanded_candidates", "supporting", "citation_expanded_candidates.csv", "Citation/reference expansion candidates from Semantic Scholar."),
    ("citation_expand_trace", "supporting", "citation_expand_trace.csv", "Per-seed trace for citation/reference expansion."),
    ("api_discovery_report", "debug", "api_discovery_report.md", "Discovery provider status report."),
    ("publisher_adapters", "debug", "publisher_adapters.json", "Publisher adapter capability registry."),
    ("strict_candidates", "debug", "strict_candidates.csv", "Metric-pass table when metric filtering is active."),
    ("backup_candidates", "debug", "backup_candidates.csv", "Metric-fail or metric-unverified backup table."),
    ("merge_base_candidates", "debug", "merge_base_candidates.csv", "Snapshot of the prior candidate pool used to compute an incremental merge delta."),
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
