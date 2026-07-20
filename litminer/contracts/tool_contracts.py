"""Single source for Agent-facing tool JSON schemas and descriptions.

The strict schemas are Litminer's executable contract. Some Agent clients
accept only a conservative JSON-Schema subset for tool declarations, so the
schema advertised over MCP is derived from the strict schema without
top-level composition keywords. Runtime validation always uses the strict
schema and therefore keeps the input-family and identifier constraints.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


CLIENT_UNSUPPORTED_TOP_LEVEL_KEYWORDS = frozenset({
    "allOf",
    "anyOf",
    "not",
    "oneOf",
})


def _s(description: str, **extra: Any) -> dict[str, Any]:
    return {"type": "string", "description": description, **extra}


def _i(description: str, **extra: Any) -> dict[str, Any]:
    return {"type": "integer", "description": description, **extra}


def _n(description: str, **extra: Any) -> dict[str, Any]:
    return {"type": "number", "description": description, **extra}


def _b(description: str) -> dict[str, Any]:
    return {"type": "boolean", "description": description}


def _sa(description: str, **extra: Any) -> dict[str, Any]:
    return {"type": "array", "items": {"type": "string"}, "description": description, **extra}


RUN_PROPERTIES: dict[str, dict[str, Any]] = {
    "input_csv": _s("Existing candidate CSV inside the workspace. Mutually exclusive with queries/query_file."),
    "queries": _sa("Discovery queries. Required unless input_csv or query_file is supplied.", minItems=1),
    "query_file": _s("Workspace file containing one discovery query per line."),
    "year_from": _i("Minimum publication year."),
    "year_to": _i("Maximum publication year."),
    "output_dir": _s("Output directory for a new or resumed run."),
    "merge_into": _s("Existing Litminer output directory to create a new research iteration in."),
    "config": _s("Runtime infrastructure config JSON."),
    "mode": _s("Runtime preset.", enum=["fast", "balanced", "expanded", "full"]),
    "resume": _b("Resume an interrupted run with the same signature; cannot be combined with merge_into."),
    "resume_allow_mismatch": _b("Allow resume despite signature mismatch; requires an audit reason."),
    "resume_mismatch_reason": _s("Audit reason for resume_allow_mismatch."),
    "time_budget_seconds": _n("Stop cleanly at a stage boundary after this wall-clock budget.", minimum=0),
    "stop_after_stage": _s("Stop after a named stage and emit partial artifacts."),
    "discovery_sources": _s("Comma-separated providers: openalex, semantic_scholar, arxiv, europe_pmc."),
    "include_arxiv": _b("Include arXiv discovery."),
    "include_europe_pmc": _b("Include Europe PMC discovery."),
    "include_semantic_scholar": _b("Include Semantic Scholar discovery."),
    "skip_openalex": _b("Disable OpenAlex discovery."),
    "triage_profile": _s("JSON semantic triage profile."),
    "required_concepts": _sa("Required semantic concepts such as name=term1|term2."),
    "optional_concepts": _sa("Optional semantic ranking concepts."),
    "negative_concepts": _sa("Negative review tags; rows are not silently deleted."),
    "enable_regex_concepts": _b("Allow reviewed re: concepts; disabled by default."),
    "exclude_article_types": _sa("Metadata article types to mark blocked."),
    "queue_priorities": _s("Comma-separated triage priorities for downstream selection."),
    "include_metadata_blocked": _b("Include metadata-blocked rows in verification/queues."),
    "fields_needed": _sa("Task-specific publisher-page fields."),
    "page_required_fields": _sa("Generic publisher-page evidence fields."),
    "openalex_api_key": _s("OpenAlex API key; normally inherited from environment."),
    "openalex_mailto": _s("OpenAlex polite-pool contact email."),
    "openalex_work_types": _s("OpenAlex work types; comma/pipe-separated or all."),
    "max_results_per_query": _i("Maximum candidates per provider/query.", minimum=1),
    "semantic_query_limit": _i("Maximum queries sent to Semantic Scholar.", minimum=0),
    "semantic_max_results": _i("Semantic Scholar maximum results per query.", minimum=1),
    "skip_crossref": _b("Disable Crossref bibliographic verification."),
    "strict_discovery": _b("Fail when provider failures prevent a reliable candidate set."),
    "parallel_providers": _b("Run different providers concurrently; provider scheduler limits still apply."),
    "provider_workers": _i("Maximum provider worker threads.", minimum=1),
    "provider_failure_threshold": _i("Skip remaining calls after repeated provider failures.", minimum=1),
    "provider_rate_limit_cooldown_seconds": _n("Fallback cooldown when Retry-After is absent.", minimum=0),
    "cache_dir": _s("Workspace-local metadata and short-lived failure cache."),
    "cache_ttl_days": _n("Positive metadata cache TTL in days.", minimum=0),
    "provider_failure_cache_ttl_seconds": _n("Exact-query transient failure cache TTL.", minimum=0),
    "cache_enabled": _b("Enable or disable metadata/failure caches."),
    "no_cache": _b("Disable caches for this invocation."),
    "crossref_checkpoint_interval": _i("Write Crossref progress every N rows.", minimum=0),
    "unpaywall_checkpoint_interval": _i("Write Unpaywall progress every N rows.", minimum=0),
    "max_crossref_rows": _i("Budget for unresolved rows from verification_queue; reusable results do not consume it.", minimum=0),
    "max_unpaywall_rows": _i("Unpaywall row budget.", minimum=0),
    "enrich_unpaywall": _b("Annotate verified rows with Unpaywall OA links."),
    "skip_unpaywall": _b("Disable Unpaywall annotation."),
    "unpaywall_email": _s("Unpaywall contact email."),
    "unpaywall_sleep": _n("Minimum delay between Unpaywall requests.", minimum=0),
    "metrics_csv": _s("Verified journal metrics CSV."),
    "min_if": _n("Minimum impact-factor threshold."),
    "skip_journal_metrics": _b("Disable journal metric annotation/filtering."),
    "target_count": _i("Requested publisher-queue count.", minimum=0),
    "queue_strict_only": _b("Queue only metric-pass rows when metric filtering is active."),
    "allow_missing_doi": _b("Allow rows without DOI into manual publisher queue."),
    "screenshot_root": _s("Screenshot root directory."),
    "probe_publishers": _b("Probe DOI/publisher pages."),
    "probe_limit": _i("Maximum publisher rows to probe.", minimum=0),
    "max_publisher_probe_rows": _i("Publisher probing row budget.", minimum=0),
    "probe_sleep": _n("Minimum delay between publisher probes.", minimum=0),
    "expand_citations": _b("Run one-hop citation/reference expansion."),
    "expand_seeds": _s("Comma-separated explicit seed DOIs."),
    "expand_top_n": _i("Mechanical high-priority seed limit.", minimum=1),
    "expand_max_per_seed": _i("Maximum expanded papers per seed.", minimum=1),
    "expand_direction": _s("Citation expansion direction.", enum=["forward", "backward", "both"]),
    "state_store": _s("Workspace-local SQLite state-store path."),
    "state_enabled": _b("Enable SQLite runtime state and request ledger."),
    "export": _sa("Optional finalize exports.", minItems=1),
    "include_unverified_export": _b("Include bibliographically unverified rows in exports and audit the risk."),
    "ascii_latex": _b("Transliterate/escape BibTeX for ASCII-oriented LaTeX workflows."),
}


def run_input_schema() -> dict[str, Any]:
    discovery_family = {
        "anyOf": [
            {"required": ["queries"]},
            {"required": ["query_file"]},
        ],
        "not": {"required": ["input_csv"]},
    }
    import_family = {
        "required": ["input_csv"],
        "not": {
            "anyOf": [
                {"required": ["queries"]},
                {"required": ["query_file"]},
            ]
        },
    }
    return {
        "type": "object",
        "properties": deepcopy(RUN_PROPERTIES),
        "oneOf": [discovery_family, import_family],
    }


def resume_input_schema() -> dict[str, Any]:
    """Run schema plus the persistent output directory required for resume."""
    schema = run_input_schema()
    schema["required"] = ["output_dir"]
    return schema


def object_schema(properties: dict[str, dict[str, Any]], required: list[str] | None = None, **extra: Any) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": deepcopy(properties),
        "required": list(required or []),
        **extra,
    }


TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "litminer_run_lit_search": run_input_schema(),
    "litminer_start_run": run_input_schema(),
    "litminer_resume_run": resume_input_schema(),
    "litminer_plan_run": run_input_schema(),
    "litminer_capabilities": object_schema({
        "providers": _sa("Providers to inspect; defaults to all registered providers."),
        "live": _b("Perform explicit low-cost network preflight and record its request."),
        "state_store": _s("Optional workspace-local SQLite state-store path."),
    }),
    "litminer_get_run": object_schema({
        "job_id": _s("Background job ID."),
        "run_id": _s("Persistent run ID."),
        "output_dir": _s("Run output directory."),
        "state_store": _s("Optional workspace-local SQLite state-store path."),
    }, anyOf=[{"required": ["job_id"]}, {"required": ["run_id"]}, {"required": ["output_dir"]}]),
    "litminer_run_status": object_schema({
        "job_id": _s("Background job ID returned by litminer_start_run."),
    }, required=["job_id"]),
    "litminer_cancel_run": object_schema({
        "job_id": _s("Live background job ID returned by litminer_start_run."),
    }, required=["job_id"]),
    "litminer_read_results": object_schema({
        "output_dir": _s("Litminer run output directory."),
        "artifact": _s("Artifact name.", enum=[
            "canonical_papers", "triaged_candidates", "publisher_queue",
            "coverage_report", "canonical_provenance", "agent_summary", "run_outcome",
            "processing_report", "search_audit_report", "export_manifest",
        ]),
        "page": _i("1-based page.", minimum=1),
        "page_size": _i("Rows per page, capped to 200.", minimum=1, maximum=200),
        "columns": _sa("CSV columns to include."),
        "max_chars": _i("Maximum characters for JSON/Markdown artifacts.", minimum=100, maximum=200000),
    }, required=["output_dir", "artifact"]),
    "litminer_export": object_schema({
        "input_csv": _s("Canonical or triaged Litminer CSV."),
        "output_dir": _s("Run directory containing canonical_papers.csv."),
        "formats": _sa("Export formats: ris and/or bibtex.", minItems=1),
        "output_prefix": _s(
            "Optional plain output file prefix without path or reserved filename characters.",
            minLength=1,
            pattern=r'^(?!\.\.?$)(?!.*[ .]$)[^<>:"/\\|?*\u0000-\u001F]+$',
        ),
        "include_unverified": _b("Include unverified rows and record them in export_manifest.json."),
        "ascii_latex": _b("Use ASCII-oriented BibTeX escaping."),
    }, required=["formats"], oneOf=[{"required": ["input_csv"]}, {"required": ["output_dir"]}]),
}


TOOL_DESCRIPTIONS = {
    "litminer_run_lit_search": (
        "Run the complete Litminer workflow synchronously. Supply exactly one input family: "
        "queries/query_file for discovery, or input_csv for imported candidates. Use merge_into for a new "
        "research iteration and resume only for an interrupted run with the same signature."
    ),
    "litminer_start_run": (
        "Start the complete Litminer workflow in a background job. Uses the same RunSpec schema as the CLI "
        "and returns job_id, run_id, status, quality, and next_actions."
    ),
    "litminer_resume_run": (
        "Resume an interrupted background run with the same input and signature. Do not use for changed queries, "
        "concepts, sources, or merge_into iterations."
    ),
    "litminer_plan_run": "Validate and normalize a RunSpec without executing provider requests or writing research artifacts.",
    "litminer_capabilities": "Inspect provider capabilities, credential/contact readiness, persisted health, and optional live preflight.",
    "litminer_get_run": "Read persistent background/run status, quality, coverage, artifacts, and next_actions.",
    "litminer_read_results": "Read paginated canonical/triage/queue results or bounded JSON/Markdown run artifacts.",
    "litminer_export": "Export canonical Litminer bibliography to RIS and/or BibTeX with an audited export manifest.",
}


def schema_for(tool_name: str) -> dict[str, Any] | None:
    """Return the strict server-side validation schema for a tool."""
    schema = TOOL_SCHEMAS.get(tool_name)
    return deepcopy(schema) if schema is not None else None


def client_schema_for(tool_name: str) -> dict[str, Any] | None:
    """Return a cross-client MCP declaration schema.

    Claude Code currently rejects tools whose *top-level* input schema uses
    composition keywords such as ``oneOf`` or ``anyOf``. Removing those
    declaration-only keywords broadens what the client may propose, but does
    not broaden what Litminer accepts: ``protocol.handle_request`` validates
    every call against :func:`schema_for` before invoking a handler.
    """
    schema = schema_for(tool_name)
    if schema is None:
        return None
    for keyword in CLIENT_UNSUPPORTED_TOP_LEVEL_KEYWORDS:
        schema.pop(keyword, None)
    return schema


def client_schema_issues(schema: dict[str, Any]) -> list[str]:
    """Return top-level keywords known to make primary clients drop a tool."""
    return sorted(CLIENT_UNSUPPORTED_TOP_LEVEL_KEYWORDS.intersection(schema))


def description_for(tool_name: str, fallback: str = "") -> str:
    return TOOL_DESCRIPTIONS.get(tool_name, fallback)
