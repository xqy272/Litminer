"""First-class discovery and verification coverage reporting."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from litminer.engine.common import read_csv_rows, utc_now, write_text_atomic
from litminer.runtime.state_store import StateStore


COVERAGE_NAME = "coverage_report.json"


def _rows(path: Path | None) -> list[dict[str, str]]:
    if path is None or not path.exists():
        return []
    _fields, rows = read_csv_rows(path)
    return rows


def _int(value: Any) -> int:
    try:
        return int(str(value or "0"))
    except ValueError:
        return 0


def build_coverage_report(
    *,
    configured_sources: list[str],
    query_count: int,
    trace_rows: list[dict[str, str]],
    candidate_count: int,
    input_mode: str,
    run_id: str = "",
    verification: dict[str, Any] | None = None,
    request_ledger: dict[str, Any] | None = None,
) -> dict[str, Any]:
    providers: list[dict[str, Any]] = []
    by_provider: dict[str, list[dict[str, str]]] = {}
    for row in trace_rows:
        by_provider.setdefault((row.get("provider") or "unknown").strip(), []).append(row)

    configured = list(dict.fromkeys(configured_sources))
    for provider in configured:
        rows = by_provider.get(provider, [])
        status_classes = Counter((row.get("status_class") or "unknown").strip() for row in rows)
        statuses = Counter((row.get("status") or "unknown").strip() for row in rows)
        successful = sum(1 for row in rows if (row.get("status_class") or "") in {"ok", "empty_or_missing"})
        returned = sum(_int(row.get("returned_count")) for row in rows)
        suppressed = sum(1 for row in rows if (row.get("status") or "").startswith("skipped"))
        failed = max(0, len(rows) - successful - suppressed)
        if not rows:
            contribution = "not_run"
        elif successful and not failed and not suppressed:
            contribution = "healthy"
        elif successful:
            contribution = "partial"
        else:
            contribution = "unavailable"
        next_actions = list(dict.fromkeys(
            row.get("next_action", "") for row in rows if row.get("next_action", "")
        ))
        providers.append({
            "provider": provider,
            "configured": True,
            "planned_queries": query_count,
            "trace_rows": len(rows),
            "successful_queries": successful,
            "failed_queries": failed,
            "suppressed_queries": suppressed,
            "candidate_count": returned,
            "status_classes": dict(sorted(status_classes.items())),
            "statuses": dict(sorted(statuses.items())),
            "coverage_contribution": contribution,
            "next_actions": next_actions,
        })

    if input_mode == "import" and not configured:
        quality = "healthy"
        reason = "Imported candidate input; discovery-source coverage is not applicable."
    else:
        healthy_or_partial = [row for row in providers if row["coverage_contribution"] in {"healthy", "partial"}]
        unavailable = [row for row in providers if row["coverage_contribution"] in {"unavailable", "not_run"}]
        if providers and not unavailable and all(row["coverage_contribution"] == "healthy" for row in providers):
            quality = "healthy"
            reason = "All configured discovery providers completed their planned calls without infrastructure failures."
        elif healthy_or_partial:
            quality = "degraded"
            reason = "At least one discovery path produced usable results, but one or more configured paths were partial or unavailable."
        elif providers:
            has_successful_empty = any(row["successful_queries"] for row in providers)
            if has_successful_empty:
                quality = "healthy"
                reason = "Configured providers executed successfully but returned no candidates for these queries."
            else:
                quality = "inconclusive"
                reason = "No configured discovery provider produced a usable query result."
        else:
            quality = "inconclusive" if input_mode != "import" else "healthy"
            reason = "No discovery coverage information was available."

    next_actions: list[str] = []
    if quality == "degraded":
        next_actions.append("inspect_provider_health_and_resume_failed_sources_without_discarding_existing_results")
    elif quality == "inconclusive":
        next_actions.append("restore_at_least_one_discovery_provider_before_interpreting_candidate_count")

    verification_data = dict(verification or {})
    return {
        "schema_version": 1,
        "generated_at": utc_now(),
        "run_id": run_id,
        "input_mode": input_mode,
        "quality": quality,
        "quality_reason": reason,
        "candidate_count": int(candidate_count),
        "query_count": int(query_count),
        "configured_sources": configured,
        "providers": providers,
        "discovery": {
            "healthy_provider_count": sum(1 for row in providers if row["coverage_contribution"] == "healthy"),
            "partial_provider_count": sum(1 for row in providers if row["coverage_contribution"] == "partial"),
            "unavailable_provider_count": sum(1 for row in providers if row["coverage_contribution"] in {"unavailable", "not_run"}),
        },
        "verification": verification_data,
        "request_ledger": request_ledger or {"enabled": False, "requests": 0},
        "next_actions": next_actions,
        "boundary": (
            "Coverage describes execution of configured retrieval and verification paths. "
            "It is not a field-level recall estimate."
        ),
    }


def write_coverage_report(
    output_dir: Path,
    *,
    configured_sources: list[str],
    query_count: int,
    candidate_count: int,
    input_mode: str,
    run_id: str = "",
    verification: dict[str, Any] | None = None,
    state_store: StateStore | None = None,
    trace_path: Path | None = None,
) -> Path:
    path = output_dir / COVERAGE_NAME
    trace_rows = _rows(trace_path or output_dir / "api_discovery_trace.csv")
    ledger = state_store.request_summary(run_id) if state_store is not None else None
    report = build_coverage_report(
        configured_sources=configured_sources,
        query_count=query_count,
        trace_rows=trace_rows,
        candidate_count=candidate_count,
        input_mode=input_mode,
        run_id=run_id,
        verification=verification,
        request_ledger=ledger,
    )
    write_text_atomic(path, json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    return path
