#!/usr/bin/env python3
"""Citation expansion pipeline stage.

Expands a candidate set by forward/backward citation traversal via
Semantic Scholar. Seeds are selected mechanically (Top N by
``triage_priority=high`` + ``triage_score`` descending) or explicitly
(``--expand-seeds doi:xxx,doi:yyy``). The Agent or user controls seeds;
Litminer does not judge scientific importance.

Design constraints (see iteration_plan.md §3.2):

- Default seed selection is a mechanical rule, not a scientific judgment.
  SKILL.md documents this.
- Expanded rows MUST go through Crossref verification — same trust path
  as normal discovery rows.
- No multi-hop expansion (1 hop only; multi-hop causes exponential blowup).
- Not enabled in fast mode (fast mode validates direction, not recall).
- S2 rate limiting falls under the normal circuit breaker and
  ``completeness_caveats`` reporting in ``result_profile``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from litminer.engine.common import read_csv_rows, write_csv_atomic
from litminer.sources.api import semantic_scholar_search


PRIORITY_RANK = {"high": 0, "medium": 1, "needs_review": 2, "low": 3}


def select_seeds(
    triaged_rows: list[dict[str, str]],
    *,
    top_n: int = 5,
    explicit_seeds: list[str] | None = None,
) -> list[str]:
    """Select seed DOIs for citation expansion.

    If ``explicit_seeds`` is provided, those DOIs are used directly.
    Otherwise, rows with ``triage_priority=high`` are sorted by
    ``triage_score`` descending and the top N DOIs are returned.

    This is a mechanical rule. It does not represent a scientific
    importance judgment — see SKILL.md "Limits as Product Definition".
    """
    if explicit_seeds:
        return [doi.strip().lower() for doi in explicit_seeds if doi.strip()]

    candidates: list[tuple[int, float, str]] = []
    for row in triaged_rows:
        priority = (row.get("triage_priority") or "").strip()
        if priority != "high":
            continue
        doi = (row.get("crossref_doi") or row.get("doi") or "").strip().lower()
        if not doi:
            continue
        try:
            score = float(row.get("triage_score") or "0")
        except ValueError:
            score = 0.0
        candidates.append((PRIORITY_RANK.get(priority, 99), -score, doi))

    candidates.sort()
    return [doi for _, _, doi in candidates[:top_n]]


def expand_citations(
    seed_dois: list[str],
    *,
    direction: str = "both",
    max_per_seed: int = 30,
) -> list[dict[str, str]]:
    """Expand seeds via Semantic Scholar citation/reference graph.

    Returns rows in uniform schema with ``discovery_source`` and
    ``source_note`` set for traceability.
    """
    results: list[dict[str, str]] = []
    for doi in seed_dois:
        if direction in ("forward", "both"):
            try:
                citations = semantic_scholar_search.get_citations(doi, max_results=max_per_seed)
                for row in citations:
                    row["discovery_source"] = "semantic_scholar_citation"
                results.extend(citations)
                print(f"  Citation expansion for {doi}: {len(citations)} rows", file=sys.stderr)
            except Exception as exc:
                print(f"  Citation expansion failed for {doi}: {exc}", file=sys.stderr)

        if direction in ("backward", "both"):
            try:
                refs = semantic_scholar_search.get_references(doi, max_results=max_per_seed)
                for row in refs:
                    row["discovery_source"] = "semantic_scholar_reference"
                results.extend(refs)
                print(f"  Reference expansion for {doi}: {len(refs)} rows", file=sys.stderr)
            except Exception as exc:
                print(f"  Reference expansion failed for {doi}: {exc}", file=sys.stderr)

    return results


def expand_from_triaged(
    triaged_path: Path,
    output_path: Path,
    *,
    top_n: int = 5,
    explicit_seeds: list[str] | None = None,
    direction: str = "both",
    max_per_seed: int = 30,
) -> dict[str, Any]:
    """Run citation expansion from a triaged CSV and write expanded candidates.

    Returns a summary dict with seed count, expanded count, and per-seed
    error info for ``agent_summary`` and ``processing_report``.
    """
    _fieldnames, rows = read_csv_rows(triaged_path)
    seeds = select_seeds(rows, top_n=top_n, explicit_seeds=explicit_seeds)

    if not seeds:
        print("Citation expansion: no seed DOIs found.", file=sys.stderr)
        write_csv_atomic([], output_path, fieldnames=[])
        return {
            "seeds": [],
            "expanded_count": 0,
            "direction": direction,
            "errors": [],
        }

    print(f"Citation expansion: {len(seeds)} seed(s), direction={direction}", file=sys.stderr)
    expanded = expand_citations(seeds, direction=direction, max_per_seed=max_per_seed)

    if expanded:
        fieldnames = list(expanded[0].keys())
    else:
        fieldnames = list(semantic_scholar_search.OUTPUT_FIELDS)
    write_csv_atomic(expanded, output_path, fieldnames=fieldnames)

    return {
        "seeds": seeds,
        "expanded_count": len(expanded),
        "direction": direction,
        "max_per_seed": max_per_seed,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Expand citations from triaged candidates.")
    parser.add_argument("--input", type=Path, required=True, help="Triaged candidates CSV")
    parser.add_argument("--output", type=Path, required=True, help="Output expanded candidates CSV")
    parser.add_argument("--top-n", type=int, default=5, help="Max seeds from high-priority rows")
    parser.add_argument("--seeds", type=str, default=None, help="Comma-separated explicit seed DOIs")
    parser.add_argument("--direction", choices=["forward", "backward", "both"], default="both")
    parser.add_argument("--max-per-seed", type=int, default=30)
    args = parser.parse_args()

    explicit = [s.strip() for s in args.seeds.split(",")] if args.seeds else None
    summary = expand_from_triaged(
        args.input,
        args.output,
        top_n=args.top_n,
        explicit_seeds=explicit,
        direction=args.direction,
        max_per_seed=args.max_per_seed,
    )
    print(f"Expanded {summary['expanded_count']} rows from {len(summary['seeds'])} seed(s).")


if __name__ == "__main__":
    main()
