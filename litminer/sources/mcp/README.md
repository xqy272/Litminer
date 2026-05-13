# Litminer MCP Server

The Litminer MCP server exposes local API wrappers and deterministic workflow
tools over stdio JSON-RPC. It is meant for Agents that need an API-first
literature workspace plus basic processing tools for triage, verification, OA
link annotation, metrics annotation, processing summaries, and publisher-page
queueing.

File path arguments are resolved relative to `LITMINER_WORKSPACE_ROOT`. If that
environment variable is unset, they resolve relative to the MCP process `cwd`.
Paths are rejected if they escape the workspace root. This keeps the skill code
directory separate from user output files.

## Run

```bash
python -m litminer.sources.mcp.server
```

Smoke test:

```bash
python -m litminer.sources.mcp.test_server
```

## Tools

| Tool | Purpose |
|------|---------|
| `litminer_search_openalex` | Search OpenAlex for candidate papers. |
| `litminer_search_semantic_scholar` | Search Semantic Scholar or expand citations/references from a DOI. |
| `litminer_search_arxiv` | Search arXiv preprints through the official Atom API. |
| `litminer_search_europe_pmc` | Search Europe PMC biomedical/life-science metadata. |
| `litminer_verify_crossref` | Verify one DOI against Crossref metadata. |
| `litminer_search_crossref_title` | Search Crossref by one title to recover a DOI. |
| `litminer_batch_crossref_title_search` | Search Crossref by multiple titles. |
| `litminer_dedupe` | Deduplicate a candidate CSV by DOI, then title. |
| `litminer_lookup_unpaywall` | Look up OA status and structured access links for one DOI. |
| `litminer_discover_api` | Run multi-query API discovery with candidate, trace, and report outputs. |
| `litminer_semantic_triage` | Tag and rank rows with Agent-supplied concepts. |
| `litminer_filter_journal_metrics` | Annotate and filter candidates by verified local journal metrics. |
| `litminer_build_publisher_queue` | Build DOI/publisher-page evidence queues. |
| `litminer_probe_publishers` | Resolve DOI landing pages and detect access/PDF/SI link status. |
| `litminer_import_websearch` | Normalize WebSearch leads as unverified candidates. |
| `litminer_processing_report` | Generate a compact source, metadata, triage, access, and queue summary. |
| `litminer_run_lit_search` | Run discovery, triage, verification, OA annotation, metric annotation, queueing, and reporting. |

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
      "year_from": 2026,
      "required_concepts": ["validation=external validation|prospective validation"],
      "optional_concepts": ["benchmark=benchmark|dataset"],
      "include_arxiv": false,
      "include_europe_pmc": false,
      "enrich_unpaywall": true,
      "output_dir": "check/mcp_run"
    }
  }
}
```

The query and semantic concepts come from the active user request. Litminer does
not provide domain defaults.

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
