# CLAUDE.md - Litminer Agent Guide

This file is for Claude Code, Codex, and other Agents working inside the Litminer repository or using Litminer as a skill. Human-facing onboarding belongs in `README.md`; machine/action-oriented guidance belongs here and in `SKILL.md`.

## Role

Litminer is an Agent-facing research information acquisition substrate. Use it when the task needs structured literature discovery, metadata verification, OA/access hints, semantic triage support, journal metric annotation, or publisher-page evidence queueing.

Litminer is domain-neutral. The Agent derives queries, year ranges, required concepts, optional concepts, negative concepts, article-type exclusions, metric thresholds, and requested publisher-page fields from the active user request.

## Hard Boundaries

- Do not answer current literature, DOI, journal metric, or publisher-page evidence questions from memory.
- Do not fabricate DOI, journal metrics, article type, access status, PDF/SI URLs, or article-level claims.
- Do not make final scientific inclusion/exclusion decisions. Tag, rank, queue, and report instead.
- Do not parse PDFs, OCR files, extract PDF tables, or inspect supplementary information inside Litminer core.
- Do not bypass paywalls or infer hidden full-text content.
- Treat WebSearch results as leads until Crossref and publisher-visible evidence validate them.
- Keep unavailable values explicit: `Unknown`, `Not verified`, empty queue fields, or a status field explaining the failure.

## Architecture Map

- `SKILL.md`: skill trigger, scope, and Agent-facing workflow summary.
- `README.md`: human-facing Chinese documentation.
- `README.en.md`: human-facing English documentation.
- `config/default.json`: infrastructure defaults only.
- `litminer/engine/run_lit_search.py`: end-to-end workflow runner.
- `litminer/engine/api_discovery.py`: unified discovery orchestrator with trace/report output.
- `litminer/engine/dedupe_papers.py`: DOI/title dedupe with complementary-field merging.
- `litminer/engine/semantic_triage.py`: runtime concept tagging, scoring, and metadata flags.
- `litminer/sources/api/crossref_verify.py`: Crossref DOI verification and title-based DOI recovery.
- `litminer/sources/api/unpaywall_lookup.py`: structured OA/access-link annotation.
- `litminer/engine/journal_metrics.py`: verified local journal metric annotation.
- `litminer/engine/build_publisher_queue.py`: publisher-page evidence queue generation.
- `litminer/engine/publisher_probe.py`: safe DOI/page access probe; no PDF reading.
- `litminer/engine/processing_report.py`: compact source, metadata, triage, Crossref, OA/access, and queue report.
- `litminer/engine/websearch_import.py`: import WebSearch leads as unverified candidates.
- `litminer/engine/validate_stage.py`: CSV stage validation.
- `litminer/sources/api/registry.py`: provider registry and capability metadata.
- `litminer/sources/mcp/server.py`: stdio JSON-RPC/MCP-compatible tool server.

## Runtime Model

Configuration is operational, not semantic.

Installation and environment policy:

- Treat `git clone .../Litminer.git` into a scanned skills directory as sufficient for skill discovery.
- For Claude Code, prefer `~/.claude/skills/litminer` or a project-local `.claude/skills/litminer`.
- For Codex, prefer `$HOME/.agents/skills/litminer` or a project-local `.agents/skills/litminer`.
- Do not present global `pip install -e .` as a required user step.
- Run scripts directly with Python 3.10+ when possible; runtime code is intentionally stdlib-only.
- If package installation is needed for console scripts or development tools, create a `.venv` inside the Litminer clone and install there.
- If MCP is configured, prefer pointing the MCP command at the `.venv` Python when a virtualenv exists.
- Keep MCP code root and user workspace separate. `litminer/sources/mcp/server.py` imports code from the Litminer clone, while file arguments resolve under `LITMINER_WORKSPACE_ROOT` or the MCP process `cwd`.
- Do not document hand-copying a subset of files as an install method unless the project ships a dedicated release package or plugin.

Allowed in config:

- source/channel toggles
- API environment variable names
- result/probe limits
- default output paths
- evidence queue defaults

Never put these in global config:

- user research topic
- domain vocabulary
- required or excluded concepts
- final inclusion criteria
- fields requested for a specific user task

Pass semantic decisions as runtime arguments.

## Recommended Workflow

### 1. Interpret The User Request

Derive:

- one or more search queries
- `year_from` / `year_to`
- DOI requirement
- article-type exclusions
- metric threshold if explicitly requested
- publisher-page fields needed by the user
- required, optional, and negative concepts

Concept argument examples:

```bash
--required-concept "validation=external validation|prospective validation"
--optional-concept "benchmark=benchmark|dataset"
--negative-concept "review=review article|survey"
```

These are examples, not defaults.

### 2. Prefer The Full Runner

```bash
python -m litminer.engine.run_lit_search \
  --query "USER_QUERY_HERE" \
  --year-from 2026 \
  --required-concept "main=term1|term2" \
  --optional-concept "secondary=term3|term4" \
  --negative-concept "negative=term5|term6" \
  --config config/default.json \
  --output-dir work/litminer_run
```

Use repeated `--query` when recall matters. Add source flags only when useful:

- `--include-semantic-scholar` for semantic recall or graph-adjacent discovery.
- `--include-arxiv` for preprint-heavy fields.
- `--include-europe-pmc` for biomedical/life-science tasks.
- `--probe-publishers` only when lightweight access/PDF/SI hints are needed.

### 3. Read Outputs In This Order

1. `processing_report.md` for counts, source distribution, metadata health, Crossref status, OA/access hints, and queue summary.
2. `feasibility_report.md` for feasibility and blocking reasons.
3. `triaged_candidates.csv` for semantic review.
4. `publisher_queue.csv` for article-page evidence work.
5. `publisher_queue_probed.csv` only if probing was enabled.

Do not mechanically scan large CSVs before checking `processing_report.md`.

## Stage Semantics

### Discovery

Use `litminer.engine.api_discovery` instead of raw provider wrappers when possible. It records provider, query ID, rank, source trace, and provider status.

```bash
python -m litminer.engine.api_discovery \
  --query "USER_QUERY_HERE" \
  --sources openalex,semantic_scholar,arxiv,europe_pmc \
  --year-from 2026 \
  --output work/api_candidates.csv \
  --trace-output work/api_discovery_trace.csv \
  --report-output work/api_discovery_report.md
```

### Crossref Verification

Crossref status is explicit:

- `verified`: DOI lookup succeeded and metadata matched.
- `title_recovered`: DOI was recovered from high-confidence title search.
- `lookup_failed`: DOI lookup failed.
- `title_lookup_failed`: no DOI and title recovery failed.
- `mismatch`: Crossref metadata conflicts with input metadata.

Only `verified` and `title_recovered` are trusted for default promotion. Failed or mismatched rows must remain blocked unless the user explicitly asks for manual review of blocked rows.

### Semantic Triage

Triage tags and ranks rows; it should not delete rows. Important fields:

- `triage_priority`
- `triage_score`
- `candidate_status`
- `semantic_tags`
- `matched_required`
- `matched_optional`
- `matched_negative`
- `missing_required`
- `hard_filter_flags`
- `metadata_status`
- `llm_review_needed`

Negative concepts are warning tags unless the user explicitly asks for hard exclusion.

### Journal Metrics

Only use verified local metric CSVs. The seed file in `references/journal_metrics_seed.csv` is a schema placeholder, not a metric source.

Do not guess impact factor, JCR quartile, CiteScore, or equivalent metrics. If no verified metric row matches, leave metric status as unverified.

### Publisher Queue

`publisher_queue.csv` is an evidence work queue, not extracted article facts. It points the Agent to publisher-visible pages and requested fields.

The publisher probe is heuristic and safe by design:

- allowed schemes: `http`, `https`
- blocks localhost, private IPs, link-local, reserved, multicast, and unsafe redirects
- records obvious PDF/SI hints
- does not read PDFs

## MCP Use

The MCP server is optional. If configured, prefer tool calls for repeatable operations; otherwise run scripts directly. File path arguments must stay inside `LITMINER_WORKSPACE_ROOT` when set, or inside the MCP process `cwd` when unset.

Start/test:

```bash
python -m litminer.sources.mcp.server
python -m litminer.sources.mcp.test_server
```

Core MCP tools:

- `litminer_discover_api`
- `litminer_run_lit_search`
- `litminer_semantic_triage`
- `litminer_verify_crossref`
- `litminer_search_crossref_title`
- `litminer_lookup_unpaywall`
- `litminer_filter_journal_metrics`
- `litminer_build_publisher_queue`
- `litminer_probe_publishers`
- `litminer_import_websearch`
- `litminer_processing_report`

## Source Policy

| Source | Use For | Boundary |
|--------|---------|----------|
| OpenAlex | Broad discovery | Primary default; preliminary metadata. |
| Semantic Scholar | Recall booster, citation/reference graph | Optional; not bibliographic authority. |
| arXiv | Preprints | Optional; useful for active preprint fields. |
| Europe PMC | Biomedical/life-science metadata and full-text links | Optional; not final article-fact verifier. |
| Crossref | DOI/title/journal/year/type verification | Bibliographic authority. |
| Unpaywall | OA status and structured access hints | Requires email; no PDF parsing. |
| Publisher page / HTML | Article-page evidence surface | No paywall bypass; no PDF parsing. |
| Journal metrics CSV | IF/JCR-style annotation | Must be externally verified. |
| WebSearch | Supplemental leads | Verify before promotion. |

## Development Rules

- Keep provider expansion modular: wrapper in `litminer/sources/api/`, registration in `litminer/sources/api/registry.py`, orchestration through `litminer/engine/api_discovery.py`, MCP exposure if useful.
- Preserve traceability: provider, query, rank, status, error, DOI, and source trace should remain visible.
- Prefer annotating and queueing over deleting.
- Keep script-level facts separate from Agent-level semantic judgement.
- Do not add runtime dependencies casually; the project intentionally uses stdlib-only runtime.
- Keep README human-facing. Put Agent operational details in `CLAUDE.md` or `SKILL.md`.

## Verification

Run after code changes:

```bash
python -m compileall litminer -q
python -m ruff check litminer test
python -m mypy litminer
python -m unittest discover -s test -p "test_*.py"
python -m litminer.sources.mcp.test_server
```

Optional network smoke tests when source wrappers changed:

```bash
python -m litminer.engine.api_discovery --query "all:graphene" --sources arxiv --max-results-per-query 1 --output check/arxiv_smoke.csv
python -m litminer.engine.api_discovery --query "cancer immunotherapy" --sources europe_pmc --max-results-per-query 1 --output check/europe_pmc_smoke.csv
```
