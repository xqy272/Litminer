# API Extraction Notes

Use structured APIs before browser or WebSearch routes. API outputs are
machine-readable, traceable, and easier to verify.

Existing wrappers:

- `litminer/sources/api/openalex_search.py`: broad discovery by keyword.
- `litminer/sources/api/semantic_scholar_search.py`: semantic search plus citation or
  reference expansion.
- `litminer/sources/api/arxiv_search.py`: preprint discovery through the official arXiv
  Atom API.
- `litminer/sources/api/europe_pmc_search.py`: Europe PMC metadata and full-text link
  discovery for biomedical/life-science work.
- `litminer/sources/api/crossref_verify.py`: DOI and bibliographic verification.
- `litminer/sources/api/unpaywall_lookup.py`: OA status and structured access-link hints
  from DOI records.

## Discovery

```bash
python -m litminer.engine.api_discovery \
  --query "user topic query" \
  --sources openalex,semantic_scholar,arxiv,europe_pmc \
  --year-from 2026 \
  --max-results-per-query 100 \
  --output work/api_candidates.csv \
  --trace-output work/api_discovery_trace.csv \
  --report-output work/api_discovery_report.md
```

Use `litminer/engine/api_discovery.py` for normal work because it records provider,
query ID, rank, run ID, source trace, and provider status.

Provider names and capabilities live in `litminer/sources/api/registry.py`. New sources
should be added there only after they have a wrapper, a clear evidence role, a
standard row mapping, and visible failure status in the discovery trace.

## Verification

After discovery and deduplication, verify metadata through Crossref:

```bash
python -m litminer.sources.api.crossref_verify \
  --input work/deduped_candidates.csv \
  --output work/verified_candidates.csv \
  --title-lookup
```

Crossref is the authority for DOI, title, journal/container, publication date,
and article type.

## OA Link Annotation

After Crossref verification, use Unpaywall to collect structured access hints:

```bash
python -m litminer.sources.api.unpaywall_lookup \
  --input work/verified_candidates.csv \
  --output work/oa_annotated_candidates.csv \
  --email "you@example.org"
```

This may add `best_oa_landing_url`, `best_oa_pdf_url`, `oa_status`, license,
version, and host type. Treat these as access-planning hints. Do not infer
article-level facts from the presence of an OA link.

## Reliability Boundary

APIs are excellent discovery and metadata channels, but they do not provide
final article-level evidence for task-specific experimental or methodological
details. Use publisher pages for page-visible evidence and keep unsupported
fields as `Unknown`.
