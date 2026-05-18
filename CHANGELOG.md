# Changelog

All notable changes to Litminer are recorded here. This project uses a simple
open-source release flow: clone the repository as the skill, and use Git tags
when you want a stable version.

## Unreleased

### Added

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

- Clarified that `pip install -e .` is for local development and console
  scripts, not the primary Agent skill installation path.
- Simplified distribution wording to avoid implying PyPI, plugin, or one-click
  installer support.
- Made `query_plan.json.source_strategy` more explicit about selected sources,
  recommendation gaps, and why recommended sources were not enabled.
- Added MCP `next_actions` to direct and background workflow responses.

### Fixed

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
