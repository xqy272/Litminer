#!/usr/bin/env python3
"""Litminer MCP server for API discovery and LLM-facing workflow tools.

This server wraps the Litminer API sources (OpenAlex, Semantic Scholar, arXiv,
Europe PMC, Crossref, Unpaywall) and deterministic engine scripts as
MCP-style JSON-RPC tools.

Usage:
    python sources/mcp/server.py
    # Or with pip-installed MCP:
    pip install mcp --break-system-packages
    python sources/mcp/server.py

Tools exposed:
    litminer_search_openalex    - Search OpenAlex for papers
    litminer_search_arxiv       - Search arXiv preprints
    litminer_search_europe_pmc  - Search Europe PMC metadata
    litminer_discover_api       - Run multi-query API discovery with trace files
    litminer_verify_crossref    - Verify DOIs against Crossref
    litminer_lookup_unpaywall   - Find structured OA links for a DOI
    litminer_dedupe             - Deduplicate paper CSV
    litminer_semantic_triage    - Annotate/rank candidates with caller-supplied concepts
    litminer_processing_report  - Summarize workflow outputs for Agent review
    litminer_build_publisher_queue - Build DOI/publisher-page queues
    litminer_run_lit_search     - Run the full Agent-facing workflow

This server uses stdlib-only HTTP (no MCP SDK dependency) for maximum
portability. The MCP protocol is JSON-RPC over stdio or HTTP.

Architecture:
    Agent -> MCP -> sources/api/* and engine/*
"""

from __future__ import annotations

import json
import os
import sys
import threading
import traceback
from pathlib import Path
from typing import Any

# Add project root to path so we can import litminer modules
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
WORKSPACE_ROOT = PROJECT_ROOT.resolve()

DEFAULT_PROTOCOL_VERSION = "2025-11-25"

# Lazy imports: only load when a tool is called.
_openalex_search = None
_crossref_verify = None
_semantic_scholar = None
_arxiv_search = None
_europe_pmc_search = None
_unpaywall_lookup = None
_import_lock = threading.Lock()


def _workspace_path(value: str, label: str = "path", must_exist: bool = False) -> Path:
    """Resolve a user-supplied path and keep MCP file access inside the project."""
    if value is None or str(value).strip() == "":
        raise ValueError(f"{label} is required")
    path = Path(str(value))
    if not path.is_absolute():
        path = WORKSPACE_ROOT / path
    resolved = path.resolve(strict=False)
    try:
        resolved.relative_to(WORKSPACE_ROOT)
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


def _jsonrpc_error(request: dict, code: int, message: str, exc: Exception | None = None) -> dict:
    error: dict[str, Any] = {"code": code, "message": message}
    if exc is not None and os.environ.get("LITMINER_MCP_DEBUG_ERRORS"):
        error["data"] = traceback.format_exc()
    return {"jsonrpc": "2.0", "id": request.get("id"), "error": error}


def _get_openalex():
    global _openalex_search
    if _openalex_search is None:
        with _import_lock:
            if _openalex_search is None:  # double-check under lock
                from sources.api import openalex_search as _mod
                _openalex_search = _mod
    return _openalex_search


def _get_crossref():
    global _crossref_verify
    if _crossref_verify is None:
        with _import_lock:
            if _crossref_verify is None:  # double-check under lock
                from sources.api import crossref_verify as _mod
                _crossref_verify = _mod
    return _crossref_verify


def _get_semantic_scholar():
    global _semantic_scholar
    if _semantic_scholar is None:
        with _import_lock:
            if _semantic_scholar is None:
                from sources.api import semantic_scholar_search as _mod
                _semantic_scholar = _mod
    return _semantic_scholar


def _get_arxiv():
    global _arxiv_search
    if _arxiv_search is None:
        with _import_lock:
            if _arxiv_search is None:
                from sources.api import arxiv_search as _mod
                _arxiv_search = _mod
    return _arxiv_search


def _get_europe_pmc():
    global _europe_pmc_search
    if _europe_pmc_search is None:
        with _import_lock:
            if _europe_pmc_search is None:
                from sources.api import europe_pmc_search as _mod
                _europe_pmc_search = _mod
    return _europe_pmc_search


def _get_unpaywall():
    global _unpaywall_lookup
    if _unpaywall_lookup is None:
        with _import_lock:
            if _unpaywall_lookup is None:
                from sources.api import unpaywall_lookup as _mod
                _unpaywall_lookup = _mod
    return _unpaywall_lookup


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
        max_results=args.get("max_results", 200),
        api_key=args.get("api_key") or os.environ.get("OPENALEX_API_KEY"),
        mailto=args.get("mailto"),
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


_engine_dedupe = None
_engine_api_discovery = None
_engine_semantic_triage = None
_engine_journal_metrics = None
_engine_build_queue = None
_engine_publisher_probe = None
_engine_websearch_import = None
_engine_processing_report = None
_engine_run_lit_search = None


def _get_engine_dedupe():
    global _engine_dedupe
    if _engine_dedupe is None:
        with _import_lock:
            if _engine_dedupe is None:
                from engine import dedupe_papers as _mod
                _engine_dedupe = _mod
    return _engine_dedupe


def _get_engine_api_discovery():
    global _engine_api_discovery
    if _engine_api_discovery is None:
        with _import_lock:
            if _engine_api_discovery is None:
                from engine import api_discovery as _mod
                _engine_api_discovery = _mod
    return _engine_api_discovery


def _get_engine_semantic_triage():
    global _engine_semantic_triage
    if _engine_semantic_triage is None:
        with _import_lock:
            if _engine_semantic_triage is None:
                from engine import semantic_triage as _mod
                _engine_semantic_triage = _mod
    return _engine_semantic_triage


def _get_engine_journal_metrics():
    global _engine_journal_metrics
    if _engine_journal_metrics is None:
        with _import_lock:
            if _engine_journal_metrics is None:
                from engine import journal_metrics as _mod
                _engine_journal_metrics = _mod
    return _engine_journal_metrics


def _get_engine_build_queue():
    global _engine_build_queue
    if _engine_build_queue is None:
        with _import_lock:
            if _engine_build_queue is None:
                from engine import build_publisher_queue as _mod
                _engine_build_queue = _mod
    return _engine_build_queue


def _get_engine_publisher_probe():
    global _engine_publisher_probe
    if _engine_publisher_probe is None:
        with _import_lock:
            if _engine_publisher_probe is None:
                from engine import publisher_probe as _mod
                _engine_publisher_probe = _mod
    return _engine_publisher_probe


def _get_engine_websearch_import():
    global _engine_websearch_import
    if _engine_websearch_import is None:
        with _import_lock:
            if _engine_websearch_import is None:
                from engine import websearch_import as _mod
                _engine_websearch_import = _mod
    return _engine_websearch_import


def _get_engine_processing_report():
    global _engine_processing_report
    if _engine_processing_report is None:
        with _import_lock:
            if _engine_processing_report is None:
                from engine import processing_report as _mod
                _engine_processing_report = _mod
    return _engine_processing_report


def _get_engine_run_lit_search():
    global _engine_run_lit_search
    if _engine_run_lit_search is None:
        with _import_lock:
            if _engine_run_lit_search is None:
                from engine import run_lit_search as _mod
                _engine_run_lit_search = _mod
    return _engine_run_lit_search


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
    output_csv = _workspace_path(args.get("output_csv", "check/api_candidates.csv"), "output_csv")
    trace_csv = _optional_workspace_path(args.get("trace_csv"), "trace_csv") or output_csv.with_name("api_discovery_trace.csv")
    report_md = _optional_workspace_path(args.get("report_md"), "report_md") or output_csv.with_name("api_discovery_report.md")
    result = mod.discover_api(
        queries,
        output_csv,
        sources=mod.parse_sources(args.get("sources", "openalex")),
        year_from=args.get("year_from"),
        max_results_per_query=args.get("max_results_per_query", 100),
        semantic_query_limit=args.get("semantic_query_limit"),
        semantic_max_results=args.get("semantic_max_results"),
        openalex_api_key=args.get("openalex_api_key") or os.environ.get("OPENALEX_API_KEY"),
        openalex_mailto=args.get("openalex_mailto"),
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
        screenshot_root=str(_workspace_path(args.get("screenshot_root", "work/screenshots"), "screenshot_root")),
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
        metrics=_optional_workspace_path(args.get("metrics_csv"), "metrics_csv", must_exist=True),
        min_if=args.get("min_if"),
        target_count=args.get("target_count"),
        queue_strict_only=args.get("queue_strict_only", False),
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
            "max_results": {"type": "integer", "required": False, "description": "Max results (default 200)"},
            "api_key": {"type": "string", "required": False, "description": "OpenAlex API key"},
            "mailto": {"type": "string", "required": False, "description": "OpenAlex polite-pool contact email"},
        },
    },
    "litminer_search_semantic_scholar": {
        "handler": tool_search_semantic_scholar,
        "description": "Search Semantic Scholar or run one-hop citation/reference expansion",
        "parameters": {
            "query": {"type": "string", "required": False, "description": "Search query"},
            "year_from": {"type": "integer", "required": False, "description": "Minimum publication year"},
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
            "max_results": {"type": "integer", "required": False, "description": "Max results"},
        },
    },
    "litminer_search_europe_pmc": {
        "handler": tool_search_europe_pmc,
        "description": "Search Europe PMC biomedical/life-science literature metadata",
        "parameters": {
            "query": {"type": "string", "required": True, "description": "Europe PMC search query"},
            "year_from": {"type": "integer", "required": False, "description": "Minimum publication year"},
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
            "max_results_per_query": {"type": "integer", "required": False, "description": "Max results per query"},
            "semantic_query_limit": {"type": "integer", "required": False, "description": "Max query count for Semantic Scholar"},
            "semantic_max_results": {"type": "integer", "required": False, "description": "Semantic Scholar max results per query"},
            "openalex_api_key": {"type": "string", "required": False, "description": "OpenAlex API key"},
            "openalex_mailto": {"type": "string", "required": False, "description": "OpenAlex polite-pool contact email"},
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
            "max_results_per_query": {"type": "integer", "required": False, "description": "Max results per discovery provider/query"},
            "skip_openalex": {"type": "boolean", "required": False, "description": "Skip OpenAlex discovery"},
            "include_semantic_scholar": {"type": "boolean", "required": False, "description": "Run Semantic Scholar too"},
            "semantic_query_limit": {"type": "integer", "required": False, "description": "Max query count for Semantic Scholar"},
            "semantic_max_results": {"type": "integer", "required": False, "description": "Semantic Scholar max results per query"},
            "skip_crossref": {"type": "boolean", "required": False, "description": "Skip Crossref verification"},
            "enrich_unpaywall": {"type": "boolean", "required": False, "description": "Annotate verified rows with Unpaywall OA links"},
            "skip_unpaywall": {"type": "boolean", "required": False, "description": "Disable Unpaywall annotation"},
            "unpaywall_email": {"type": "string", "required": False, "description": "Unpaywall email"},
            "unpaywall_sleep": {"type": "number", "required": False, "description": "Delay between Unpaywall requests"},
            "metrics_csv": {"type": "string", "required": False, "description": "Verified metrics CSV"},
            "min_if": {"type": "number", "required": False, "description": "Minimum IF threshold"},
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
        return {
            "jsonrpc": "2.0",
            "id": request.get("id"),
            "result": {
                "protocolVersion": requested_version,
                "serverInfo": {"name": "litminer", "version": "1.0.0"},
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
    for line in sys.stdin:
        line = line.strip()
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
