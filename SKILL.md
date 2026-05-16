---
name: litminer
description: >
  Agent-facing literature search substrate for API discovery, metadata
  verification, semantic triage support, journal metric annotation, and
  publisher-page evidence queueing. Litminer is domain-neutral and expects the
  Agent to derive queries and semantic concepts from the active user request.
  It includes basic automated processing tools to reduce mechanical review work.
---

# Litminer

## Skill Contract

Litminer is a skill first, and a CLI/MCP package second. The Agent should use
this skill to produce traceable local research artifacts, not to answer from
memory or to generate a final review directly.

Use this skill when the user asks for:

- current or recent literature discovery
- DOI/title/journal/year verification
- OA/access-link annotation
- candidate screening with explicit inclusion/exclusion concepts
- journal-metric annotation from a verified local table
- publisher-page evidence queues for later inspection
- a reproducible trail of what sources were queried and what failed

Do not use this skill as the sole answer when the user only needs a simple
definition, wants manual prose editing, or asks for final scientific judgement
without any retrieval step.

The Agent owns the scientific intent. Litminer owns repeatable mechanics:
retrieval, metadata normalization, status flags, reports, and queues.

## Core Boundary

Use Litminer when an Agent needs a specialized research information acquisition
layer, not just generic WebSearch/WebFetch. Litminer retrieves information from
scholarly channels and performs basic deterministic processing so the Agent can
focus on scientific judgement instead of mechanical cleanup.

Core responsibilities:

1. Discover candidate papers through structured APIs.
2. Verify bibliographic metadata through Crossref.
3. Add structured OA/access-link hints through Unpaywall when DOI and contact
   email are available.
4. Deduplicate, validate, summarize, classify, and queue retrieved information.
5. Tag, rank, and flag candidates with concepts supplied at runtime by the
   Agent.
6. Annotate journal metrics from a verified local table when the user asks for
   metric filtering.
7. Build DOI/publisher-page queues for article-page inspection.

Out of core:

- Domain knowledge files and hard-coded topical vocabularies.
- Automatic final inclusion decisions.
- PDF reading, OCR, PDF table extraction, or SI parsing.
- WebSearch as a primary source of truth.

## Processing Boundary

The processing layer is basic but essential. It may:

- normalize DOI and metadata fields
- deduplicate candidates
- verify bibliographic metadata
- annotate OA/access hints
- group rows by source, status, priority, and missing fields
- generate compact reports for Agent review
- build queues for publisher-page inspection

It must not make final scientific inclusion decisions or invent unavailable
facts.

## Agent Decision Flow

1. Decide whether the request needs structured literature retrieval. If yes,
   use Litminer before giving final claims.
2. Derive runtime semantics from the active user request: queries, year range,
   required concepts, optional concepts, negative concepts, article types,
   metric thresholds, and publisher-page fields.
3. Check the environment if this is a new workspace, a new machine, or a prior
   tool call failed. Prefer `bootstrap`, `doctor`, `offline_smoke`, or
   `litminer_workspace_doctor`.
4. Choose the lightest useful run:
   - `fast`: first pass, query validation, environment validation, low latency.
   - `balanced`: normal verified workflow with Crossref/Unpaywall.
   - `expanded` / `full`: deeper semantic recall and provider concurrency when the user
      accepts higher latency and rate-limit risk.
5. If interrupted, resume before restarting. Use `--resume` with the same
   output directory only when the user request has not changed; Litminer checks
   `run_manifest.json` signatures before reusing old CSVs.
6. Read `processing_report.md` and Trust Tiers before scanning large CSVs.
7. Deliver results with counts, local artifact paths, uncertainty, missing
   evidence, and next actions. Do not silently promote weak rows.

## Skill Runtime Modes

Recommended default sequence:

```bash
python -m litminer.engine.bootstrap
python -m litminer.engine.doctor
python -m litminer.engine.offline_smoke
python -m litminer.engine.run_lit_search --mode fast ...
python -m litminer.engine.run_lit_search --mode balanced ...
```

Use direct CLI when MCP is unavailable. Use MCP when the Agent has a configured
stdio server and needs structured tool calls, workspace path enforcement, or
compact CSV summaries.

When MCP path access fails, do not retry blindly. Call
`litminer_workspace_doctor` or run:

```bash
python -m litminer.engine.doctor --workspace WORKSPACE_ROOT --explain-path PATH
```

## Runtime Configuration

`config/default.json` is infrastructure configuration only. It can select
channels, environment variable names, limits, output paths, and evidence queue
defaults.

Do not put user goals, literature topics, search queries, required concepts, or
requested extraction fields into global config. The Agent should infer those
from the current conversation and pass them as runtime arguments.

For setup checks, run:

```bash
python -m litminer.engine.bootstrap
python -m litminer.engine.doctor
python -m litminer.engine.offline_smoke
```

Use `doctor --config PATH` before trusting a user-provided runtime config.
When workspace paths are confusing, run `doctor --workspace PATH --explain-path SOME_PATH`
or call the MCP `litminer_workspace_doctor` tool before attempting a long run.

Runtime file boundary:

- Treat the Litminer clone as the skill/code directory, not as the user's data
  directory.
- Default CLI and MCP workflow outputs should live under `.litminer/` in the
  active workspace.
- If `LITMINER_WORKSPACE_ROOT` is set, use it as the workspace root; otherwise
  default relative outputs resolve under the process `cwd`.
- In MCP mode, never read or write paths outside `LITMINER_WORKSPACE_ROOT` or
  the MCP process `cwd` fallback.

## Source Policy

| Source | Use For | Boundary |
|--------|---------|----------|
| OpenAlex | Broad API discovery | Preliminary metadata only. |
| Semantic Scholar | Semantic recall and citation/reference expansion | Optional recall booster. |
| arXiv | Preprint discovery | Optional; best for fields where preprints matter. |
| Europe PMC | Biomedical/life-science metadata and full-text links | Optional; not a final article-fact verifier. |
| Crossref | DOI/title/journal/year/type verification | Bibliographic authority. |
| Unpaywall | OA status and structured full-text link hints | Requires email; no PDF parsing. |
| Publisher page / HTML | Article access and visible article evidence | No paywall bypassing; no PDF parsing. |
| Journal metrics CSV | IF/JCR-style annotation | Must be verified; never guessed. |
| WebSearch | Supplemental leads | Verify before promotion. |

## Workflow

### 1. Interpret The User Request

The Agent should derive:

- search query strings
- year range, DOI requirements, article type filters, metric thresholds
- requested publisher-page fields
- required, optional, and negative semantic concepts

Pass concepts at runtime, for example:

```bash
--required-concept "validation=external validation|prospective validation"
--optional-concept "benchmark=benchmark|dataset"
--negative-concept "review=review article|survey"
```

These examples are not defaults.

For fragile semantics, use JSON concept expressions in a triage profile or as a
single CLI value:

```json
{
  "required": [
    {"name": "photocatalytic_h2", "all_of": ["photocatalytic", {"near": ["hydrogen", "production"], "window": 8}]}
  ],
  "negative": [
    {"name": "h2o2_only", "any_of": ["hydrogen peroxide", "H2O2"]}
  ]
}
```

Supported expression operators are `all_of`, `any_of`, `not`, `near`, and
`not_near`. They are triage signals; the Agent still owns final judgement.

### 2. Run The Workflow

```bash
python -m litminer.engine.run_lit_search \
  --mode fast \
  --query "USER_QUERY_HERE" \
  --year-from 2026 \
  --required-concept "main=term1|term2" \
  --optional-concept "secondary=term3|term4" \
  --config config/default.json \
  --output-dir .litminer/runs/litminer_run
```

Use `--mode fast` for the first pass to validate Python, workspace, network,
queries, and semantic concepts without slow enrichment. Use `--mode balanced`
after the first pass for Crossref/Unpaywall verification, or `--mode expanded`
(`--mode full` alias) only
when semantic recall is worth the extra time and Semantic Scholar rate-limit
risk. `full` does not automatically enable arXiv or Europe PMC; add those
sources only when the user's domain warrants them.
After a timeout or interrupted run, pass `--resume` with the same `--output-dir`
to reuse existing stage CSVs. Do this only when the user request and candidate
universe have not changed. Litminer refuses automatic resume when the run
signature does not match. Inspect `run_manifest.json` for completed, skipped,
and reused stages. Batch Crossref and Unpaywall stages write periodic
checkpoints, so resuming should reuse already annotated rows instead of starting
from the first DOI again. To move from a tiny `fast` trial to a broader
`balanced` or `expanded` run, prefer a new output directory unless you
intentionally want to enrich the already-discovered candidate set.

Use multiple `--query` values when recall matters. Add
`--include-semantic-scholar` or set it in config when semantic recall or
citation expansion is useful. Add `--include-arxiv` for preprint-heavy fields
or `--include-europe-pmc` for biomedical/life-science searches.

For long or uncertain runs, set explicit controls:

```bash
python -m litminer.engine.run_lit_search \
  --mode balanced \
  --query "USER_QUERY_HERE" \
  --time-budget-seconds 600 \
  --max-crossref-rows 200 \
  --max-unpaywall-rows 200 \
  --stop-after-stage triage
```

Time budgets stop cleanly at stage boundaries and write partial reports. Row
budgets mark unprocessed Crossref/Unpaywall rows as `skipped_budget` instead of
silently dropping them.

### 3. Use Discovery Only When Needed

```bash
python -m litminer.engine.api_discovery \
  --query "USER_QUERY_HERE" \
  --sources openalex,semantic_scholar,arxiv,europe_pmc \
  --year-from 2026 \
  --output .litminer/runs/litminer_run/api_candidates.csv \
  --trace-output .litminer/runs/litminer_run/api_discovery_trace.csv \
  --report-output .litminer/runs/litminer_run/api_discovery_report.md
```

Prefer this over raw provider wrappers because it records provider, query ID,
rank, source trace, per-source status, status class, retry-after hints, and
the next action an Agent should take. Use `--provider-failure-threshold` when a
broken provider should be skipped after repeated failures. Use
`--provider-rate-limit-cooldown-seconds` to avoid hammering a provider again in
the same run after a 429/rate-limit response.

### 4. Triage Without Deleting

```bash
python -m litminer.engine.semantic_triage \
  --input .litminer/runs/litminer_run/deduped_candidates.csv \
  --output .litminer/runs/litminer_run/triaged_candidates.csv \
  --required-concept "main=term1|term2" \
  --optional-concept "secondary=term3|term4" \
  --negative-concept "negative=term5|term6" \
  --year-from 2026 \
  --require-doi
```

Important output columns:

- `triage_priority`
- `triage_score`
- `semantic_tags`
- `matched_required`, `matched_optional`, `matched_negative`
- `missing_required`
- `hard_filter_flags`
- `metadata_status`
- `llm_review_needed`

Negative concepts and low priority are review signals. They are not deletion
commands unless the Agent explicitly applies a downstream hard filter.

### 5. Verify And Queue Evidence

```bash
python -m litminer.sources.api.crossref_verify \
  --input .litminer/runs/litminer_run/selected_candidates.csv \
  --output .litminer/runs/litminer_run/verified_candidates.csv \
  --title-lookup

python -m litminer.sources.api.unpaywall_lookup \
  --input .litminer/runs/litminer_run/verified_candidates.csv \
  --output .litminer/runs/litminer_run/oa_annotated_candidates.csv

python -m litminer.engine.build_publisher_queue \
  --input .litminer/runs/litminer_run/oa_annotated_candidates.csv \
  --output .litminer/runs/litminer_run/publisher_queue.csv \
  --priorities high,medium,needs_review \
  --fields-needed "field_from_user_request"
```

Optional publisher probe:

```bash
python -m litminer.engine.publisher_probe \
  --input .litminer/runs/litminer_run/publisher_queue.csv \
  --output .litminer/runs/litminer_run/publisher_queue_probed.csv \
  --limit 20
```

The probe resolves DOI landing pages and records access/PDF/SI hints. It does
not read PDFs.

Generate a compact automated processing report:

```bash
python -m litminer.engine.processing_report \
  --output-dir .litminer/runs/litminer_run
```

### 6. Import WebSearch Leads Only As Supplement

```bash
python -m litminer.engine.websearch_import \
  --input .litminer/runs/litminer_run/websearch_raw.csv \
  --output .litminer/runs/litminer_run/websearch_candidates.csv \
  --query "gap-focused query"
```

Imported rows are marked `lead_unverified` and must pass Crossref and
publisher-page verification before promotion.

## MCP Use

Start the local server:

```bash
python -m litminer.sources.mcp.server
```

Preferred tools:

- `litminer_search_openalex`
- `litminer_discover_api`
- `litminer_search_semantic_scholar`
- `litminer_search_arxiv`
- `litminer_search_europe_pmc`
- `litminer_verify_crossref`
- `litminer_search_crossref_title`
- `litminer_lookup_unpaywall`
- `litminer_semantic_triage`
- `litminer_filter_journal_metrics`
- `litminer_build_publisher_queue`
- `litminer_probe_publishers`
- `litminer_import_websearch`
- `litminer_processing_report`
- `litminer_agent_summary`
- `litminer_read_csv_summary`
- `litminer_workspace_doctor`
- `litminer_bootstrap`
- `litminer_start_run`
- `litminer_run_status`
- `litminer_resume_run`
- `litminer_cancel_run`
- `litminer_run_lit_search`
- `litminer_publisher_adapters`
- `litminer_validate_journal_metrics`
- `litminer_field_provenance`

MCP call preference:

1. `litminer_workspace_doctor` when paths, workspaces, or file visibility are
   uncertain.
2. `litminer_bootstrap` on a new Windows-heavy or unknown environment.
3. `litminer_start_run` + `litminer_run_status` for long end-to-end work;
   use `litminer_run_lit_search` only when synchronous execution is acceptable.
4. `litminer_agent_summary` when the Agent needs machine-readable run state.
5. `litminer_read_csv_summary` for large CSV review instead of loading whole
   files into context.
6. Single-purpose tools only when continuing from an intermediate artifact or
   debugging one stage.

## Artifact Contract

The Agent should treat these files as the skill's main outputs:

- `processing_report.md`: first reading surface; includes stage counts, provider
  health, Trust Tiers, metadata health, and queue summaries.
- `agent_summary.json`: first machine-readable status surface; includes trust
  tiers, provider health, artifact paths, warnings, and recommended next actions.
- `query_plan.json`: Agent-derived queries, concepts, sources, and run controls.
- `field_provenance.json`: field-level source/trust map for queued or probed rows.
- `feasibility_report.md`: explains whether user constraints are currently
  feasible and why counts may be too low.
- `run_manifest.json`: machine-readable stage status, run signature, resume
  information, row counts, file fingerprints, and reused/skipped stages.
- `triaged_candidates.csv`: semantic review surface, not final inclusion.
- `publisher_queue.csv`: page-inspection queue, not extracted article facts.
- `api_discovery_trace.csv`: provider/query/status trail for debugging source
  failures.

## Delivery Rules

- Do not fabricate DOI, journal metrics, article type, or publisher evidence.
- Keep unavailable values as `Unknown`, `Not verified`, or explicit empty queue
  fields.
- Keep metadata flags separate from task-specific semantic tags.
- Explain when counts are limited by year, DOI recovery, metric verification, or
  publisher-page access.
- Use `processing_report.md` to reduce mechanical CSV scanning before deep
  reading or final judgement.
- Use Trust Tiers in `processing_report.md` to distinguish discovered
  candidates, Crossref-trusted rows, metric-pass rows, and publisher queues.
- Use `run_manifest.json` when deciding whether a run can be resumed rather
  than restarted.
- Treat WebSearch-only rows as leads until verified through Crossref and
  publisher pages.
- In the final user response, report what was actually verified, what remains
  unknown, which constraints limited counts, and where the local artifacts are.
