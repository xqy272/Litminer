# Europe PMC Adapter

Use Europe PMC for biomedical and life-science discovery, especially when the
task benefits from PubMed/PMC-adjacent metadata, abstracts, cited-by counts, or
full-text link hints.

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
- Publisher pages remain the preferred surface for task-specific article
  evidence.
