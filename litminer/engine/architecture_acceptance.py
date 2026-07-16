"""Deterministic acceptance probes for next-generation Agent contracts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable

from litminer.engine.common import write_csv_atomic, write_text_atomic
from litminer.evidence.coverage import build_coverage_report
from litminer.exporters.exporter import export_bibliography
from litminer.runtime.provider_runtime import ProviderRuntime
from litminer.runtime.provider_scheduler import ProviderScheduler
from litminer.runtime.state_store import StateStore
from litminer.sources.mcp import server as mcp_server


def _write(path: Path, payload: dict[str, Any]) -> Path:
    write_text_atomic(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    return path


def degraded_coverage(output_dir: Path) -> dict[str, Any]:
    report = build_coverage_report(
        configured_sources=["openalex", "semantic_scholar", "arxiv", "europe_pmc"],
        query_count=1, candidate_count=2, input_mode="discover",
        trace_rows=[
            {"provider": "openalex", "status": "ok", "status_class": "ok", "returned_count": "2"},
            {"provider": "semantic_scholar", "status": "empty_result", "status_class": "empty_or_missing", "returned_count": "0"},
            {"provider": "arxiv", "status": "network_error", "status_class": "network", "returned_count": "0"},
            {"provider": "europe_pmc", "status": "rate_limited", "status_class": "rate_limited", "returned_count": "0"},
        ],
    )
    _write(output_dir / "coverage_report.json", report)
    return {"scenario": "degraded_coverage", "passed": report["quality"] == "degraded", "quality": report["quality"]}


def inconclusive_coverage(output_dir: Path) -> dict[str, Any]:
    report = build_coverage_report(
        configured_sources=["openalex", "arxiv"], query_count=1, candidate_count=0,
        input_mode="discover", trace_rows=[
            {"provider": "openalex", "status": "tls_error", "status_class": "tls", "returned_count": "0"},
            {"provider": "arxiv", "status": "network_error", "status_class": "network", "returned_count": "0"},
        ],
    )
    _write(output_dir / "coverage_report.json", report)
    return {"scenario": "inconclusive_coverage", "passed": report["quality"] == "inconclusive", "quality": report["quality"]}


def persisted_cooldown(output_dir: Path) -> dict[str, Any]:
    state_path = output_dir / "state.sqlite3"
    ProviderScheduler(StateStore(state_path)).record("openalex", status_class="rate_limited", retry_after_seconds=60)
    runtime = ProviderRuntime(StateStore(state_path), run_id="acceptance-second-run")
    blocked = False
    try:
        runtime.execute("openalex", "search", "acceptance", lambda: [])
    except Exception:
        blocked = True
    ledger = StateStore(state_path).request_summary("acceptance-second-run")
    _write(output_dir / "request_ledger.json", ledger)
    return {"scenario": "persisted_cooldown", "passed": blocked and ledger.get("requests") == 1, "blocked": blocked, "ledger": ledger}


def invalid_mcp_input(output_dir: Path) -> dict[str, Any]:
    response = mcp_server.handle_request({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "litminer_start_run", "arguments": {}},
    }) or {}
    _write(output_dir / "mcp_response.json", response)
    result = response.get("result") or {}
    error = (result.get("structuredContent") or {}).get("error") or {}
    return {"scenario": "invalid_mcp_input", "passed": result.get("isError") is True and error.get("class") == "validation", "error_class": error.get("class")}


def export_exclusion(output_dir: Path) -> dict[str, Any]:
    input_csv = output_dir / "canonical_papers.csv"
    write_csv_atomic([
        {"paper_id": "p1", "entry_type": "article", "title": "Trusted", "trusted_bibliography": "true", "export_eligible": "true", "retraction_status": "unknown"},
        {"paper_id": "p2", "entry_type": "article", "title": "Unverified", "trusted_bibliography": "false", "export_eligible": "false", "retraction_status": "unknown"},
        {"paper_id": "p3", "entry_type": "article", "title": "Retracted", "trusted_bibliography": "true", "export_eligible": "false", "retraction_status": "retracted"},
    ], input_csv)
    manifest = export_bibliography(input_csv, output_dir, formats=["ris", "bibtex"])
    return {"scenario": "export_exclusion", "passed": manifest["exported_rows"] == 1 and manifest["excluded_rows"] == 2, "exported_rows": manifest["exported_rows"], "excluded_reasons": manifest["excluded_reasons"]}


SCENARIOS: dict[str, Callable[[Path], dict[str, Any]]] = {
    "degraded_coverage": degraded_coverage,
    "inconclusive_coverage": inconclusive_coverage,
    "persisted_cooldown": persisted_cooldown,
    "invalid_mcp_input": invalid_mcp_input,
    "export_exclusion": export_exclusion,
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    result = SCENARIOS[args.scenario](args.output_dir)
    _write(args.output_dir / "acceptance_result.json", result)
    print(json.dumps(result, ensure_ascii=False))
    if not result.get("passed"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
