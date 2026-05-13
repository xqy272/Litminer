#!/usr/bin/env python3
"""Generate a compact processing report for Litminer CSV outputs.

The report is deterministic and LLM-facing: it summarizes source distribution,
metadata health, triage status, OA/access hints, and queue next actions so the
Agent does not need to scan large CSVs mechanically.
"""

from __future__ import annotations

import argparse
import csv
import re
from collections import Counter
from pathlib import Path


DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$", re.I)


def read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader)


def normalize_doi(value: str) -> str:
    value = (value or "").strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "doi:"):
        if value.startswith(prefix):
            value = value[len(prefix):]
    return value.strip().rstrip(".,;)[]")


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
    }
    rows = {name: read_rows(path) for name, path in paths.items()}

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
    for name in ["api", "api_trace", "deduped", "triaged", "selected", "verified", "oa", "metrics", "queue", "probed"]:
        path = paths[name]
        if path.exists():
            lines.append(f"- {path.name}: {len(rows[name])}")
    if len(rows["api"]) and len(rows["deduped"]):
        lines.append(f"- duplicates_or_removed_before_dedupe: {max(0, len(rows['api']) - len(rows['deduped']))}")

    lines.extend(["", "## Source Distribution", ""])
    for field in ["discovery_provider", "discovery_source"]:
        counter = count_values(candidate_rows, field)
        if counter:
            lines.extend([f"### `{field}`", "", *table(counter), ""])

    if rows["api_trace"]:
        lines.extend(["## Discovery Trace Health", ""])
        for field in ["provider", "status"]:
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
            for field in ["crossref_status", "crossref_verified", "crossref_lookup_method"]:
                lines.extend([f"### `{field}`", "", *table(count_values(crossref_rows, field)), ""])

    if rows["triaged"]:
        lines.extend(["## Triage Summary", ""])
        for field in ["triage_priority", "metadata_status", "candidate_status", "llm_review_needed"]:
            lines.extend([f"### `{field}`", "", *table(count_values(rows["triaged"], field)), ""])

    access_rows = rows["probed"] or rows["queue"] or rows["oa"]
    if access_rows:
        lines.extend(["## Access And OA Hints", ""])
        for field in ["unpaywall_status", "is_oa", "oa_status", "access_status", "html_status", "pdf_status", "si_status"]:
            counter = count_values(access_rows, field)
            if counter:
                lines.extend([f"### `{field}`", "", *table(counter), ""])

    if rows["queue"]:
        lines.extend(["## Queue Next Actions", ""])
        lines.extend(table(count_values(rows["queue"], "next_action"), limit=8))
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

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")
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
