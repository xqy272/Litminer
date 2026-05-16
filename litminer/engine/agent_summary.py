#!/usr/bin/env python3
"""Write a compact machine-readable summary for Agent decision making."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from litminer.engine.common import read_csv_rows, write_text_atomic
from litminer.engine import workflow_state
from litminer.engine import publisher_adapters


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


def _manifest_stages(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    stages = manifest.get("stages", [])
    return stages if isinstance(stages, list) else []


def _status_by_stage(manifest: dict[str, Any]) -> dict[str, str]:
    statuses: dict[str, str] = {}
    for stage in _manifest_stages(manifest):
        if isinstance(stage, dict) and stage.get("name"):
            statuses[str(stage["name"])] = str(stage.get("status") or "")
    return statuses


def _next_actions(summary: dict[str, Any]) -> list[str]:
    actions: list[str] = []
    trust = summary["trust_tiers"]
    provider_statuses = summary.get("provider_statuses", {})
    provider_status_classes = summary.get("provider_status_classes", {})
    if summary.get("partial"):
        actions.append("Resume the run with the same output_dir if the user request has not changed.")
    if provider_status_classes.get("rate_limited") or provider_statuses.get("skipped_rate_limit_cooldown"):
        actions.append("Review provider retry_after_seconds in api_discovery_trace.csv before retrying rate-limited sources.")
    if any(status not in {"ok", "empty_result"} for status in provider_statuses):
        actions.append("Inspect api_discovery_trace.csv before treating low counts as scientific absence.")
    if trust["discovered_or_deduped"] and not trust["crossref_trusted"]:
        actions.append("Run or resume Crossref verification before presenting bibliographic facts as verified.")
    if trust["publisher_queue"] and not trust["publisher_probe_checked"]:
        actions.append("Use publisher_queue.csv for page inspection; publisher access has not been probed.")
    if not trust["publisher_queue"]:
        actions.append("Review triaged_candidates.csv and constraints before broadening queries or relaxing filters.")
    if trust["metric_pass"] == 0 and summary.get("metric_statuses"):
        actions.append("Check journal metric coverage before enforcing metric thresholds.")
    return actions


def build_summary(output_dir: Path, warnings: list[str] | None = None) -> dict[str, Any]:
    paths = {
        "api_candidates": output_dir / "api_candidates.csv",
        "api_trace": output_dir / "api_discovery_trace.csv",
        "deduped_candidates": output_dir / "deduped_candidates.csv",
        "verified_candidates": output_dir / "verified_candidates.csv",
        "triaged_candidates": output_dir / "triaged_candidates.csv",
        "selected_candidates": output_dir / "selected_candidates.csv",
        "oa_annotated_candidates": output_dir / "oa_annotated_candidates.csv",
        "metrics_annotated_candidates": output_dir / "metrics_annotated_candidates.csv",
        "publisher_queue": output_dir / "publisher_queue.csv",
        "publisher_queue_probed": output_dir / "publisher_queue_probed.csv",
        "query_plan": output_dir / "query_plan.json",
        "field_provenance": output_dir / "field_provenance.json",
        "publisher_adapters": output_dir / "publisher_adapters.json",
        "processing_report": output_dir / "processing_report.md",
        "feasibility_report": output_dir / "feasibility_report.md",
        "run_manifest": workflow_state.manifest_path(output_dir),
    }
    rows = {name: read_rows(path) for name, path in paths.items() if path.suffix == ".csv"}
    manifest = workflow_state.load_manifest(output_dir)
    run_status = str(manifest.get("run_status") or ("completed" if manifest.get("completed_at") else "unknown"))
    stop_reason = str(manifest.get("stop_reason") or "")

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
        "stage_statuses": _status_by_stage(manifest),
        "trust_tiers": {
            "discovered_or_deduped": len(rows.get("deduped_candidates", []) or rows.get("api_candidates", [])),
            "crossref_trusted": crossref_trusted,
            "metric_pass": metric_pass,
            "publisher_queue": len(rows.get("publisher_queue", [])),
            "publisher_probe_checked": publisher_probe_checked,
        },
        "provider_statuses": count_values(rows.get("api_trace", []), "status"),
        "provider_status_classes": count_values(rows.get("api_trace", []), "status_class"),
        "provider_next_actions": count_values(rows.get("api_trace", []), "next_action"),
        "provider_counts": count_values(rows.get("api_trace", []), "provider"),
        "triage_priorities": count_values(rows.get("triaged_candidates", []), "triage_priority"),
        "candidate_statuses": count_values(rows.get("triaged_candidates", []), "candidate_status"),
        "metadata_statuses": count_values(rows.get("triaged_candidates", []), "metadata_status"),
        "metric_statuses": count_values(rows.get("metrics_annotated_candidates", []), "metric_filter_status"),
        "access_statuses": count_values(rows.get("publisher_queue_probed", []), "access_status"),
        "warnings": warnings or [],
        "publisher_adapters": publisher_adapters.adapter_rows(),
        "artifacts": {name: artifact(path) for name, path in paths.items()},
    }
    summary["next_actions"] = _next_actions(summary)
    return summary


def write_summary(output_dir: Path, warnings: list[str] | None = None, output_path: Path | None = None) -> Path:
    output = output_path or output_dir / SUMMARY_NAME
    summary = build_summary(output_dir, warnings=warnings)
    write_text_atomic(output, json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
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
