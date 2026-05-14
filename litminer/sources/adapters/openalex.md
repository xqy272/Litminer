# OpenAlex Adapter

## Purpose

Use OpenAlex for broad, fast candidate discovery and preliminary metadata:
title, DOI, year, venue, abstract, authors, concepts, citation count, and
landing page URL.

## Do Not Use For

- Final DOI/title/year/journal authority; verify with Crossref.
- Journal impact factor verification.
- Publisher-page access status.
- Experimental or article-specific values that require page evidence.

## Scripted Discovery

```bash
python -m litminer.sources.api.openalex_search \
  --query "user topic query" \
  --year-from 2026 \
  --max-results 200 \
  --output .litminer/runs/litminer_run/candidates_openalex.csv
```

Run multiple query variants when recall matters. The Agent should derive query
families from the active user request, not from project defaults.

## After Discovery

Recommended next steps:

```bash
python -m litminer.engine.dedupe_papers .litminer/runs/litminer_run/candidates_openalex.csv .litminer/runs/litminer_run/deduped_candidates.csv
python -m litminer.engine.semantic_triage --input .litminer/runs/litminer_run/deduped_candidates.csv --output .litminer/runs/litminer_run/triaged_candidates.csv --required-concept "concept=term1|term2"
python -m litminer.sources.api.crossref_verify --input .litminer/runs/litminer_run/triaged_candidates.csv --output .litminer/runs/litminer_run/verified_candidates.csv --title-lookup
```

## Reliability Boundary

OpenAlex is a discovery source. Treat its metadata as preliminary until
Crossref verification and publisher-page inspection are complete.
