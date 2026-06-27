# Europe PMC Adapter

Use Europe PMC for biomedical and life-science discovery, especially when the
task benefits from PubMed/PMC-adjacent metadata, abstracts, cited-by counts, or
full-text link hints.

DOI-bearing rows use the DOI URL as `landing_page_url` so Agent-facing paper
lists point to the article route. Keep the Europe PMC/PubMed-adjacent record
page in `europe_pmc_url` for provenance and access context.

## Command

```bash
python -m litminer.engine.api_discovery \
  --query "cancer immunotherapy" \
  --sources europe_pmc \
  --year-from 2026 \
  --max-results-per-query 50 \
  --output .litminer/runs/litminer_run/europe_pmc_candidates.csv \
  --trace-output .litminer/runs/litminer_run/europe_pmc_trace.csv \
  --report-output .litminer/runs/litminer_run/europe_pmc_report.md
```

## Evidence Boundary

- Treat Europe PMC as discovery and metadata enrichment.
- Verify DOI/title/year/container facts through Crossref before promotion.
- Use full-text URLs as access-planning hints only.
- Do not present the Europe PMC/PubMed record page as the primary article link
  when a DOI or publisher-facing URL is available.
- Publisher pages remain the preferred surface for task-specific article
  evidence.
