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
  --output work/candidates_openalex.csv
```

Run multiple query variants when recall matters. The Agent should derive query
families from the active user request, not from project defaults.

## After Discovery

Recommended next steps:

```bash
python -m litminer.engine.dedupe_papers work/candidates_openalex.csv work/deduped_candidates.csv
python -m litminer.engine.semantic_triage --input work/deduped_candidates.csv --output work/triaged_candidates.csv --required-concept "concept=term1|term2"
python -m litminer.sources.api.crossref_verify --input work/triaged_candidates.csv --output work/verified_candidates.csv --title-lookup
```

## Reliability Boundary

OpenAlex is a discovery source. Treat its metadata as preliminary until
Crossref verification and publisher-page inspection are complete.
