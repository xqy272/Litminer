#!/usr/bin/env python3
"""Human-readable search audit report for research reproducibility.

This module extends Litminer's honesty principle from the Agent surface
(``agent_summary.json``) to a human-readable surface (``search_audit_report.md``).
It is **not** a product positioning expansion — Litminer's user is still the
Agent. The audit report exists so a researcher can answer the question
"how did you find these papers?" to a colleague, using the same information
the Agent already has.

Design constraints (see iteration_plan.md §3.3):

- The audit report's information must be **consistent** with what the Agent
  receives via ``agent_summary.json`` and ``result_profile.json``. No
  "Agent knows but researcher doesn't" information gap.
- Format is natural-language Markdown, not JSON.
- This is the outermost Trust Tier: research-process auditability.
- It is **not** a "user group expansion to Agent + researcher" — it is
  the same honesty principle applied to a human-readable format.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from litminer.engine.common import read_csv_rows, write_text_atomic


AUDIT_NAME = "search_audit_report.md"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    _fields, rows = read_csv_rows(path)
    return rows


def _format_concepts(plan: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    concepts = plan.get("concepts") or {}
    for kind in ("required", "optional", "negative"):
        items = concepts.get(kind) or []
        if not items:
            continue
        lines.append(f"**{kind.title()} concepts:**")
        for item in items:
            if isinstance(item, dict):
                name = item.get("name", "")
                patterns = item.get("patterns") or item.get("all_of") or item.get("any_of") or []
                if isinstance(patterns, list):
                    pattern_str = ", ".join(str(p) for p in patterns)
                else:
                    pattern_str = str(patterns)
                lines.append(f"- {name}: {pattern_str}")
            elif isinstance(item, str):
                lines.append(f"- {item}")
        lines.append("")
    return lines


def _format_source_health(trace_rows: list[dict[str, str]]) -> list[str]:
    lines: list[str] = []
    if not trace_rows:
        lines.append("No discovery trace available.")
        lines.append("")
        return lines

    provider_stats: dict[str, dict[str, int]] = {}
    for row in trace_rows:
        provider = (row.get("provider") or "").strip() or "<blank>"
        status = (row.get("status") or "").strip() or "<blank>"
        if provider not in provider_stats:
            provider_stats[provider] = {}
        provider_stats[provider][status] = provider_stats[provider].get(status, 0) + 1

    lines.append("| Provider | Status | Count |")
    lines.append("|----------|--------|-------|")
    for provider in sorted(provider_stats):
        for status, count in sorted(provider_stats[provider].items(), key=lambda x: (-x[1], x[0])):
            lines.append(f"| {provider} | {status} | {count} |")
    lines.append("")
    return lines


def _format_exclusions(manifest: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    stages = manifest.get("stages") or []
    if isinstance(stages, list):
        for stage in stages:
            if not isinstance(stage, dict):
                continue
            name = str(stage.get("name") or "")
            status = str(stage.get("status") or "")
            message = str(stage.get("message") or "")
            if not name:
                continue
            line = f"- {name}: {status}"
            if message:
                line += f" ({message})"
            lines.append(line)
    lines.append("")
    return lines


def _format_completeness_caveats(caveats: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    caveat_text = caveats.get("caveat_text") or ""
    if caveat_text:
        lines.append(caveat_text)
        lines.append("")
        lines.append(
            "Note: these are search-process completeness signals (provider failures, rate limits). "
            "Litminer cannot claim result completeness (field coverage)."
        )
        lines.append("")
    return lines


def build_audit_report(
    output_dir: Path,
    output_path: Path | None = None,
) -> Path:
    """Build a human-readable audit report from run artifacts."""
    plan = _read_json(output_dir / "query_plan.json")
    summary = _read_json(output_dir / "agent_summary.json")
    profile = _read_json(output_dir / "result_profile.json")
    manifest = _read_json(output_dir / "run_manifest.json")
    coverage = _read_json(output_dir / "coverage_report.json")
    outcome = _read_json(output_dir / "run_outcome.json")
    export_manifest = _read_json(output_dir / "export_manifest.json")
    trace_rows = _read_csv(output_dir / "api_discovery_trace.csv")
    canonical_rows = _read_csv(output_dir / "canonical_papers.csv")

    lines: list[str] = [
        "# Search Audit Report",
        "",
        f"Output directory: `{output_dir}`",
        "",
        "This report documents the search strategy, source health, and exclusions "
        "for reproducibility. It is generated from the same artifacts the Agent reads "
        "(`agent_summary.json`, `query_plan.json`, `result_profile.json`, `run_manifest.json`).",
        "",
    ]

    # Queries
    queries = plan.get("queries") or []
    lines.append("## Search Strategy")
    lines.append("")
    if queries:
        lines.append("**Queries used:**")
        for i, query in enumerate(queries, 1):
            lines.append(f"{i}. `{query}`")
        lines.append("")
    year_from = plan.get("year_from")
    year_to = plan.get("year_to")
    if year_from or year_to:
        lines.append(f"**Year range:** {year_from or '*'} – {year_to or '*'}")
        lines.append("")

    # Concepts
    concept_lines = _format_concepts(plan)
    if concept_lines:
        lines.append("## Concept Configuration")
        lines.append("")
        lines.extend(concept_lines)

    # Sources
    sources = plan.get("sources") or plan.get("discovery_sources") or []
    if sources:
        lines.append("## Discovery Sources")
        lines.append("")
        if isinstance(sources, str):
            sources = [s.strip() for s in sources.split(",") if s.strip()]
        for source in sources:
            lines.append(f"- {source}")
        lines.append("")

    # Source health
    lines.append("## Source Health")
    lines.append("")
    lines.extend(_format_source_health(trace_rows))

    if coverage:
        lines.extend([
            "## Coverage Quality",
            "",
            f"- Run status: {outcome.get('status') or manifest.get('run_status') or 'unknown'}",
            f"- Retrieval quality: {coverage.get('quality', 'inconclusive')}",
            f"- Reason: {coverage.get('quality_reason', '')}",
            f"- Candidate count: {coverage.get('candidate_count', 0)}",
            "",
            "This quality label describes execution of configured providers. "
            "It is not an estimate of field-level recall or scientific completeness.",
            "",
        ])
        ledger = coverage.get("request_ledger") if isinstance(coverage.get("request_ledger"), dict) else {}
        if ledger:
            lines.extend([
                "### Provider Request Ledger",
                "",
                f"- Attempts and scheduler skips: {ledger.get('requests', 0)}",
                f"- Retries: {ledger.get('retries', 0)}",
                f"- Provider wait seconds: {ledger.get('wait_seconds', 0)}",
                "",
            ])

    # Exclusions / stage status
    lines.append("## Stage Status and Exclusions")
    lines.append("")
    lines.extend(_format_exclusions(manifest))

    # Trust tiers
    trust = summary.get("trust_tiers") or {}
    if trust:
        lines.append("## Trust Tiers")
        lines.append("")

    if canonical_rows:
        trusted = sum(1 for row in canonical_rows if (row.get("trusted_bibliography") or "").lower() == "true")
        eligible = sum(1 for row in canonical_rows if (row.get("export_eligible") or "").lower() == "true")
        lines.extend([
            "## Canonical Bibliography And Provenance",
            "",
            f"- Canonical papers: {len(canonical_rows)}",
            f"- Trusted bibliography: {trusted}",
            f"- Default export eligible: {eligible}",
            "- `canonical_provenance.json` records the selected source and reason for every canonical field.",
            "",
        ])

    if export_manifest:
        lines.extend([
            "## Export Audit",
            "",
            f"- Formats: {', '.join(export_manifest.get('formats') or [])}",
            f"- Exported rows: {export_manifest.get('exported_rows', 0)}",
            f"- Excluded rows: {export_manifest.get('excluded_rows', 0)}",
            f"- Unverified rows explicitly exported: {export_manifest.get('unverified_exported', 0)}",
            f"- Exclusion reasons: {json.dumps(export_manifest.get('excluded_reasons') or {}, ensure_ascii=False, sort_keys=True)}",
            "",
        ])
        lines.append(f"- Discovered/deduped: {trust.get('discovered_or_deduped', 0)}")
        lines.append(f"- Crossref-verified: {trust.get('crossref_trusted', 0)}")
        lines.append(f"- Metric-pass: {trust.get('metric_pass', 0)}")
        lines.append(f"- Publisher queue: {trust.get('publisher_queue', 0)}")
        lines.append("")
        lines.append(
            "Interpretation: discovery rows are candidates, not verified facts. "
            "Crossref-verified rows have bibliographic metadata support. "
            "Metric-pass rows only mean the local metric table matched. "
            "Publisher queue rows identify pages to inspect, not extracted evidence."
        )
        lines.append("")

    # Result profile summary
    if profile and not profile.get("degraded"):
        all_rows = profile.get("all_rows") or {}
        verified = profile.get("crossref_verified")
        lines.append("## Result Summary")
        lines.append("")
        lines.append(f"Total rows: {all_rows.get('total_rows', 0)}")
        if verified:
            lines.append(f"Crossref-verified rows: {(verified or {}).get('total_rows', 0)}")
        year_dist = all_rows.get("year_distribution") or {}
        if year_dist:
            lines.append("")
            lines.append("Year distribution:")
            for year, count in sorted(year_dist.items()):
                lines.append(f"- {year}: {count}")
        priority_dist = all_rows.get("triage_priority_distribution") or {}
        if priority_dist:
            lines.append("")
            lines.append("Triage priority distribution:")
            for priority, count in sorted(priority_dist.items(), key=lambda x: (-x[1], x[0])):
                lines.append(f"- {priority}: {count}")
        lines.append("")

    # Completeness caveats
    caveats = profile.get("completeness_caveats") if profile else None
    if caveats:
        lines.append("## Completeness Caveats")
        lines.append("")
        lines.extend(_format_completeness_caveats(caveats))

    # Run status
    # The manifest is the canonical final run state. Prefer it over a summary
    # that may have been written at an earlier resumable stage.
    run_status = manifest.get("run_status") or summary.get("run_status") or ""
    stop_reason = manifest.get("stop_reason") or summary.get("stop_reason") or ""
    if run_status:
        lines.append("## Run Status")
        lines.append("")
        lines.append(f"Status: {run_status}")
        if stop_reason:
            lines.append(f"Stop reason: {stop_reason}")
        lines.append("")

    lines.extend([
        "## How to Reproduce",
        "",
        "To reproduce this search:",
        "1. Use the same queries, year range, and concepts listed above.",
        "2. Run with the same `--mode` and source flags.",
        "3. Use `--resume` with the same `--output-dir` to reuse cached results.",
        "4. Compare `agent_summary.json` trust tiers to verify equivalent coverage.",
        "",
    ])

    output = output_path or output_dir / AUDIT_NAME
    write_text_atomic(output, "\n".join(lines) + "\n")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a human-readable search audit report.")
    parser.add_argument("--output-dir", type=Path, required=True, help="Litminer run output directory")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    path = build_audit_report(args.output_dir, args.output)
    print(f"Search audit report: {path}")


if __name__ == "__main__":
    main()
