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

## Runtime Configuration

`config/default.json` is infrastructure configuration only. It can select
channels, environment variable names, limits, output paths, and evidence queue
defaults.

Do not put user goals, literature topics, search queries, required concepts, or
requested extraction fields into global config. The Agent should infer those
from the current conversation and pass them as runtime arguments.

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

### 2. Run The Workflow

```bash
python -m litminer.engine.run_lit_search \
  --query "USER_QUERY_HERE" \
  --year-from 2026 \
  --required-concept "main=term1|term2" \
  --optional-concept "secondary=term3|term4" \
  --config config/default.json \
  --output-dir work/litminer_run
```

Use multiple `--query` values when recall matters. Add
`--include-semantic-scholar` or set it in config when semantic recall or
citation expansion is useful. Add `--include-arxiv` for preprint-heavy fields
or `--include-europe-pmc` for biomedical/life-science searches.

### 3. Use Discovery Only When Needed

```bash
python -m litminer.engine.api_discovery \
  --query "USER_QUERY_HERE" \
  --sources openalex,semantic_scholar,arxiv,europe_pmc \
  --year-from 2026 \
  --output work/api_candidates.csv \
  --trace-output work/api_discovery_trace.csv \
  --report-output work/api_discovery_report.md
```

Prefer this over raw provider wrappers because it records provider, query ID,
rank, source trace, and per-source status.

### 4. Triage Without Deleting

```bash
python -m litminer.engine.semantic_triage \
  --input work/deduped_candidates.csv \
  --output work/triaged_candidates.csv \
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
  --input work/selected_candidates.csv \
  --output work/verified_candidates.csv \
  --title-lookup

python -m litminer.sources.api.unpaywall_lookup \
  --input work/verified_candidates.csv \
  --output work/oa_annotated_candidates.csv

python -m litminer.engine.build_publisher_queue \
  --input work/oa_annotated_candidates.csv \
  --output work/publisher_queue.csv \
  --priorities high,medium,needs_review \
  --fields-needed "field_from_user_request"
```

Optional publisher probe:

```bash
python -m litminer.engine.publisher_probe \
  --input work/publisher_queue.csv \
  --output work/publisher_queue_probed.csv \
  --limit 20
```

The probe resolves DOI landing pages and records access/PDF/SI hints. It does
not read PDFs.

Generate a compact automated processing report:

```bash
python -m litminer.engine.processing_report \
  --output-dir work/litminer_run
```

### 6. Import WebSearch Leads Only As Supplement

```bash
python -m litminer.engine.websearch_import \
  --input work/websearch_raw.csv \
  --output work/websearch_candidates.csv \
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
- `litminer_run_lit_search`

## Delivery Rules

- Do not fabricate DOI, journal metrics, article type, or publisher evidence.
- Keep unavailable values as `Unknown`, `Not verified`, or explicit empty queue
  fields.
- Keep metadata flags separate from task-specific semantic tags.
- Explain when counts are limited by year, DOI recovery, metric verification, or
  publisher-page access.
- Use `processing_report.md` to reduce mechanical CSV scanning before deep
  reading or final judgement.
- Treat WebSearch-only rows as leads until verified through Crossref and
  publisher pages.
