#!/usr/bin/env python3
"""Generate a compact processing report for Litminer CSV outputs.

The report is deterministic and LLM-facing: it summarizes source distribution,
metadata health, triage status, OA/access hints, and queue next actions so the
Agent does not need to scan large CSVs mechanically.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

from litminer.engine import artifacts
from litminer.engine import status_policy
from litminer.engine.common import normalize_doi, read_csv_rows, write_text_atomic

DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$", re.I)


def read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    _fieldnames, rows = read_csv_rows(path)
    return rows


def count_values(rows: list[dict[str, str]], field: str, empty_label: str = "empty") -> Counter[str]:
    counter: Counter[str] = Counter()
    for row in rows:
        value = (row.get(field) or "").strip() or empty_label
        counter[value] += 1
    return counter


def table(counter: Counter[str], limit: int = 12) -> list[str]:
    if not counter:
        return ["- none: 0"]
    return [f"- {key}: {value}" for key, value in counter.most_common(limit)]


def metadata_health(rows: list[dict[str, str]]) -> dict[str, int]:
    total = len(rows)
    with_doi = 0
    invalid_doi = 0
    with_title = 0
    with_year = 0
    with_journal = 0
    for row in rows:
        doi = normalize_doi(row.get("crossref_doi") or row.get("doi") or "")
        if doi:
            with_doi += 1
            if not DOI_RE.match(doi):
                invalid_doi += 1
        if (row.get("crossref_title") or row.get("title") or "").strip():
            with_title += 1
        if (row.get("crossref_year") or row.get("publication_year") or row.get("year") or "").strip():
            with_year += 1
        if (row.get("crossref_container") or row.get("journal") or "").strip():
            with_journal += 1
    return {
        "rows": total,
        "with_doi": with_doi,
        "missing_doi": total - with_doi,
        "invalid_doi": invalid_doi,
        "with_title": with_title,
        "missing_title": total - with_title,
        "with_year": with_year,
        "missing_year": total - with_year,
        "with_journal": with_journal,
        "missing_journal": total - with_journal,
    }


def read_manifest(output_dir: Path) -> dict:
    path = output_dir / "run_manifest.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def read_json_object(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def append_trust_summary(lines: list[str], rows: dict[str, list[dict[str, str]]]) -> None:
    discovered = len(rows["deduped"] or rows["api"])
    verified_rows = rows["verified"] or rows["oa"]
    trusted_crossref = sum(
        1
        for row in verified_rows
        if (row.get("crossref_status") or "").strip() in {"verified", "title_recovered"}
    )
    metric_pass = sum(
        1
        for row in rows["metrics"]
        if (row.get("metric_filter_status") or "").strip() == "pass"
    )
    queued = len(rows["queue"])
    probed = sum(1 for row in rows["probed"] if (row.get("publisher_probe_at") or "").strip())

    lines.extend([
        "## Trust Tiers",
        "",
        f"- discovered_or_deduped: {discovered}",
        f"- crossref_trusted: {trusted_crossref}",
        f"- metric_pass: {metric_pass}",
        f"- publisher_queue: {queued}",
        f"- publisher_probe_checked: {probed}",
        "",
        "Interpretation:",
        "- Discovery rows are candidates, not verified article facts.",
        "- Crossref trusted rows have bibliographic metadata support.",
        "- Metric-pass rows only mean the local verified metric table matched the journal threshold.",
        "- Publisher queue rows identify pages to inspect; they are not extracted full-text evidence.",
        "",
    ])


def append_health(lines: list[str], label: str, rows: list[dict[str, str]]) -> None:
    health = metadata_health(rows)
    lines.extend([
        f"### {label}",
        "",
        f"- rows: {health['rows']}",
        f"- DOI present: {health['with_doi']} / {health['rows']}",
        f"- DOI missing: {health['missing_doi']}",
        f"- DOI invalid format: {health['invalid_doi']}",
        f"- title missing: {health['missing_title']}",
        f"- year missing: {health['missing_year']}",
        f"- journal missing: {health['missing_journal']}",
        "",
    ])


def write_report(output_dir: Path, output_path: Path | None = None) -> Path:
    output_path = output_path or output_dir / "processing_report.md"
    paths = {
        "api": output_dir / "api_candidates.csv",
        "api_trace": output_dir / "api_discovery_trace.csv",
        "deduped": output_dir / "deduped_candidates.csv",
        "triaged": output_dir / "triaged_candidates.csv",
        "selected": output_dir / "selected_candidates.csv",
        "verified": output_dir / "verified_candidates.csv",
        "oa": output_dir / "oa_annotated_candidates.csv",
        "metrics": output_dir / "metrics_annotated_candidates.csv",
        "queue": output_dir / "publisher_queue.csv",
        "probed": output_dir / "publisher_queue_probed.csv",
        "query_plan": output_dir / "query_plan.json",
        "field_provenance": output_dir / "field_provenance.json",
        "publisher_adapters": output_dir / "publisher_adapters.json",
        "agent_summary": output_dir / "agent_summary.json",
        "artifacts_index": output_dir / artifacts.INDEX_NAME,
    }
    rows = {name: read_rows(path) if path.suffix == ".csv" else [] for name, path in paths.items()}
    manifest = read_manifest(output_dir)
    query_plan = read_json_object(paths["query_plan"])
    source_strategy = query_plan.get("source_strategy") if isinstance(query_plan.get("source_strategy"), dict) else {}

    candidate_rows = (
        rows["oa"] or rows["verified"] or rows["selected"] or
        rows["triaged"] or rows["deduped"] or rows["api"]
    )

    lines = [
        "# Litminer Processing Report",
        "",
        f"Output directory: `{output_dir}`",
        "",
        "## Stage Counts",
        "",
    ]
    for name in [
        "api", "api_trace", "deduped", "triaged", "selected", "verified",
        "oa", "metrics", "queue", "probed", "query_plan", "field_provenance",
        "publisher_adapters", "agent_summary",
        "artifacts_index",
    ]:
        path = paths[name]
        if path.exists():
            if path.suffix == ".csv":
                lines.append(f"- {path.name}: {len(rows[name])}")
            else:
                lines.append(f"- {path.name}: present")
    if manifest:
        lines.append(f"- run_manifest.json: {len(manifest.get('stages', []))} stage records")
        cache_config = manifest.get("cache") if isinstance(manifest.get("cache"), dict) else {}
        stages = manifest.get("stages") if isinstance(manifest.get("stages"), list) else []
        if stages:
            lines.extend(["", "## Stage Recovery Semantics", ""])
            for stage in stages[-12:]:
                if not isinstance(stage, dict):
                    continue
                name = str(stage.get("name") or "")
                status = str(stage.get("status") or "")
                if not name:
                    continue
                lines.append(
                    f"- {name}: {status} "
                    f"({status_policy.classify_status(status)}; {status_policy.next_action(status)})"
                )
            lines.append("")
        if cache_config:
            lines.extend([
                "## Cache And Recovery",
                "",
                f"- enabled: {cache_config.get('enabled')}",
                f"- cache_dir: `{cache_config.get('cache_dir', '')}`",
                f"- ttl_days: {cache_config.get('ttl_days')}",
                f"- provider_failure_ttl_seconds: {cache_config.get('provider_failure_ttl_seconds')}",
                "- Cache entries are local retrieval hints; they are not evidence and do not replace run artifacts.",
                "",
            ])
    if len(rows["api"]) and len(rows["deduped"]):
        lines.append(f"- duplicates_or_removed_before_dedupe: {max(0, len(rows['api']) - len(rows['deduped']))}")

    lines.append("")
    append_trust_summary(lines, rows)

    lines.extend(["", "## Source Distribution", ""])
    for field in ["discovery_provider", "discovery_source"]:
        counter = count_values(candidate_rows, field)
        if counter:
            lines.extend([f"### `{field}`", "", *table(counter), ""])

    if rows["api_trace"]:
        lines.extend(["## Discovery Trace Health", ""])
        for field in ["provider", "status", "status_class", "http_status", "transient_error", "cache_status", "next_action"]:
            lines.extend([f"### `{field}`", "", *table(count_values(rows["api_trace"], field)), ""])
        problem_rows = [
            row for row in rows["api_trace"]
            if (row.get("status") or "") not in {"ok"}
        ]
        if problem_rows:
            lines.extend(["### Non-OK Provider Calls", ""])
            for row in problem_rows[:12]:
                error = (row.get("error") or "").replace("\n", " ")[:160]
                lines.append(
                    f"- {row.get('provider', '')} {row.get('query_id', '')}: "
                    f"{row.get('status', '')}; returned={row.get('returned_count', '')}; error={error}"
                )
            lines.append("")

    lines.extend(["## Metadata Health", ""])
    if rows["deduped"]:
        append_health(lines, "Deduped candidates", rows["deduped"])
    if rows["verified"] or rows["oa"]:
        append_health(lines, "Verified/OA-annotated candidates", rows["oa"] or rows["verified"])
        crossref_rows = rows["verified"]
        if crossref_rows:
            lines.extend(["## Crossref Verification", ""])
            for field in ["crossref_status", "crossref_verified", "crossref_lookup_method", "crossref_cache_status"]:
                lines.extend([f"### `{field}`", "", *table(count_values(crossref_rows, field)), ""])

    if rows["triaged"]:
        lines.extend(["## Triage Summary", ""])
        for field in ["triage_priority", "metadata_status", "candidate_status", "llm_review_needed"]:
            lines.extend([f"### `{field}`", "", *table(count_values(rows["triaged"], field)), ""])

    access_rows = rows["probed"] or rows["queue"] or rows["oa"]
    if access_rows:
        lines.extend(["## Access And OA Hints", ""])
        for field in [
            "unpaywall_status", "unpaywall_cache_status", "is_oa", "oa_status",
            "access_status", "html_status", "pdf_status", "si_status",
        ]:
            counter = count_values(access_rows, field)
            if counter:
                lines.extend([f"### `{field}`", "", *table(counter), ""])

    if rows["queue"]:
        lines.extend(["## Queue Next Actions", ""])
        lines.extend(table(count_values(rows["queue"], "next_action"), limit=8))
        lines.append("")

    artifact_index = artifacts.build_index(output_dir)
    if artifact_index.get("by_tier"):
        lines.extend(["## Artifact Tiers", ""])
        for tier in ("primary", "supporting", "debug"):
            names = artifact_index.get("by_tier", {}).get(tier, [])
            if names:
                lines.append(f"- {tier}: {', '.join(str(name) for name in names)}")
        lines.append("")

    if (
        paths["query_plan"].exists()
        or paths["field_provenance"].exists()
        or paths["publisher_adapters"].exists()
        or paths["artifacts_index"].exists()
    ):
        lines.extend(["## Agent Control Artifacts", ""])
        if paths["artifacts_index"].exists():
            lines.append("- `artifacts_index.json`: compact map of primary, supporting, and debug artifacts.")
        if paths["query_plan"].exists():
            lines.append("- `query_plan.json`: runtime query/source/concept plan derived by the Agent.")
        if paths["field_provenance"].exists():
            lines.append("- `field_provenance.json`: field-level source/trust map for queued or probed rows.")
        if paths["publisher_adapters"].exists():
            lines.append("- `publisher_adapters.json`: built-in and external publisher-inspection adapter boundary.")
        lines.append("")

    if source_strategy:
        lines.extend(["## Source Strategy", ""])
        selected = source_strategy.get("selected_sources") or []
        recommended = source_strategy.get("recommended_sources") or []
        missing = source_strategy.get("missing_recommended_sources") or []
        risk_flags = source_strategy.get("risk_flags") or []
        if selected:
            lines.append(f"- selected_sources: {', '.join(str(item) for item in selected)}")
        if recommended:
            lines.append(f"- recommended_sources: {', '.join(str(item) for item in recommended)}")
        if missing:
            lines.append(f"- missing_recommended_sources: {', '.join(str(item) for item in missing)}")
        if risk_flags:
            lines.append(f"- risk_flags: {', '.join(str(item) for item in risk_flags)}")
        lines.append("")

    lines.extend([
        "## Agent Guidance",
        "",
        "- Start review from `triaged_candidates.csv`; it preserves rows and exposes semantic/metadata flags.",
        "- Use this report to choose where mechanical cleanup is needed before deep reading.",
        "- Treat OA/PDF URLs as access hints, not article-level evidence.",
        "- Use `publisher_queue.csv` for page inspection tasks and `publisher_queue_probed.csv` when access probing was enabled.",
        "",
    ])

    write_text_atomic(output_path, "\n".join(lines))
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a Litminer processing report.")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    path = write_report(args.output_dir, args.output)
    print(f"Processing report: {path}")


if __name__ == "__main__":
    main()
