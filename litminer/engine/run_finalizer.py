"""Final artifacts, coverage, canonical projection, export, and outcome."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from litminer.contracts.outcomes import RunOutcome
from litminer.engine import (
    agent_summary,
    artifacts,
    concept_diagnostics,
    processing_report,
    provenance,
    publisher_adapters,
    query_plan,
    research_session,
    result_profile,
    search_audit_report,
    workflow_state,
)
from litminer.engine.common import write_text_atomic
from litminer.evidence import canonicalize as canonical_evidence
from litminer.evidence import coverage as coverage_model
from litminer.exporters.exporter import export_bibliography
from litminer.runtime.run_lifecycle import record_manifest_stage
from litminer.runtime.state_store import StateStore


PARTIAL_RUN_STATUS_CLASSES = {
    "auth",
    "budget_limited",
    "error",
    "network",
    "partial",
    "rate_limited",
}


def refresh_processing_report(
    out_dir: Path,
    warnings: list[str] | None = None,
) -> None:
    try:
        processing_report.write_report(
            out_dir,
            out_dir / "processing_report.md",
        )
    except Exception as exc:
        print(
            f"WARNING: failed to refresh processing_report.md: {exc}",
            file=sys.stderr,
        )
    try:
        agent_summary.write_summary(out_dir, warnings=warnings)
    except Exception as exc:
        print(
            f"WARNING: failed to refresh agent_summary.json: {exc}",
            file=sys.stderr,
        )


def aggregate_run_status(
    manifest: dict[str, Any],
    requested_status: str,
) -> str:
    if requested_status != "completed":
        return requested_status
    stages = manifest.get("stages", [])
    if not isinstance(stages, list):
        return requested_status
    from litminer.engine import status_policy

    for stage in stages:
        if not isinstance(stage, dict):
            continue
        status_class = status_policy.classify_status(
            str(stage.get("status") or "")
        )
        if status_class in PARTIAL_RUN_STATUS_CLASSES:
            return "partial"
    return requested_status


def write_publisher_adapters_artifact(out_dir: Path) -> Path:
    path = out_dir / "publisher_adapters.json"
    write_text_atomic(
        path,
        json.dumps({
            "schema_version": 1,
            "adapters": publisher_adapters.adapter_rows(),
        }, indent=2)
        + "\n",
    )
    return path


def make_report(
    out_dir: Path,
    counts: dict[str, int],
    args: argparse.Namespace,
    strict_path: Path | None,
    backup_path: Path | None,
    queue_priorities: set[str],
    warnings: list[str] | None = None,
) -> None:
    target = args.target_count
    feasible_count = counts.get("publisher_queue", 0)
    blocking_reasons: list[str] = []
    if counts.get("deduped", 0) == 0:
        blocking_reasons.append(
            "No candidates remained after discovery/merge/deduplication."
        )
    if counts.get("triaged", 0) == 0:
        blocking_reasons.append("No rows reached semantic triage.")
    if feasible_count == 0:
        if args.min_if is not None and getattr(
            args,
            "skip_journal_metrics",
            False,
        ):
            blocking_reasons.append(
                "Metric filtering was requested but journal metrics are disabled."
            )
        elif args.min_if is not None and counts.get("metric_pass", 0) == 0:
            blocking_reasons.append(
                "No metric-pass candidates are available under the current IF threshold."
            )
        else:
            blocking_reasons.append(
                "No candidates reached the publisher evidence queue under "
                "the current constraints."
            )
    if target is not None and feasible_count < target:
        blocking_reasons.append(
            f"Current feasible count {feasible_count} is below requested "
            f"target {target}."
        )
    feasible = not blocking_reasons

    lines = [
        "# Litminer Feasibility Report",
        "",
        f"Output directory: `{out_dir}`",
        f"Run mode: `{getattr(args, 'mode', None) or 'custom/default'}`",
        f"Year from: `{args.year_from or 'none'}`",
        f"Target count: `{target if target is not None else 'not specified'}`",
        f"Minimum IF: `{args.min_if if args.min_if is not None else 'not specified'}`",
        f"Metric queue mode: `{'strict-pass-only' if args.queue_strict_only else 'annotate-only'}`",
        f"Queued triage priorities: `{', '.join(sorted(queue_priorities))}`",
        f"Overall: `{'FEASIBLE' if feasible else 'NOT_FEASIBLE'}`",
        "",
        "## Counts",
        "",
    ]
    for key in [
        "discovery_files",
        "merge_base_rows",
        "deduped",
        "pretriaged",
        "pretriage_high",
        "verification_queue",
        "verification_queue_doi",
        "verification_queue_title_lookup",
        "crossref_verified",
        "crossref_title_recovered",
        "crossref_mismatch",
        "crossref_lookup_failed",
        "crossref_missing_doi",
        "crossref_title_lookup_failed",
        "crossref_rate_limited",
        "crossref_network_error",
        "crossref_auth_error",
        "crossref_response_parse_error",
        "crossref_provider_error",
        "crossref_skipped_budget",
        "triaged",
        "triage_high",
        "triage_medium",
        "triage_needs_review",
        "triage_low",
        "metadata_blocked",
        "selected_for_verification",
        "verified",
        "selected_unverified",
        "unpaywall_ok",
        "unpaywall_skipped_missing_email",
        "unpaywall_missing_doi",
        "unpaywall_not_found",
        "unpaywall_rate_limited",
        "unpaywall_network_error",
        "unpaywall_response_parse_error",
        "unpaywall_error",
        "unpaywall_skipped_budget",
        "metric_pass",
        "metric_backup",
        "publisher_queue",
        "publisher_probed",
    ]:
        if key in counts:
            lines.append(f"- {key}: {counts[key]}")

    lines.extend(["", "## Feasibility", ""])
    if feasible:
        lines.append(
            "The current constraints appear feasible from the available "
            "candidate set."
        )
    else:
        lines.append(
            "The current constraints do not reach the requested count. "
            "Do not fabricate rows; inspect lower-priority candidates or ask "
            "to relax constraints."
        )
        lines.extend(["", "Blocking reasons:"])
        for reason in blocking_reasons:
            lines.append(f"- {reason}")
    if strict_path:
        lines.append(f"- Metric-pass table: `{strict_path.name}`")
    if backup_path:
        lines.append(f"- Metric backup table: `{backup_path.name}`")

    if warnings:
        lines.extend(["", "## Configuration Warnings", ""])
        for warning in warnings:
            lines.append(f"- {warning}")

    lines.extend([
        "",
        "## Next Actions",
        "",
        "- Use `triaged_candidates.csv` as the Agent review surface; "
        "scripts rank and tag but do not make final scientific judgement.",
        "- Use `publisher_queue.csv` to inspect DOI landing pages and "
        "publisher-visible article pages.",
        "- Use Unpaywall OA links as structured access hints when available; "
        "verify article-level claims on publisher-visible pages.",
        "- Record PDF/SI URLs when publisher pages expose them; PDF parsing "
        "is outside Litminer core.",
        "- Treat WebSearch as supplemental only; metadata and publisher pages "
        "remain the primary evidence path.",
    ])
    write_text_atomic(
        out_dir / "feasibility_report.md",
        "\n".join(lines) + "\n",
    )


def finalize_run(
    out_dir: Path,
    manifest: dict[str, Any],
    counts: dict[str, int],
    args: argparse.Namespace,
    strict_path: Path | None,
    backup_path: Path | None,
    queue_priorities: set[str],
    warnings: list[str],
    *,
    run_status: str = "completed",
    stop_reason: str = "",
    triaged: Path | None = None,
    publisher_queue: Path | None = None,
) -> dict[str, str]:
    if stop_reason:
        warnings = [*warnings, stop_reason]
        record_manifest_stage(
            out_dir,
            manifest,
            "run_control",
            "stopped",
            message=stop_reason,
        )
    final_status = aggregate_run_status(manifest, run_status)
    if final_status == "partial" and run_status == "completed":
        warnings = [
            *warnings,
            "One or more stages completed with partial, rate-limit, budget, "
            "or error status.",
        ]
    make_report(
        out_dir,
        counts,
        args,
        strict_path,
        backup_path,
        queue_priorities,
        warnings=warnings,
    )
    write_publisher_adapters_artifact(out_dir)
    manifest["run_status"] = final_status
    if stop_reason:
        manifest["stop_reason"] = stop_reason
    manifest["completed_at"] = workflow_state.utc_now()
    workflow_state.write_manifest(out_dir, manifest)
    result_profile_path = result_profile.write_profile(
        out_dir / "triaged_candidates.csv",
        out_dir / "api_discovery_trace.csv",
        workflow_state.manifest_path(out_dir),
    )
    concept_diagnostics_path = concept_diagnostics.write_diagnostics(
        out_dir / "triaged_candidates.csv"
    )
    iteration_id = str(
        getattr(args, "session_iteration_id", "") or "iteration_001"
    )
    queries = list(getattr(args, "session_queries", []) or [])
    delta_profile_path = research_session.write_delta(
        getattr(args, "merge_base_path", None),
        out_dir / "triaged_candidates.csv",
        iteration_id=iteration_id,
        queries=queries,
    )
    delta_profile = json.loads(
        delta_profile_path.read_text(encoding="utf-8")
    )
    session_manifest_path = research_session.append_iteration(
        out_dir,
        iteration_id=iteration_id,
        queries=queries,
        concepts={
            "required": list(getattr(args, "required_concept", []) or []),
            "optional": list(getattr(args, "optional_concept", []) or []),
            "negative": list(getattr(args, "negative_concept", []) or []),
        },
        delta=delta_profile,
        run_status=final_status,
        merge_mode=bool(getattr(args, "merge_into", None)),
    )
    canonical_input = triaged or out_dir / "triaged_candidates.csv"
    (
        canonical_path,
        canonical_provenance_path,
        canonical_counts,
    ) = canonical_evidence.build_canonical_artifacts(
        canonical_input,
        out_dir,
        state_store=getattr(args, "state_store_instance", None),
    )
    counts["canonical_rows"] = canonical_counts["rows"]
    counts["canonical_trusted"] = canonical_counts["trusted"]
    counts["export_eligible"] = canonical_counts["export_eligible"]
    configured_sources = list(
        getattr(args, "selected_discovery_sources", []) or []
    )
    coverage_path = coverage_model.write_coverage_report(
        out_dir,
        configured_sources=configured_sources,
        query_count=len(queries),
        candidate_count=workflow_state.row_count(canonical_input),
        input_mode=getattr(args, "run_spec").input.mode,
        run_id=str(manifest.get("run_id") or ""),
        verification={
            "candidate_rows": workflow_state.row_count(canonical_input),
            "crossref_verified": counts.get("crossref_verified", 0),
            "crossref_title_recovered": counts.get(
                "crossref_title_recovered",
                0,
            ),
            "crossref_skipped_budget": counts.get(
                "crossref_skipped_budget",
                0,
            ),
            "crossref_provider_failures": sum(
                counts.get(key, 0)
                for key in (
                    "crossref_rate_limited",
                    "crossref_network_error",
                    "crossref_auth_error",
                    "crossref_response_parse_error",
                    "crossref_provider_error",
                )
            ),
            "unpaywall_ok": counts.get("unpaywall_ok", 0),
            "unpaywall_skipped_budget": counts.get(
                "unpaywall_skipped_budget",
                0,
            ),
        },
        state_store=getattr(args, "state_store_instance", None),
    )
    coverage_data = json.loads(coverage_path.read_text(encoding="utf-8"))
    export_result: dict[str, Any] = {}
    if getattr(args, "export_formats", None):
        export_result = export_bibliography(
            canonical_path,
            out_dir,
            formats=list(args.export_formats),
            include_unverified=bool(args.include_unverified_export),
            ascii_latex=bool(args.ascii_latex),
        )
    manifest["run_quality"] = coverage_data.get(
        "quality",
        "inconclusive",
    )
    manifest["coverage_report"] = str(coverage_path)
    manifest["canonical_papers"] = str(canonical_path)
    manifest["export_manifest"] = str(export_result.get("manifest") or "")
    workflow_state.write_manifest(out_dir, manifest)
    refresh_processing_report(out_dir, warnings=warnings)
    audit_report_path = search_audit_report.build_audit_report(out_dir)
    artifact_index_path = artifacts.write_index(out_dir)
    artifact_paths = {
        "triaged_candidates": str(
            triaged or out_dir / "triaged_candidates.csv"
        ),
        "canonical_papers": str(canonical_path),
        "canonical_provenance": str(canonical_provenance_path),
        "coverage_report": str(coverage_path),
        "feasibility_report": str(out_dir / "feasibility_report.md"),
        "processing_report": str(out_dir / "processing_report.md"),
        "agent_summary": str(out_dir / agent_summary.SUMMARY_NAME),
        "result_profile": str(result_profile_path),
        "concept_diagnostics": str(concept_diagnostics_path),
        "delta_profile": str(delta_profile_path),
        "research_session_manifest": str(session_manifest_path),
        "search_audit_report": str(audit_report_path),
        "query_plan": str(out_dir / query_plan.PLAN_NAME),
        "run_spec": str(out_dir / "run_spec.json"),
        "field_provenance": str(out_dir / provenance.PROVENANCE_NAME),
        "publisher_adapters": str(out_dir / "publisher_adapters.json"),
        "publisher_queue": str(
            publisher_queue or out_dir / "publisher_queue.csv"
        ),
        "run_manifest": str(workflow_state.manifest_path(out_dir)),
        "artifacts_index": str(artifact_index_path),
    }
    for format_name, output in (export_result.get("outputs") or {}).items():
        artifact_paths[f"export_{format_name}"] = str(
            output.get("path") or ""
        )
    if export_result.get("manifest"):
        artifact_paths["export_manifest"] = str(export_result["manifest"])
    next_actions = list(coverage_data.get("next_actions") or [])
    if final_status == "partial":
        next_actions.append(
            "resume_same_run_only_if_the_run_spec_is_unchanged"
        )
    outcome = RunOutcome(
        run_id=str(manifest.get("run_id") or ""),
        status=final_status
        if final_status in {"partial", "completed", "cancelled", "failed"}
        else "completed",
        quality=str(coverage_data.get("quality") or "inconclusive"),
        output_dir=str(out_dir),
        artifacts=artifact_paths,
        coverage=coverage_data,
        warnings=tuple(warnings),
        next_actions=tuple(dict.fromkeys(next_actions)),
    )
    outcome_path = outcome.write(out_dir / "run_outcome.json")
    artifact_paths["run_outcome"] = str(outcome_path)
    outcome = RunOutcome(
        run_id=outcome.run_id,
        status=outcome.status,
        quality=outcome.quality,
        output_dir=outcome.output_dir,
        artifacts=artifact_paths,
        coverage=outcome.coverage,
        warnings=outcome.warnings,
        next_actions=outcome.next_actions,
    )
    outcome.write(outcome_path)
    state_store = getattr(args, "state_store_instance", None)
    if isinstance(state_store, StateStore):
        state_store.complete_iteration(
            outcome.run_id,
            status=outcome.status,
            quality=outcome.quality,
        )
        state_store.record_outcome(outcome.to_dict())
    manifest["run_quality"] = outcome.quality
    manifest["run_outcome"] = str(outcome_path)
    workflow_state.write_manifest(out_dir, manifest)
    refresh_processing_report(out_dir, warnings=warnings)
    artifacts.write_index(out_dir, artifact_index_path)
    if isinstance(state_store, StateStore):
        index_data = json.loads(
            artifact_index_path.read_text(encoding="utf-8")
        )
        for record in index_data.get("artifacts", []):
            if record.get("exists"):
                state_store.record_artifact(
                    run_id=outcome.run_id,
                    name=str(record.get("name") or ""),
                    path=str(record.get("path") or ""),
                    sha256=str(record.get("sha256") or ""),
                )
    return {
        "status": final_status,
        "quality": outcome.quality,
        "run_id": outcome.run_id,
        "output_dir": str(out_dir),
        **artifact_paths,
    }
