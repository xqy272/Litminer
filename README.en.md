# Litminer

[中文](README.md) | English

Litminer is a local research-information skill for AI agents. It gives Claude Code, Codex, and other agentic tools a structured literature-discovery and evidence-preparation layer instead of relying only on generic WebSearch/WebFetch.

Litminer is not a review writer, a domain knowledge base, or a PDF reader. It discovers, verifies, deduplicates, annotates, summarizes, and queues literature information so the Agent can spend its reasoning budget on the scientific judgement.

## Connect The Skill First

The repository root contains `SKILL.md`, so the repository directory itself is the skill folder. The minimum installation is cloning this repository into a skills directory that your Agent scans. That clone writes only the repository files into that directory; it does not run `pip install` and does not modify the global Python environment. Python 3.10+ is only needed when the Agent actually runs Litminer scripts or the optional MCP server.

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

Codex also provides `$skill-installer` for downloading skills from other repositories. This README documents direct `git clone` because it is explicit, auditable, and easy to update. `[[skills.config]]` is mainly for enable/disable overrides on discovered skills, not a required Litminer installation step. If Litminer later needs one-click installation, bundled MCP config, or richer distribution metadata, package it as a Codex plugin.

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
args = ["C:/Users/your-name/.agents/skills/litminer/sources/mcp/server.py"]
cwd = "C:/Users/your-name/.agents/skills/litminer"
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
- Codex Agent Skills: <https://developers.openai.com/codex/skills>
- Codex config reference: <https://developers.openai.com/codex/config-reference#configtoml>
- Codex plugins: <https://developers.openai.com/codex/plugins/build>

## Python Environment And Isolation

Installing or discovering the skill does not require `pip install`. The clone commands above only place Litminer repository files in the skills directory; they do not write into global `site-packages` or change the user's Python configuration.

Running scripts or the MCP server requires Python 3.10+. Litminer has no runtime dependencies outside the Python standard library, so you can run these directly from the Litminer directory:

```bash
python engine/run_lit_search.py --help
python sources/mcp/test_server.py
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

The current recommendation is to clone the full repository instead of asking users to copy a subset of files manually. Litminer needs `SKILL.md`, `engine/`, `sources/`, `config/`, and related files to work reliably; the full repository also keeps source review, tests, and updates straightforward. If a cleaner user-side installation is needed later, publish a dedicated release package or plugin rather than relying on manual file selection.

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
python -m unittest discover -s test -p "test_*.py"
python sources/mcp/test_server.py
```
