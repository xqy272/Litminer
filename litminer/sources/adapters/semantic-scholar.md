# Semantic Scholar Adapter

## Purpose

Use Semantic Scholar for semantic recall, abstracts, DOI/external-ID
cross-checks, and one-hop citation/reference expansion.

## Do Not Use For

- Final DOI/title/year/journal authority; verify with Crossref.
- Journal metric verification.
- Publisher-page access status.
- Experimental or article-specific values that require publisher evidence.

## Scripted Discovery

```bash
python -m litminer.sources.api.semantic_scholar_search \
  --query "user topic query" \
  --year-from 2026 \
  --max-results 100 \
  --output .litminer/runs/litminer_run/candidates_s2.csv
```

Citation expansion:

```bash
python -m litminer.sources.api.semantic_scholar_search \
  --citation-expand "10.xxxx/yyyy" \
  --max-results 100 \
  --output .litminer/runs/litminer_run/citations_s2.csv
```

Use expansion sparingly. One hop is usually enough unless the user asks for an
exhaustive review.

## After Discovery

Merge with OpenAlex when both are available, then dedupe and run semantic
triage:

```bash
python -m litminer.engine.merge_csv .litminer/runs/litminer_run/candidates_openalex.csv .litminer/runs/litminer_run/candidates_s2.csv --allow-missing --output .litminer/runs/litminer_run/merged_candidates.csv
python -m litminer.engine.dedupe_papers .litminer/runs/litminer_run/merged_candidates.csv .litminer/runs/litminer_run/deduped_candidates.csv
python -m litminer.engine.semantic_triage --input .litminer/runs/litminer_run/deduped_candidates.csv --output .litminer/runs/litminer_run/triaged_candidates.csv --required-concept "concept=term1|term2"
```

## Reliability Boundary

Semantic Scholar is a recall and graph source. Verify metadata through Crossref
and inspect publisher pages for article-level evidence.
