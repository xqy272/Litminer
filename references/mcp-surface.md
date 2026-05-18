# Litminer MCP Surface Reference

Use this file when configuring or debugging the optional MCP server.

MCP is an execution surface for the Litminer skill. It should make repeatable
operations easier, not replace the skill's runtime judgement. The Agent still
derives queries, concepts, sources, and constraints from the user request.

## Tool Profiles

The server reads `LITMINER_MCP_TOOL_PROFILE`.

Default:

```text
LITMINER_MCP_TOOL_PROFILE=workflow
```

The workflow profile keeps `tools/list` compact for Agents. It lists only the
tools normally needed to run, resume, inspect, and summarize a literature
workflow.

Advanced profile:

```text
LITMINER_MCP_TOOL_PROFILE=all
```

Use `all` only when an Agent needs low-level source wrappers, one-off DOI
lookups, metrics validation, provenance generation, or stage debugging.

The server still implements the advanced handlers internally; the profile
controls the advertised surface so ordinary Agents are not distracted by every
stage tool.

## Workflow Tools

Listed by default:

| Tool | Purpose |
|------|---------|
| `litminer_workspace_doctor` | Diagnose workspace root, writability, and path mapping. |
| `litminer_bootstrap` | Generate first-run Python/workspace/contact-email reports. |
| `litminer_run_lit_search` | Run the full workflow synchronously. |
| `litminer_start_run` | Start the full workflow as a background job and return `next_actions`. |
| `litminer_run_status` | Poll background job state, `next_actions`, and summaries when present. |
| `litminer_resume_run` | Start a background run with resume enabled. |
| `litminer_cancel_run` | Request cooperative job cancellation. |
| `litminer_discover_api` | Run multi-query API discovery with trace/report outputs. |
| `litminer_semantic_triage` | Tag and rank rows with caller-supplied concepts. |
| `litminer_build_publisher_queue` | Build DOI/publisher-page evidence queues. |
| `litminer_processing_report` | Generate a compact human-readable run report. |
| `litminer_agent_summary` | Generate machine-readable run status and next actions. |
| `litminer_read_csv_summary` | Read paginated CSV summaries instead of loading large files. |

Prefer `litminer_start_run` plus `litminer_run_status` for long retrieval.
Use `litminer_run_lit_search` only when synchronous execution is acceptable.
Follow returned `next_actions` before retrying, broadening sources, or loading
large CSV files.

## Advanced Tools

Advertised only with `LITMINER_MCP_TOOL_PROFILE=all`:

- source wrappers: `litminer_search_openalex`,
  `litminer_search_semantic_scholar`, `litminer_search_arxiv`,
  `litminer_search_europe_pmc`
- Crossref/Unpaywall one-off helpers: `litminer_verify_crossref`,
  `litminer_batch_verify_crossref`, `litminer_search_crossref_title`,
  `litminer_batch_crossref_title_search`, `litminer_lookup_unpaywall`
- stage tools: `litminer_dedupe`, `litminer_filter_journal_metrics`,
  `litminer_probe_publishers`, `litminer_import_websearch`
- governance/debug tools: `litminer_validate_journal_metrics`,
  `litminer_field_provenance`, `litminer_publisher_adapters`

## Workspace Configuration

Set the MCP process `cwd` to the user workspace or set
`LITMINER_WORKSPACE_ROOT` explicitly. All file arguments must stay under that
workspace root.

Codex-style config:

```toml
[mcp_servers.litminer]
command = "python"
args = ["C:/Users/you/.agents/skills/litminer/litminer/sources/mcp/server.py"]
cwd = "D:/path/to/project"
env = {
  LITMINER_WORKSPACE_ROOT = "D:/path/to/project",
  LITMINER_MCP_TOOL_PROFILE = "workflow"
}
env_vars = [
  "OPENALEX_API_KEY",
  "OPENALEX_MAILTO",
  "CROSSREF_MAILTO",
  "UNPAYWALL_EMAIL",
  "LITMINER_CONTACT_EMAIL"
]
```

On Windows, prefer an absolute Python executable path or a project-local
virtualenv Python if the default `python` command is unreliable.

## JSON-RPC Example

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
      "year_from": 2026,
      "required_concepts": ["validation=external validation|prospective validation"],
      "optional_concepts": ["benchmark=benchmark|dataset"],
      "output_dir": ".litminer/runs/mcp_run"
    }
  }
}
```

After a timeout, call `litminer_resume_run` with the same `output_dir` if the
request has not changed. Inspect `agent_summary.json`, `processing_report.md`,
and `api_discovery_trace.csv` before changing sources or rerunning broadly.
