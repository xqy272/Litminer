# Testing

Litminer uses a four-layer testing architecture. Layers 1, 2, and the quick
part of layer 3 are deterministic and CI-mandatory. Live provider checks and
long soak profiles are manual/on-demand.

## Layer 1 — Unit (mock-isolated)

`test_litminer_core.py` preserves the established pipeline regressions;
`test_next_architecture.py` covers Contract Layer parity, structured errors,
SQLite migration/rollback/recovery, provider cooldown and HTTP ledger,
coverage quality, canonical provenance, RIS/BibTeX, and new MCP tools. Tests are
fast and network-isolated.

`test_stabilization.py` covers controlled provider probes, Agent client output
parsing, and Windows batch-command handling.

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

## Layer 3 — Native resilience and soak

```bash
python -m litminer.engine.runtime_resilience --profile quick --output-dir .litminer/test/resilience
python -m litminer.engine.runtime_soak --profile quick --output-dir .litminer/test/soak
```

Resilience uses real subprocess termination for CLI and MCP recovery and
upgrades a seeded SQLite v1 database to v2. Quick soak repeats WAL, atomic
write, persisted job, cooldown, canonical/export, and offline
run/resume/merge checks. Standard and long profiles are native release checks.

## Layer 4 — Provider and real Agent (live network)

The provider acceptance CLI uses each real parser and a shared request ledger:

```bash
python -m litminer.engine.provider_acceptance --profile core --output-dir .litminer/test/providers
python -m litminer.engine.provider_acceptance --profile full --output-dir .litminer/test/providers-full --allow-skipped
```

`core` is OpenAlex plus Crossref. `full` adds Semantic Scholar, arXiv, Europe
PMC, and Unpaywall; missing Unpaywall contact email is a structured skip only
when `--allow-skipped` is explicit.

Optional installed-client acceptance:

```bash
python -m litminer.engine.agent_client_acceptance --agent all --real --allow-missing-client --output-dir .litminer/test/real-agents
```

## Full verification

After code changes, run everything:

```bash
python scripts/run_ci.py --profile full
```

Run live providers and standard/long soak separately; they are deliberately not
part of every pull request.
