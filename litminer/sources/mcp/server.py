#!/usr/bin/env python3
"""Litminer MCP server for API discovery and LLM-facing workflow tools.

This server wraps the Litminer API sources (OpenAlex, Semantic Scholar, arXiv,
Europe PMC, Crossref, Unpaywall) and deterministic engine scripts as
MCP-style JSON-RPC tools.

Usage:
    python -m litminer.sources.mcp.server

Tools exposed:
    litminer_search_openalex    - Search OpenAlex for papers
    litminer_search_arxiv       - Search arXiv preprints
    litminer_search_europe_pmc  - Search Europe PMC metadata
    litminer_discover_api       - Run multi-query API discovery with trace files
    litminer_verify_crossref    - Verify DOIs against Crossref
    litminer_batch_verify_crossref - Verify multiple DOIs against Crossref
    litminer_lookup_unpaywall   - Find structured OA links for a DOI
    litminer_dedupe             - Deduplicate paper CSV
    litminer_semantic_triage    - Annotate/rank candidates with caller-supplied concepts
    litminer_processing_report  - Summarize workflow outputs for Agent review
    litminer_read_csv_summary   - Read compact, paginated CSV summaries
    litminer_build_publisher_queue - Build DOI/publisher-page queues
    litminer_run_lit_search     - Run the full Agent-facing workflow

This server uses stdlib-only JSON-RPC over stdio (no MCP SDK dependency) for
maximum portability.

Architecture:
    Agent -> MCP -> litminer.sources.api.* and litminer.engine.*
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any

# Add repository root to path so this file also works when launched directly by
# absolute path from an Agent MCP config.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from litminer import __version__
from litminer.engine import workspace

DEFAULT_PROTOCOL_VERSION = "2025-11-25"
SUPPORTED_PROTOCOL_VERSIONS = {DEFAULT_PROTOCOL_VERSION, "2024-11-05"}
MAX_STDIN_LINE_BYTES = int(os.environ.get("LITMINER_MCP_MAX_LINE_BYTES", str(16 * 1024 * 1024)))

_import_lock = threading.Lock()


def _lazy_import(module_path: str):
    """Return a thread-safe getter that imports a module on first use."""
    module: Any | None = None

    def getter():
        nonlocal module
        if module is None:
            with _import_lock:
                if module is None:
                    module = importlib.import_module(module_path)
        return module

    return getter


# Lazy imports: only load when a tool is called.
_get_openalex = _lazy_import("litminer.sources.api.openalex_search")
_get_crossref = _lazy_import("litminer.sources.api.crossref_verify")
_get_semantic_scholar = _lazy_import("litminer.sources.api.semantic_scholar_search")
_get_arxiv = _lazy_import("litminer.sources.api.arxiv_search")
_get_europe_pmc = _lazy_import("litminer.sources.api.europe_pmc_search")
_get_unpaywall = _lazy_import("litminer.sources.api.unpaywall_lookup")
_get_common = _lazy_import("litminer.engine.common")
_get_engine_dedupe = _lazy_import("litminer.engine.dedupe_papers")
_get_engine_api_discovery = _lazy_import("litminer.engine.api_discovery")
_get_engine_semantic_triage = _lazy_import("litminer.engine.semantic_triage")
_get_engine_journal_metrics = _lazy_import("litminer.engine.journal_metrics")
_get_engine_build_queue = _lazy_import("litminer.engine.build_publisher_queue")
_get_engine_publisher_probe = _lazy_import("litminer.engine.publisher_probe")
_get_engine_websearch_import = _lazy_import("litminer.engine.websearch_import")
_get_engine_processing_report = _lazy_import("litminer.engine.processing_report")
_get_engine_run_lit_search = _lazy_import("litminer.engine.run_lit_search")


def _workspace_root() -> Path:
    """Return the user workspace root for MCP file operations."""
    return workspace.workspace_root()


def _workspace_path(value: str, label: str = "path", must_exist: bool = False) -> Path:
    """Resolve a user path while keeping MCP file access inside the workspace."""
    if value is None or str(value).strip() == "":
        raise ValueError(f"{label} is required")
    root = _workspace_root()
    path = Path(str(value))
    if not path.is_absolute():
        path = root / path
    resolved = path.resolve(strict=False)
    # Resolve before containment checks so existing symlinks cannot point a
    # workspace-relative path outside the configured workspace root.
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} escapes Litminer workspace: {value}") from exc
    if must_exist and not resolved.exists():
        raise FileNotFoundError(f"{label} not found: {value}")
    return resolved


def _optional_workspace_path(value: str | None, label: str = "path",
                             must_exist: bool = False) -> Path | None:
    if not value:
        return None
    return _workspace_path(value, label=label, must_exist=must_exist)


def _as_string_set(value: Any) -> set[str]:
    """Coerce MCP scalar/list arguments into a normalized string set."""
    if value is None or value == "":
        return set()
    if isinstance(value, str):
        raw = value.replace(";", ",").split(",")
    elif isinstance(value, (list, tuple, set)):
        raw = [str(item) for item in value]
    else:
        raw = [str(value)]
    return {item.strip() for item in raw if item.strip()}


def _positive_int(value: Any, default: int, minimum: int = 1, maximum: int | None = None) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(maximum, parsed)
    return parsed


def _count_values(rows: list[dict[str, str]], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = (row.get(field) or "").strip() or "<blank>"
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def _compact_columns(fieldnames: list[str]) -> list[str]:
    preferred = [
        "title",
        "crossref_title",
        "doi",
        "crossref_doi",
        "publication_year",
        "crossref_year",
        "journal",
        "crossref_container",
        "triage_priority",
        "triage_score",
        "candidate_status",
        "metadata_status",
        "crossref_status",
        "metric_filter_status",
        "publisher_url",
        "best_oa_url",
    ]
    selected = [field for field in preferred if field in fieldnames]
    if selected:
        return selected
    return fieldnames[: min(len(fieldnames), 12)]


def _jsonrpc_error(request: dict, code: int, message: str, exc: Exception | None = None) -> dict:
    error: dict[str, Any] = {"code": code, "message": message}
    if exc is not None and os.environ.get("LITMINER_MCP_DEBUG_ERRORS"):
        error["data"] = traceback.format_exc()
    return {"jsonrpc": "2.0", "id": request.get("id"), "error": error}


# Tool handlers

def tool_search_openalex(args: dict) -> dict:
    """Search OpenAlex for literature candidates.

    Parameters:
        query (str): Search query string
        year_from (int, optional): Minimum publication year
        max_results (int, optional): Max results (default 200)
        api_key (str, optional): OpenAlex API key
    """
    oa = _get_openalex()
    results = oa.search(
        query=args["query"],
        year_from=args.get("year_from"),
        year_to=args.get("year_to"),
        max_results=args.get("max_results", 200),
        api_key=args.get("api_key") or os.environ.get("OPENALEX_API_KEY"),
        mailto=args.get("mailto"),
        work_types=args.get("work_types", "article"),
    )
    return {
        "count": len(results),
        "results": results[:20],  # Return first 20 to avoid context overflow
        "truncated": len(results) > 20,
        "total_found": len(results),
    }


def tool_search_semantic_scholar(args: dict) -> dict:
    """Search Semantic Scholar or expand citations/references."""
    s2 = _get_semantic_scholar()
    max_results = args.get("max_results", 200)
    results: list[dict[str, str]] = []

    if args.get("query"):
        results.extend(s2.search(
            query=args["query"],
            year_from=args.get("year_from"),
            year_to=args.get("year_to"),
            max_results=max_results,
        ))
    if args.get("citation_expand"):
        results.extend(s2.get_citations(args["citation_expand"], max_results=max_results))
    if args.get("reference_expand"):
        results.extend(s2.get_references(args["reference_expand"], max_results=max_results))
    if not results:
        raise ValueError("Provide query, citation_expand, or reference_expand")

    return {
        "count": len(results),
        "results": results[:20],
        "truncated": len(results) > 20,
        "total_found": len(results),
    }


def tool_search_arxiv(args: dict) -> dict:
    """Search arXiv through the official Atom API."""
    mod = _get_arxiv()
    results = mod.search(
        query=args["query"],
        year_from=args.get("year_from"),
        year_to=args.get("year_to"),
        max_results=args.get("max_results", 100),
    )
    return {
        "count": len(results),
        "results": results[:20],
        "truncated": len(results) > 20,
        "total_found": len(results),
    }


def tool_search_europe_pmc(args: dict) -> dict:
    """Search Europe PMC through its REST API."""
    mod = _get_europe_pmc()
    results = mod.search(
        query=args["query"],
        year_from=args.get("year_from"),
        year_to=args.get("year_to"),
        max_results=args.get("max_results", 100),
    )
    return {
        "count": len(results),
        "results": results[:20],
        "truncated": len(results) > 20,
        "total_found": len(results),
    }


def tool_verify_crossref(args: dict) -> dict:
    """Verify a DOI against Crossref.

    Parameters:
        doi (str): DOI to verify
    """
    cr = _get_crossref()
    doi_clean = cr.normalize_doi(args["doi"])
    meta = cr.verify_doi(doi_clean)

    if meta is None:
        return {"verified": False, "doi": doi_clean, "error": "DOI not found or lookup failed"}

    return {"verified": True, "doi": doi_clean, "metadata": meta}


def tool_batch_verify_crossref(args: dict) -> dict:
    """Verify multiple DOIs against Crossref."""
    cr = _get_crossref()
    dois = args.get("dois", [])
    if not isinstance(dois, list) or not dois:
        raise ValueError("dois must be a non-empty list")

    max_items = _positive_int(args.get("max_items"), default=100, minimum=1, maximum=500)
    sleep_s = float(args.get("sleep_s") or 0.0)
    output = []
    verified = 0
    failed = 0
    skipped = 0
    seen: set[str] = set()

    for raw_doi in dois[:max_items]:
        doi_clean = cr.normalize_doi(raw_doi)
        if not doi_clean:
            skipped += 1
            output.append({"doi": "", "verified": False, "error": "missing DOI"})
            continue
        if doi_clean in seen:
            skipped += 1
            output.append({"doi": doi_clean, "verified": False, "error": "duplicate DOI"})
            continue
        seen.add(doi_clean)

        meta = cr.verify_doi(doi_clean)
        if meta is None:
            failed += 1
            output.append({"doi": doi_clean, "verified": False, "error": "DOI not found or lookup failed"})
        else:
            verified += 1
            output.append({"doi": doi_clean, "verified": True, "metadata": meta})
        if sleep_s > 0:
            time.sleep(sleep_s)

    truncated = len(dois) > max_items
    return {
        "count": len(output),
        "verified": verified,
        "failed": failed,
        "skipped": skipped,
        "truncated": truncated,
        "max_items": max_items,
        "results": output,
    }


def tool_search_crossref_title(args: dict) -> dict:
    """Search Crossref by paper title to find a DOI.

    Parameters:
        title (str): Paper title to search
        max_results (int, optional): Max results (default 5)
    """
    cr = _get_crossref()
    results = cr.search_by_title(
        title=args["title"],
        max_results=args.get("max_results", 5),
    )
    return {"count": len(results), "results": results}


def tool_batch_crossref_title_search(args: dict) -> dict:
    """Search Crossref by multiple titles."""
    cr = _get_crossref()
    titles = args.get("titles", [])
    if not isinstance(titles, list) or not titles:
        raise ValueError("titles must be a non-empty list")

    max_results = args.get("max_results", 3)
    output = []
    for title in titles:
        title_text = str(title)
        results = cr.search_by_title(title_text, max_results=max_results)
        output.append({
            "title": title_text,
            "count": len(results),
            "results": results,
        })
    return {"count": len(output), "results": output}


def tool_lookup_unpaywall(args: dict) -> dict:
    """Look up OA locations for one DOI through Unpaywall."""
    mod = _get_unpaywall()
    result = mod.lookup_doi(args["doi"], email=args.get("email"))
    return mod.flatten_response(result)


def tool_dedupe(args: dict) -> dict:
    """Deduplicate a CSV of paper candidates."""
    mod = _get_engine_dedupe()
    input_path = _workspace_path(args["input_csv"], "input_csv", must_exist=True)
    output_path = _workspace_path(args["output_csv"], "output_csv")
    mod.dedupe(input_path, output_path, args.get("doi_field", "doi"), args.get("title_field", "title"))
    return {"status": "ok", "output": str(output_path)}


def tool_discover_api(args: dict) -> dict:
    """Run unified multi-query API discovery and write trace artifacts."""
    mod = _get_engine_api_discovery()
    queries = mod.load_queries(
        query=args.get("queries") or [],
        query_file=_optional_workspace_path(args.get("query_file"), "query_file", must_exist=True),
    )
    if not queries:
        raise ValueError("Provide queries or query_file")
    output_csv = _workspace_path(
        args.get("output_csv", f"{workspace.DEFAULT_RUN_DIR}/api_candidates.csv"),
        "output_csv",
    )
    trace_csv = _optional_workspace_path(args.get("trace_csv"), "trace_csv") or output_csv.with_name("api_discovery_trace.csv")
    report_md = _optional_workspace_path(args.get("report_md"), "report_md") or output_csv.with_name("api_discovery_report.md")
    result = mod.discover_api(
        queries,
        output_csv,
        sources=mod.parse_sources(args.get("sources", "openalex")),
        year_from=args.get("year_from"),
        year_to=args.get("year_to"),
        max_results_per_query=args.get("max_results_per_query", 100),
        semantic_query_limit=args.get("semantic_query_limit"),
        semantic_max_results=args.get("semantic_max_results"),
        openalex_api_key=args.get("openalex_api_key") or os.environ.get("OPENALEX_API_KEY"),
        openalex_mailto=args.get("openalex_mailto"),
        openalex_work_types=args.get("openalex_work_types", "article"),
        strict_discovery=args.get("strict_discovery", False),
        parallel_providers=args.get("parallel_providers", False),
        provider_workers=args.get("provider_workers"),
        trace_csv=trace_csv,
        report_md=report_md,
    )
    return {"status": "ok", **result}


def tool_semantic_triage(args: dict) -> dict:
    """Annotate and rank candidates with caller-supplied semantic concepts."""
    mod = _get_engine_semantic_triage()
    counts = mod.triage_csv(
        _workspace_path(args["input_csv"], "input_csv", must_exist=True),
        _workspace_path(args["output_csv"], "output_csv"),
        profile_path=_optional_workspace_path(args.get("profile"), "profile", must_exist=True),
        required_concepts=args.get("required_concepts") or [],
        optional_concepts=args.get("optional_concepts") or [],
        negative_concepts=args.get("negative_concepts") or [],
        year_from=args.get("year_from"),
        year_to=args.get("year_to"),
        require_doi=args.get("require_doi", False),
        exclude_article_types=args.get("exclude_article_types") or [],
        allow_regex=not args.get("disable_regex_concepts", False),
    )
    return {"status": "ok", "output": args["output_csv"], "counts": counts}


def tool_filter_journal_metrics(args: dict) -> dict:
    """Annotate/filter a CSV by verified journal metrics."""
    mod = _get_engine_journal_metrics()
    counts = mod.filter_csv(
        _workspace_path(args["input_csv"], "input_csv", must_exist=True),
        _workspace_path(args["output_csv"], "output_csv"),
        metrics_path=_optional_workspace_path(args.get("metrics_csv"), "metrics_csv", must_exist=True) or mod.DEFAULT_METRICS,
        min_if=args.get("min_if"),
        pass_output=_optional_workspace_path(args.get("pass_output_csv"), "pass_output_csv"),
        backup_output=_optional_workspace_path(args.get("backup_output_csv"), "backup_output_csv"),
    )
    return {"status": "ok", "output": args["output_csv"], "counts": counts}


def tool_build_publisher_queue(args: dict) -> dict:
    """Build a publisher extraction queue."""
    mod = _get_engine_build_queue()
    raw_decisions = args.get("decisions")
    if isinstance(raw_decisions, str):
        decisions = {item.strip() for item in raw_decisions.split(",") if item.strip()}
    elif raw_decisions is None:
        decisions = set()
    else:
        decisions = {str(item).strip() for item in raw_decisions if str(item).strip()}

    def _set(value, default=None):
        if value is None:
            return set(default or [])
        if isinstance(value, str):
            return {item.strip() for item in value.replace(";", ",").split(",") if item.strip()}
        return {str(item).strip() for item in value if str(item).strip()}

    counts = mod.build_queue(
        _workspace_path(args["input_csv"], "input_csv", must_exist=True),
        _workspace_path(args["output_csv"], "output_csv"),
        decisions=decisions,
        priorities=_set(args.get("priorities"), {"high", "medium", "needs_review"}),
        statuses=_set(args.get("statuses")),
        include_metadata_blocked=args.get("include_metadata_blocked", False),
        screenshot_root=str(_workspace_path(
            args.get("screenshot_root", workspace.DEFAULT_SCREENSHOT_ROOT),
            "screenshot_root",
        )),
        require_doi=not args.get("allow_missing_doi", False),
        fields_needed=args.get("fields_needed"),
        page_required_fields=args.get("page_required_fields"),
    )
    return {"status": "ok", "output": args["output_csv"], "counts": counts}


def tool_probe_publishers(args: dict) -> dict:
    """Probe DOI/publisher pages for access/PDF/SI status."""
    mod = _get_engine_publisher_probe()
    counts = mod.probe_csv(
        _workspace_path(args["input_csv"], "input_csv", must_exist=True),
        _workspace_path(args["output_csv"], "output_csv"),
        limit=args.get("limit"),
        sleep_s=args.get("sleep_s", 0.5),
    )
    return {"status": "ok", "output": args["output_csv"], "counts": counts}


def tool_import_websearch(args: dict) -> dict:
    """Normalize WebSearch leads into unverified candidate rows."""
    mod = _get_engine_websearch_import()
    counts = mod.import_websearch(
        _workspace_path(args["input_csv"], "input_csv", must_exist=True),
        _workspace_path(args["output_csv"], "output_csv"),
        default_query=args.get("query", ""),
    )
    return {"status": "ok", "output": args["output_csv"], "counts": counts}


def tool_processing_report(args: dict) -> dict:
    """Generate a compact processing report for a Litminer run directory."""
    mod = _get_engine_processing_report()
    output_dir = _workspace_path(args["output_dir"], "output_dir", must_exist=True)
    output = _optional_workspace_path(args.get("output"), "output")
    path = mod.write_report(output_dir, output)
    return {"status": "ok", "output": str(path)}


def tool_read_csv_summary(args: dict) -> dict:
    """Read a compact, paginated summary from a CSV inside the workspace."""
    common = _get_common()
    input_path = _workspace_path(args["input_csv"], "input_csv", must_exist=True)
    fieldnames, rows = common.read_csv_rows(input_path)
    if not fieldnames:
        raise ValueError("input_csv has no header")

    priority_filter = _as_string_set(args.get("priority") or args.get("priorities"))
    candidate_status_filter = _as_string_set(args.get("candidate_status") or args.get("candidate_statuses"))
    metadata_status_filter = _as_string_set(args.get("metadata_status") or args.get("metadata_statuses"))

    filtered = []
    for row in rows:
        if priority_filter and row.get("triage_priority") not in priority_filter:
            continue
        if candidate_status_filter and row.get("candidate_status") not in candidate_status_filter:
            continue
        if metadata_status_filter and row.get("metadata_status") not in metadata_status_filter:
            continue
        filtered.append(row)

    sort_by = str(args.get("sort_by") or "").strip()
    if sort_by:
        if sort_by not in fieldnames:
            raise ValueError(f"sort_by column not found: {sort_by}")
        reverse = bool(args.get("sort_desc", False))
        if sort_by == "triage_priority":
            order = {"high": 0, "medium": 1, "needs_review": 2, "low": 3}
            filtered.sort(key=lambda row: (order.get(row.get(sort_by, ""), 99), row.get("title", "")))
        elif sort_by in {"triage_score", "relevance_score", "cited_by_count", "journal_metric"}:
            def numeric_key(row: dict[str, str]) -> float:
                try:
                    return float(row.get(sort_by) or 0)
                except ValueError:
                    return 0.0
            filtered.sort(key=numeric_key, reverse=reverse)
        else:
            filtered.sort(key=lambda row: row.get(sort_by, ""), reverse=reverse)

    page_size = _positive_int(args.get("page_size"), default=20, minimum=1, maximum=200)
    page = _positive_int(args.get("page"), default=1, minimum=1)
    offset = (page - 1) * page_size
    page_rows = filtered[offset:offset + page_size]

    raw_columns = args.get("columns")
    if raw_columns is None:
        columns = _compact_columns(fieldnames)
    elif isinstance(raw_columns, str):
        columns = [item.strip() for item in raw_columns.replace(";", ",").split(",") if item.strip()]
    else:
        columns = [str(item).strip() for item in raw_columns if str(item).strip()]
    missing = [column for column in columns if column not in fieldnames]
    if missing:
        raise ValueError(f"columns not found: {', '.join(missing)}")

    max_cell_chars = _positive_int(args.get("max_cell_chars"), default=300, minimum=20, maximum=2000)

    def project(row: dict[str, str]) -> dict[str, str]:
        out = {}
        for column in columns:
            value = row.get(column, "")
            if len(value) > max_cell_chars:
                value = value[:max_cell_chars - 1].rstrip() + "..."
            out[column] = value
        return out

    total_pages = (len(filtered) + page_size - 1) // page_size if filtered else 0
    status_fields = [
        "triage_priority",
        "candidate_status",
        "metadata_status",
        "crossref_status",
        "metric_filter_status",
        "access_status",
    ]
    return {
        "status": "ok",
        "input_csv": str(input_path),
        "fieldnames": fieldnames,
        "selected_columns": columns,
        "row_count": len(rows),
        "filtered_count": len(filtered),
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "truncated": offset + len(page_rows) < len(filtered),
        "counts": {
            field: _count_values(filtered, field)
            for field in status_fields
            if field in fieldnames
        },
        "rows": [project(row) for row in page_rows],
    }


def tool_run_lit_search(args: dict) -> dict:
    """Run the Agent-facing Litminer workflow."""
    mod = _get_engine_run_lit_search()
    import argparse as _argparse
    ns = _argparse.Namespace(
        input_csv=_optional_workspace_path(args.get("input_csv"), "input_csv", must_exist=True),
        query=args.get("queries"),
        query_file=_optional_workspace_path(args.get("query_file"), "query_file", must_exist=True),
        year_from=args.get("year_from"),
        year_to=args.get("year_to"),
        output_dir=_optional_workspace_path(args.get("output_dir"), "output_dir"),
        discovery_sources=args.get("discovery_sources"),
        include_arxiv=args.get("include_arxiv"),
        include_europe_pmc=args.get("include_europe_pmc"),
        config=_optional_workspace_path(args.get("config"), "config", must_exist=True),
        triage_profile=_optional_workspace_path(args.get("triage_profile"), "triage_profile", must_exist=True),
        required_concept=args.get("required_concepts") or [],
        optional_concept=args.get("optional_concepts") or [],
        negative_concept=args.get("negative_concepts") or [],
        exclude_article_type=args.get("exclude_article_types") or [],
        queue_priorities=args.get("queue_priorities"),
        include_metadata_blocked=args.get("include_metadata_blocked"),
        fields_needed=args.get("fields_needed"),
        page_required_field=args.get("page_required_fields"),
        openalex_api_key=args.get("openalex_api_key"),
        openalex_mailto=args.get("openalex_mailto"),
        openalex_work_types=args.get("openalex_work_types"),
        enrich_unpaywall=args.get("enrich_unpaywall"),
        skip_unpaywall=args.get("skip_unpaywall"),
        unpaywall_email=args.get("unpaywall_email"),
        unpaywall_sleep=args.get("unpaywall_sleep"),
        max_results_per_query=args.get("max_results_per_query"),
        skip_openalex=args.get("skip_openalex"),
        include_semantic_scholar=args.get("include_semantic_scholar"),
        semantic_query_limit=args.get("semantic_query_limit"),
        semantic_max_results=args.get("semantic_max_results"),
        skip_crossref=args.get("skip_crossref"),
        strict_discovery=args.get("strict_discovery"),
        parallel_providers=args.get("parallel_providers"),
        provider_workers=args.get("provider_workers"),
        metrics=_optional_workspace_path(args.get("metrics_csv"), "metrics_csv", must_exist=True),
        min_if=args.get("min_if"),
        skip_journal_metrics=args.get("skip_journal_metrics"),
        target_count=args.get("target_count"),
        queue_strict_only=args.get("queue_strict_only"),
        allow_missing_doi=args.get("allow_missing_doi"),
        screenshot_root=_optional_workspace_path(args.get("screenshot_root"), "screenshot_root"),
        probe_publishers=args.get("probe_publishers"),
        probe_limit=args.get("probe_limit"),
        probe_sleep=args.get("probe_sleep"),
    )
    return {"status": "ok", **mod.run(ns)}


# Tool registry

TOOLS: dict[str, dict] = {
    "litminer_search_openalex": {
        "handler": tool_search_openalex,
        "description": "Search OpenAlex for literature candidates",
        "parameters": {
            "query": {"type": "string", "required": True, "description": "Search query"},
            "year_from": {"type": "integer", "required": False, "description": "Minimum publication year"},
            "year_to": {"type": "integer", "required": False, "description": "Maximum publication year"},
            "max_results": {"type": "integer", "required": False, "description": "Max results (default 200)"},
            "api_key": {"type": "string", "required": False, "description": "OpenAlex API key"},
            "mailto": {"type": "string", "required": False, "description": "OpenAlex polite-pool contact email"},
            "work_types": {"type": "string", "required": False, "description": "OpenAlex work types; comma/pipe-separated, or 'all'"},
        },
    },
    "litminer_search_semantic_scholar": {
        "handler": tool_search_semantic_scholar,
        "description": "Search Semantic Scholar or run one-hop citation/reference expansion",
        "parameters": {
            "query": {"type": "string", "required": False, "description": "Search query"},
            "year_from": {"type": "integer", "required": False, "description": "Minimum publication year"},
            "year_to": {"type": "integer", "required": False, "description": "Maximum publication year"},
            "max_results": {"type": "integer", "required": False, "description": "Max results"},
            "citation_expand": {"type": "string", "required": False, "description": "Seed DOI for forward citations"},
            "reference_expand": {"type": "string", "required": False, "description": "Seed DOI for references"},
        },
    },
    "litminer_search_arxiv": {
        "handler": tool_search_arxiv,
        "description": "Search arXiv preprints through the official Atom API",
        "parameters": {
            "query": {"type": "string", "required": True, "description": "arXiv search query, e.g. all:graphene"},
            "year_from": {"type": "integer", "required": False, "description": "Minimum publication year"},
            "year_to": {"type": "integer", "required": False, "description": "Maximum publication year"},
            "max_results": {"type": "integer", "required": False, "description": "Max results"},
        },
    },
    "litminer_search_europe_pmc": {
        "handler": tool_search_europe_pmc,
        "description": "Search Europe PMC biomedical/life-science literature metadata",
        "parameters": {
            "query": {"type": "string", "required": True, "description": "Europe PMC search query"},
            "year_from": {"type": "integer", "required": False, "description": "Minimum publication year"},
            "year_to": {"type": "integer", "required": False, "description": "Maximum publication year"},
            "max_results": {"type": "integer", "required": False, "description": "Max results"},
        },
    },
    "litminer_verify_crossref": {
        "handler": tool_verify_crossref,
        "description": "Verify a DOI against Crossref metadata",
        "parameters": {
            "doi": {"type": "string", "required": True, "description": "DOI to verify"},
        },
    },
    "litminer_batch_verify_crossref": {
        "handler": tool_batch_verify_crossref,
        "description": "Verify multiple DOIs against Crossref metadata",
        "parameters": {
            "dois": {"type": "array", "items": {"type": "string"}, "required": True, "description": "DOIs to verify"},
            "max_items": {"type": "integer", "required": False, "description": "Maximum DOI count to process"},
            "sleep_s": {"type": "number", "required": False, "description": "Optional delay between Crossref requests"},
        },
    },
    "litminer_search_crossref_title": {
        "handler": tool_search_crossref_title,
        "description": "Search Crossref by paper title to find a DOI",
        "parameters": {
            "title": {"type": "string", "required": True, "description": "Paper title"},
            "max_results": {"type": "integer", "required": False, "description": "Max results"},
        },
    },
    "litminer_batch_crossref_title_search": {
        "handler": tool_batch_crossref_title_search,
        "description": "Search Crossref by multiple paper titles",
        "parameters": {
            "titles": {"type": "array", "items": {"type": "string"}, "required": True, "description": "Paper titles"},
            "max_results": {"type": "integer", "required": False, "description": "Max results per title"},
        },
    },
    "litminer_dedupe": {
        "handler": tool_dedupe,
        "description": "Deduplicate paper CSV by DOI and title",
        "parameters": {
            "input_csv": {"type": "string", "required": True, "description": "Input CSV path"},
            "output_csv": {"type": "string", "required": True, "description": "Output CSV path"},
            "doi_field": {"type": "string", "required": False, "description": "DOI column name"},
            "title_field": {"type": "string", "required": False, "description": "Title column name"},
        },
    },
    "litminer_lookup_unpaywall": {
        "handler": tool_lookup_unpaywall,
        "description": "Look up structured open-access links for one DOI through Unpaywall",
        "parameters": {
            "doi": {"type": "string", "required": True, "description": "DOI to look up"},
            "email": {"type": "string", "required": False, "description": "Unpaywall email; falls back to UNPAYWALL_EMAIL or LITMINER_CONTACT_EMAIL"},
        },
    },
    "litminer_discover_api": {
        "handler": tool_discover_api,
        "description": "Run multi-query API discovery across registered discovery providers with trace/report outputs",
        "parameters": {
            "queries": {"type": "array", "items": {"type": "string"}, "required": False, "description": "Search queries"},
            "query_file": {"type": "string", "required": False, "description": "File with one query per line"},
            "sources": {"type": "string", "required": False, "description": "Comma-separated providers: openalex, semantic_scholar, arxiv, europe_pmc"},
            "year_from": {"type": "integer", "required": False, "description": "Minimum publication year"},
            "year_to": {"type": "integer", "required": False, "description": "Maximum publication year"},
            "max_results_per_query": {"type": "integer", "required": False, "description": "Max results per query"},
            "semantic_query_limit": {"type": "integer", "required": False, "description": "Max query count for Semantic Scholar"},
            "semantic_max_results": {"type": "integer", "required": False, "description": "Semantic Scholar max results per query"},
            "openalex_api_key": {"type": "string", "required": False, "description": "OpenAlex API key"},
            "openalex_mailto": {"type": "string", "required": False, "description": "OpenAlex polite-pool contact email"},
            "openalex_work_types": {"type": "string", "required": False, "description": "OpenAlex work types; comma/pipe-separated, or 'all'"},
            "strict_discovery": {"type": "boolean", "required": False, "description": "Fail when provider errors prevent a reliable candidate set"},
            "parallel_providers": {"type": "boolean", "required": False, "description": "Run different providers for the same query concurrently"},
            "provider_workers": {"type": "integer", "required": False, "description": "Max provider worker threads"},
            "output_csv": {"type": "string", "required": False, "description": "Unified candidate output CSV"},
            "trace_csv": {"type": "string", "required": False, "description": "Discovery trace CSV"},
            "report_md": {"type": "string", "required": False, "description": "Discovery report markdown"},
        },
    },
    "litminer_semantic_triage": {
        "handler": tool_semantic_triage,
        "description": "Annotate and rank candidates with LLM-supplied semantic concepts; rows are tagged, not deleted",
        "parameters": {
            "input_csv": {"type": "string", "required": True, "description": "Input candidate CSV path"},
            "output_csv": {"type": "string", "required": True, "description": "Triaged output CSV path"},
            "profile": {"type": "string", "required": False, "description": "JSON triage profile path"},
            "required_concepts": {"type": "array", "items": {"type": "string"}, "required": False, "description": "Required concepts, e.g. name=term1|term2"},
            "optional_concepts": {"type": "array", "items": {"type": "string"}, "required": False, "description": "Optional ranking concepts"},
            "negative_concepts": {"type": "array", "items": {"type": "string"}, "required": False, "description": "Caller-supplied negative tags; not hard deletions"},
            "year_from": {"type": "integer", "required": False, "description": "Minimum publication year metadata flag"},
            "year_to": {"type": "integer", "required": False, "description": "Maximum publication year metadata flag"},
            "require_doi": {"type": "boolean", "required": False, "description": "Mark missing DOI as metadata-blocking"},
            "exclude_article_types": {"type": "array", "items": {"type": "string"}, "required": False, "description": "Metadata article types to mark blocked"},
            "disable_regex_concepts": {"type": "boolean", "required": False, "description": "Reject re: concepts instead of compiling caller regex"},
        },
    },
    "litminer_filter_journal_metrics": {
        "handler": tool_filter_journal_metrics,
        "description": "Annotate and filter candidates by verified journal metrics",
        "parameters": {
            "input_csv": {"type": "string", "required": True, "description": "Input CSV path"},
            "output_csv": {"type": "string", "required": True, "description": "Annotated output CSV"},
            "metrics_csv": {"type": "string", "required": False, "description": "Verified metrics CSV"},
            "min_if": {"type": "number", "required": False, "description": "Pass requires IF > min_if"},
            "pass_output_csv": {"type": "string", "required": False, "description": "Metric-pass output CSV"},
            "backup_output_csv": {"type": "string", "required": False, "description": "Metric-fail/unverified output CSV"},
        },
    },
    "litminer_build_publisher_queue": {
        "handler": tool_build_publisher_queue,
        "description": "Build DOI-based publisher-page evidence queue",
        "parameters": {
            "input_csv": {"type": "string", "required": True, "description": "Input CSV path"},
            "output_csv": {"type": "string", "required": True, "description": "Queue output CSV"},
            "priorities": {"type": "array", "items": {"type": "string"}, "required": False, "description": "Triage priorities to queue"},
            "statuses": {"type": "array", "items": {"type": "string"}, "required": False, "description": "candidate_status values to queue"},
            "decisions": {"type": "array", "items": {"type": "string"}, "required": False, "description": "Legacy screening decisions to queue"},
            "include_metadata_blocked": {"type": "boolean", "required": False, "description": "Also queue metadata_status=blocked rows"},
            "fields_needed": {"type": "array", "items": {"type": "string"}, "required": False, "description": "Task-specific fields requested from publisher pages"},
            "page_required_fields": {"type": "array", "items": {"type": "string"}, "required": False, "description": "Generic publisher-page evidence fields"},
            "screenshot_root": {"type": "string", "required": False, "description": "Screenshot root directory"},
            "allow_missing_doi": {"type": "boolean", "required": False, "description": "Queue rows without DOI"},
        },
    },
    "litminer_probe_publishers": {
        "handler": tool_probe_publishers,
        "description": "Resolve DOI landing pages and detect access/PDF/SI status",
        "parameters": {
            "input_csv": {"type": "string", "required": True, "description": "Publisher queue CSV"},
            "output_csv": {"type": "string", "required": True, "description": "Probed queue output CSV"},
            "limit": {"type": "integer", "required": False, "description": "Max rows to probe"},
            "sleep_s": {"type": "number", "required": False, "description": "Delay between requests"},
        },
    },
    "litminer_import_websearch": {
        "handler": tool_import_websearch,
        "description": "Normalize WebSearch leads into unverified candidate rows for Crossref/publisher verification",
        "parameters": {
            "input_csv": {"type": "string", "required": True, "description": "Raw WebSearch result CSV"},
            "output_csv": {"type": "string", "required": True, "description": "Normalized candidate CSV"},
            "query": {"type": "string", "required": False, "description": "Default query when input lacks a query column"},
        },
    },
    "litminer_processing_report": {
        "handler": tool_processing_report,
        "description": "Generate a compact source/metadata/triage/access summary for a Litminer output directory",
        "parameters": {
            "output_dir": {"type": "string", "required": True, "description": "Litminer output directory"},
            "output": {"type": "string", "required": False, "description": "Report markdown path"},
        },
    },
    "litminer_read_csv_summary": {
        "handler": tool_read_csv_summary,
        "description": "Read a compact, paginated CSV summary for Agent review",
        "parameters": {
            "input_csv": {"type": "string", "required": True, "description": "CSV path inside the workspace"},
            "page": {"type": "integer", "required": False, "description": "1-based page number"},
            "page_size": {"type": "integer", "required": False, "description": "Rows per page; capped to 200"},
            "columns": {"type": "array", "items": {"type": "string"}, "required": False, "description": "Columns to include"},
            "priority": {"type": "string", "required": False, "description": "Comma-separated triage_priority filter"},
            "candidate_status": {"type": "string", "required": False, "description": "Comma-separated candidate_status filter"},
            "metadata_status": {"type": "string", "required": False, "description": "Comma-separated metadata_status filter"},
            "sort_by": {"type": "string", "required": False, "description": "Column to sort by"},
            "sort_desc": {"type": "boolean", "required": False, "description": "Sort descending"},
            "max_cell_chars": {"type": "integer", "required": False, "description": "Maximum characters per returned cell"},
        },
    },
    "litminer_run_lit_search": {
        "handler": tool_run_lit_search,
        "description": "Run API-first discovery, semantic triage, metadata verification, IF annotation, queueing, and reporting",
        "parameters": {
            "input_csv": {"type": "string", "required": False, "description": "Existing candidate CSV"},
            "queries": {"type": "array", "items": {"type": "string"}, "required": False, "description": "Search queries"},
            "query_file": {"type": "string", "required": False, "description": "Query file path"},
            "year_from": {"type": "integer", "required": False, "description": "Minimum year"},
            "year_to": {"type": "integer", "required": False, "description": "Maximum year"},
            "output_dir": {"type": "string", "required": False, "description": "Output directory"},
            "config": {"type": "string", "required": False, "description": "Runtime infrastructure config JSON"},
            "discovery_sources": {"type": "string", "required": False, "description": "Comma-separated API providers"},
            "include_arxiv": {"type": "boolean", "required": False, "description": "Run arXiv discovery too"},
            "include_europe_pmc": {"type": "boolean", "required": False, "description": "Run Europe PMC discovery too"},
            "triage_profile": {"type": "string", "required": False, "description": "JSON semantic triage profile"},
            "required_concepts": {"type": "array", "items": {"type": "string"}, "required": False, "description": "Required semantic concepts"},
            "optional_concepts": {"type": "array", "items": {"type": "string"}, "required": False, "description": "Optional semantic concepts"},
            "negative_concepts": {"type": "array", "items": {"type": "string"}, "required": False, "description": "Negative semantic tags"},
            "exclude_article_types": {"type": "array", "items": {"type": "string"}, "required": False, "description": "Metadata article types to mark blocked"},
            "queue_priorities": {"type": "string", "required": False, "description": "Comma-separated triage priorities"},
            "include_metadata_blocked": {"type": "boolean", "required": False, "description": "Also verify/queue metadata_status=blocked rows"},
            "fields_needed": {"type": "array", "items": {"type": "string"}, "required": False, "description": "Task-specific publisher-page fields"},
            "page_required_fields": {"type": "array", "items": {"type": "string"}, "required": False, "description": "Generic publisher-page evidence fields"},
            "openalex_api_key": {"type": "string", "required": False, "description": "OpenAlex API key"},
            "openalex_mailto": {"type": "string", "required": False, "description": "OpenAlex polite-pool contact email"},
            "openalex_work_types": {"type": "string", "required": False, "description": "OpenAlex work types; comma/pipe-separated, or 'all'"},
            "max_results_per_query": {"type": "integer", "required": False, "description": "Max results per discovery provider/query"},
            "skip_openalex": {"type": "boolean", "required": False, "description": "Skip OpenAlex discovery"},
            "include_semantic_scholar": {"type": "boolean", "required": False, "description": "Run Semantic Scholar too"},
            "semantic_query_limit": {"type": "integer", "required": False, "description": "Max query count for Semantic Scholar"},
            "semantic_max_results": {"type": "integer", "required": False, "description": "Semantic Scholar max results per query"},
            "skip_crossref": {"type": "boolean", "required": False, "description": "Skip Crossref verification"},
            "strict_discovery": {"type": "boolean", "required": False, "description": "Fail when provider errors prevent a reliable candidate set"},
            "parallel_providers": {"type": "boolean", "required": False, "description": "Run different providers for the same query concurrently"},
            "provider_workers": {"type": "integer", "required": False, "description": "Max provider worker threads"},
            "enrich_unpaywall": {"type": "boolean", "required": False, "description": "Annotate verified rows with Unpaywall OA links"},
            "skip_unpaywall": {"type": "boolean", "required": False, "description": "Disable Unpaywall annotation"},
            "unpaywall_email": {"type": "string", "required": False, "description": "Unpaywall email"},
            "unpaywall_sleep": {"type": "number", "required": False, "description": "Delay between Unpaywall requests"},
            "metrics_csv": {"type": "string", "required": False, "description": "Verified metrics CSV"},
            "min_if": {"type": "number", "required": False, "description": "Minimum IF threshold"},
            "skip_journal_metrics": {"type": "boolean", "required": False, "description": "Disable journal metric annotation/filtering"},
            "target_count": {"type": "integer", "required": False, "description": "Requested count"},
            "queue_strict_only": {"type": "boolean", "required": False, "description": "Queue only metric-pass rows"},
            "allow_missing_doi": {"type": "boolean", "required": False, "description": "Allow rows without DOI into queue"},
            "screenshot_root": {"type": "string", "required": False, "description": "Screenshot root directory"},
            "probe_publishers": {"type": "boolean", "required": False, "description": "Probe DOI/publisher pages"},
            "probe_limit": {"type": "integer", "required": False, "description": "Max publisher rows to probe"},
            "probe_sleep": {"type": "number", "required": False, "description": "Delay between publisher probe requests"},
        },
    },
}


# JSON-RPC handler (MCP protocol subset)

def handle_request(request: dict) -> dict | None:
    """Handle a JSON-RPC request."""
    method = request.get("method", "")

    # tools/list
    if method == "tools/list":
        tools_list = []
        for name, tool in TOOLS.items():
            properties = {
                key: {k: v for k, v in schema.items() if k != "required"}
                for key, schema in tool["parameters"].items()
            }
            tools_list.append({
                "name": name,
                "description": tool["description"],
                "inputSchema": {
                    "type": "object",
                    "properties": properties,
                    "required": [k for k, v in tool["parameters"].items() if v.get("required")],
                },
            })
        return {
            "jsonrpc": "2.0",
            "id": request.get("id"),
            "result": {"tools": tools_list},
        }

    # tools/call
    if method == "tools/call":
        tool_name = request.get("params", {}).get("name", "")
        arguments = request.get("params", {}).get("arguments", {})

        if tool_name not in TOOLS:
            return _jsonrpc_error(request, -32601, f"Unknown tool: {tool_name}")

        try:
            result = TOOLS[tool_name]["handler"](arguments)
            return {
                "jsonrpc": "2.0",
                "id": request.get("id"),
                "result": {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]},
            }
        except Exception as e:
            return _jsonrpc_error(request, -32000, str(e), exc=e)

    # initialize
    if method == "initialize":
        requested_version = (
            request.get("params", {}).get("protocolVersion")
            or DEFAULT_PROTOCOL_VERSION
        )
        if requested_version not in SUPPORTED_PROTOCOL_VERSIONS:
            return _jsonrpc_error(
                request,
                -32602,
                f"Unsupported protocolVersion: {requested_version}. "
                f"Supported: {', '.join(sorted(SUPPORTED_PROTOCOL_VERSIONS))}",
            )
        return {
            "jsonrpc": "2.0",
            "id": request.get("id"),
            "result": {
                "protocolVersion": requested_version,
                "serverInfo": {"name": "litminer", "version": __version__},
                "capabilities": {"tools": {"listChanged": False}},
            },
        }

    # Notifications have no id; silently accept with no response.
    if request.get("id") is None:
        return None  # MCP notifications require no response

    return _jsonrpc_error(request, -32601, f"Unknown method: {method}")


# Main (stdio transport)

def main() -> None:
    """Run the MCP server over stdio."""
    print("Litminer MCP Server starting on stdio", file=sys.stderr)
    for raw_line in sys.stdin.buffer:
        if len(raw_line) > MAX_STDIN_LINE_BYTES:
            print(json.dumps({
                "jsonrpc": "2.0",
                "id": None,
                "error": {
                    "code": -32700,
                    "message": f"Input line exceeds {MAX_STDIN_LINE_BYTES} bytes",
                },
            }), flush=True)
            continue
        line = raw_line.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            response = handle_request(request)
            if response is not None:
                print(json.dumps(response), flush=True)
        except json.JSONDecodeError as e:
            print(json.dumps({
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": f"Parse error: {e}"},
            }), flush=True)


if __name__ == "__main__":
    main()
