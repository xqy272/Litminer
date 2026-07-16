# Litminer Runtime And Recovery Reference

Use this file when a run is slow, interrupted, rate-limited, path-blocked, or
affected by local environment issues.

## First Checks

Run these before a long live search on a new machine or workspace:

```bash
python -m litminer.engine.bootstrap
python -m litminer.engine.doctor
python -m litminer.engine.offline_smoke
```

`offline_smoke` does not use the network. If it fails, fix local Python,
workspace, or file permissions before debugging API providers.

On Windows, check both `python` and `py -3`:

```powershell
python --version
py -3 --version
```

If only `py -3` works, use `py -3 -m litminer...` or point MCP `command` at an
absolute Python executable path.

## Workspace Rules

Keep the skill/code directory separate from the user workspace. Runtime outputs
belong under `.litminer/` in the active workspace.

When `LITMINER_WORKSPACE_ROOT` is set, default relative outputs resolve under
that root. In MCP mode, file arguments must stay under that root. When it is
unset, MCP uses the process `cwd` as the workspace root.

If an Agent reports that the workspace is not working, run:

```bash
python -m litminer.engine.doctor --workspace WORKSPACE_ROOT --explain-path SOME_PATH
```

or call `litminer_workspace_doctor` through MCP.

## SQLite Runtime State

By default Litminer keeps internal recovery state at
`.litminer/state/litminer.sqlite3`. This database stores research
session/iteration state, stage records, provider health and cooldowns, one row
per HTTP attempt, source observations, canonical field provenance, artifact
snapshots, background jobs, and final outcomes.

The SQLite database is internal state, not the deliverable. CSV, JSON,
Markdown, RIS, and BibTeX artifacts remain portable snapshots and can be read
without SQLite. Disable state only when necessary with `--no-state-store`; doing
so removes cross-process cooldown and job-recovery continuity.

Use a custom workspace-local database with `--state-store PATH` or
`LITMINER_STATE_STORE`. Export a portable diagnostic snapshot with:

```bash
python -m litminer.runtime.state_store --state-store .litminer/state/litminer.sqlite3 --output state_snapshot.json
```

The database uses migrations, foreign keys, WAL mode, transaction rollback,
and short-lived connections so interrupted Windows processes do not leave
normal operations permanently locked.

## Resume Rules

Use `--resume` with the same `--output-dir` after a timeout or interruption.
Do this only when the user request, queries, concepts, sources, year range, and
major run controls have not changed.

Litminer writes `run_manifest.json` with a run signature. If the signature does
not match, use a new output directory. Use `--resume-allow-mismatch` only after
manual review, and always provide `--resume-mismatch-reason`.

Crossref and Unpaywall stages checkpoint periodically. Resuming should reuse
already annotated rows instead of starting at the first DOI again.

MCP background jobs are double-written to `.litminer/jobs/*.json` and SQLite.
`litminer_start_run` returns both `job_id` and persistent `run_id`.
`litminer_get_run` can recover by `job_id`, `run_id`, or `output_dir`. A
queued/running job loaded after its worker process disappeared is reported as
`interrupted`, never as a still-running ghost job.

If the request, queries, concepts, sources, or mode changed, do not force a
resume-signature mismatch. Use `--merge-into EXISTING_OUTPUT_DIR` instead. It
creates a new research iteration, snapshots the prior candidate pool, reruns
downstream stages, and writes `delta_profile.json` and
`research_session_manifest.json`. `--resume` and `--merge-into` cannot be
combined.

## Time And Row Budgets

Use these controls for long or uncertain tasks:

- `--time-budget-seconds N`: stop cleanly at a stage boundary after the budget.
- `--stop-after-stage STAGE`: intentionally produce partial artifacts.
- `--max-crossref-rows N`: spend the budget on unresolved rows after
  `verification_queue.csv` ordering; reusable verified rows do not consume it,
  and overflow rows are marked `skipped_budget`.
- `--max-unpaywall-rows N`: mark overflow rows as `skipped_budget`.
- `--max-publisher-probe-rows N`: cap publisher probing when `--probe-limit` is
  not set.

Budgeted rows are not silently dropped. Inspect `verification_queue.csv`,
`bibliographic_status=pending_budget`, and
`workflow_status=pending_bibliographic_verification`. These rows remain in the
candidate backlog but do not enter the publisher evidence queue when Crossref
was enabled.

## Provider Failure Semantics

Discovery trace fields:

- `status`: provider-specific status for this query call.
- `status_class`: normalized class such as `ok`, `rate_limited`, `network`,
  `auth`, `partial`, `skipped`, `budget_limited`, or `error`.
- `http_status`: HTTP status when the provider exposed one.
- `transient_error`: whether the provider wrapper marked the failure transient.
- `retry_after_seconds`: provider retry hint when available.
- `cache_status`: `hit` or `store` for short-lived provider failure cache.
- `next_action`: Agent-facing recovery hint.

Treat `network`, `auth`, and `rate_limited` as retrieval limitations. They are
not evidence that relevant literature does not exist.

Crossref operational states belong in `crossref_status` and
`crossref_error_code`. `crossref_mismatches` is reserved for actual title,
year, journal, or other metadata disagreements. Provider failures and budget
exhaustion must not set `llm_review_needed`; scientific and bibliographic review
are separate.

Use `--provider-failure-threshold N` to stop repeatedly calling a provider that
fails during the same run. Use `--provider-rate-limit-cooldown-seconds N` to
avoid immediate repeat calls after a 429 when no provider `Retry-After` is
available.

Provider cooldown is provider-wide and persisted. Crossref, Unpaywall,
discovery providers, Semantic Scholar citation expansion, live preflight, and
advanced MCP wrappers all use the same scheduler and request ledger. A later
run or restarted MCP process checks `not_before` before issuing another request.
Scheduler skips are recorded with attempt `0`, while actual HTTP attempts are
recorded individually with hashed query/URL identifiers rather than sensitive
raw URLs or API keys.

Read `coverage_report.json.request_ledger` for aggregate attempts, retries,
wait time, and provider/status counts. Use `processing_report.md` or
`search_audit_report.md` for the human-readable view.

## Cache Boundary

Litminer cache is workspace-local acceleration. It is not evidence and should
not be cited instead of run artifacts.

Crossref and Unpaywall cache only positive results:

- Crossref caches successful DOI verification and high-confidence title DOI
  recovery.
- Unpaywall caches `ok` OA/access responses.
- Failed, not-found, missing, mismatch, skipped, and budgeted rows are not
  durable cache evidence.

Provider failure cache is intentionally short-lived and conservative:

- cached: rate limits, network failures, and explicitly transient provider
  errors with no returned rows
- not cached: auth failures, generic errors, mismatches, not-found responses,
  and partial calls that returned rows

After fixing network permission, proxy, certificates, API keys, or contact
email setup, use `--no-cache` if stale failure state could affect the run.

## Common Cases

`status_class=rate_limited`: wait for `retry_after_seconds`, reduce query
volume, lower result limits, or resume later.

`status_class=network`: check Agent network approval, proxy, DNS, TLS
certificates, and host environment restrictions.

`status_class=auth`: check API key, provider access policy, contact email, or
whether the provider requires registration. Rerun after fixing it; auth
failures are not cached by default.

`skipped_cached_provider_failure`: the same provider/query recently hit a
transient failure. Wait for TTL or rerun with `--no-cache`.

`skipped_budget`: increase the row budget or continue from current artifacts.
The highest relevance DOI-bearing rows were attempted first; inspect
`verification_queue_rank` before choosing the next budget.

Capability count is zero: inspect `agent_summary.json.capability_statuses`.
`not_run` means the feature was disabled/skipped, while `completed` with count
zero means it ran and found no trusted rows.

`skipped_missing_email` in Unpaywall: set `UNPAYWALL_EMAIL` or
`LITMINER_CONTACT_EMAIL` and rerun.

PowerShell Chinese text looks garbled: set UTF-8 output in the shell. This is a
terminal display issue unless the file itself is corrupted.
