# Testing

Litminer uses a three-layer testing architecture. Layers 1 and 2 are
deterministic and CI-mandatory. Layer 3 is manual/on-demand.

## Layer 1 — Unit (mock-isolated)

`test_litminer_core.py` preserves the established pipeline regressions;
`test_next_architecture.py` covers Contract Layer parity, structured errors,
SQLite migration/rollback/recovery, provider cooldown and HTTP ledger,
coverage quality, canonical provenance, RIS/BibTeX, and new MCP tools. Tests are
fast and network-isolated.

```bash
python -m unittest discover -s test -p "test_*.py"
```

## Layer 2 — Contract (offline black-box)

Scenario-driven subprocess tests in `agent_scenarios.json`, executed by
`run_agent_scenarios.py`. Each scenario runs a real CLI command against fixture
CSVs and asserts over exit codes, stdout/stderr, JSON artifacts, and CSV
contents. No network access. Tests the skill contract that Agents depend on.

```bash
python test/run_agent_scenarios.py
python test/run_agent_scenarios.py --profile known_issue
```

Profiles:

| Profile | Purpose | CI |
|---------|---------|----|
| `offline` | Deterministic contract tests | Yes |
| `known_issue` | Regression contracts for previously discovered defects; entries may be xfail until fixed | Yes |
| `live` | Real API integration | No — manual |
| `architecture` | Next-generation contract/state/evidence/export acceptance probes | Yes |
| `failure_injection` | Deterministic provider-failure and quality semantics | Yes |

The unittest bridge in `test_agent_scenarios.py` runs both `offline` and
`known_issue` profiles so they are included in `python -m unittest discover`.

### Adding a scenario

Add an entry to `agent_scenarios.json`. Required fields:

- `id`: unique identifier
- `profiles`: at least one of `offline`, `known_issue`, `live`
- `command`: the CLI invocation with `{python}`, `{output_dir}`, and fixture placeholders
- `expect`: assertions (exit_code, files_exist, json checks, csv checks)

Optional: `setup_commands`, `env`, `timeout_seconds`, `expected_failure`.

The `agent_expectation` field is documentation-only — it records what an Agent
should conclude from the output but is never validated programmatically.

### Fixtures

Test fixtures live in `test/fixtures/`:

| File | Rows | Purpose |
|------|------|---------|
| `agent_mixed_candidates.csv` | 5 | DOI rows, missing DOI, review with prompt injection |
| `triaged_duplicate_doi.csv` | 3 | Same DOI appearing twice for queue dedupe testing |
| `websearch_candidates.csv` | 3 | WebSearch leads with `web_search` source |
| `empty_candidates.csv` | 0 | Header-only CSV for zero-result degradation |
| `live_crossref_doi.csv` | 1 | Known DOI for live Crossref verification |

## Layer 3 — Provider (live network)

Scenarios with `"profiles": ["live"]` make real API calls. Run them manually
after changing source wrappers or response parsing:

```bash
python test/run_agent_scenarios.py --profile live
```

These verify that Litminer's URL construction, header setting, and response
parsing work against current real APIs. Failures here may indicate upstream API
changes that broke Litminer's integration.

## Full verification

After code changes, run everything:

```bash
python -m compileall litminer -q
python -m ruff check litminer test
python -m mypy litminer
python -m unittest discover -s test -p "test_*.py"
python -m litminer.sources.mcp.test_server
python -m litminer.engine.architecture_acceptance --scenario degraded_coverage --output-dir .litminer/test/acceptance
python -m litminer.engine.bootstrap --output-dir .litminer/bootstrap
python -m litminer.engine.doctor
python -m litminer.engine.offline_smoke
python -m litminer.engine.journal_metrics --validate --metrics references/journal_metrics_seed.csv
python test/run_agent_scenarios.py
python test/run_agent_scenarios.py --profile known_issue
```
