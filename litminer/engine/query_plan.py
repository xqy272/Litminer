#!/usr/bin/env python3
"""Generate a lightweight, machine-readable query plan for Agent runs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from litminer.engine.common import write_text_atomic
from litminer.engine import source_strategy


PLAN_NAME = "query_plan.json"


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _source_rationale(sources: list[str]) -> dict[str, str]:
    rationale = {}
    for source in sources:
        if source == "openalex":
            rationale[source] = "broad scholarly API discovery"
        elif source == "semantic_scholar":
            rationale[source] = "semantic recall booster; higher rate-limit risk"
        elif source == "arxiv":
            rationale[source] = "preprint discovery for domains where preprints matter"
        elif source == "europe_pmc":
            rationale[source] = "biomedical/life-science metadata and full-text-link discovery"
        else:
            rationale[source] = "caller-selected discovery source"
    return rationale


def build_plan(
    *,
    queries: list[str],
    year_from: int | None = None,
    year_to: int | None = None,
    required_concepts: list[str] | None = None,
    optional_concepts: list[str] | None = None,
    negative_concepts: list[str] | None = None,
    discovery_sources: list[str] | None = None,
    mode: str = "",
    controls: dict[str, Any] | None = None,
) -> dict[str, Any]:
    sources = _as_list(discovery_sources)
    controls = controls or {}
    return {
        "schema_version": 1,
        "mode": mode,
        "queries": _as_list(queries),
        "query_count": len(_as_list(queries)),
        "year_range": {
            "from": year_from,
            "to": year_to,
        },
        "concepts": {
            "required": _as_list(required_concepts),
            "optional": _as_list(optional_concepts),
            "negative": _as_list(negative_concepts),
        },
        "discovery_sources": sources,
        "source_rationale": _source_rationale(sources),
        "source_strategy": source_strategy.build_strategy(
            queries=_as_list(queries),
            selected_sources=sources,
            required_concepts=_as_list(required_concepts),
            optional_concepts=_as_list(optional_concepts),
            negative_concepts=_as_list(negative_concepts),
            year_from=year_from,
            mode=mode,
            controls=controls,
        ),
        "run_controls": controls,
        "agent_notes": [
            "Queries and concepts are runtime intent derived by the Agent, not global Litminer defaults.",
            "Required concepts are triage signals; Litminer tags and ranks but does not make final scientific judgement.",
            "Negative concepts are review signals unless a downstream hard filter explicitly applies them.",
        ],
    }


def write_plan(output_dir: Path, plan: dict[str, Any], output_path: Path | None = None) -> Path:
    path = output_path or output_dir / PLAN_NAME
    write_text_atomic(path, json.dumps(plan, indent=2, ensure_ascii=False) + "\n")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Write a Litminer query plan JSON.")
    parser.add_argument("--query", action="append", default=[])
    parser.add_argument("--year-from", type=int, default=None)
    parser.add_argument("--year-to", type=int, default=None)
    parser.add_argument("--required-concept", action="append", default=[])
    parser.add_argument("--optional-concept", action="append", default=[])
    parser.add_argument("--negative-concept", action="append", default=[])
    parser.add_argument("--discovery-source", action="append", default=[])
    parser.add_argument("--mode", default="")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    plan = build_plan(
        queries=args.query,
        year_from=args.year_from,
        year_to=args.year_to,
        required_concepts=args.required_concept,
        optional_concepts=args.optional_concept,
        negative_concepts=args.negative_concept,
        discovery_sources=args.discovery_source,
        mode=args.mode,
    )
    write_plan(args.output.parent, plan, output_path=args.output)
    print(f"Query plan: {args.output}")


if __name__ == "__main__":
    main()
