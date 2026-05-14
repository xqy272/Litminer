# arXiv Adapter

Use arXiv for preprint discovery when the user topic belongs to a field where
preprints are a meaningful signal. It is an optional discovery source, not a
bibliographic authority.

## Command

```bash
python -m litminer.engine.api_discovery \
  --query "all:graphene" \
  --sources arxiv \
  --year-from 2026 \
  --max-results-per-query 50 \
  --output .litminer/runs/litminer_run/arxiv_candidates.csv \
  --trace-output .litminer/runs/litminer_run/arxiv_trace.csv \
  --report-output .litminer/runs/litminer_run/arxiv_report.md
```

Advanced arXiv query syntax can be passed directly. Prefer explicit arXiv
fields such as `all:`, `ti:`, `au:`, `abs:`, and `cat:` when the Agent needs a
precise query.

## Evidence Boundary

- Treat rows as preprint discovery leads.
- Verify DOI-bearing rows through Crossref when available.
- Keep arXiv category and PDF URL as access/context hints.
- Do not infer peer-reviewed publication status from arXiv metadata alone.
