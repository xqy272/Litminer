# Litminer MCP Server

The Litminer MCP server exposes local API wrappers and deterministic workflow
tools over stdio JSON-RPC. It is meant for Agents that need an API-first
literature workspace plus basic processing tools for triage, verification, OA
link annotation, metrics annotation, processing summaries, and publisher-page
queueing.

MCP is an execution surface for the Litminer skill, not a replacement for
`SKILL.md`. The Agent should still derive queries and semantic concepts from
the active user request, use the lightest adequate workflow mode, and report
Trust Tiers rather than treating every discovered row as verified evidence.

File path arguments are resolved relative to `LITMINER_WORKSPACE_ROOT`. If that
environment variable is unset, they resolve relative to the MCP process `cwd`.
Paths are rejected if they escape the workspace root. Default workflow outputs
go under `.litminer/` inside that workspace, keeping the skill code directory
separate from user output files.

## Run

```bash
python -m litminer.sources.mcp.server
```

Smoke test:

```bash
python -m litminer.sources.mcp.test_server
```

## Primary Tools

By default, `tools/list` uses `LITMINER_MCP_TOOL_PROFILE=workflow` and only
advertises the workflow tools below. Set `LITMINER_MCP_TOOL_PROFILE=all` when
you need lower-level source, stage, or debug tools.

| Tool | Purpose |
|------|---------|
| `litminer_discover_api` | Run multi-query API discovery with candidate, trace, and report outputs. |
| `litminer_run_lit_search` | Run discovery, triage, verification, OA annotation, metric annotation, queueing, and reporting. |
| `litminer_start_run` | Start a long workflow in the background and return a job ID. |
| `litminer_run_status` | Poll background workflow status and read `agent_summary.json` when present. |
| `litminer_resume_run` | Start a background workflow with resume enabled. |
| `litminer_cancel_run` | Request cooperative cancellation for a background workflow. |
| `litminer_semantic_triage` | Tag and rank rows with Agent-supplied concepts. |
| `litminer_build_publisher_queue` | Build DOI/publisher-page evidence queues. |
| `litminer_processing_report` | Generate a compact source, metadata, triage, access, and queue summary. |
| `litminer_agent_summary` | Generate machine-readable run status, trust tiers, source strategy, artifacts, and next actions. |
| `litminer_read_csv_summary` | Return filtered, paginated CSV rows plus status counts for Agent review. |
| `litminer_workspace_doctor` | Diagnose workspace root, writability, and path mapping. |
| `litminer_bootstrap` | Generate first-run Python/workspace/contact-email reports. |

## Stage-Specific And Debug Tools

These are advertised only with `LITMINER_MCP_TOOL_PROFILE=all`.

| Tool | Purpose |
|------|---------|
| `litminer_search_openalex` | Search OpenAlex for candidate papers. |
| `litminer_search_semantic_scholar` | Search Semantic Scholar or expand citations/references from a DOI. |
| `litminer_search_arxiv` | Search arXiv preprints through the official Atom API. |
| `litminer_search_europe_pmc` | Search Europe PMC biomedical/life-science metadata. |
| `litminer_verify_crossref` | Verify one DOI against Crossref metadata. |
| `litminer_batch_verify_crossref` | Verify multiple DOIs against Crossref metadata. |
| `litminer_search_crossref_title` | Search Crossref by one title to recover a DOI. |
| `litminer_batch_crossref_title_search` | Search Crossref by multiple titles. |
| `litminer_dedupe` | Deduplicate a candidate CSV by DOI, then title. |
| `litminer_lookup_unpaywall` | Look up OA status and structured access links for one DOI. |
| `litminer_filter_journal_metrics` | Annotate and filter candidates by verified local journal metrics. |
| `litminer_probe_publishers` | Resolve DOI landing pages and detect access/PDF/SI link status. |
| `litminer_import_websearch` | Normalize WebSearch leads as unverified candidates. |
| `litminer_validate_journal_metrics` | Validate metric CSV columns, source fields, numeric IF values, aliases, and ISSNs. |
| `litminer_field_provenance` | Generate field-level source/trust provenance JSON for a CSV. |
| `litminer_publisher_adapters` | List publisher inspection adapter capabilities and boundaries. |

## Example: Run Workflow

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "name": "litminer_run_lit_search",
    "arguments": {
      "queries": ["machine learning enzyme stability external validation"],
      "mode": "fast",
      "resume": true,
      "year_from": 2026,
      "required_concepts": ["validation=external validation|prospective validation"],
      "optional_concepts": ["benchmark=benchmark|dataset"],
      "include_arxiv": false,
      "include_europe_pmc": false,
      "enrich_unpaywall": true,
      "output_dir": ".litminer/runs/mcp_run"
    }
  }
}
```

The query and semantic concepts come from the active user request. Litminer does
not provide domain defaults.

The full workflow writes `run_manifest.json` beside the CSV outputs. Use the
`resume` argument with the same `output_dir` after a timeout or interrupted run
to reuse completed stage CSVs. Resume is signature-checked against the prior
query, concepts, year range, sources, and key workflow options. Use
`provider_failure_threshold` to stop retrying a provider after repeated failures
in one discovery run. Discovery trace rows include `status_class`,
`retry_after_seconds`, `cache_status`, and `next_action`; use
`provider_rate_limit_cooldown_seconds` to avoid repeating calls to a
rate-limited provider during the same run.
`status_class=network` or `auth` points to environment/access setup rather than
literature absence.
The full workflow also uses a workspace-local cache for stable DOI metadata and
short-lived transient provider failures. `skipped_cached_provider_failure`
means a recent rate-limit/network/transient provider failure was reused to
avoid an immediate repeated call; fix the environment and set
`no_cache`/`cache_enabled=false`, or wait for the TTL. Auth and generic errors
are not cached by default because they should be fixed and retried.

For long runs, prefer `litminer_start_run` and poll with
`litminer_run_status`. The workflow writes `query_plan.json`,
`run_manifest.json`, `processing_report.md`, `agent_summary.json`, and
`artifacts_index.json` as it progresses or finalizes. Use
`time_budget_seconds`, `stop_after_stage`,
`max_crossref_rows`, `max_unpaywall_rows`, and `max_publisher_probe_rows` to
bound cost and latency.
Inspect `query_plan.json.source_strategy` for missing recommended sources and
retrieval risk flags before deciding whether to broaden a search.

## Workspace Diagnostics

If a file path is rejected or an Agent reports that the workspace is not working,
call `litminer_workspace_doctor` before running a long workflow:

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "tools/call",
  "params": {
    "name": "litminer_workspace_doctor",
    "arguments": {
      "paths": ["input.csv", "../outside.csv"]
    }
  }
}
```

The response includes `workspace_root`, default output paths, write status, and
per-path `inside_workspace` flags. On Windows, prefer native Windows paths or
workspace-relative paths visible to the MCP process.

## Source Policy

Use direct channels first:

1. OpenAlex and Semantic Scholar for candidate discovery.
2. arXiv when preprint discovery matters.
3. Europe PMC for biomedical/life-science discovery.
4. Crossref for DOI/title/journal/year verification.
5. Unpaywall for structured OA status and access-link hints.
6. Publisher landing pages and publisher-visible HTML for article-level
   evidence and access status.
7. WebSearch only as supplemental lead generation.

Publisher probes may record PDF/SI URLs, but PDF parsing is outside Litminer
core.
