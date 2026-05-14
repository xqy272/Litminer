#!/usr/bin/env python3
"""Offline end-to-end smoke test for a Litminer installation.

This command uses an embedded CSV fixture and disables network-backed stages.
It verifies that package imports, CSV processing, triage, queue generation, and
report writing work after installation.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from litminer.engine import run_lit_search
from litminer.engine import workspace
from litminer.engine.common import write_csv_atomic


FIXTURE_ROWS = [
    {
        "title": "Machine learning accelerates enzyme stability screening",
        "doi": "10.1234/litminer.001",
        "publication_year": "2026",
        "journal": "Journal of Research Methods",
        "abstract": (
            "This article reports an automated workflow for enzyme stability "
            "screening with benchmark data and external validation."
        ),
        "article_type": "original_research",
        "discovery_source": "offline_fixture",
    },
    {
        "title": "Automated enzyme stability screening with external validation",
        "doi": "10.1234/litminer.001",
        "publication_year": "2026",
        "journal": "Journal of Research Methods",
        "abstract": (
            "The study describes machine learning assisted screening of enzyme "
            "variants and reports validated stability measurements."
        ),
        "article_type": "original_research",
        "discovery_source": "offline_fixture_duplicate",
    },
    {
        "title": "Review of machine learning for enzyme engineering",
        "doi": "10.1234/litminer.002",
        "publication_year": "2025",
        "journal": "Biotechnology Reviews",
        "abstract": "This review summarizes recent machine learning approaches for enzyme engineering.",
        "article_type": "review",
        "discovery_source": "offline_fixture",
    },
]


def write_fixture(path: Path) -> None:
    fieldnames = list(FIXTURE_ROWS[0].keys())
    write_csv_atomic(FIXTURE_ROWS, path, fieldnames=fieldnames)


def read_count(path: Path) -> int:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return sum(1 for _row in csv.DictReader(handle))


def run(output_dir: Path) -> dict[str, str | int]:
    output_dir.mkdir(parents=True, exist_ok=True)
    input_csv = output_dir / "offline_fixture.csv"
    write_fixture(input_csv)

    args = argparse.Namespace(
        input_csv=input_csv,
        query=None,
        query_file=None,
        year_from=2025,
        year_to=None,
        output_dir=output_dir,
        config=None,
        triage_profile=None,
        required_concept=["validation=external validation|validated stability"],
        optional_concept=["benchmark=benchmark data|screening"],
        negative_concept=["review=review article|review"],
        exclude_article_type=["review"],
        queue_priorities="high,medium,needs_review",
        include_metadata_blocked=False,
        fields_needed=["external validation", "benchmark data", "screening workflow"],
        page_required_field=["abstract", "methods", "results"],
        openalex_api_key=None,
        openalex_mailto=None,
        discovery_sources="openalex",
        max_results_per_query=100,
        skip_openalex=True,
        include_semantic_scholar=False,
        include_arxiv=False,
        include_europe_pmc=False,
        semantic_query_limit=3,
        semantic_max_results=50,
        skip_crossref=True,
        enrich_unpaywall=False,
        skip_unpaywall=True,
        unpaywall_email=None,
        unpaywall_sleep=0,
        metrics=None,
        min_if=None,
        target_count=None,
        queue_strict_only=False,
        allow_missing_doi=False,
        screenshot_root=output_dir / "screenshots",
        probe_publishers=False,
        probe_limit=None,
        probe_sleep=0,
    )

    result = run_lit_search.run(args)
    required_files = [
        output_dir / "deduped_candidates.csv",
        output_dir / "triaged_candidates.csv",
        output_dir / "selected_candidates.csv",
        output_dir / "publisher_queue.csv",
        output_dir / "feasibility_report.md",
        output_dir / "processing_report.md",
    ]
    missing = [str(path) for path in required_files if not path.exists()]
    if missing:
        raise RuntimeError("offline smoke missing expected outputs: " + ", ".join(missing))

    queue_count = read_count(output_dir / "publisher_queue.csv")
    if queue_count < 1:
        raise RuntimeError("offline smoke expected at least one publisher queue row")

    return {
        **result,
        "input_csv": str(input_csv),
        "publisher_queue_rows": queue_count,
        "triaged_rows": read_count(output_dir / "triaged_candidates.csv"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Litminer offline smoke test without network access.")
    parser.add_argument("--output-dir", type=Path, default=workspace.resolve_workspace_path(workspace.DEFAULT_SMOKE_DIR))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        result = run(args.output_dir)
    except Exception as exc:
        print(f"Litminer offline smoke failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Litminer offline smoke passed: {result['output_dir']}")
        print(f"Publisher queue rows: {result['publisher_queue_rows']}")
        print(f"Processing report: {result['processing_report']}")


if __name__ == "__main__":
    main()
