# Litminer

[中文](README.md) | English

Litminer is a local research-information skill for AI agents. It gives Claude Code, Codex, and other agentic tools a structured literature-discovery and evidence-preparation layer instead of relying only on generic WebSearch/WebFetch.

Litminer is not a review writer, a domain knowledge base, or a PDF reader. It discovers, verifies, deduplicates, annotates, summarizes, and queues literature information so the Agent can spend its reasoning budget on the scientific judgement.

2026-05 update: the workflow now writes `query_plan.json`,
`field_provenance.json`, and `publisher_adapters.json`, supports
`--time-budget-seconds`, `--stop-after-stage`, `--max-crossref-rows`, and
`--max-unpaywall-rows`, and exposes background MCP jobs through
`litminer_start_run` / `litminer_run_status`. On a new or Windows-heavy
environment, start with `python -m litminer.engine.bootstrap`.

Distribution is intentionally simple: the full repository is the skill bundle.
Clone it from GitHub, use release tags for stable versions, and check
[CHANGELOG.md](CHANGELOG.md) before upgrading. `pip install -e .` is only an
optional developer and console-script path.

## Connect The Skill First

The repository root contains `SKILL.md`, so the repository directory itself is the skill folder. The minimum installation is cloning this repository into a skills directory that your Agent scans. That clone writes only the repository files into that directory; it does not run `pip install` and does not modify the global Python environment. Python 3.10+ is only needed when the Agent actually runs Litminer scripts or the optional MCP server.

### Version Choice And Updates

To follow the latest development version, clone the default branch:

```bash
git clone https://github.com/xqy272/Litminer.git ~/.agents/skills/litminer
```

For stable use, prefer GitHub release tags. After `v0.1.0` is published, pin it
like this:

```bash
git clone --branch v0.1.0 --depth 1 https://github.com/xqy272/Litminer.git ~/.agents/skills/litminer
```

To update an existing install, run this from the Litminer directory:

```bash
git pull --ff-only
python -m litminer.engine.bootstrap
python -m litminer.engine.offline_smoke
```

Read [CHANGELOG.md](CHANGELOG.md) before crossing versions. If Litminer is
installed under a project-level `.agents/skills/` or `.claude/skills/`
directory, keep that folder as a normal Git checkout instead of copying files
by hand.

### Claude Code

Install Litminer as a user-level skill:

```bash
# macOS / Linux
mkdir -p ~/.claude/skills
git clone https://github.com/xqy272/Litminer.git ~/.claude/skills/litminer
```

```powershell
# Windows PowerShell
New-Item -ItemType Directory -Force "$HOME\.claude\skills" | Out-Null
git clone https://github.com/xqy272/Litminer.git "$HOME\.claude\skills\litminer"
```

Or install it into a target project:

```bash
mkdir -p .claude/skills
git clone https://github.com/xqy272/Litminer.git .claude/skills/litminer
```

Restart or reload Claude Code skills, then invoke it naturally:

```text
Use Litminer to find recent papers on enzyme stability external validation and build a publisher queue.
```

### Codex

Codex scans user-level `$HOME/.agents/skills` and repository-level `.agents/skills`. Clone Litminer into one of those locations so Codex can discover the folder containing `SKILL.md`.

User-level install:

```bash
# macOS / Linux
mkdir -p ~/.agents/skills
git clone https://github.com/xqy272/Litminer.git ~/.agents/skills/litminer
```

```powershell
# Windows PowerShell
New-Item -ItemType Directory -Force "$HOME\.agents\skills" | Out-Null
git clone https://github.com/xqy272/Litminer.git "$HOME\.agents\skills\litminer"
```

Project-level install:

```bash
# Run at the target project root
mkdir -p .agents/skills
git clone https://github.com/xqy272/Litminer.git .agents/skills/litminer
```

Restart or reload Codex skills after cloning, then invoke it naturally:

```text
Use Litminer to find recent papers, verify DOI metadata, annotate OA links, and build a publisher queue.
```

Codex also provides `$skill-installer` for downloading skills from other repositories. This README documents direct `git clone` because it is explicit, auditable, and easy to update. `[[skills.config]]` is mainly for enable/disable overrides on discovered skills, not a required Litminer installation step.

### Post-Install Configuration And Check

For skill-only use, no extra configuration file is usually required. Confirm that one of these paths exists:

```text
~/.claude/skills/litminer/SKILL.md
~/.agents/skills/litminer/SKILL.md
target-project/.claude/skills/litminer/SKILL.md
target-project/.agents/skills/litminer/SKILL.md
```

Then restart or reload Claude Code / Codex skills and ask the Agent to use Litminer in natural language.

After installation, run a local check and offline smoke test from the Litminer directory:

```bash
python -m litminer.engine.bootstrap
python -m litminer.engine.doctor
python -m litminer.engine.offline_smoke
```

These commands do not need API keys. `offline_smoke` does not access the network; it uses an embedded fixture and writes `processing_report.md` and `publisher_queue.csv` under `.litminer/runs/offline_smoke/` in the active workspace.

If Codex has discovered the skill and you want an explicit enable/disable override, add this to user-level `~/.codex/config.toml` or project-level `.codex/config.toml`:

```toml
[[skills.config]]
path = "C:/Users/your-name/.agents/skills/litminer"
enabled = true
```

This block is not required for normal installation. Use it only when debugging skill enablement or applying a team/project override.

### Optional MCP Tools

The skill tells the Agent when to use Litminer. MCP exposes Litminer operations as callable tools. MCP is optional.

Verify the local server:

```bash
python -m litminer.sources.mcp.test_server
```

MCP uses two path roots:

- Skill install directory: the Litminer code location, such as `~/.claude/skills/litminer` or `~/.agents/skills/litminer`.
- User workspace: where input CSVs, reports, and default `.litminer/` runtime outputs live. Set it with `LITMINER_WORKSPACE_ROOT`; when unset, the MCP process `cwd` is used. MCP rejects paths outside this workspace.

Codex MCP example:

The config file is usually user-level `~/.codex/config.toml` or project-level `.codex/config.toml`. Replace the paths below with your skill install directory and user workspace:

```toml
[mcp_servers.litminer]
command = "python"
args = ["C:/Users/your-name/.agents/skills/litminer/litminer/sources/mcp/server.py"]
cwd = "D:/path/to/your/project"
env = { LITMINER_WORKSPACE_ROOT = "D:/path/to/your/project", LITMINER_MCP_TOOL_PROFILE = "workflow" }
env_vars = [
  "OPENALEX_API_KEY",
  "OPENALEX_MAILTO",
  "CROSSREF_MAILTO",
  "UNPAYWALL_EMAIL",
  "LITMINER_CONTACT_EMAIL",
]
```

If you use a virtual environment, you can set `command` to that Python executable, for example `C:/Users/your-name/.agents/skills/litminer/.venv/Scripts/python.exe` on Windows.

Template files are available:

- Codex MCP: [config/mcp.codex.example.toml](config/mcp.codex.example.toml)
- Claude Code MCP: [config/mcp.claude.example.json](config/mcp.claude.example.json)

MCP defaults to `LITMINER_MCP_TOOL_PROFILE=workflow`, so `tools/list` exposes
only the workflow tools for environment checks, long runs, resume, discovery,
triage, queueing, reports, summaries, and paginated CSV reading. Set
`LITMINER_MCP_TOOL_PROFILE=all` only when an Agent needs lower-level source,
stage, or debug tools. See [references/mcp-surface.md](references/mcp-surface.md).

Official references:

- Claude Code Skills: <https://docs.claude.com/en/docs/claude-code/skills>
- Claude Code MCP: <https://docs.claude.com/en/docs/claude-code/mcp>
- Codex Agent Skills: <https://developers.openai.com/codex/skills>
- Codex config reference: <https://developers.openai.com/codex/config-reference#configtoml>
- Codex plugins: <https://developers.openai.com/codex/plugins/build>

## File Locations And Boundaries

Litminer separates the install directory from the runtime workspace:

- The install directory contains code and documentation only. `git clone https://github.com/xqy272/Litminer.git .../litminer` writes only repository files into that `litminer` directory; it does not create search outputs, virtual environments, or global Python packages.
- CLI defaults write runtime outputs under `.litminer/` in the active workspace, such as `.litminer/runs/litminer_run/`, `.litminer/runs/offline_smoke/`, and `.litminer/screenshots/`.
- If `LITMINER_WORKSPACE_ROOT` is set, CLI default relative outputs resolve under that directory. If it is unset, they resolve under the process `cwd`.
- In MCP mode, all file arguments must stay under `LITMINER_WORKSPACE_ROOT`; if unset, they must stay under the MCP process `cwd`. Path escapes are rejected.
- Explicit `--output-dir`, `--output`, MCP tool arguments, and absolute paths are treated as explicit user choices and are outside the default-location guarantee.

Recommended setup: point MCP `LITMINER_WORKSPACE_ROOT` at the target project
root and add the target project's `.litminer/` to `.gitignore`:

```gitignore
.litminer/
```

Avoid writing search outputs into the skill install directory by default,
because multi-project outputs should not be mixed with code.

## Python Environment And Isolation

Installing or discovering the skill does not require `pip install`. The clone commands above only place Litminer repository files in the skills directory; they do not write into global `site-packages` or change the user's Python configuration.

Running scripts or the MCP server requires Python 3.10+. Litminer has no runtime dependencies outside the Python standard library, so you can run these directly from the Litminer directory:

```bash
python -m litminer.engine.run_lit_search --help
python -m litminer.sources.mcp.test_server
```

Use `pip install` only when you want console scripts or development tools such as Ruff and mypy. To avoid polluting the user's machine, create a local virtual environment inside the Litminer clone:

```bash
# macOS / Linux
cd ~/.claude/skills/litminer  # or ~/.agents/skills/litminer
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

```powershell
# Windows PowerShell
cd "$HOME\.claude\skills\litminer"  # or "$HOME\.agents\skills\litminer"
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
```

Install development and verification tools only inside an activated `.venv`:

```bash
python -m pip install -e ".[dev]"
```

`.venv/` is ignored by Git. If you configure MCP, you may point the MCP `command` at the virtualenv Python for stable execution, for example `.venv/Scripts/python.exe` on Windows or `.venv/bin/python` on macOS/Linux.

The current recommendation is to clone the full repository instead of asking users to copy a subset of files manually. Litminer needs `SKILL.md`, `litminer/`, `config/`, and related files to work reliably; the full repository also keeps source review, tests, and updates straightforward.

The current distribution boundary is clone-as-skill first. `pip install -e .` is appropriate for local development and console scripts, but a regular wheel install is not yet a complete skill installation because Agent discovery still depends on `SKILL.md`, config templates, and documentation folders.

## API, Source, And Publisher Configuration

Litminer has two configuration layers:

- Environment variables: API contact emails and optional API keys. Set them in your shell, Agent/MCP config, or system environment.
- Runtime config JSON: source switches, limits, output paths, and publisher-probe policy. Save it per project and pass it with `--config`.

### API Contact And Keys

Most sources do not require API keys. OpenAlex, Crossref, and Unpaywall recommend or require a contact email. Litminer first reads source-specific variables, then falls back to `LITMINER_CONTACT_EMAIL`.

| Variable | Purpose | Required |
|----------|---------|----------|
| `LITMINER_CONTACT_EMAIL` | General contact email fallback for OpenAlex, Crossref, and Unpaywall. | Recommended |
| `OPENALEX_MAILTO` | OpenAlex polite-pool contact email. | Recommended |
| `CROSSREF_MAILTO` | Crossref User-Agent contact email. | Recommended |
| `UNPAYWALL_EMAIL` | Unpaywall query email. Without it, the Unpaywall stage is marked skipped. | Required for Unpaywall |
| `OPENALEX_API_KEY` | OpenAlex API key. Usually not needed unless your access policy requires it. | Optional |

You can copy variable names from [.env.example](.env.example), but do not commit real emails or keys.

macOS / Linux:

```bash
export LITMINER_CONTACT_EMAIL="you@example.org"
export UNPAYWALL_EMAIL="you@example.org"
# Optional
export OPENALEX_API_KEY="your-openalex-api-key"
```

Windows PowerShell:

```powershell
$env:LITMINER_CONTACT_EMAIL = "you@example.org"
$env:UNPAYWALL_EMAIL = "you@example.org"
# Optional
$env:OPENALEX_API_KEY = "your-openalex-api-key"
```

When using MCP, you can also put these in the MCP server `env` or `env_vars`. Use `env` for fixed values and `env_vars` to forward variable names from the local machine.

### Sources, Limits, And Publisher Policy

The default runtime config is [config/default.json](config/default.json). Do not put personal emails, API keys, or topic-specific search terms in that file. For project-specific settings, create a workspace file such as `.litminer/config.json`:

```json
{
  "channels": {
    "openalex": true,
    "semantic_scholar": true,
    "arxiv": true,
    "europe_pmc": false,
    "crossref": true,
    "unpaywall": true,
    "publisher_probe": true
  },
  "limits": {
    "max_results_per_query": 80,
    "semantic_query_limit": 3,
    "semantic_max_results": 50,
    "publisher_probe_limit": 20,
    "publisher_probe_sleep": 1.0,
    "strict_discovery": false,
    "parallel_providers": false,
    "provider_workers": null,
    "provider_failure_threshold": 2,
    "provider_rate_limit_cooldown_seconds": 60.0,
    "unpaywall_sleep": 0.1
  },
  "outputs": {
    "default_output_dir": ".litminer/runs/litminer_run",
    "screenshot_root": ".litminer/screenshots"
  },
  "cache": {
    "enabled": true,
    "cache_dir": ".litminer/cache",
    "ttl_days": 30.0,
    "provider_failure_ttl_seconds": 300.0
  },
  "evidence": {
    "require_doi_for_queue": true,
    "queue_priorities": "high,medium,needs_review",
    "include_metadata_blocked": false,
    "queue_strict_only": true
  }
}
```

You can also copy [config/example.user.json](config/example.user.json) and edit it per project.

Pass it at runtime:

```bash
python -m litminer.engine.run_lit_search \
  --query "your literature query" \
  --year-from 2026 \
  --config .litminer/config.json
```

When calling through MCP, set the tool `config` argument to a JSON path inside the workspace. The path must stay under `LITMINER_WORKSPACE_ROOT`; do not point it to an arbitrary file outside the workspace or the skill install directory.

Check a config file:

```bash
python -m litminer.engine.doctor --config .litminer/config.json
```

For one-off runs, you can skip JSON and override behavior with CLI flags:

```bash
python -m litminer.engine.run_lit_search \
  --query "your literature query" \
  --year-from 2026 \
  --discovery-sources openalex,semantic_scholar,arxiv,europe_pmc \
  --max-results-per-query 80 \
  --openalex-work-types article \
  --enrich-unpaywall \
  --probe-publishers \
  --probe-limit 20 \
  --probe-sleep 1.0 \
  --fields-needed "dataset,external validation,benchmark"
```

Publisher-related settings:

- `--fields-needed` / `page_required_fields`: fields the Agent should inspect on publisher pages, such as dataset, validation method, external benchmark, or supplementary links.
- `queue_priorities` / `--queue-priorities`: triage priorities included in `publisher_queue.csv`.
- `require_doi_for_queue` / `--allow-missing-doi`: by default, a DOI is required for publisher queueing; use `--allow-missing-doi` only when explicitly needed.
- `strict_discovery` / `--strict-discovery`: fail the workflow when API provider errors make the candidate set unreliable instead of only producing an empty-result report.
- `parallel_providers` / `--parallel-providers`: optionally run different API providers for the same query concurrently. This uses stdlib threads and keeps calls to the same provider serial across queries.
- `openalex_work_types` / `--openalex-work-types`: controls OpenAlex work type filtering. The default is `article`; pass `all` to disable the type filter.
- `queue_strict_only` / `--queue-strict-only` / `--queue-all-metric-statuses`: with `--min-if`, Litminer defaults to queueing only metric-pass rows. Use queue-all mode when you want annotation without hard filtering.
- `publisher_probe` / `--probe-publishers`: lightweight reachability and PDF/SI-link probing only. It does not parse PDFs or bypass paywalls.
- `publisher_probe_limit` / `--probe-limit` and `publisher_probe_sleep` / `--probe-sleep`: control probe count and delay to avoid hitting publisher pages too aggressively.
- `cache.enabled` / `--no-cache`: workspace-local cache for positive Crossref/Unpaywall DOI metadata and short-lived transient provider failures. Failed, not-found, mismatch, auth, and generic error rows are not treated as long-lived evidence. Cache is operational acceleration, not evidence.

## Quick Start

```bash
python -m litminer.engine.run_lit_search \
  --query "machine learning enzyme stability external validation" \
  --year-from 2026 \
  --required-concept "validation=external validation|prospective validation" \
  --optional-concept "benchmark=benchmark|dataset" \
  --negative-concept "review=review article|survey" \
  --config config/default.json \
  --output-dir .litminer/runs/litminer_run
```

The query and concepts are examples. In normal use, the Agent derives them from the user's request.

`re:` regex concepts are disabled by default. Enable them only for reviewed
trusted profiles with `--enable-regex-concepts` or the MCP
`enable_regex_concepts` parameter.

## Workflow

`litminer.engine.run_lit_search` performs:

1. Runtime query/source/concept planning with advisory source strategy.
2. API discovery, OpenAlex by default.
3. DOI/title deduplication with complementary-field merging.
4. Crossref verification and title-based DOI recovery.
5. Runtime semantic triage.
6. Unpaywall OA/access-link annotation.
7. Optional verified journal metric annotation.
8. Publisher-page evidence queue generation.
9. Field-level provenance, feasibility, processing reports, and artifact index.

## Main Outputs

| File | Purpose |
|------|---------|
| `api_candidates.csv` | Unified API discovery output. |
| `api_discovery_trace.csv` | Query/source trace with status class, retry-after hints, and next-action guidance. |
| `api_discovery_report.md` | Provider status report. |
| `deduped_candidates.csv` | Deduplicated and merged candidates. |
| `verified_candidates.csv` | Crossref verification output with explicit status fields. |
| `triaged_candidates.csv` | Semantic tags, priorities, and metadata flags. |
| `selected_candidates.csv` | Priority-selected candidates. |
| `oa_annotated_candidates.csv` | Unpaywall OA/access hints. |
| `metrics_annotated_candidates.csv` | Verified journal metric annotation. |
| `publisher_queue.csv` | Publisher-page evidence queue. |
| `publisher_queue_probed.csv` | Optional access/PDF/SI probe output. |
| `feasibility_report.md` | Counts and blocking reasons. |
| `processing_report.md` | Compact source, metadata, triage, access, and queue summary. |
| `agent_summary.json` | Machine-readable trust tiers, stage status, primary artifacts, source strategy, and next actions. |
| `artifacts_index.json` | Primary/supporting/debug artifact map for Agent navigation. |
| `query_plan.json` | Agent-derived queries, sources, concepts, run controls, and advisory source strategy. |
| `field_provenance.json` | Field-level source and trust map for queued or probed rows. |
| `publisher_adapters.json` | Built-in/external publisher inspection adapter boundaries. |
| `run_manifest.json` | Stage status, resume metadata, row counts, fingerprints, and run signature. |

`query_plan.json` includes `source_strategy` hints such as missing recommended
sources, single-query recall risk, weak triage concepts, and metadata-lag risk.
These hints do not automatically broaden retrieval; the Agent decides whether
the user's task justifies another source pass.

## Boundaries

Litminer does not:

- answer current literature, DOI, journal metric, or publisher-page evidence questions from memory
- make final scientific inclusion decisions
- parse PDFs, OCR, supplementary information, or tables
- bypass paywalls
- guess unverifiable DOI, IF/JCR metrics, or article-level facts

Agent-facing rules and operating details live in [CLAUDE.md](CLAUDE.md) and [SKILL.md](SKILL.md).

## Beta Limits, Failures, And Retries

For the first beta, run `python -m litminer.engine.doctor` and `python -m litminer.engine.offline_smoke` before live searches. If either fails, fix local Python, paths, or config before using network-backed sources.

Use explicit run controls for long or uncertain tasks:

- `--time-budget-seconds N`: stop cleanly at a stage boundary once the budget is exhausted.
- `--stop-after-stage STAGE`: write partial reports after a named stage.
- `--max-crossref-rows N` / `--max-unpaywall-rows N`: mark overflow rows as `skipped_budget`.
- `--max-publisher-probe-rows N`: cap publisher probing when `--probe-limit` is not set.
- `--no-cache`: bypass local metadata/failure cache after fixing network, proxy, API key, or contact-email setup.

Common cases:

- API request failure or rate limit: inspect `api_discovery_trace.csv` fields `status_class`, `http_status`, `transient_error`, and `next_action`. `rate_limited` usually means resume later or reduce request volume; `network` / `auth` usually means Agent network permission, proxy/certificate, API key, or contact-email setup. Do not treat an empty failed source as final evidence.
- `api_discovery_trace.csv` reports `skipped_cached_provider_failure`: a recent rate-limit, network, or explicitly transient provider failure was reused to avoid immediately repeating a failing call. Wait for the TTL or rerun with `--no-cache` after the environment is fixed. Auth and generic errors are not cached by default.
- Unpaywall reports `skipped_missing_email`: set `UNPAYWALL_EMAIL` or `LITMINER_CONTACT_EMAIL`, then rerun.
- Crossref reports `lookup_failed` or `mismatch`: keep the blocking status; do not fabricate DOI values. Broaden discovery or verify manually when needed.
- Publisher pages are unreachable: lower `--probe-limit`, increase `--probe-sleep`, or skip probing and inspect `publisher_queue.csv` manually.
- Too few candidates: add queries, enable Semantic Scholar/arXiv/Europe PMC, or relax required concepts. Do not invent papers to reach a target count.
- MCP path errors: ensure inputs, outputs, and `config` all live under `LITMINER_WORKSPACE_ROOT`.

## Verification

```bash
python -m compileall litminer -q
python -m ruff check litminer test
python -m mypy litminer
python -m unittest discover -s test -p "test_*.py"
python -m litminer.sources.mcp.test_server
python -m litminer.engine.bootstrap --output-dir .litminer/bootstrap
python -m litminer.engine.doctor
python -m litminer.engine.offline_smoke
python -m litminer.engine.journal_metrics --validate --metrics references/journal_metrics_seed.csv
```

## License

This project is licensed under the [MIT License](LICENSE).
