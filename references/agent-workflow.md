# Litminer Agent Workflow Reference

Use this file when `SKILL.md` is not enough to plan a concrete run.

## Responsibilities

The Agent derives user-specific semantics:

- search queries and query variants
- year range
- required, optional, and negative concepts
- article-type exclusions
- DOI requirements
- journal metric thresholds
- publisher-page fields to inspect

Litminer supplies deterministic mechanics:

- structured scholarly API discovery
- trace rows for every provider/query call
- DOI/title deduplication
- Crossref metadata verification
- semantic tags and priorities
- Unpaywall OA/access hints
- verified local journal metric annotation
- publisher-page evidence queues
- processing reports, summaries, provenance, and manifests

## Run Modes

Use `fast` first on uncertain environments or uncertain query wording. It keeps
latency low and validates the path from query to triage.

Use `balanced` for the normal verified workflow. It keeps Semantic Scholar,
arXiv, Europe PMC, and publisher probing opt-in unless the user request needs
them.

Use `expanded` or `full` when recall is worth extra time and rate-limit risk.
These modes enable Semantic Scholar by default but still keep arXiv and Europe
PMC explicit because they are domain-specific.

## Query And Concept Planning

Prefer multiple focused queries over one broad query when recall matters. A
single broad query often creates noisy candidates that only match one part of a
compound scientific condition.

Use required concepts to represent the user's must-have scientific condition.
Use optional concepts for ranking signals. Use negative concepts for likely
false positives, review articles, incompatible methods, or misleading terms.

Examples:

```bash
--required-concept "photocatalytic_h2=photocatalytic hydrogen production|H2 evolution"
--required-concept "degradation=pollutant degradation|wastewater degradation"
--negative-concept "h2o2=hydrogen peroxide|H2O2"
--negative-concept "review=review article|survey"
```

Negative concepts should flag rows. They should not silently delete rows unless
the downstream workflow explicitly applies a hard filter.

## Source Selection

Use OpenAlex as the broad default. Use Semantic Scholar as a recall booster or
for citation/reference-adjacent discovery, with explicit awareness of rate
limits. Use arXiv in preprint-heavy fields. Use Europe PMC for biomedical and
life-science topics. Use WebSearch only as supplemental lead generation that
must be verified before promotion.

Inspect `query_plan.json.source_strategy` after each run. Treat
`missing_recommended_sources` and `risk_flags` as advisory retrieval-gap hints,
not automatic source changes.
Use `source_strategy.source_selection` to distinguish sources selected for the
current run from advisory recommendations. Litminer does not silently expand
sources; `automatic_expansion` should remain false unless a future release
explicitly changes that behavior.

## Stage Interpretation

Discovery outputs candidates. Do not present discovery-only rows as verified
paper facts.

Crossref outputs bibliographic trust. Only `verified` and `title_recovered`
are trusted by default. Treat `mismatch`, `lookup_failed`, and
`title_lookup_failed` as blockers unless the user asks for manual review.

Semantic triage outputs a review surface. Important columns include
`triage_priority`, `triage_score`, `semantic_tags`, `matched_required`,
`matched_optional`, `matched_negative`, `missing_required`, `metadata_status`,
and `llm_review_needed`.

Unpaywall outputs OA/access hints. It does not read PDFs or prove article-level
claims.

Journal metrics output metric annotations from a verified local table. Missing
metric coverage is not evidence that a journal lacks a metric.

Publisher queue outputs pages and fields to inspect. It is not an extracted
full-text dataset.

## Output Reading Order

Read `agent_summary.json` first. It gives run status, trust tiers, artifact
read order, provider health, cache state, and next actions.

For stable machine-readable contracts, see `artifact-contracts.md` and
`csv-fields.md`.

Read `processing_report.md` second for human-readable status and counts.

Read `artifacts_index.json` when selecting the next local file. It is the
canonical inventory of primary, supporting, and debug artifacts.

Only then open large CSVs. In MCP mode, prefer `litminer_read_csv_summary` for
pagination and status-count previews.

## Delivery To Users

Report:

- what was actually queried
- how many candidates were discovered, deduped, verified, metric-passed, and
  queued
- which sources failed or were rate-limited
- which constraints limited the count
- which fields remain unknown
- which local artifacts contain the evidence
- what the next retrieval or inspection step should be

Do not inflate counts by mixing candidates, verified papers, metric-pass rows,
and publisher queues into one number without trust-tier labels.
