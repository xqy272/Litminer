#!/usr/bin/env python3
"""Unified API discovery orchestrator for Litminer.

This module is the API-layer entry point. It runs one or more structured API
sources, maps their rows into a shared candidate surface, and writes trace
artifacts so an Agent can see what was queried and what each source returned.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from litminer.sources.api import arxiv_search
from litminer.sources.api import europe_pmc_search
from litminer.sources.api import openalex_search
from litminer.sources.api import registry as provider_registry
from litminer.sources.api import semantic_scholar_search


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
    fields = list(fallback_fields)
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


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
                 max_results: int, openalex_api_key: str | None,
                 openalex_mailto: str | None = None) -> list[dict[str, str]]:
    if provider == "openalex":
        return openalex_search.search(
            query=query,
            year_from=year_from,
            max_results=max_results,
            api_key=openalex_api_key or os.environ.get("OPENALEX_API_KEY"),
            mailto=openalex_mailto,
        )
    if provider == "semantic_scholar":
        return semantic_scholar_search.search(
            query=query,
            year_from=year_from,
            max_results=max_results,
        )
    if provider == "arxiv":
        return arxiv_search.search(
            query=query,
            year_from=year_from,
            max_results=max_results,
        )
    if provider == "europe_pmc":
        return europe_pmc_search.search(
            query=query,
            year_from=year_from,
            max_results=max_results,
        )
    raise ValueError(f"Unsupported provider: {provider}")


def discover_api(queries: list[str],
                 output_csv: Path,
                 sources: list[str] | None = None,
                 year_from: int | None = None,
                 max_results_per_query: int = 100,
                 semantic_query_limit: int | None = None,
                 semantic_max_results: int | None = None,
                 openalex_api_key: str | None = None,
                 openalex_mailto: str | None = None,
                 trace_csv: Path | None = None,
                 report_md: Path | None = None,
                 run_id: str | None = None) -> dict[str, object]:
    providers = parse_sources(sources)
    run_id = run_id or make_run_id()
    retrieved_at = utc_now()

    candidates: list[dict[str, str]] = []
    traces: list[dict[str, str]] = []

    for q_index, query in enumerate(queries, start=1):
        query_id = f"q{q_index:03d}"
        for provider in providers:
            if provider == "semantic_scholar" and semantic_query_limit is not None:
                if q_index > semantic_query_limit:
                    continue
            provider_max = (
                semantic_max_results
                if provider == "semantic_scholar" and semantic_max_results is not None
                else max_results_per_query
            )
            started_at = utc_now()
            status = "ok"
            error = ""
            rows: list[dict[str, str]] = []
            try:
                rows = run_provider(
                    provider,
                    query,
                    year_from=year_from,
                    max_results=provider_max,
                    openalex_api_key=openalex_api_key,
                    openalex_mailto=openalex_mailto,
                )
            except Exception as exc:
                partial_rows = getattr(exc, "partial_results", []) or []
                rows = partial_rows
                status = str(getattr(exc, "status", "error") or "error")
                if rows and status == "error":
                    status = "partial_error"
                error = f"{type(exc).__name__}: {exc}"
                print(f"WARNING: {provider} query {query_id} failed: {error}", file=sys.stderr)
            if status == "ok" and not rows:
                status = "empty_result"

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
                "max_results": str(provider_max),
                "returned_count": str(len(rows)),
                "status": status,
                "error": error,
                "started_at": started_at,
                "ended_at": utc_now(),
            })

    write_csv(candidates, output_csv, DEFAULT_OUTPUT_FIELDS)
    if trace_csv is not None:
        write_csv(traces, trace_csv, TRACE_FIELDS)
    if report_md is not None:
        write_report(report_md, output_csv, trace_csv, candidates, traces)

    return {
        "run_id": run_id,
        "candidate_count": len(candidates),
        "query_count": len(queries),
        "providers": providers,
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
    report_md.parent.mkdir(parents=True, exist_ok=True)
    report_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run unified API discovery.")
    parser.add_argument("--query", action="append", default=[])
    parser.add_argument("--query-file", type=Path, default=None)
    parser.add_argument("--sources", default="openalex",
                        help="Comma-separated providers: openalex, semantic_scholar, arxiv, europe_pmc")
    parser.add_argument("--year-from", type=int, default=None)
    parser.add_argument("--max-results-per-query", type=int, default=100)
    parser.add_argument("--semantic-query-limit", type=int, default=None)
    parser.add_argument("--semantic-max-results", type=int, default=None)
    parser.add_argument("--openalex-api-key", default=None)
    parser.add_argument("--openalex-mailto", default=None,
                        help="Contact email for OpenAlex polite pool")
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
        max_results_per_query=args.max_results_per_query,
        semantic_query_limit=args.semantic_query_limit,
        semantic_max_results=args.semantic_max_results,
        openalex_api_key=args.openalex_api_key,
        openalex_mailto=args.openalex_mailto,
        trace_csv=args.trace_output,
        report_md=args.report_output,
    )
    print(json.dumps(result, indent=2), file=sys.stderr)


if __name__ == "__main__":
    main()
