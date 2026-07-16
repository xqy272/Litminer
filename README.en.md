# Litminer

[中文](README.md) | English

Litminer is a research-information skill for AI agents. It helps Claude Code,
Codex, and other agents discover literature from scholarly sources, verify DOI
metadata, triage candidates, and produce auditable reports plus publisher-page
queues.

It is not a review writer, PDF reader, or knowledge base. Litminer makes
retrieval, verification, failure logging, and evidence handoff repeatable; the
Agent and user still make the final scientific judgement.

## What It Does

- Discover candidates from OpenAlex, Semantic Scholar, arXiv, Europe PMC, and similar sources.
- Verify DOI, title, journal, year, and article type through Crossref, with retraction status detection.
- Annotate OA and access hints through Unpaywall.
- Pretriage deduplicated candidates, then build a budget-aware Crossref queue ordered by relevance, DOI availability, and metadata state.
- Keep bibliographic trust, scientific review, and workflow readiness separate, with field-level match evidence and concept diagnostics.
- Expand from high-priority seeds via citation/reference graph to find papers keyword search misses.
- Generate stratified result statistics (`result_profile`) and a human-readable audit report (`search_audit_report`).
- Share one typed `RunSpec`/`RunOutcome` contract across CLI, MCP, background jobs, error classification, and recovery actions.
- Persist stage state, provider-wide cooldown, per-attempt HTTP ledger, source observations, and canonical provenance in workspace-local SQLite.
- Emit `coverage_report.json` so execution status stays independent from `healthy`, `degraded`, or `inconclusive` retrieval quality.
- Separate raw observations from canonical bibliography and export trusted RIS/BibTeX with an audited manifest.
- Merge a new research iteration into an existing candidate pool with `--merge-into`, with delta and lineage artifacts.
- Extract structured metadata (`citation_keywords`, `citation_online_date`, `citation_funder_name`) from publisher HTML meta tags.
- Build DOI/publisher-page evidence queues for follow-up inspection.

## Install

Distribution is intentionally simple: **the full repository is the skill
bundle**. Use Git clone for normal use, and pin stable versions with GitHub
release tags. `pip install -e .` is only for local development and console
scripts.

### Codex

```bash
mkdir -p ~/.agents/skills
git clone https://github.com/xqy272/Litminer.git ~/.agents/skills/litminer
```

Windows PowerShell:

```powershell
New-Item -ItemType Directory -Force "$HOME\.agents\skills" | Out-Null
git clone https://github.com/xqy272/Litminer.git "$HOME\.agents\skills\litminer"
```

### Claude Code

```bash
mkdir -p ~/.claude/skills
git clone https://github.com/xqy272/Litminer.git ~/.claude/skills/litminer
```

Project-level installs are also fine: clone into `.agents/skills/litminer` or
`.claude/skills/litminer` inside the target project.

### Pin And Update

Pin a stable release:

```bash
git clone --branch <release-tag> --depth 1 https://github.com/xqy272/Litminer.git ~/.agents/skills/litminer
```

Update an existing checkout:

```bash
cd ~/.agents/skills/litminer
git pull --ff-only
python -m litminer.engine.bootstrap
python -m litminer.engine.offline_smoke
```

Read [CHANGELOG.md](CHANGELOG.md) before crossing versions.

## First Check

Litminer scripts require Python 3.10+. Runtime code uses only the Python
standard library.

```bash
python -m litminer.engine.bootstrap
python -m litminer.engine.doctor
python -m litminer.engine.offline_smoke
```

These commands do not need API keys. `offline_smoke` does not access the
network and writes sample output under `.litminer/runs/offline_smoke/`.

Ignore runtime outputs in user projects:

```gitignore
.litminer/
```

## Quick Run

```bash
python -m litminer.engine.run_lit_search \
  --mode fast \
  --query "machine learning enzyme stability external validation" \
  --year-from 2026 \
  --required-concept "validation=external validation|prospective validation" \
  --optional-concept "benchmark=benchmark|dataset" \
  --negative-concept "review=review article|survey" \
  --output-dir .litminer/runs/litminer_run
```

Start with `--mode fast` to validate paths and concepts. Use `--mode balanced`
or `--mode expanded` after the candidate direction looks right.

`re:` regex concepts are disabled by default. Enable them only for reviewed
trusted profiles with `--enable-regex-concepts` or the MCP
`enable_regex_concepts` parameter.

If the user intent, queries, or concepts change, do not use `--resume`. Start a
new research iteration instead:

```bash
python -m litminer.engine.run_lit_search \
  --mode balanced \
  --query "new focused query" \
  --required-concept "main=term1|term2" \
  --merge-into .litminer/runs/litminer_run
```

`--resume` is only for an interrupted run with the same signature.
`--merge-into` reruns dedupe, semantic ranking, verification ordering, and final
triage while preserving iteration lineage.

Export trusted bibliography during finalize:

```bash
python -m litminer.engine.run_lit_search \
  --mode balanced \
  --input-csv candidates.csv \
  --export ris \
  --export bibtex \
  --output-dir .litminer/runs/exported_run
```

Unverified, retracted, and missing-title records are excluded by default.
`--include-unverified-export` is explicit and remains audited in
`export_manifest.json`.

## Main Outputs

| File | Purpose |
|------|---------|
| `query_plan.json` | Agent-derived queries, sources, concepts, and run controls. |
| `run_spec.json` | Normalized typed run contract shared by CLI and MCP. |
| `run_outcome.json` | Execution status, retrieval quality, artifacts, warnings, and next actions. |
| `coverage_report.json` | Provider/query/verification coverage and request-ledger summary. |
| `api_candidates.csv` | API-discovered candidates. |
| `api_discovery_trace.csv` | Query/source/status/failure trace. |
| `deduped_candidates.csv` | DOI/title deduplicated candidates. |
| `pretriaged_candidates.csv` | Semantic ordering used before spending Crossref budget. |
| `verification_queue.csv` | Deterministic relevance- and DOI-aware bibliographic work queue. |
| `verified_candidates.csv` | Crossref verification output (with retraction status). |
| `triaged_candidates.csv` | Semantic evidence plus bibliographic, scientific-review, and workflow states. |
| `canonical_papers.csv` | Canonical bibliography projection for delivery/export. |
| `canonical_provenance.json` | Source, trust class, and selection reason for every canonical field. |
| `concept_diagnostics.json` | Concept match rates, source distributions, and low-selectivity warnings. |
| `citation_expanded_candidates.csv` | Citation/reference expansion candidates (when `--expand-citations` is enabled). |
| `publisher_queue.csv` | DOI/publisher-page evidence queue; verification-enabled runs default to bibliographically verified rows. |
| `publisher_queue_probed.csv` | Access probe results (when `--probe-publishers` is enabled). |
| `publisher_queue_html_meta.csv` | Publisher HTML meta field extraction (when `--probe-publishers` is enabled). |
| `result_profile.json` | Stratified descriptive statistics and search-process completeness caveats. |
| `delta_profile.json` | Current-iteration additions by priority, source, journal, and bibliographic trust. |
| `research_session_manifest.json` | Cross-iteration queries, concepts, status, and delta lineage. |
| `search_audit_report.md` | Human-readable audit report for research reproducibility. |
| `processing_report.md` | Source, metadata, triage, OA/access, citation expansion, HTML meta, and queue summary. |
| `agent_summary.json` | Machine-readable capability, verification-lane, concept-diagnostic, delta, and result-profile summary. |
| `run_manifest.json` | Stage status, reuse records, row counts, fingerprints, and signature. |
| `litminer_export.ris` / `.bib` | Optional RIS/BibTeX delivery files. |
| `export_manifest.json` | Export eligibility, exclusions, key conflicts, and hashes. |

## Optional MCP

MCP is optional. It exposes Litminer operations as callable Agent tools.

```bash
python -m litminer.sources.mcp.test_server
```

The default MCP surface contains nine high-level tools: doctor, capabilities,
plan, start, get, resume, cancel, read results, and export. Synchronous
full-run, provider wrappers, and individual stage tools remain in
`LITMINER_MCP_TOOL_PROFILE=all`. Tool failures return structured
`isError=true`/`ErrorEnvelope` results instead of requiring traceback parsing.

Recommended MCP environment:

```bash
LITMINER_WORKSPACE_ROOT=/path/to/your/project
LITMINER_CONTACT_EMAIL=you@example.org
```

See [litminer/sources/mcp/README.md](litminer/sources/mcp/README.md) and
[references/mcp-surface.md](references/mcp-surface.md).

## Common Commands

| Task | Command |
|------|---------|
| Environment check | `python -m litminer.engine.doctor` |
| Offline smoke test | `python -m litminer.engine.offline_smoke` |
| Main workflow help | `python -m litminer.engine.run_lit_search --help` |
| Semantic triage help | `python -m litminer.engine.semantic_triage --help` |
| MCP self-test | `python -m litminer.sources.mcp.test_server` |
| RIS/BibTeX export | `python -m litminer.exporters.exporter --help` |
| SQLite state export | `python -m litminer.runtime.state_store --output state_snapshot.json` |
| Architecture acceptance | `python -m litminer.engine.architecture_acceptance --help` |
| Unit tests | `python -m unittest discover -s test -p "test_*.py"` |

## Boundaries

Litminer does not:

- answer current literature, DOI, metric, or publisher-page questions from memory
- write the final review or make final inclusion decisions
- parse PDFs, OCR, supplementary information, or tables
- bypass paywalls
- guess unverifiable DOI, IF/JCR metrics, or article-level facts

## More Documentation

- [User guide](references/user-guide.en.md): install details, config, MCP, run modes, and troubleshooting.
- [Artifact contracts](references/artifact-contracts.md): stable output contracts for Agents.
- [CSV fields](references/csv-fields.md): field meanings, stages, and trust levels.
- [Agent safety](references/agent-safety.md): external-content and prompt-injection rules.
- [Agent workflow](references/agent-workflow.md): query planning, source choice, and delivery.
- [Runtime recovery](references/runtime-recovery.md): resume, budget, cache, and provider failures.
- [MCP surface](references/mcp-surface.md): tool profiles, workspace rules, and JSON-RPC examples.
- [Release checklist](references/release-checklist.md): lightweight release steps.
- [Examples](examples/README.md): minimal end-to-end example.
- [Testing](test/README.md): three-layer testing architecture.
- [SKILL.md](SKILL.md): skill entry read by Agents.
- [CLAUDE.md](CLAUDE.md): detailed Agent operating guide.

## Project Layout

```text
Litminer/
|-- README.md
|-- SKILL.md
|-- CLAUDE.md
|-- config/
|-- litminer/
|-- references/
|-- examples/
`-- test/
```

`litminer/contracts/` owns Agent-facing contracts; `litminer/runtime/` owns
SQLite, provider scheduling, and stage state; `litminer/evidence/` separates
observations from canonical papers; `litminer/exporters/` owns trusted
bibliography delivery. `litminer/engine/` retains deterministic stages and the
compatibility orchestrator, while `litminer/sources/mcp/` is the stdio adapter.

## License

[MIT License](LICENSE)
