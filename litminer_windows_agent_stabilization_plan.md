# Litminer Windows-First Agent Stabilization And Decomposition Plan

> Status: Implemented; Windows validated; current macOS evidence needs refresh
> Baseline: 8c19225 feat: implement next-generation Litminer architecture
> Date: 2026-07-16
> Windows evidence: full CI with 169 tests, real Codex/Claude Code, a healthy
> strict six-provider release gate, and a 185.95-second standard soak with 120
> iterations, 9 pipeline cycles, zero failures/lock retries, SQLite integrity
> `ok`, and WAL mode
> macOS evidence: `test-macos` passed on Python 3.10/3.12/3.14 at commit
> `e404555`; the current uncommitted changes plus `live-macos`/`soak-macos`
> require commit, push, and native GitHub Actions runs
> Evidence note: this is the implementation record. Every release must
> regenerate current evidence under `references/release-checklist.md`; these
> historical runs are not a permanent green badge.

## 1. Decision

Litminer will optimize for two supported desktop operating systems and two
primary Agent clients:

1. Windows is the primary platform and release gate.
2. macOS is the secondary platform and release gate.
3. Codex is a primary Agent client.
4. Claude Code is a primary Agent client.
5. Linux is not a supported release target.
6. Docker/Linux checks may be used for isolated experiments, but they are not
   product acceptance evidence.

This decision does not replace the next-generation architecture implemented
after commit 5157395. It defines the stabilization, acceptance, and
decomposition work required before Litminer should be treated as a stable
desktop Agent skill.

## 2. Current Baseline

The repository already has:

- shared RunSpec, RunOutcome, and ErrorEnvelope contracts
- workspace-local SQLite runtime state
- provider-wide cooldown and per-attempt request ledger
- source observations and canonical bibliography projection
- coverage quality separated from execution status
- audited RIS and BibTeX export
- a compact nine-tool default MCP surface
- deterministic unit, Agent scenario, MCP, and architecture acceptance tests
- compatibility with existing CLI arguments and run artifacts

At baseline commit 8c19225, the remaining work was stabilization and boundary
completion:

- CI still treats Ubuntu as the primary environment
- Windows-specific behavior is not a first-class CI gate
- macOS is not currently tested
- CLAUDE.md is stale relative to the new contract and MCP surface
- Codex has no root AGENTS.md operating guide
- provider live acceptance is incomplete
- crash/restart and long-running behavior are not tested through real process
  termination
- the SQLite migration framework has rollback tests but no real upgrade from
  one shipped schema version to another
- run_lit_search.py and the MCP server still contain compatibility-era
  orchestration concentration

## 3. Non-Negotiable Principles

### 3.1 Preserve existing work

This is not a rewrite. Existing CLI flags, default artifacts, resume rules,
merge semantics, skill installation layout, and advanced MCP tools remain
compatible unless an explicit migration note is added.

### 3.2 Native operating-system evidence

Windows behavior must be tested on native Windows runners. macOS behavior must
be tested on native macOS runners. Linux containers cannot substitute for
either platform.

### 3.3 Shared core, thin Agent adapters

Codex and Claude Code must consume the same machine contracts, MCP schemas,
artifacts, and safety boundaries. AGENTS.md and CLAUDE.md are client-specific
operating adapters, not independent product specifications.

### 3.4 Live tests are controlled

Live provider acceptance uses minimal requests, records every attempt, honors
provider policy, never bypasses public limits, and is manual or scheduled
rather than a required check on every pull request.

### 3.5 Refactor only behind acceptance gates

Runner and MCP decomposition starts only after Windows/macOS, Agent contract,
provider, and resilience tests can detect compatibility regressions.

## 4. Target Validation Architecture

### 4.1 Local commands

One cross-platform Python entry point owns CI command sequencing:

    python scripts/run_ci.py --profile quick
    python scripts/run_ci.py --profile full
    python scripts/run_ci.py --profile live
    python scripts/run_ci.py --profile soak

Native convenience wrappers call the same entry point:

    scripts/run_ci.ps1
    scripts/run_ci.sh

The Python runner does not install dependencies silently. It reports missing
development tools and tells the user to install the dev extra in a project
virtual environment.

### 4.2 GitHub Actions

Required jobs:

- Windows lint/type gate on Python 3.14
- Windows full test matrix on Python 3.10 through 3.14
- macOS full tests on Python 3.10, 3.12, and 3.14
- Windows MCP and offline Agent acceptance
- macOS MCP and offline Agent acceptance

Manual jobs:

- Windows release provider gate
- macOS release provider gate
- Windows runtime soak
- macOS short runtime soak

Ubuntu is removed from the required matrix.

### 4.3 Acceptance evidence

Every acceptance command writes a JSON report containing:

- schema version
- platform and Python version
- scenario name
- status
- duration
- artifacts
- structured errors
- explicit skipped reason

Terminal text is never the only evidence.

## 5. Codex And Claude Code Contract

### 5.1 Shared contract

SKILL.md remains the shared skill definition. A new
references/agent-operating-contract.md defines:

- supported operating systems
- installation model
- default high-level MCP tools
- canonical artifact read order
- execution status versus retrieval quality
- resume versus merge behavior
- external-content safety
- export trust policy
- required recovery behavior

### 5.2 Codex adapter

Root AGENTS.md will:

- direct Codex to SKILL.md and the shared operating contract
- document Windows-first PowerShell behavior
- explain CLI versus MCP selection
- require the nine-tool workflow profile by default
- define artifact read order and delivery boundaries
- prohibit silent provider broadening and scientific overclaiming

### 5.3 Claude Code adapter

Root CLAUDE.md will be rewritten against the same contract:

- remove stale legacy tool lists
- add RunSpec, RunOutcome, coverage, canonical evidence, and export semantics
- document Windows and macOS installation
- document Claude Code MCP configuration without secrets
- use the same default tool and artifact ordering as Codex

### 5.4 Agent acceptance

A deterministic harness validates both adapters without requiring an LLM:

- required files and install paths
- configuration examples
- default MCP tool list
- structured invalid-input errors
- plan/start/get/read/resume/export workflow
- path boundary behavior
- partial and degraded result semantics

A real-Agent profile invokes installed Codex and Claude Code CLIs against the
project checkout with a read-only client sandbox and per-invocation MCP
configuration. It checks machine-observable tool calls and structured results,
not exact natural-language wording. Missing clients fail by default;
--allow-missing-client is only a developer diagnostic and is never release
evidence.

## 6. Provider Live Acceptance

The provider acceptance command supports:

    python -m litminer.engine.provider_acceptance --profile core
    python -m litminer.engine.provider_acceptance --profile full
    python -m litminer.engine.provider_acceptance --profile release
    python -m litminer.engine.provider_acceptance --provider openalex

Core profile:

- OpenAlex
- Crossref

Full profile:

- OpenAlex
- Crossref
- Semantic Scholar
- arXiv
- Europe PMC
- Unpaywall

Release profile:

- probes the same six providers
- requires OpenAlex and Crossref success
- allows optional providers to degrade only for structured transient
  rate-limit, network, TLS, timeout, or HTTP 5xx failures
- rejects missing contact data, auth, parser, validation, and internal errors
- never accepts `--allow-skipped`

Each provider probe must:

- use its real parser rather than only checking response bytes
- issue the smallest practical request
- use ProviderRuntime and the shared request ledger
- report success, empty, skipped, or structured failure
- record response shape evidence without storing credentials or raw sensitive
  URLs
- distinguish provider unavailability from an empty literature result

Live acceptance is manual or scheduled. Pull-request CI runs deterministic
failure injection instead.

## 7. Runtime Resilience

### 7.1 Real crash and restart

Acceptance must terminate real subprocesses rather than only raising mocked
exceptions:

- crash the CLI immediately after a completed stage record
- restart and resume the same run
- verify preserved run_id and reusable stages
- kill an MCP process after a background job is queued/running
- load the job from a new process and report interrupted
- verify that no completed job is downgraded by stale artifacts

### 7.2 SQLite migration

Introduce a real second migration and a fixture representing the previously
shipped schema. Acceptance must verify:

- version 1 database upgrades to the current version
- existing sessions, iterations, jobs, and outcomes remain readable
- the migration is idempotent
- a deliberately broken migration rolls back completely
- exported state includes the current schema version and new tables

The second migration adds an append-only runtime event ledger used to audit job,
run, and recovery transitions without replacing the current jobs or outcomes
tables.

### 7.3 Soak

The soak runner repeatedly exercises:

- state-store connections and WAL behavior
- atomic artifact replacement
- background job persistence
- provider cooldown reads and writes
- canonical projection and export
- short offline pipeline runs
- resume and merge iterations

Profiles:

- quick: suitable for required CI
- standard: several minutes, manual
- long: duration-based Windows release soak

The report includes iteration counts, failures, lock retries, database
integrity, artifact hashes, and elapsed time.

## 8. Gradual Decomposition

### 8.1 Runner

run_lit_search.py remains the compatibility CLI adapter, but moves lifecycle
work into focused modules:

- runtime/run_lifecycle.py: session, iteration, state-store, and RunContext
  initialization
- engine/run_finalizer.py: coverage, canonical projection, exports, outcome,
  reports, and artifact finalization

The runner retains argument parsing and stage ordering. It must not reimplement
the extracted logic.

Target: reduce run_lit_search.py below 2400 lines without changing public CLI
behavior.

### 8.2 MCP

server.py remains the stdio entry point, but moves responsibilities into:

- sources/mcp/protocol.py: JSON-RPC and MCP response construction
- sources/mcp/job_registry.py: in-memory and persisted job state
- sources/mcp/workflow_tools.py: high-level plan/start/get/read/export helpers

Legacy/advanced tools remain available. The default nine-tool surface and all
schemas remain unchanged.

Target: reduce server.py below 1500 lines while preserving direct imports used
by existing tests and compatibility callers.

## 9. Documentation And Distribution

Update:

- README.md and README.en.md
- SKILL.md
- AGENTS.md
- CLAUDE.md
- references/agent-operating-contract.md
- references/mcp-surface.md
- references/runtime-recovery.md
- references/user-guide.md and English counterpart
- test/README.md
- release checklist
- changelog

Documentation must state:

- Windows primary, macOS secondary
- Linux unsupported
- Docker is not release acceptance
- Codex and Claude Code are primary clients
- MCP is optional but first-class
- no secrets are committed in example configuration

## 10. Verification Matrix

Required deterministic checks:

- compileall
- Ruff
- mypy
- full unittest suite
- offline Agent scenarios
- known-issue scenarios
- architecture acceptance
- MCP self-test
- Codex adapter acceptance
- Claude Code adapter acceptance
- crash/restart acceptance
- migration acceptance
- quick soak
- git diff whitespace check

Manual or scheduled checks:

- Windows provider release gate
- macOS provider release gate
- real Codex CLI MCP acceptance on Windows
- real Claude Code MCP acceptance on Windows
- Windows standard/long soak
- macOS quick/standard soak
- RIS import in Zotero
- BibTeX import in JabRef or Zotero

## 11. Definition Of Done

This plan is complete only when:

1. Required CI runs natively on Windows and macOS.
2. Linux is no longer described as a supported release target.
3. Local CI has one shared command implementation.
4. AGENTS.md and CLAUDE.md match the live MCP and artifact contracts.
5. Codex and Claude deterministic acceptance are green.
6. Every registered scholarly provider has a controlled live acceptance path.
7. Real CLI and MCP process termination recover honestly.
8. A shipped SQLite schema upgrades through a real migration.
9. Quick soak is required and long soak is available.
10. run_lit_search.py is below 2400 lines.
11. MCP server.py is below 1500 lines.
12. Existing CLI, default MCP, advanced MCP, resume, merge, and artifacts remain
    compatible.
13. Documentation, tests, and changelog describe the same product boundary.

## 12. Explicit Non-Goals

- Linux release support
- Docker as a substitute for Windows/macOS testing
- cloud job service
- multi-user database
- paywall bypass
- PDF or supplementary-information parsing in core
- automatic scientific inclusion decisions
- provider-rate-limit evasion
- rewriting the pipeline from scratch
