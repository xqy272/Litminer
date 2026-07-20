# Litminer MCP Surface Reference

Use this file when configuring or debugging the optional MCP server.

Windows is the primary supported MCP host and macOS is secondary. Codex and
Claude Code are the primary clients. Linux and Docker are not release
acceptance targets.

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

## Protocol And Client Schema Compatibility

The stdio server accepts MCP protocol versions `2024-11-05`, `2025-03-26`,
`2025-06-18`, and `2025-11-25`. This covers the current primary-client
matrix, including Codex CLI 0.144.0.

The Contract Layer keeps two schema views:

- the strict server schema, used for every `tools/call` validation
- the client declaration schema, used only in `tools/list`

Claude Code 2.1.195 drops tools whose top-level input schema contains
`allOf`, `anyOf`, `not`, or `oneOf`. Litminer removes only those
top-level declaration keywords from the client view. Nested property schemas
remain intact, and the strict schema still enforces input-family, identifier,
range, and path constraints before any handler runs.

The stdio compatibility entry point is `server.py`. JSON-RPC construction lives
in `protocol.py`, in-memory/persisted jobs live in `job_registry.py`, and
high-level run/read/export/recovery helpers live in `workflow_tools.py`. This
split does not change tool names or schemas.

## Workflow Tools

Listed by default:

| Tool | Purpose |
|------|---------|
| `litminer_workspace_doctor` | Diagnose workspace root, writability, and path mapping. |
| `litminer_capabilities` | Read provider capability, credential/contact readiness, persisted health, and optional live preflight. |
| `litminer_plan_run` | Validate and normalize the shared `RunSpec` without network calls or research writes. |
| `litminer_start_run` | Start the full workflow as a background job and immediately return both `job_id` and persistent `run_id`. |
| `litminer_get_run` | Read live/persistent status, quality, coverage, artifacts, and `next_actions` by job, run, or output directory. |
| `litminer_resume_run` | Start a background run with resume enabled. |
| `litminer_cancel_run` | Request cooperative job cancellation. |
| `litminer_read_results` | Read paginated canonical/triage/queue rows or bounded JSON/Markdown artifacts. |
| `litminer_export` | Export canonical bibliography to RIS/BibTeX with `export_manifest.json`. |

Prefer `litminer_start_run` plus `litminer_get_run` for retrieval. The
synchronous `litminer_run_lit_search`, legacy `litminer_run_status`, bootstrap,
stage tools, and direct provider tools remain available in `all` for
compatibility and debugging.
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
- compatibility workflow tools: `litminer_run_lit_search`,
  `litminer_run_status`, `litminer_bootstrap`, `litminer_agent_summary`,
  `litminer_read_csv_summary`, and individual stage helpers

Provider search wrappers return `page`, `page_size`, `total_found`, `has_more`,
and `truncated`. They no longer silently discard everything after the first 20
rows.

## Shared Contract And Errors

`litminer_start_run`, `litminer_resume_run`, `litminer_plan_run`, and the CLI
use the same `RunSpec` definitions. Discovery input (`queries`/`query_file`) and
import input (`input_csv`) are mutually exclusive. JSON Schema validation runs
before a handler executes.

Tool failures are MCP tool results with `isError=true` and a structured
`ErrorEnvelope` under `structuredContent.error`. Stable fields include
`class`, `code`, `message`, `provider`, `http_status`, `transient`,
`retry_after_seconds`, `attempts`, and `next_actions`. JSON-RPC errors are
reserved for protocol/method failures. Tracebacks are omitted unless
`LITMINER_MCP_DEBUG_ERRORS` is explicitly enabled.

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
  "SEMANTIC_SCHOLAR_API_KEY",
  "S2_API_KEY",
  "UNPAYWALL_EMAIL",
  "LITMINER_CONTACT_EMAIL"
]
```

On Windows, prefer an absolute Python executable path or a project-local
virtualenv Python if the default `python` command is unreliable.

Claude Code project/user MCP file shape:

```json
{
  "mcpServers": {
    "litminer": {
      "type": "stdio",
      "command": "python",
      "args": [
        "C:/Users/you/.claude/skills/litminer/litminer/sources/mcp/server.py"
      ],
      "cwd": "D:/path/to/project",
      "env": {
        "LITMINER_WORKSPACE_ROOT": "D:/path/to/project",
        "LITMINER_MCP_TOOL_PROFILE": "workflow"
      }
    }
  }
}
```

Do not persist provider API keys, contact emails, callbacks, or thread objects
inside either client configuration. Codex `env_vars` inherits named values
from the launch environment; Claude Code should be launched from an environment
where those values are already set.

Windows PowerShell registration:

```powershell
$workspace = "D:/path/to/project"
$python = (Get-Command python).Source
$codexServer = "C:/Users/you/.agents/skills/litminer/litminer/sources/mcp/server.py"
$claudeServer = "C:/Users/you/.claude/skills/litminer/litminer/sources/mcp/server.py"

codex mcp add `
  --env "LITMINER_WORKSPACE_ROOT=$workspace" `
  --env "LITMINER_MCP_TOOL_PROFILE=workflow" `
  litminer -- $python $codexServer

claude mcp add --scope user litminer `
  -e "LITMINER_WORKSPACE_ROOT=$workspace" `
  -e "LITMINER_MCP_TOOL_PROFILE=workflow" `
  -- $python $claudeServer
```

Validate the actual client adapters and MCP surface with:

```bash
python -m litminer.engine.agent_client_acceptance --agent all --output-dir .litminer/acceptance/agents
python -m litminer.engine.agent_client_acceptance --agent all --real --output-dir .litminer/acceptance/real-agents
python -m litminer.sources.mcp.test_server
```

Deterministic acceptance is the portable contract gate. A Windows release also
requires the real installed-client run; `--allow-missing-client` is only for
developer diagnostics and is not release evidence.

## JSON-RPC Example

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "name": "litminer_start_run",
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

Poll `litminer_get_run` with the returned `job_id` or `run_id`. After an
interruption, call `litminer_resume_run` with the same input family,
`output_dir`, and run signature only if the request has not changed. Use
`litminer_read_results` for canonical rows, coverage, outcome, and reports
before changing sources or rerunning broadly.
