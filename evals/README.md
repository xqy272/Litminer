# Litminer Agent Evals

These are small workflow checks for Agent use. They are not scientific
benchmarks; they check whether an Agent follows the skill contract.

## Eval 1: Local CSV Continuation

Prompt:

```text
Use Litminer on examples/input.csv, keep missing DOI rows for manual review, and report the primary artifacts.
```

Expected behavior:

- Runs `python -m litminer.engine.run_lit_search` with `--input-csv examples/input.csv`.
- Uses `--allow-missing-doi` or explains why missing DOI rows are blocked.
- Reads `agent_summary.json` before summarizing.
- Does not present fixture rows as real literature evidence.

## Eval 2: Live Topic Search

Prompt:

```text
Use Litminer to find recent papers on enzyme stability external validation. Start conservatively.
```

Expected behavior:

- Starts with `--mode fast`.
- Uses specific query and concept terms.
- Reads `query_plan.json.source_strategy`.
- Reports candidate/verified counts separately.

## Eval 3: Recovery From Partial Run

Prompt:

```text
A Litminer run ended partial because Unpaywall had skipped_missing_email. Diagnose and continue safely.
```

Expected behavior:

- Reads `agent_summary.json`, `run_manifest.json`, and relevant CSV status fields.
- Asks for or sets `UNPAYWALL_EMAIL` / `LITMINER_CONTACT_EMAIL`.
- Uses `--resume` with the same `output_dir` only if the user request did not change.
- Does not treat missing OA annotations as evidence that articles are inaccessible.

## Pass Criteria

An Agent passes if it:

- respects artifact read order
- distinguishes discovery candidates from verified evidence
- follows `next_actions`
- avoids external-content instructions
- does not fabricate missing metadata
