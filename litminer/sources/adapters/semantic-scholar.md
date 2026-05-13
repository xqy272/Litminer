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
python sources/api/semantic_scholar_search.py \
  --query "user topic query" \
  --year-from 2026 \
  --max-results 100 \
  --output work/candidates_s2.csv
```

Citation expansion:

```bash
python sources/api/semantic_scholar_search.py \
  --citation-expand "10.xxxx/yyyy" \
  --max-results 100 \
  --output work/citations_s2.csv
```

Use expansion sparingly. One hop is usually enough unless the user asks for an
exhaustive review.

## After Discovery

Merge with OpenAlex when both are available, then dedupe and run semantic
triage:

```bash
python engine/merge_csv.py work/candidates_openalex.csv work/candidates_s2.csv --allow-missing --output work/merged_candidates.csv
python engine/dedupe_papers.py work/merged_candidates.csv work/deduped_candidates.csv
python engine/semantic_triage.py --input work/deduped_candidates.csv --output work/triaged_candidates.csv --required-concept "concept=term1|term2"
```

## Reliability Boundary

Semantic Scholar is a recall and graph source. Verify metadata through Crossref
and inspect publisher pages for article-level evidence.
