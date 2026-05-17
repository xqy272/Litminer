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

## Resume Rules

Use `--resume` with the same `--output-dir` after a timeout or interruption.
Do this only when the user request, queries, concepts, sources, year range, and
major run controls have not changed.

Litminer writes `run_manifest.json` with a run signature. If the signature does
not match, use a new output directory. Use `--resume-allow-mismatch` only after
manual review, and always provide `--resume-mismatch-reason`.

Crossref and Unpaywall stages checkpoint periodically. Resuming should reuse
already annotated rows instead of starting at the first DOI again.

## Time And Row Budgets

Use these controls for long or uncertain tasks:

- `--time-budget-seconds N`: stop cleanly at a stage boundary after the budget.
- `--stop-after-stage STAGE`: intentionally produce partial artifacts.
- `--max-crossref-rows N`: mark overflow rows as `skipped_budget`.
- `--max-unpaywall-rows N`: mark overflow rows as `skipped_budget`.
- `--max-publisher-probe-rows N`: cap publisher probing when `--probe-limit` is
  not set.

Budgeted rows are not silently dropped. Inspect their explicit skipped status.

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

Use `--provider-failure-threshold N` to stop repeatedly calling a provider that
fails during the same run. Use `--provider-rate-limit-cooldown-seconds N` to
avoid immediate repeat calls after a 429 when no provider `Retry-After` is
available.

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

`skipped_missing_email` in Unpaywall: set `UNPAYWALL_EMAIL` or
`LITMINER_CONTACT_EMAIL` and rerun.

PowerShell Chinese text looks garbled: set UTF-8 output in the shell. This is a
terminal display issue unless the file itself is corrupted.
