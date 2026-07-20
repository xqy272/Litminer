# Litminer Codex Guide

Contract-Version: 1

Codex must read SKILL.md and references/agent-operating-contract.md before
operating Litminer. This file adds Codex-specific guidance and does not
override the shared contract.

## Supported Targets

- Windows is primary.
- macOS is secondary.
- Native GitHub Actions `macos-latest` runners are the macOS evidence
  path when no local Mac is available.
- Linux and Docker are not release targets.

## Skill Installation

User-level:

    ~/.agents/skills/litminer

Project-level:

    .agents/skills/litminer

The full repository is the skill package.

## Codex Operating Rules

1. Prefer the skill workflow over ad hoc provider calls.
2. Use MCP when structured results and background jobs improve the task.
3. Use CLI when MCP is unavailable or workspace mapping is unclear.
4. On Windows, use the project virtual-environment Python when present.
5. Keep all user inputs and outputs inside LITMINER_WORKSPACE_ROOT.
6. Never modify user inputs in place.
7. Keep scientific decisions with the user and Agent.

## Default MCP Tools

- litminer_workspace_doctor
- litminer_capabilities
- litminer_plan_run
- litminer_start_run
- litminer_get_run
- litminer_resume_run
- litminer_cancel_run
- litminer_read_results
- litminer_export

Do not enable the advanced profile unless the task specifically needs it.

## Recommended Workflow

1. Run doctor for a new or failing workspace.
2. Inspect capabilities when live provider readiness is relevant.
3. Plan the RunSpec before expensive retrieval.
4. Start the background workflow.
5. Poll get_run with job_id or run_id.
6. Read run_outcome and coverage before large CSVs.
7. Resume only unchanged runs; use merge_into for changed intent.
8. Export only canonical bibliography and report exclusions.

## Artifact Read Order

1. run_outcome.json
2. coverage_report.json
3. agent_summary.json
4. canonical_papers.csv
5. canonical_provenance.json
6. result_profile.json
7. research_session_manifest.json
8. delta_profile.json
9. processing_report.md
10. search_audit_report.md
11. artifacts_index.json
12. run_spec.json
13. query_plan.json

## Windows Notes

- Prefer scripts/run_ci.ps1 for local validation.
- Prefer absolute Windows Python paths in MCP configuration.
- Treat PermissionError and WinError 5 or 32 as possible file-lock conditions.
- Preserve UTF-8 for Chinese paths and reports.
- Do not translate Windows paths through a Linux container.

## Development

Quick validation:

    powershell -ExecutionPolicy Bypass -File scripts/run_ci.ps1 quick

Full validation requires the dev extra:

    powershell -ExecutionPolicy Bypass -File scripts/run_ci.ps1 full

Do not commit generated .litminer runtime state.
