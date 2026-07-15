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

Plain intent queries are compiled transparently into explicit `all:` terms with
implicit AND semantics. For example, `large language model validation` becomes
`all:large AND all:language AND all:model AND all:validation`. Quoted phrases,
parentheses, and explicit Boolean operators are preserved during compilation.

Advanced arXiv query syntax can be passed directly and is not rewritten.
Prefer explicit arXiv fields such as `all:`, `ti:`, `au:`, `abs:`, and `cat:`
when the Agent needs a precise query.

Every arXiv row records the original intent in `discovery_query`, the effective
query in `provider_query`, and `provider_query_mode` as
`plain_and_compiled` or `advanced_raw`. Inspect these fields when auditing
unexpected recall or precision.

## Evidence Boundary

- Treat rows as preprint discovery leads.
- For biomedical searches, use arXiv as a supplemental preprint lane alongside
  Europe PMC or another biomedical metadata source.
- Verify DOI-bearing rows through Crossref when available.
- Keep arXiv category and PDF URL as access/context hints.
- Do not infer peer-reviewed publication status from arXiv metadata alone.
