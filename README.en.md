# Litminer

[中文](README.md) | English

Litminer is a local research-information skill for AI agents. It gives Claude Code, Codex, and other agentic tools a structured literature-discovery and evidence-preparation layer instead of relying only on generic WebSearch/WebFetch.

Litminer is not a review writer, a domain knowledge base, or a PDF reader. It discovers, verifies, deduplicates, annotates, summarizes, and queues literature information so the Agent can spend its reasoning budget on the scientific judgement.

## Connect The Skill First

The repository root contains `SKILL.md`, so the repository directory itself is the skill folder.

### Claude Code

Install Litminer as a user-level skill:

```bash
mkdir -p ~/.claude/skills
git clone <this-repo-url> ~/.claude/skills/litminer
```

Or install it into a target project:

```bash
mkdir -p .claude/skills
git clone <this-repo-url> .claude/skills/litminer
```

Restart or reload Claude Code skills, then invoke it naturally:

```text
Use Litminer to find recent papers on enzyme stability external validation and build a publisher queue.
```

### Codex

Register the folder that contains `SKILL.md` in `~/.codex/config.toml` or a trusted project-level `.codex/config.toml`:

```toml
[[skills.config]]
path = "D:/Projects/Litminer"
enabled = true
```

Restart or reload Codex skills after editing the config.

### Optional MCP Tools

The skill tells the Agent when to use Litminer. MCP exposes Litminer operations as callable tools. MCP is optional.

Verify the local server:

```bash
python sources/mcp/test_server.py
```

Codex MCP example:

```toml
[mcp_servers.litminer]
command = "python"
args = ["D:/Projects/Litminer/sources/mcp/server.py"]
cwd = "D:/Projects/Litminer"
env_vars = [
  "OPENALEX_API_KEY",
  "OPENALEX_MAILTO",
  "CROSSREF_MAILTO",
  "UNPAYWALL_EMAIL",
  "LITMINER_CONTACT_EMAIL",
]
```

Official references:

- Claude Code Skills: <https://docs.claude.com/en/docs/claude-code/skills>
- Claude Code MCP: <https://docs.claude.com/en/docs/claude-code/mcp>
- Codex config reference: <https://developers.openai.com/codex/config-reference#configtoml>

## Install

Litminer uses Python 3.10+ and has no runtime dependencies outside the standard library.

```bash
python -m pip install -e .
```

Development tools:

```bash
python -m pip install -e ".[dev]"
```

Optional API contact environment variables:

- `OPENALEX_MAILTO` or `LITMINER_CONTACT_EMAIL`
- `CROSSREF_MAILTO` or `LITMINER_CONTACT_EMAIL`
- `UNPAYWALL_EMAIL` or `LITMINER_CONTACT_EMAIL`
- `OPENALEX_API_KEY` when available

## Quick Start

```bash
python engine/run_lit_search.py \
  --query "machine learning enzyme stability external validation" \
  --year-from 2026 \
  --required-concept "validation=external validation|prospective validation" \
  --optional-concept "benchmark=benchmark|dataset" \
  --negative-concept "review=review article|survey" \
  --config config/default.json \
  --output-dir check/litminer_run
```

The query and concepts are examples. In normal use, the Agent derives them from the user's request.

## Workflow

`engine/run_lit_search.py` performs:

1. API discovery, OpenAlex by default.
2. DOI/title deduplication with complementary-field merging.
3. Crossref verification and title-based DOI recovery.
4. Runtime semantic triage.
5. Unpaywall OA/access-link annotation.
6. Optional verified journal metric annotation.
7. Publisher-page evidence queue generation.
8. Feasibility and processing reports.

## Main Outputs

| File | Purpose |
|------|---------|
| `api_candidates.csv` | Unified API discovery output. |
| `api_discovery_trace.csv` | Query/source trace. |
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

## Boundaries

Litminer does not:

- answer current literature, DOI, journal metric, or publisher-page evidence questions from memory
- make final scientific inclusion decisions
- parse PDFs, OCR, supplementary information, or tables
- bypass paywalls
- guess unverifiable DOI, IF/JCR metrics, or article-level facts

Agent-facing rules and operating details live in [CLAUDE.md](CLAUDE.md) and [SKILL.md](SKILL.md).

## Verification

```bash
python -m compileall engine sources -q
python -m ruff check engine sources test
python -m mypy engine sources
python -m unittest test.test_litminer_core
python sources/mcp/test_server.py
```
