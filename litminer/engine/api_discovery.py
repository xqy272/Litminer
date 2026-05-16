#!/usr/bin/env python3
"""Unified API discovery orchestrator for Litminer.

This module is the API-layer entry point. It runs one or more structured API
sources, maps their rows into a shared candidate surface, and writes trace
artifacts so an Agent can see what was queried and what each source returned.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from litminer.sources.api import arxiv_search
from litminer.sources.api import europe_pmc_search
from litminer.sources.api import openalex_search
from litminer.sources.api import registry as provider_registry
from litminer.sources.api import semantic_scholar_search
from litminer.engine.common import write_csv_atomic, write_text_atomic


DEFAULT_OUTPUT_FIELDS = [
    "title",
    "doi",
    "publication_year",
    "journal",
    "abstract",
    "article_type",
    "cited_by_count",
    "authors",
    "landing_page_url",
    "url",
    "pdf_url",
    "best_full_text_url",
    "openalex_id",
    "s2_id",
    "arxiv_id",
    "pmid",
    "pmcid",
    "europe_pmc_id",
    "discovery_source",
    "discovery_provider",
    "discovery_query",
    "discovery_query_id",
    "discovery_rank",
    "discovery_run_id",
    "source_trace",
    "retrieved_at",
    "source_note",
]

TRACE_FIELDS = [
    "discovery_run_id",
    "provider",
    "query_id",
    "query",
    "year_from",
    "year_to",
    "max_results",
    "returned_count",
    "status",
    "error",
    "started_at",
    "ended_at",
]

def provider_capability_rows(names: list[str] | None = None) -> list[dict[str, str]]:
    return provider_registry.provider_capability_rows(names)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_run_id() -> str:
    return datetime.now(timezone.utc).strftime("litminer_%Y%m%dT%H%M%SZ")


def load_queries(query: list[str] | None = None,
                 query_file: Path | None = None) -> list[str]:
    queries: list[str] = []
    if query_file:
        queries.extend(
            line.strip()
            for line in query_file.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        )
    queries.extend(query or [])
    seen: set[str] = set()
    unique: list[str] = []
    for item in queries:
        key = item.strip()
        if key and key not in seen:
            seen.add(key)
            unique.append(key)
    return unique


def parse_sources(value: str | list[str] | None) -> list[str]:
    return provider_registry.parse_provider_list(value, default=["openalex"], discovery_only=True)


def write_csv(rows: list[dict[str, str]], output: Path,
              fallback_fields: list[str]) -> None:
    write_csv_atomic(rows, output, fallback_fields=fallback_fields)


def enrich_row(row: dict[str, str], provider: str, query_id: str, query: str,
               rank: int, run_id: str, retrieved_at: str) -> dict[str, str]:
    out = {key: ("" if value is None else str(value)) for key, value in row.items()}
    out["discovery_source"] = out.get("discovery_source") or provider
    out["discovery_provider"] = provider
    out["discovery_query"] = query
    out["discovery_query_id"] = query_id
    out["discovery_rank"] = str(rank)
    out["discovery_run_id"] = run_id
    out["retrieved_at"] = retrieved_at
    doi = out.get("doi", "")
    title = out.get("title", "")
    out["source_trace"] = f"{provider}:{query_id}:rank{rank}:doi={doi or 'none'}:title={title[:80]}"
    return out


def run_provider(provider: str, query: str, year_from: int | None,
                 year_to: int | None,
                 max_results: int, openalex_api_key: str | None,
                 openalex_mailto: str | None = None,
                 openalex_work_types: str | list[str] | None = "article") -> list[dict[str, str]]:
    if provider == "openalex":
        return openalex_search.search(
            query=query,
            year_from=year_from,
            year_to=year_to,
            max_results=max_results,
            api_key=openalex_api_key or os.environ.get("OPENALEX_API_KEY"),
            mailto=openalex_mailto,
            work_types=openalex_work_types,
        )
    if provider == "semantic_scholar":
        return semantic_scholar_search.search(
            query=query,
            year_from=year_from,
            year_to=year_to,
            max_results=max_results,
        )
    if provider == "arxiv":
        return arxiv_search.search(
            query=query,
            year_from=year_from,
            year_to=year_to,
            max_results=max_results,
        )
    if provider == "europe_pmc":
        return europe_pmc_search.search(
            query=query,
            year_from=year_from,
            year_to=year_to,
            max_results=max_results,
        )
    raise ValueError(f"Unsupported provider: {provider}")


def _run_provider_call(
    provider: str,
    query_id: str,
    query: str,
    year_from: int | None,
    year_to: int | None,
    provider_max: int,
    openalex_api_key: str | None,
    openalex_mailto: str | None,
    openalex_work_types: str | list[str] | None,
) -> dict[str, Any]:
    started_at = utc_now()
    status = "ok"
    error = ""
    rows: list[dict[str, str]] = []
    try:
        rows = run_provider(
            provider,
            query,
            year_from=year_from,
            year_to=year_to,
            max_results=provider_max,
            openalex_api_key=openalex_api_key,
            openalex_mailto=openalex_mailto,
            openalex_work_types=openalex_work_types,
        )
    except Exception as exc:
        rows = getattr(exc, "partial_results", []) or []
        status = str(getattr(exc, "status", "error") or "error")
        if rows and status == "error":
            status = "partial_error"
        error = f"{type(exc).__name__}: {exc}"
        print(f"WARNING: {provider} query {query_id} failed: {error}", file=sys.stderr)
    if status == "ok" and not rows:
        status = "empty_result"

    return {
        "provider": provider,
        "provider_max": provider_max,
        "rows": rows,
        "status": status,
        "error": error,
        "started_at": started_at,
        "ended_at": utc_now(),
    }


def _skipped_provider_call(provider: str, provider_max: int, failure_count: int) -> dict[str, Any]:
    now = utc_now()
    return {
        "provider": provider,
        "provider_max": provider_max,
        "rows": [],
        "status": "skipped_circuit_breaker",
        "error": f"Skipped after {failure_count} failed provider call(s) in this discovery run.",
        "started_at": now,
        "ended_at": now,
    }


def _is_provider_failure(status: str) -> bool:
    return status not in {"ok", "empty_result", "skipped_circuit_breaker"}


def discover_api(queries: list[str],
                 output_csv: Path,
                 sources: list[str] | None = None,
                 year_from: int | None = None,
                 year_to: int | None = None,
                 max_results_per_query: int = 100,
                  semantic_query_limit: int | None = None,
                  semantic_max_results: int | None = None,
                  openalex_api_key: str | None = None,
                  openalex_mailto: str | None = None,
                  openalex_work_types: str | list[str] | None = "article",
                  strict_discovery: bool = False,
                  parallel_providers: bool = False,
                  provider_workers: int | None = None,
                  provider_failure_threshold: int | None = None,
                  trace_csv: Path | None = None,
                  report_md: Path | None = None,
                  run_id: str | None = None) -> dict[str, object]:
    providers = parse_sources(sources)
    run_id = run_id or make_run_id()
    retrieved_at = utc_now()

    candidates: list[dict[str, str]] = []
    traces: list[dict[str, str]] = []
    provider_failures: dict[str, int] = {}

    for q_index, query in enumerate(queries, start=1):
        query_id = f"q{q_index:03d}"
        plan: list[tuple[str, int]] = []
        for provider in providers:
            if provider == "semantic_scholar" and semantic_query_limit is not None:
                if q_index > semantic_query_limit:
                    continue
            provider_max = (
                semantic_max_results
                if provider == "semantic_scholar" and semantic_max_results is not None
                else max_results_per_query
            )
            plan.append((provider, provider_max))

        provider_results: list[dict[str, Any] | None] = [None] * len(plan)
        runnable_plan: list[tuple[int, str, int]] = []
        for idx, (provider, provider_max) in enumerate(plan):
            failure_count = provider_failures.get(provider, 0)
            if provider_failure_threshold is not None and failure_count >= provider_failure_threshold:
                provider_results[idx] = _skipped_provider_call(provider, provider_max, failure_count)
            else:
                runnable_plan.append((idx, provider, provider_max))

        if parallel_providers and len(runnable_plan) > 1:
            workers = max(1, min(len(runnable_plan), provider_workers or len(runnable_plan)))
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = [
                    executor.submit(
                        _run_provider_call,
                        provider,
                        query_id,
                        query,
                        year_from,
                        year_to,
                        provider_max,
                        openalex_api_key,
                        openalex_mailto,
                        openalex_work_types,
                    )
                    for _idx, provider, provider_max in runnable_plan
                ]
                for (idx, _provider, _provider_max), future in zip(runnable_plan, futures):
                    provider_results[idx] = future.result()
        else:
            for idx, provider, provider_max in runnable_plan:
                provider_results[idx] = _run_provider_call(
                    provider,
                    query_id,
                    query,
                    year_from,
                    year_to,
                    provider_max,
                    openalex_api_key,
                    openalex_mailto,
                    openalex_work_types,
                )

        for provider_result in provider_results:
            if provider_result is None:
                continue
            provider = str(provider_result["provider"])
            rows = provider_result["rows"]
            if _is_provider_failure(str(provider_result["status"])):
                provider_failures[provider] = provider_failures.get(provider, 0) + 1

            for rank, row in enumerate(rows, start=1):
                candidates.append(enrich_row(
                    row=row,
                    provider=provider,
                    query_id=query_id,
                    query=query,
                    rank=rank,
                    run_id=run_id,
                    retrieved_at=retrieved_at,
                ))

            traces.append({
                "discovery_run_id": run_id,
                "provider": provider,
                "query_id": query_id,
                "query": query,
                "year_from": str(year_from or ""),
                "year_to": str(year_to or ""),
                "max_results": str(provider_result["provider_max"]),
                "returned_count": str(len(rows)),
                "status": str(provider_result["status"]),
                "error": str(provider_result["error"]),
                "started_at": str(provider_result["started_at"]),
                "ended_at": str(provider_result["ended_at"]),
            })

    write_csv(candidates, output_csv, DEFAULT_OUTPUT_FIELDS)
    if trace_csv is not None:
        write_csv(traces, trace_csv, TRACE_FIELDS)
    if report_md is not None:
        write_report(report_md, output_csv, trace_csv, candidates, traces)

    status_counts = dict(Counter(trace["status"] for trace in traces))
    non_infra_statuses = {"ok", "empty_result"}
    discovery_failed = bool(traces) and any(
        trace["status"] not in non_infra_statuses for trace in traces
    )
    all_provider_calls_failed = bool(traces) and all(
        trace["status"] not in non_infra_statuses for trace in traces
    )
    if strict_discovery and discovery_failed and (not candidates or all_provider_calls_failed):
        raise RuntimeError(
            "Strict discovery failed: provider errors prevented a reliable candidate set. "
            f"status_counts={status_counts}; trace_csv={trace_csv or ''}"
        )

    return {
        "run_id": run_id,
        "candidate_count": len(candidates),
        "query_count": len(queries),
        "providers": providers,
        "provider_statuses": status_counts,
        "provider_failures": provider_failures,
        "output_csv": str(output_csv),
        "trace_csv": str(trace_csv) if trace_csv else "",
        "report_md": str(report_md) if report_md else "",
    }


def write_report(report_md: Path, output_csv: Path, trace_csv: Path | None,
                 candidates: list[dict[str, str]],
                 traces: list[dict[str, str]]) -> None:
    by_provider: dict[str, int] = {}
    by_status: dict[str, int] = {}
    for row in candidates:
        provider = row.get("discovery_provider", "unknown")
        by_provider[provider] = by_provider.get(provider, 0) + 1
    for trace in traces:
        status = trace.get("status", "unknown")
        by_status[status] = by_status.get(status, 0) + 1

    lines = [
        "# API Discovery Report",
        "",
        f"Candidate CSV: `{output_csv}`",
        f"Trace CSV: `{trace_csv or ''}`",
        f"Candidates: {len(candidates)}",
        f"Queries x providers: {len(traces)}",
        "",
        "## Provider Counts",
        "",
    ]
    if by_provider:
        for provider, count in sorted(by_provider.items()):
            lines.append(f"- {provider}: {count}")
    else:
        lines.append("- none: 0")
    lines.extend([
        "",
        "## Provider Statuses",
        "",
    ])
    if by_status:
        for status, count in sorted(by_status.items()):
            lines.append(f"- {status}: {count}")
    else:
        lines.append("- none: 0")
    lines.extend([
        "",
        "## Provider Capabilities",
        "",
        "| Provider | Role | Key | Contact | Year Filter | DOI Lookup | Abstract | Rate Policy |",
        "|----------|------|-----|---------|-------------|------------|----------|-------------|",
    ])
    capability_names = sorted(set(by_provider) | {trace["provider"] for trace in traces} | {"crossref", "unpaywall"})
    for row in provider_capability_rows(capability_names):
        lines.append(
            f"| {row['provider']} | {row.get('role', '')} | {row.get('requires_key', '')} | "
            f"{row.get('requires_contact', '')} | {row.get('supports_year_filter', '')} | "
            f"{row.get('supports_doi_lookup', '')} | {row.get('returns_abstract', '')} | "
            f"{row.get('rate_limit_policy', '')} |"
        )

    lines.extend([
        "",
        "## Query Trace",
        "",
        "| Provider | Query ID | Returned | Status | Query | Error |",
        "|----------|----------|----------|--------|-------|-------|",
    ])
    for trace in traces:
        query = trace["query"].replace("|", "\\|")
        error = trace.get("error", "").replace("|", "\\|")
        lines.append(
            f"| {trace['provider']} | {trace['query_id']} | "
            f"{trace['returned_count']} | {trace['status']} | {query} | {error} |"
        )
    write_text_atomic(report_md, "\n".join(lines) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run unified API discovery.")
    parser.add_argument("--query", action="append", default=[])
    parser.add_argument("--query-file", type=Path, default=None)
    parser.add_argument("--sources", default="openalex",
                        help="Comma-separated providers: openalex, semantic_scholar, arxiv, europe_pmc")
    parser.add_argument("--year-from", type=int, default=None)
    parser.add_argument("--year-to", type=int, default=None)
    parser.add_argument("--max-results-per-query", type=int, default=100)
    parser.add_argument("--semantic-query-limit", type=int, default=None)
    parser.add_argument("--semantic-max-results", type=int, default=None)
    parser.add_argument("--openalex-api-key", default=None)
    parser.add_argument("--openalex-mailto", default=None,
                        help="Contact email for OpenAlex polite pool")
    parser.add_argument("--openalex-work-types", default="article",
                        help="OpenAlex work type filter. Comma/pipe-separated; use 'all' to disable.")
    parser.add_argument("--strict-discovery", action="store_true",
                        help="Fail if provider errors prevent a reliable candidate set")
    parser.add_argument("--parallel-providers", action="store_true",
                        help="Run different providers for the same query concurrently")
    parser.add_argument("--provider-workers", type=int, default=None,
                        help="Max provider worker threads when --parallel-providers is set")
    parser.add_argument("--provider-failure-threshold", type=int, default=None,
                        help="Skip remaining calls for a provider after this many failed calls")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--trace-output", type=Path, default=None)
    parser.add_argument("--report-output", type=Path, default=None)
    args = parser.parse_args()

    queries = load_queries(args.query, args.query_file)
    if not queries:
        parser.error("Provide --query or --query-file")

    result = discover_api(
        queries,
        args.output,
        sources=parse_sources(args.sources),
        year_from=args.year_from,
        year_to=args.year_to,
        max_results_per_query=args.max_results_per_query,
        semantic_query_limit=args.semantic_query_limit,
        semantic_max_results=args.semantic_max_results,
        openalex_api_key=args.openalex_api_key,
        openalex_mailto=args.openalex_mailto,
        openalex_work_types=args.openalex_work_types,
        strict_discovery=args.strict_discovery,
        parallel_providers=args.parallel_providers,
        provider_workers=args.provider_workers,
        provider_failure_threshold=args.provider_failure_threshold,
        trace_csv=args.trace_output,
        report_md=args.report_output,
    )
    print(json.dumps(result, indent=2), file=sys.stderr)


if __name__ == "__main__":
    main()
