# Litminer Artifact Contracts

This document defines the stable artifacts an Agent should prefer over ad hoc
file inspection. Fields may grow over time, but existing stable fields should
not be renamed without a changelog entry.

## Contract Levels

| Level | Meaning |
|-------|---------|
| Stable | Agent-safe for automation and user-facing citations of run state. |
| Extensible | New fields may be added; consumers should ignore unknown keys. |
| Debug | Useful for troubleshooting, not a primary contract. |

## `agent_summary.json`

Level: Stable, extensible.

Purpose: first file an Agent should read after a run.

Stable keys:

- `schema_version`
- `output_dir`
- `run_status`
- `partial`
- `stage_statuses`
- `stage_status_classes`
- `stage_next_actions`
- `trust_tiers`
- `provider_statuses`
- `provider_status_classes`
- `provider_next_actions`
- `source_strategy`
- `primary_artifacts`
- `next_actions`

Agent rule: use `next_actions` and `stage_next_actions` before rerunning broad
discovery or presenting a low candidate count as scientific absence.

## `run_manifest.json`

Level: Stable, extensible.

Purpose: audit trail for stages, reuse, fingerprints, cache config, run
signature, and partial completion.

Stable keys:

- `run_id`
- `run_status`
- `mode`
- `started_at`
- `completed_at`
- `stop_reason`
- `resume_enabled`
- `cache`
- `run_signature`
- `stages`

Each stage record should preserve:

- `name`
- `status`
- `message`
- `input_path`
- `output_path`
- `row_count`
- `output_sha256`
- `output_fields`

`input` and `output` may also appear as compatibility aliases for older Agent
readers. Prefer `input_path` and `output_path` in new automation.

Agent rule: if `run_status` is `partial`, do not describe the run as complete.
Use `--resume` only when the user request has not changed.

## `query_plan.json`

Level: Stable, extensible.

Purpose: record runtime intent derived by the Agent.

Stable keys:

- `schema_version`
- `mode`
- `queries`
- `query_count`
- `year_range`
- `concepts`
- `discovery_sources`
- `source_rationale`
- `source_strategy`
- `run_controls`
- `agent_notes`

`source_strategy.source_selection` distinguishes selected sources from
recommended-but-not-selected sources. `automatic_expansion` must remain false
unless a future version explicitly changes source-selection behavior.

Agent rule: `source_strategy` is advisory. It never expands sources silently.

## `api_discovery_trace.csv`

Level: Stable.

Purpose: provider/query audit trail.

Stable fields:

- `query_id`
- `query`
- `provider`
- `status`
- `status_class`
- `http_status`
- `retry_after_seconds`
- `transient_error`
- `cache_status`
- `next_action`
- `error`

Agent rule: inspect `status_class`, `transient_error`, and `next_action`
before treating empty source results as evidence.

## `triaged_candidates.csv`

Level: Stable for triage fields, extensible for source/provider metadata.

Purpose: Agent review surface after semantic triage.

Stable fields:

- `title`
- `doi`
- `publication_year`
- `journal`
- `triage_priority`
- `triage_score`
- `triage_reasons`
- `matched_required`
- `matched_optional`
- `matched_negative`
- `candidate_status`
- `metadata_status`
- `llm_review_needed`

Agent rule: triage priorities are ranking signals, not final inclusion
decisions.

## `publisher_queue.csv`

Level: Stable for queue fields, extensible for evidence fields.

Purpose: queue of DOI/publisher pages for follow-up inspection.

Stable fields:

- `title`
- `doi`
- `doi_url`
- `publisher_url`
- `fields_needed`
- `next_action`
- `triage_priority`
- `candidate_status`
- `metadata_status`

Agent rule: publisher queue rows are inspection targets, not extracted
article-level claims.

## Debug And Supporting Artifacts

Files such as `api_discovery_report.md`, validation reports, probed publisher
outputs, and cache files are useful for troubleshooting. Agents should prefer
the primary artifacts above unless a failure path asks for a specific debug
file.
