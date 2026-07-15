# Changelog

All notable changes to Litminer are recorded here. This project uses a simple
open-source release flow: clone the repository as the skill, and use Git tags
when you want a stable version.

## Unreleased

### Added

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
