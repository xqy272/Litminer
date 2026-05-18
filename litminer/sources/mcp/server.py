#!/usr/bin/env python3
"""Litminer MCP server for API discovery and LLM-facing workflow tools.

This server wraps the Litminer API sources (OpenAlex, Semantic Scholar, arXiv,
Europe PMC, Crossref, Unpaywall) and deterministic engine scripts as
MCP-style JSON-RPC tools.

Usage:
    python -m litminer.sources.mcp.server

Tool surface:
    By default, tools/list advertises the compact workflow profile controlled
    by LITMINER_MCP_TOOL_PROFILE=workflow. Set LITMINER_MCP_TOOL_PROFILE=all to
    advertise lower-level source, stage, and debug tools.

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
import uuid
from pathlib import Path
from typing import Any

# Add repository root to path so this file also works when launched directly by
# absolute path from an Agent MCP config.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from litminer import __version__
from litminer.engine import workspace
from litminer.engine.common import write_text_atomic

DEFAULT_PROTOCOL_VERSION = "2025-11-25"
SUPPORTED_PROTOCOL_VERSIONS = {DEFAULT_PROTOCOL_VERSION, "2024-11-05"}
MAX_STDIN_LINE_BYTES = int(os.environ.get("LITMINER_MCP_MAX_LINE_BYTES", str(16 * 1024 * 1024)))
MCP_TOOL_PROFILE_ENV = "LITMINER_MCP_TOOL_PROFILE"
DEFAULT_MCP_TOOL_PROFILE = "workflow"
WORKFLOW_TOOL_NAMES = [
    "litminer_workspace_doctor",
    "litminer_bootstrap",
    "litminer_run_lit_search",
    "litminer_start_run",
    "litminer_run_status",
    "litminer_resume_run",
    "litminer_cancel_run",
    "litminer_discover_api",
    "litminer_semantic_triage",
    "litminer_build_publisher_queue",
    "litminer_processing_report",
    "litminer_agent_summary",
    "litminer_read_csv_summary",
]

_import_lock = threading.Lock()
_jobs_lock = threading.Lock()
JOBS: dict[str, dict[str, Any]] = {}


def _safe_job_id(job_id: str) -> str:
    if not job_id or not all(ch.isalnum() or ch in "-_" for ch in job_id):
        raise ValueError(f"invalid Litminer job_id: {job_id!r}")
    return job_id


def _job_record_path(job_id: str) -> Path:
    return _workspace_root() / ".litminer" / "jobs" / f"{_safe_job_id(job_id)}.json"


def _public_job_record(job: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in job.items()
        if key not in {"cancel_event", "thread"} and not key.startswith("_")
    }


def _persist_job_unlocked(job: dict[str, Any]) -> None:
    job_id = str(job.get("job_id") or "")
    if not job_id:
        return
    path = _job_record_path(job_id)
    write_text_atomic(path, json.dumps(_public_job_record(job), indent=2, ensure_ascii=False) + "\n")


def _update_job(job_id: str, **fields: Any) -> None:
    with _jobs_lock:
        if job_id not in JOBS:
            raise ValueError(f"unknown Litminer job_id: {job_id}")
        JOBS[job_id].update(fields)
        _persist_job_unlocked(JOBS[job_id])


def _load_persisted_job(job_id: str) -> dict[str, Any]:
    path = _job_record_path(job_id)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    if data.get("status") in {"queued", "running", "cancelling"}:
        data = {
            **data,
            "status": "interrupted",
            "note": "This job record was loaded from disk, but no live MCP worker owns it.",
        }
    return data


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
_get_engine_agent_summary = _lazy_import("litminer.engine.agent_summary")
_get_engine_run_lit_search = _lazy_import("litminer.engine.run_lit_search")
_get_engine_doctor = _lazy_import("litminer.engine.doctor")
_get_engine_bootstrap = _lazy_import("litminer.engine.bootstrap")
_get_engine_publisher_adapters = _lazy_import("litminer.engine.publisher_adapters")
_get_engine_provenance = _lazy_import("litminer.engine.provenance")


def _workspace_root() -> Path:
    """Return the user workspace root for MCP file operations."""
    return workspace.workspace_root()


def _workspace_escape_message(label: str, value: str, root: Path, resolved: Path) -> str:
    return (
        f"{label} escapes Litminer workspace: requested={value!r}; "
        f"workspace_root={root}; resolved_path={resolved}. "
        f"Use a path under {workspace.WORKSPACE_ENV}, move the file into the workspace, "
        f"or set {workspace.WORKSPACE_ENV} to the project directory that contains the file."
    )


def _workspace_missing_message(label: str, value: str, root: Path, resolved: Path) -> str:
    return (
        f"{label} not found: requested={value!r}; "
        f"workspace_root={root}; resolved_path={resolved}. "
        "Use a workspace-relative path or place the file under the configured workspace root."
    )


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
        raise ValueError(_workspace_escape_message(label, str(value), root, resolved)) from exc
    if must_exist and not resolved.exists():
        raise FileNotFoundError(_workspace_missing_message(label, str(value), root, resolved))
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
        provider_failure_threshold=args.get("provider_failure_threshold"),
        provider_rate_limit_cooldown_seconds=args.get("provider_rate_limit_cooldown_seconds", 60.0),
        provider_failure_cache_dir=_optional_workspace_path(args.get("cache_dir"), "cache_dir"),
        provider_failure_cache_enabled=not bool(args.get("no_cache", False)),
        provider_failure_cache_ttl_seconds=args.get("provider_failure_cache_ttl_seconds"),
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
        allow_regex=bool(args.get("enable_regex_concepts") or args.get("allow_regex_concepts", False))
        and not bool(args.get("disable_regex_concepts", False)),
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


def tool_validate_journal_metrics(args: dict) -> dict:
    """Validate journal metrics CSV governance fields and duplicate mappings."""
    mod = _get_engine_journal_metrics()
    metrics_path = _optional_workspace_path(args.get("metrics_csv"), "metrics_csv", must_exist=True) or mod.DEFAULT_METRICS
    result = mod.validate_metrics(metrics_path, require_numeric_if=bool(args.get("require_numeric_if", False)))
    return {"status": result["status"], "metrics_csv": str(metrics_path), **result}


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


def tool_agent_summary(args: dict) -> dict:
    """Generate or read a machine-readable Agent summary for a run directory."""
    mod = _get_engine_agent_summary()
    output_dir = _workspace_path(args["output_dir"], "output_dir", must_exist=True)
    output = _optional_workspace_path(args.get("output"), "output")
    path = mod.write_summary(output_dir, output_path=output)
    return {"status": "ok", "output": str(path), "summary": mod.build_summary(output_dir)}


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
        "crossref_cache_status",
        "unpaywall_status",
        "unpaywall_cache_status",
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


def tool_workspace_doctor(args: dict) -> dict:
    """Diagnose MCP workspace root, writability, and path mapping."""
    mod = _get_engine_doctor()
    raw_paths = args.get("paths") or []
    if isinstance(raw_paths, str):
        paths = [item.strip() for item in raw_paths.replace(";", ",").split(",") if item.strip()]
    else:
        paths = [str(item).strip() for item in raw_paths if str(item).strip()]
    workspace_root = _optional_workspace_path(args.get("workspace_root"), "workspace_root")
    report = mod.workspace_report(
        workspace=workspace_root,
        explain_paths=paths,
        create=bool(args.get("create_workspace", False)),
    )
    healthy = (
        report.get("workspace_exists")
        and report.get("workspace_is_dir")
        and report.get("workspace_writable")
    )
    return {"status": "ok" if healthy else "warning", **report}


def _run_namespace(args: dict):
    import argparse as _argparse
    return _argparse.Namespace(
        input_csv=_optional_workspace_path(args.get("input_csv"), "input_csv", must_exist=True),
        query=args.get("queries"),
        query_file=_optional_workspace_path(args.get("query_file"), "query_file", must_exist=True),
        year_from=args.get("year_from"),
        year_to=args.get("year_to"),
        output_dir=_optional_workspace_path(args.get("output_dir"), "output_dir"),
        mode=args.get("mode"),
        resume=args.get("resume", False),
        resume_allow_mismatch=args.get("resume_allow_mismatch", False),
        resume_mismatch_reason=args.get("resume_mismatch_reason", ""),
        time_budget_seconds=args.get("time_budget_seconds"),
        stop_after_stage=args.get("stop_after_stage"),
        discovery_sources=args.get("discovery_sources"),
        include_arxiv=args.get("include_arxiv"),
        include_europe_pmc=args.get("include_europe_pmc"),
        config=_optional_workspace_path(args.get("config"), "config", must_exist=True),
        triage_profile=_optional_workspace_path(args.get("triage_profile"), "triage_profile", must_exist=True),
        required_concept=args.get("required_concepts") or [],
        optional_concept=args.get("optional_concepts") or [],
        negative_concept=args.get("negative_concepts") or [],
        allow_regex_concepts=bool(args.get("enable_regex_concepts") or args.get("allow_regex_concepts", False))
        and not bool(args.get("disable_regex_concepts", False)),
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
        provider_failure_threshold=args.get("provider_failure_threshold"),
        provider_rate_limit_cooldown_seconds=args.get("provider_rate_limit_cooldown_seconds"),
        cache_dir=_optional_workspace_path(args.get("cache_dir"), "cache_dir"),
        cache_ttl_days=args.get("cache_ttl_days"),
        provider_failure_cache_ttl_seconds=args.get("provider_failure_cache_ttl_seconds"),
        cache_enabled=(False if args.get("no_cache") else args.get("cache_enabled")),
        crossref_checkpoint_interval=args.get("crossref_checkpoint_interval"),
        unpaywall_checkpoint_interval=args.get("unpaywall_checkpoint_interval"),
        max_crossref_rows=args.get("max_crossref_rows"),
        max_unpaywall_rows=args.get("max_unpaywall_rows"),
        metrics=_optional_workspace_path(args.get("metrics_csv"), "metrics_csv", must_exist=True),
        min_if=args.get("min_if"),
        skip_journal_metrics=args.get("skip_journal_metrics"),
        target_count=args.get("target_count"),
        queue_strict_only=args.get("queue_strict_only"),
        allow_missing_doi=args.get("allow_missing_doi"),
        screenshot_root=_optional_workspace_path(args.get("screenshot_root"), "screenshot_root"),
        probe_publishers=args.get("probe_publishers"),
        probe_limit=args.get("probe_limit"),
        max_publisher_probe_rows=args.get("max_publisher_probe_rows"),
        probe_sleep=args.get("probe_sleep"),
    )


def tool_run_lit_search(args: dict) -> dict:
    """Run the Agent-facing Litminer workflow."""
    mod = _get_engine_run_lit_search()
    ns = _run_namespace(args)
    result = mod.run(ns)
    run_status = result.pop("status", "completed")
    return {"status": "ok", "run_status": run_status, **result}


def _job_snapshot(job_id: str) -> dict[str, Any]:
    job_id = _safe_job_id(str(job_id or ""))
    with _jobs_lock:
        job = _public_job_record(JOBS.get(job_id) or {})
    if not job:
        job = _load_persisted_job(job_id)
    if not job:
        raise ValueError(f"unknown Litminer job_id: {job_id}")
    output_dir = job.get("output_dir")
    if output_dir:
        summary_path = Path(output_dir) / "agent_summary.json"
        if job.get("status") in {"completed", "partial", "failed"} and summary_path.exists():
            try:
                job["agent_summary"] = json.loads(summary_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                job["agent_summary_path"] = str(summary_path)
        else:
            job["agent_summary_path"] = str(summary_path)
    return job


def _run_job(job_id: str, ns: Any) -> None:
    mod = _get_engine_run_lit_search()
    _update_job(job_id, status="running", started_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    try:
        result = mod.run(ns)
        _update_job(
            job_id,
            status=result.get("status", "completed"),
            result=result,
            output_dir=result.get("output_dir", _job_snapshot(job_id).get("output_dir", "")),
            ended_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )
    except Exception as exc:
        _update_job(
            job_id,
            status="failed",
            error=str(exc),
            traceback=traceback.format_exc() if os.environ.get("LITMINER_MCP_DEBUG_ERRORS") else "",
            ended_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )


def tool_start_run(args: dict) -> dict:
    """Start a Litminer workflow in a background thread."""
    ns = _run_namespace(args)
    if getattr(ns, "output_dir", None) is None:
        ns = _get_engine_run_lit_search().normalize_args(ns)
    job_id = str(uuid.uuid4())
    cancel_event = threading.Event()
    ns.cancel_check = cancel_event.is_set
    with _jobs_lock:
        JOBS[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "output_dir": str(getattr(ns, "output_dir", "") or ""),
            "cancel_requested": False,
            "cancel_event": cancel_event,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        _persist_job_unlocked(JOBS[job_id])
    thread = threading.Thread(target=_run_job, args=(job_id, ns), daemon=False)
    with _jobs_lock:
        JOBS[job_id]["thread"] = thread
    thread.start()
    return {
        "status": "queued",
        "job_id": job_id,
        "output_dir": str(getattr(ns, "output_dir", "") or ""),
        "status_tool": "litminer_run_status",
    }


def tool_run_status(args: dict) -> dict:
    """Return background Litminer job status."""
    return _job_snapshot(str(args.get("job_id") or ""))


def tool_resume_run(args: dict) -> dict:
    """Start a background run with resume enabled."""
    resumed_args = dict(args)
    resumed_args["resume"] = True
    return tool_start_run(resumed_args)


def tool_cancel_run(args: dict) -> dict:
    """Request cancellation for a background run.

    Cancellation is cooperative at the job registry layer. Current engine
    stages finish their active provider call/stage before the run can stop.
    """
    job_id = str(args.get("job_id") or "")
    with _jobs_lock:
        if job_id not in JOBS:
            raise ValueError(f"unknown Litminer job_id: {job_id}")
        cancel_event = JOBS[job_id].get("cancel_event")
        if isinstance(cancel_event, threading.Event):
            cancel_event.set()
        JOBS[job_id]["cancel_requested"] = True
        if JOBS[job_id].get("status") in {"queued", "running"}:
            JOBS[job_id]["status"] = "cancelling"
        _persist_job_unlocked(JOBS[job_id])
    return {"status": "cancel_requested", "job_id": job_id, "note": "Engine will stop at the next stage boundary."}


def tool_bootstrap(args: dict) -> dict:
    """Generate first-run bootstrap reports."""
    mod = _get_engine_bootstrap()
    workspace_root = _optional_workspace_path(args.get("workspace_root"), "workspace_root")
    output_dir = _workspace_path(args.get("output_dir", ".litminer/bootstrap"), "output_dir")
    outputs = mod.write_reports(output_dir, workspace_root=workspace_root)
    return {"status": "ok", **outputs, "report": mod.build_report(workspace_root)}


def tool_publisher_adapters(args: dict) -> dict:
    """List publisher inspection adapter capabilities and boundaries."""
    mod = _get_engine_publisher_adapters()
    return {"status": "ok", "adapters": mod.adapter_rows()}


def tool_field_provenance(args: dict) -> dict:
    """Generate field-level provenance for a CSV."""
    mod = _get_engine_provenance()
    input_csv = _workspace_path(args["input_csv"], "input_csv", must_exist=True)
    output = _workspace_path(args.get("output", "field_provenance.json"), "output")
    path = mod.write_from_csv(input_csv, output)
    return {"status": "ok", "output": str(path)}


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
            "provider_failure_threshold": {"type": "integer", "required": False, "description": "Skip remaining provider calls after this many failures"},
            "provider_rate_limit_cooldown_seconds": {"type": "number", "required": False, "description": "Default cooldown for repeated calls to a rate-limited provider"},
            "cache_dir": {"type": "string", "required": False, "description": "Workspace-local cache directory for provider failure state"},
            "provider_failure_cache_ttl_seconds": {"type": "number", "required": False, "description": "TTL for cached provider failures"},
            "no_cache": {"type": "boolean", "required": False, "description": "Disable provider failure cache for this call"},
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
            "enable_regex_concepts": {"type": "boolean", "required": False, "description": "Allow re: semantic concepts; disabled by default"},
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
    "litminer_validate_journal_metrics": {
        "handler": tool_validate_journal_metrics,
        "description": "Validate journal metrics CSV required columns, source fields, numeric IF values, and duplicate aliases/ISSNs",
        "parameters": {
            "metrics_csv": {"type": "string", "required": False, "description": "Verified metrics CSV"},
            "require_numeric_if": {"type": "boolean", "required": False, "description": "Require every metric row to have numeric impact_factor"},
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
    "litminer_agent_summary": {
        "handler": tool_agent_summary,
        "description": "Generate a compact machine-readable Agent summary JSON for a Litminer output directory",
        "parameters": {
            "output_dir": {"type": "string", "required": True, "description": "Litminer output directory"},
            "output": {"type": "string", "required": False, "description": "Summary JSON path"},
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
    "litminer_workspace_doctor": {
        "handler": tool_workspace_doctor,
        "description": "Diagnose Litminer MCP workspace root, write access, and path mapping",
        "parameters": {
            "workspace_root": {"type": "string", "required": False, "description": "Optional workspace root to inspect"},
            "paths": {"type": "array", "items": {"type": "string"}, "required": False, "description": "Paths to explain relative to the workspace root"},
            "create_workspace": {"type": "boolean", "required": False, "description": "Create the workspace root if missing"},
        },
    },
    "litminer_bootstrap": {
        "handler": tool_bootstrap,
        "description": "Generate first-run Python/workspace/contact-email bootstrap reports for Agent environments",
        "parameters": {
            "workspace_root": {"type": "string", "required": False, "description": "Workspace root to inspect"},
            "output_dir": {"type": "string", "required": False, "description": "Bootstrap report output directory"},
        },
    },
    "litminer_publisher_adapters": {
        "handler": tool_publisher_adapters,
        "description": "List publisher inspection adapter capabilities and boundaries",
        "parameters": {},
    },
    "litminer_field_provenance": {
        "handler": tool_field_provenance,
        "description": "Generate field-level source/trust provenance JSON for a Litminer CSV",
        "parameters": {
            "input_csv": {"type": "string", "required": True, "description": "Input CSV path"},
            "output": {"type": "string", "required": False, "description": "Provenance JSON output path"},
        },
    },
    "litminer_start_run": {
        "handler": tool_start_run,
        "description": "Start the full Litminer workflow in a background job and return a job_id",
        "parameters": {},
    },
    "litminer_run_status": {
        "handler": tool_run_status,
        "description": "Inspect a background Litminer workflow job by job_id",
        "parameters": {
            "job_id": {"type": "string", "required": True, "description": "Job ID returned by litminer_start_run"},
        },
    },
    "litminer_resume_run": {
        "handler": tool_resume_run,
        "description": "Start a background Litminer workflow with resume enabled",
        "parameters": {},
    },
    "litminer_cancel_run": {
        "handler": tool_cancel_run,
        "description": "Request cooperative cancellation for a background Litminer job",
        "parameters": {
            "job_id": {"type": "string", "required": True, "description": "Job ID returned by litminer_start_run"},
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
            "mode": {"type": "string", "required": False, "description": "Runtime preset: fast, balanced, expanded, or full"},
            "resume": {"type": "boolean", "required": False, "description": "Reuse existing stage CSVs in output_dir"},
            "resume_allow_mismatch": {"type": "boolean", "required": False, "description": "Allow resume despite a manifest signature mismatch"},
            "resume_mismatch_reason": {"type": "string", "required": False, "description": "Audit note required with resume_allow_mismatch"},
            "time_budget_seconds": {"type": "number", "required": False, "description": "Stop cleanly after a stage once this budget is exhausted"},
            "stop_after_stage": {"type": "string", "required": False, "description": "Stop after query_plan, discovery, merge, dedupe, crossref, triage, selection, unpaywall, metrics, queue, or probe"},
            "discovery_sources": {"type": "string", "required": False, "description": "Comma-separated API providers"},
            "include_arxiv": {"type": "boolean", "required": False, "description": "Run arXiv discovery too"},
            "include_europe_pmc": {"type": "boolean", "required": False, "description": "Run Europe PMC discovery too"},
            "triage_profile": {"type": "string", "required": False, "description": "JSON semantic triage profile"},
            "required_concepts": {"type": "array", "items": {"type": "string"}, "required": False, "description": "Required semantic concepts"},
            "optional_concepts": {"type": "array", "items": {"type": "string"}, "required": False, "description": "Optional semantic concepts"},
            "negative_concepts": {"type": "array", "items": {"type": "string"}, "required": False, "description": "Negative semantic tags"},
            "enable_regex_concepts": {"type": "boolean", "required": False, "description": "Allow re: semantic concepts; disabled by default"},
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
            "provider_failure_threshold": {"type": "integer", "required": False, "description": "Skip remaining provider calls after this many failures"},
            "provider_rate_limit_cooldown_seconds": {"type": "number", "required": False, "description": "Default cooldown for repeated calls to a rate-limited provider"},
            "cache_dir": {"type": "string", "required": False, "description": "Workspace-local cache directory"},
            "cache_ttl_days": {"type": "number", "required": False, "description": "TTL for Crossref/Unpaywall metadata cache"},
            "provider_failure_cache_ttl_seconds": {"type": "number", "required": False, "description": "TTL for cached provider failures"},
            "cache_enabled": {"type": "boolean", "required": False, "description": "Enable or disable Litminer cache"},
            "no_cache": {"type": "boolean", "required": False, "description": "Disable Litminer cache for this run"},
            "crossref_checkpoint_interval": {"type": "integer", "required": False, "description": "Write Crossref progress every N rows"},
            "unpaywall_checkpoint_interval": {"type": "integer", "required": False, "description": "Write Unpaywall progress every N rows"},
            "max_crossref_rows": {"type": "integer", "required": False, "description": "Crossref row budget; remaining rows are marked skipped_budget"},
            "max_unpaywall_rows": {"type": "integer", "required": False, "description": "Unpaywall row budget; remaining rows are marked skipped_budget"},
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
            "max_publisher_probe_rows": {"type": "integer", "required": False, "description": "Publisher probing row budget used when probe_limit is not set"},
            "probe_sleep": {"type": "number", "required": False, "description": "Delay between publisher probe requests"},
        },
    },
}

for _async_tool_name in ("litminer_start_run", "litminer_resume_run"):
    TOOLS[_async_tool_name]["parameters"] = dict(TOOLS["litminer_run_lit_search"]["parameters"])


def _tool_profile() -> str:
    raw = os.environ.get(MCP_TOOL_PROFILE_ENV, DEFAULT_MCP_TOOL_PROFILE).strip().lower()
    if raw in {"", "default", "core"}:
        return DEFAULT_MCP_TOOL_PROFILE
    if raw in {"workflow", "all", "advanced", "debug"}:
        return raw
    return DEFAULT_MCP_TOOL_PROFILE


def _visible_tool_names() -> list[str]:
    profile = _tool_profile()
    if profile in {"all", "advanced", "debug"}:
        return list(TOOLS)
    return [name for name in WORKFLOW_TOOL_NAMES if name in TOOLS]


# JSON-RPC handler (MCP protocol subset)

def handle_request(request: dict) -> dict | None:
    """Handle a JSON-RPC request."""
    method = request.get("method", "")

    # tools/list
    if method == "tools/list":
        tools_list = []
        for name in _visible_tool_names():
            tool = TOOLS[name]
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
