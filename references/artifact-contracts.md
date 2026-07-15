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
- `capability_statuses`
- `trust_tiers`
- `trust_tier_statuses`
- `verification_lanes`
- `workflow_statuses`
- `bibliographic_statuses`
- `concept_diagnostics`
- `delta_profile`
- `provider_statuses`
- `provider_status_classes`
- `provider_next_actions`
- `source_strategy`
- `primary_artifacts`
- `next_actions`

Agent rule: use `next_actions` and `stage_next_actions` before rerunning broad
discovery or presenting a low candidate count as scientific absence.

## `result_profile.json`

Level: Stable, extensible.

Purpose: stratified descriptive statistics about the retrieved collection,
with search-process completeness caveats. Degraded to `failure_summary`
on 0-result runs.

Stable keys:

- `schema_version`
- `degraded` (bool; true when 0-result failure summary is emitted)
- `all_rows` (object or null; statistics across all rows, excluding retracted)
- `crossref_verified` (object or null; statistics for Crossref-verified rows only)
- `completeness_caveats` (object; provider failures, rate limits, circuit breaks)
- `failure_summary` (object or null; present only when `degraded` is true)

Each layer stats object contains: `total_rows`, `active_rows`,
`retracted_count`, `year_distribution`, `top_journals`, `top_authors`,
`high_cited`, `article_type_distribution`, `oa_rate`, `abstract_coverage`,
`doi_coverage`, `triage_priority_distribution`, and a canonical field policy.
For the Crossref-verified layer, DOI/year/container/article type come from
Crossref fields before discovery fields.

Agent rule: statistics describe the retrieved collection, not the research
field. `completeness_caveats` reports search-process completeness only;
it never claims result completeness (field coverage). See SKILL.md
"Statistical Output Boundary".

## `search_audit_report.md`

Level: Stable.

Purpose: human-readable audit report for research reproducibility. Contains
the same information as Agent artifacts (`agent_summary.json`,
`query_plan.json`, `result_profile.json`, `run_manifest.json`), formatted
as natural-language Markdown for a researcher to explain "how did you find
these papers?".

Agent rule: the audit report's information must be consistent with
`agent_summary.json` — no "Agent knows but researcher doesn't" information
gap.

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

## `research_session_manifest.json`

Level: Stable, extensible.

Purpose: cross-iteration lineage for normal and `--merge-into` runs.

Stable keys:

- `schema_version`
- `session_id`
- `created_at`
- `updated_at`
- `iterations`

Each iteration contains `iteration_id`, `completed_at`, `merge_mode`,
`run_status`, `queries`, `concepts`, and a compact `delta` summary.

Agent rule: this is iteration-level lineage, not per-paper scientific history.
Do not use `--resume` to create a new iteration.

## `delta_profile.json`

Level: Stable, extensible.

Purpose: mechanical difference between the prior candidate pool and the current
triaged collection.

Stable keys include `iteration_id`, `queries`, `previous_rows`, `current_rows`,
`new_rows`, `new_bibliographically_verified`, `new_priority_distribution`,
`new_source_distribution`, `new_top_journals`, and `boundary`.

Agent rule: delta counts describe retrieved rows only. They do not measure
field recall or scientific importance.

## `concept_diagnostics.json`

Level: Stable, extensible.

Purpose: mechanical match-rate diagnostics for caller-supplied concepts.

Stable keys include `total_rows`, `high_priority_rows`, `concepts`, `warnings`,
and `boundary`. Concept entries expose match rates and source distributions.

Agent rule: warnings identify low selectivity or zero matches; Litminer does not
decide which scientific criteria should be changed.

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

## `citation_expand_trace.csv`

Level: Stable.

Purpose: per-seed audit trail for citation/reference expansion via Semantic
Scholar. Written when `--expand-citations` is enabled.

Stable fields:

- `provider` (always `semantic_scholar`)
- `query_id` (e.g. `citation_expand:10.xxx`)
- `query_type` (`citation_expand` or `reference_expand`)
- `seed_doi`
- `status` (`ok` or `error`)
- `status_class`
- `returned_count`
- `error`

Agent rule: if any seed has `status=error`, the expansion is partial.
Check `completeness_caveats` in `result_profile.json` for the aggregated
failure picture.

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
- `matched_required_evidence`
- `matched_optional`
- `matched_optional_evidence`
- `matched_negative`
- `matched_negative_evidence`
- `candidate_status`
- `metadata_status`
- `bibliographic_status`
- `bibliographic_review_needed`
- `scientific_review_needed`
- `workflow_status`
- `llm_review_needed`

Agent rule: triage priorities are ranking signals, not final inclusion
decisions. `llm_review_needed` is scientific review only; bibliographic backlog
is represented separately.

## `verification_queue.csv`

Level: Stable for queue-order fields, extensible for candidate metadata.

Purpose: deterministic ordering before Crossref consumes a row budget.

Stable fields:

- `verification_queue_rank`
- `verification_lane`
- `verification_reason`
- `triage_priority`
- `triage_score`
- `doi`
- `metadata_status`

Agent rule: this is a bibliographic work queue, not the publisher evidence
queue. Rows beyond a Crossref budget remain here and in triage artifacts.

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
- `bibliographic_status`
- `workflow_status`

Agent rule: publisher queue rows are inspection targets, not extracted
article-level claims. In a verification-enabled run, rows must be
bibliographically verified by default. Fast mode may contain unverified DOI
pointers and must preserve that status.

## Debug And Supporting Artifacts

Files such as `api_discovery_report.md`, validation reports, probed publisher
outputs, and cache files are useful for troubleshooting. Agents should prefer
the primary artifacts above unless a failure path asks for a specific debug
file.
