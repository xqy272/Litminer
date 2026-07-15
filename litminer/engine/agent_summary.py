#!/usr/bin/env python3
"""Write a compact machine-readable summary for Agent decision making."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from litminer.engine.common import read_csv_rows, write_text_atomic
from litminer.engine import artifacts as artifact_index
from litminer.engine import concept_diagnostics
from litminer.engine import result_profile
from litminer.engine import research_session
from litminer.engine import workflow_state
from litminer.engine import publisher_adapters
from litminer.engine import status_policy


SUMMARY_NAME = "agent_summary.json"


def read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    _fieldnames, rows = read_csv_rows(path)
    return rows


def count_values(rows: list[dict[str, str]], field: str) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        counts[(row.get(field) or "").strip() or "<blank>"] += 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def artifact(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "exists": path.exists(),
        "rows": workflow_state.row_count(path) if path.suffix == ".csv" else 0,
        "sha256": workflow_state.file_sha256(path),
    }


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _manifest_stages(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    stages = manifest.get("stages", [])
    return stages if isinstance(stages, list) else []


def _status_by_stage(manifest: dict[str, Any]) -> dict[str, str]:
    statuses: dict[str, str] = {}
    for stage in _manifest_stages(manifest):
        if isinstance(stage, dict) and stage.get("name"):
            statuses[str(stage["name"])] = str(stage.get("status") or "")
    return statuses


def _status_classes(statuses: dict[str, str]) -> dict[str, str]:
    return {name: status_policy.classify_status(status) for name, status in statuses.items()}


def _status_next_actions(statuses: dict[str, str]) -> dict[str, str]:
    return {name: status_policy.next_action(status) for name, status in statuses.items()}


def _capability_status(stage_status: str) -> str:
    normalized = (stage_status or "").strip().lower()
    if not normalized or normalized.startswith("skipped_disabled"):
        return "not_run"
    status_class = status_policy.classify_status(normalized)
    if status_class == "ok" or normalized == "skipped_existing":
        return "completed"
    if status_class in {"budget_limited", "partial", "rate_limited", "network", "auth", "error"}:
        return "partial"
    if status_class == "skipped":
        return "not_run"
    return status_class


def _next_actions(summary: dict[str, Any]) -> list[str]:
    actions: list[str] = []
    trust = summary["trust_tiers"]
    provider_statuses = summary.get("provider_statuses", {})
    provider_status_classes = summary.get("provider_status_classes", {})
    source_strategy = summary.get("source_strategy", {})
    concept_summary = summary.get("concept_diagnostics")
    cache = summary.get("cache", {}) if isinstance(summary.get("cache"), dict) else {}
    if summary.get("partial"):
        actions.append("Resume the run with the same output_dir if the user request has not changed.")
    if provider_status_classes.get("rate_limited") or provider_statuses.get("skipped_rate_limit_cooldown"):
        actions.append(
            "Review provider retry_after_seconds in api_discovery_trace.csv "
            "before retrying rate-limited sources."
        )
    if provider_statuses.get("skipped_cached_provider_failure"):
        actions.append("Wait for provider failure cache TTL or rerun with --no-cache after fixing the environment.")
    if any(status not in {"ok", "empty_result"} for status in provider_statuses):
        actions.append("Inspect api_discovery_trace.csv before treating low counts as scientific absence.")
    if trust["discovered_or_deduped"] and not trust["bibliographically_verified"]:
        actions.append("Run or resume Crossref verification before presenting bibliographic facts as verified.")
    if trust["publisher_queue"] and not trust["publisher_probe_checked"]:
        actions.append("Use publisher_queue.csv for page inspection; publisher access has not been probed.")
    if not trust["publisher_queue"]:
        actions.append("Review triaged_candidates.csv and constraints before broadening queries or relaxing filters.")
    if trust["metric_pass"] == 0 and summary.get("metric_statuses"):
        actions.append("Check journal metric coverage before enforcing metric thresholds.")
    if isinstance(source_strategy, dict):
        missing_sources = source_strategy.get("missing_recommended_sources") or []
        if missing_sources:
            actions.append(
                "Consider an additional discovery pass for recommended sources not selected: "
                + ", ".join(str(source) for source in missing_sources)
                + "."
            )
        risk_flags = set(source_strategy.get("risk_flags") or [])
        if "single_query_low_recall_risk" in risk_flags:
            actions.append("Consider adding focused synonym or mechanism queries before treating recall as complete.")
    if isinstance(concept_summary, dict) and concept_summary.get("warnings"):
        actions.append(
            "Inspect concept_diagnostics.json before treating high priority as precise; "
            "one or more required concepts have low selectivity or zero matches."
        )
    provider_cache = cache.get("provider_failure_cache_statuses", {}) if isinstance(cache, dict) else {}
    if isinstance(provider_cache, dict) and provider_cache.get("store"):
        actions.append(
            "Provider failure cache stored a transient failure; resume later before repeating broad discovery."
        )
    return actions


def build_summary(output_dir: Path, warnings: list[str] | None = None) -> dict[str, Any]:
    paths = {
        "api_candidates": output_dir / "api_candidates.csv",
        "api_discovery_trace": output_dir / "api_discovery_trace.csv",
        "deduped_candidates": output_dir / "deduped_candidates.csv",
        "pretriaged_candidates": output_dir / "pretriaged_candidates.csv",
        "verification_queue": output_dir / "verification_queue.csv",
        "verified_candidates": output_dir / "verified_candidates.csv",
        "triaged_candidates": output_dir / "triaged_candidates.csv",
        "selected_candidates": output_dir / "selected_candidates.csv",
        "oa_annotated_candidates": output_dir / "oa_annotated_candidates.csv",
        "metrics_annotated_candidates": output_dir / "metrics_annotated_candidates.csv",
        "publisher_queue": output_dir / "publisher_queue.csv",
        "publisher_queue_probed": output_dir / "publisher_queue_probed.csv",
        "publisher_queue_html_meta": output_dir / "publisher_queue_html_meta.csv",
        "query_plan": output_dir / "query_plan.json",
        "field_provenance": output_dir / "field_provenance.json",
        "publisher_adapters": output_dir / "publisher_adapters.json",
        "processing_report": output_dir / "processing_report.md",
        "feasibility_report": output_dir / "feasibility_report.md",
        "run_manifest": workflow_state.manifest_path(output_dir),
        "artifacts_index": output_dir / artifact_index.INDEX_NAME,
        "result_profile": output_dir / result_profile.PROFILE_NAME,
        "concept_diagnostics": output_dir / concept_diagnostics.DIAGNOSTICS_NAME,
        "delta_profile": output_dir / research_session.DELTA_NAME,
        "research_session_manifest": output_dir / research_session.SESSION_NAME,
    }
    rows = {name: read_rows(path) for name, path in paths.items() if path.suffix == ".csv"}
    manifest = workflow_state.load_manifest(output_dir)
    plan = read_json(paths["query_plan"])
    source_strategy = plan.get("source_strategy") if isinstance(plan.get("source_strategy"), dict) else {}
    run_status = str(manifest.get("run_status") or ("completed" if manifest.get("completed_at") else "unknown"))
    stop_reason = str(manifest.get("stop_reason") or "")
    artifact_inventory = artifact_index.build_index(output_dir)
    stage_statuses = _status_by_stage(manifest)
    capability_statuses = {
        name: _capability_status(stage_statuses.get(name, ""))
        for name in ("crossref", "unpaywall", "metrics", "publisher_probe", "publisher_html_extract")
    }

    verified_rows = rows.get("verified_candidates", []) or rows.get("oa_annotated_candidates", [])
    crossref_trusted = sum(
        1
        for row in verified_rows
        if (row.get("crossref_status") or "").strip() in {"verified", "title_recovered"}
    )
    metric_pass = sum(
        1
        for row in rows.get("metrics_annotated_candidates", [])
        if (row.get("metric_filter_status") or "").strip() == "pass"
    )
    publisher_probe_checked = sum(
        1
        for row in rows.get("publisher_queue_probed", [])
        if (row.get("publisher_probe_at") or "").strip()
    )

    summary: dict[str, Any] = {
        "schema_version": 1,
        "output_dir": str(output_dir),
        "run_id": manifest.get("run_id", ""),
        "mode": manifest.get("mode", ""),
        "run_status": run_status,
        "partial": run_status == "partial",
        "stop_reason": stop_reason,
        "status_reason": stop_reason or run_status,
        "completed_at": manifest.get("completed_at", ""),
        "resume_enabled": bool(manifest.get("resume_enabled", False)),
        "stage_statuses": stage_statuses,
        "stage_status_classes": _status_classes(stage_statuses),
        "stage_next_actions": _status_next_actions(stage_statuses),
        "capability_statuses": capability_statuses,
        "trust_tiers": {
            "discovered_or_deduped": len(rows.get("deduped_candidates") if rows.get("deduped_candidates") is not None else rows.get("api_candidates", [])),
            "bibliographically_verified": crossref_trusted,
            "crossref_trusted": crossref_trusted,
            "metric_pass": metric_pass,
            "publisher_queue": len(rows.get("publisher_queue", [])),
            "publisher_probe_checked": publisher_probe_checked,
        },
        "trust_tier_statuses": {
            "bibliographic_verification": {
                "status": capability_statuses["crossref"],
                "count": crossref_trusted,
            },
            "journal_metrics": {
                "status": capability_statuses["metrics"],
                "count": metric_pass,
            },
            "publisher_probe": {
                "status": capability_statuses["publisher_probe"],
                "count": publisher_probe_checked,
            },
        },
        "provider_statuses": count_values(rows.get("api_discovery_trace", []), "status"),
        "provider_status_classes": count_values(rows.get("api_discovery_trace", []), "status_class"),
        "provider_http_statuses": count_values(rows.get("api_discovery_trace", []), "http_status"),
        "provider_transient_errors": count_values(rows.get("api_discovery_trace", []), "transient_error"),
        "provider_next_actions": count_values(rows.get("api_discovery_trace", []), "next_action"),
        "provider_counts": count_values(rows.get("api_discovery_trace", []), "provider"),
        "cache": {
            "config": manifest.get("cache", {}),
            "crossref_cache_statuses": count_values(rows.get("verified_candidates", []), "crossref_cache_status"),
            "unpaywall_cache_statuses": count_values(rows.get("oa_annotated_candidates", []), "unpaywall_cache_status"),
            "provider_failure_cache_statuses": count_values(rows.get("api_discovery_trace", []), "cache_status"),
        },
        "triage_priorities": count_values(rows.get("triaged_candidates", []), "triage_priority"),
        "verification_lanes": count_values(rows.get("verification_queue", []), "verification_lane"),
        "candidate_statuses": count_values(rows.get("triaged_candidates", []), "candidate_status"),
        "workflow_statuses": count_values(rows.get("triaged_candidates", []), "workflow_status"),
        "bibliographic_statuses": count_values(rows.get("triaged_candidates", []), "bibliographic_status"),
        "scientific_review_statuses": count_values(rows.get("triaged_candidates", []), "scientific_review_needed"),
        "metadata_statuses": count_values(rows.get("triaged_candidates", []), "metadata_status"),
        "metric_statuses": count_values(rows.get("metrics_annotated_candidates", []), "metric_filter_status"),
        "access_statuses": count_values(rows.get("publisher_queue_probed", []), "access_status"),
        "retraction_statuses": count_values(rows.get("verified_candidates", []), "retraction_status"),
        "html_meta_statuses": count_values(rows.get("publisher_queue_html_meta", []), "html_meta_status"),
        "warnings": warnings or [],
        "source_strategy": source_strategy,
        "artifact_tiers": artifact_inventory.get("by_tier", {}),
        "artifact_read_order": artifact_inventory.get("read_order", []),
        "primary_artifacts": artifact_inventory.get("by_tier", {}).get("primary", []),
        "publisher_adapters": publisher_adapters.adapter_rows(),
        "artifacts": artifact_inventory.get("artifacts_by_name", {}),
        "artifacts_index_path": str(paths["artifacts_index"]),
        "result_profile_path": str(paths["result_profile"]),
    }

    profile_path = paths["result_profile"]
    if profile_path.exists():
        try:
            full_profile = json.loads(profile_path.read_text(encoding="utf-8"))
            summary["result_profile"] = result_profile.to_summary_dict(full_profile)
        except (json.JSONDecodeError, ValueError):
            summary["result_profile"] = {"error": "result_profile.json could not be parsed"}
    else:
        summary["result_profile"] = None

    diagnostics_path = paths["concept_diagnostics"]
    if diagnostics_path.exists():
        diagnostics = read_json(diagnostics_path)
        summary["concept_diagnostics"] = concept_diagnostics.to_summary(diagnostics)
    else:
        summary["concept_diagnostics"] = None

    delta_path = paths["delta_profile"]
    if delta_path.exists():
        delta = read_json(delta_path)
        expected_iteration = str(
            (plan.get("run_controls") or {}).get("session_iteration_id") or ""
        )
        if expected_iteration and delta.get("iteration_id") != expected_iteration:
            summary["delta_profile"] = {
                "status": "stale_previous_iteration",
                "expected_iteration_id": expected_iteration,
                "available_iteration_id": delta.get("iteration_id", ""),
            }
        else:
            summary["delta_profile"] = delta
    else:
        summary["delta_profile"] = None

    summary["next_actions"] = _next_actions(summary)
    return summary


def write_summary(output_dir: Path, warnings: list[str] | None = None, output_path: Path | None = None) -> Path:
    output = output_path or output_dir / SUMMARY_NAME
    summary = build_summary(output_dir, warnings=warnings)
    write_text_atomic(output, json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    refreshed = build_summary(output_dir, warnings=warnings)
    if refreshed.get("artifacts") != summary.get("artifacts"):
        write_text_atomic(output, json.dumps(refreshed, indent=2, ensure_ascii=False) + "\n")
    return output


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Generate a Litminer Agent summary JSON.")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    path = write_summary(args.output_dir, output_path=args.output)
    print(f"Agent summary: {path}")


if __name__ == "__main__":
    main()
