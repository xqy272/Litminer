# Changelog

All notable changes to Litminer are recorded here. This project uses a simple
open-source release flow: clone the repository as the skill, and use Git tags
when you want a stable version.

## Unreleased

### Added

- Added Windows-primary/macOS-secondary native CI, one cross-platform CI
  orchestrator, and PowerShell/macOS wrappers. Linux and Docker are no longer
  release acceptance targets.
- Added shared Codex/Claude Code operating contracts, root client adapters,
  deterministic adapter acceptance, and optional real-client CLI acceptance.
- Added controlled parser-level live acceptance for OpenAlex, Crossref,
  Semantic Scholar, arXiv, Europe PMC, and Unpaywall, with structured skips and
  an isolated SQLite request ledger.
- Added SQLite schema migration 2 with append-only `runtime_events`, v1 upgrade
  acceptance, idempotency checks, and deliberately broken migration rollback.
- Added native subprocess crash/restart acceptance for CLI stage recovery and
  MCP worker loss, plus quick/standard/long runtime soak profiles.
- Added a typed Contract Layer with shared `RunSpec`, `RunOutcome`, JSON-Schema
  validation, stable `ErrorEnvelope` classes, and generated MCP descriptions.
- Added workspace-local SQLite runtime state for sessions, iterations, stages,
  provider health/cooldown, one-row-per-attempt request ledger, source
  observations, canonical papers/provenance, artifacts, jobs, and outcomes,
  including migration rollback and portable state export.
- Added a provider-wide scheduler/runtime used by discovery, Crossref,
  Unpaywall, Semantic Scholar citation expansion, live preflight, and advanced
  MCP wrappers. Cooldowns survive later runs and MCP restarts.
- Added `coverage_report.json` with independent `healthy`, `degraded`, and
  `inconclusive` retrieval quality plus verification and request-ledger data.
- Added `canonical_papers.csv` and `canonical_provenance.json`, keeping raw
  source observations separate from canonical bibliography and scientific
  annotations.
- Added audited RIS and BibTeX export, stable collision handling,
  Unicode/ASCII-LaTeX modes, default trust/retraction exclusions, and
  `export_manifest.json`.
- Added `litminer-export`, `litminer-state-export`, and deterministic
  next-architecture acceptance entry points plus Agent scenarios for degraded
  coverage, all-provider failure, persisted cooldown, invalid MCP input, and
  export exclusion.
- Added a two-pass `dedupe -> pretriage -> verification_queue -> Crossref ->
  final triage` workflow so limited verification budgets are spent on the most
  relevant DOI-bearing rows first, including after citation expansion.
- Added orthogonal bibliographic, scientific-review, and workflow status fields,
  plus field-level semantic match evidence and `concept_diagnostics.json`.
- Added incremental research sessions with `--merge-into`,
  `delta_profile.json`, `research_session_manifest.json`, and MCP schema
  support.
- Added transparent arXiv query compilation with `provider_query` and
  `provider_query_mode` audit fields.
- Added explicit capability states so a zero count can be distinguished from a
  capability that was not run.
- Added a concise Chinese README entry page and moved detailed usage material
  into `references/user-guide.md`.
- Added English README parity plus `references/user-guide.en.md`.
- Added artifact contracts, CSV field dictionary, Agent safety rules, release
  checklist, a minimal example, Agent eval prompts, and MCP self-test coverage
  in GitHub Actions.
- Documented the lightweight distribution path: Git clone as the official skill
  install, release tags for stable versions, and `CHANGELOG.md` for upgrades.
- Added README guidance for updating an existing clone with `git pull --ff-only`
  followed by `bootstrap` and `offline_smoke`.
- Added explicit README guidance that `.litminer/` is a runtime output folder
  and should stay ignored in user projects.

### Changed

- Split run initialization/recovery wiring into `runtime/run_lifecycle.py` and
  artifact/outcome finalization into `engine/run_finalizer.py`; the compatibility
  runner is now below 2400 lines.
- Split MCP protocol, job persistence, and high-level workflow helpers into
  focused modules; the stdio server remains the compatibility entry point and
  is now below 1500 lines.
- Reduced the default MCP surface to nine high-level tools: doctor,
  capabilities, plan, start, get, resume, cancel, read results, and export.
  Synchronous full-run, low-level provider, legacy status/summary, and stage
  tools remain in the advanced profile.
- MCP schemas now come directly from the Contract Layer, express mutually
  exclusive input modes and ranges, validate before execution, and expose
  explicit provider/result pagination.
- MCP tool failures now return `isError=true` with structured error data;
  JSON-RPC errors are reserved for protocol/method failures.
- Processing and search-audit reports now include run quality, coverage,
  request-ledger, canonical provenance, and bibliography export sections.
- Crossref-verified `result_profile` statistics now use Crossref DOI, year,
  container, and article type as canonical fields.
- When Crossref is enabled, the publisher evidence queue now defaults to
  bibliographically verified rows; fast mode still permits unverified DOI
  discovery pointers.
- Biomedical source strategy now treats arXiv as supplemental, and plain arXiv
  intent queries are made explicit rather than relying on provider defaults.
- Clarified that `pip install -e .` is for local development and console
  scripts, not the primary Agent skill installation path.
- Simplified distribution wording to avoid implying PyPI, plugin, or one-click
  installer support.
- Made `query_plan.json.source_strategy` more explicit about selected sources,
  recommendation gaps, and why recommended sources were not enabled.
- Added MCP `next_actions` to direct and background workflow responses.

### Fixed

- Made Python 3.10 Agent acceptance validate the Codex TOML template without
  adding a third-party runtime dependency, and made Windows launcher detection
  safe to exercise from native macOS Python 3.10.
- Canonicalized SQLite run-path identity and made path assertions alias-aware,
  covering GitHub Runner short paths on Windows and `/var` versus
  `/private/var` on macOS.
- Isolated the missing-Unpaywall-email regression from user-level contact
  environment variables.
- Restored the exact feasibility-report Markdown contract after finalizer
  extraction, including inline-code markers used by existing Agent tests.
- Made real Codex acceptance compatible with current Windows npm `.CMD`
  launchers by placing global approval flags before `exec` and sending the
  long prompt over stdin; Claude JSON envelopes remain supported.
- Aligned Crossref live acceptance with the parser's
  `crossref_doi`/`crossref_title` fields and fixed Unpaywall's direct
  DOI CLI JSON import plus no-contact live preflight handling.
- Absolutized runtime-soak output roots before spawning child workflows so
  relative Windows paths survive first-run, resume, and merge cycles; failed
  pipeline reports now retain bounded child stderr.
- Closed SQLite initialization connections explicitly on Windows, made
  migration application transactional, and prevented callback/thread objects
  from entering serialized `RunSpec` output.
- Made MCP background jobs preallocate a persistent `run_id`, double-write job
  state to JSON/SQLite, classify background `SystemExit`, and report abandoned
  queued/running jobs as `interrupted` after worker loss.
- Made `litminer_get_run` prefer SQLite state for active/new runs, avoid reading
  artifacts while a background writer is active, and retry short-lived Windows
  atomic-replace locks instead of failing the run.
- Rejected export prefixes containing path/alternate-stream or reserved
  filename characters at both MCP schema and exporter boundaries.
- Made BibTeX keys deterministic and unique even when malformed canonical input
  repeats a `paper_id`.
- Reserved `crossref_mismatches` for real metadata disagreements and moved row
  budgets, provider failures, and lookup failures to status/error fields.
- Made Crossref row budgets count unresolved work rather than reusable verified
  rows.
- Prevented provider failures and budget exhaustion from being mislabeled as
  scientific `llm_review_needed` work.
- Kept `--resume` inside the active research iteration and refreshed the final
  Agent summary before generating `search_audit_report.md`, preventing false
  extra iterations and stale `Status: unknown` audit output.
- Rebuilt `artifacts_index.json` after final report generation so delivered
  summary, processing-report, and audit-report hashes are current.
- Routed negative-concept matches to scientific review even when their
  relevance priority remains medium.
- Rejected duplicate CSV header fields during merge instead of silently
  overwriting values, with direct union-schema and empty/missing-input coverage.
- Cleaned the tracked Agent regressions: partial CLI runs no longer claim
  completion or print nonexistent triage paths, regex rejection no longer emits
  a traceback, and publisher queues deduplicate normalized DOI values.
- Made the minimal example complete successfully while still exercising a
  missing-DOI manual-review row with a landing-page URL.
- Aligned run manifest stage records with the documented `input_path` /
  `output_path` artifact contract while retaining `input` / `output` aliases.
- Marked `query_plan.json` source selection as `input_csv` when a run starts
  from local CSV instead of API discovery.
- Removed stage-level `use_as_verified_for_this_stage` guidance from MCP
  run-level `next_actions`.
- Made cache writes lock-protected so concurrent runs do not overwrite each
  other's JSON cache entries.
- Propagated partial stage states into final run status for rate limit, budget,
  network, auth, provider, and validation failures.
- Made caller-supplied `re:` semantic concepts opt-in.
- Added MCP background job persistence and stage-boundary cancellation.
