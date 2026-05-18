# Litminer Minimal Example

This example uses a tiny local CSV so users can test the workflow shape without
waiting for live discovery.
One fixture row intentionally has no DOI but includes a landing-page URL, so
`--allow-missing-doi` still produces a valid manual-review queue target.

Run from the repository root:

```bash
python -m litminer.engine.run_lit_search \
  --mode fast \
  --input-csv examples/input.csv \
  --required-concept "validation=external validation|prospective validation" \
  --optional-concept "benchmark=benchmark|dataset" \
  --negative-concept "review=review article|survey" \
  --allow-missing-doi \
  --output-dir .litminer/runs/example
```

Expected primary outputs:

- `.litminer/runs/example/query_plan.json`
- `.litminer/runs/example/triaged_candidates.csv`
- `.litminer/runs/example/publisher_queue.csv`
- `.litminer/runs/example/processing_report.md`
- `.litminer/runs/example/agent_summary.json`

Expected `agent_summary.json` run status: `completed`.

This example is for installation and workflow verification only. The rows are
fixtures, not literature evidence.
