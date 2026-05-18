# Litminer User Guide

This guide holds details that do not belong in the README entry page:
installation locations, updates, Python setup, configuration, MCP, run modes,
and troubleshooting.

## Distribution

Litminer is distributed as a full repository skill bundle. Clone the repository
instead of copying individual files.

Key files and folders:

- `SKILL.md`
- `CLAUDE.md`
- `litminer/`
- `config/`
- `references/`

`pip install -e .` is for development and console scripts. A wheel install is
not a full Agent skill install because Agents still need to discover `SKILL.md`.

## Install Locations

Codex user-level:

```bash
mkdir -p ~/.agents/skills
git clone https://github.com/xqy272/Litminer.git ~/.agents/skills/litminer
```

Claude Code user-level:

```bash
mkdir -p ~/.claude/skills
git clone https://github.com/xqy272/Litminer.git ~/.claude/skills/litminer
```

Project-level install:

```bash
mkdir -p .agents/skills
git clone https://github.com/xqy272/Litminer.git .agents/skills/litminer
```

Restart or reload Agent skills after installing.

## Version And Updates

Pin a release:

```bash
git clone --branch <release-tag> --depth 1 https://github.com/xqy272/Litminer.git ~/.agents/skills/litminer
```

Update:

```bash
cd ~/.agents/skills/litminer
git pull --ff-only
python -m litminer.engine.bootstrap
python -m litminer.engine.offline_smoke
```

Read [CHANGELOG.md](../CHANGELOG.md) before crossing versions.

## Workspace

Set a workspace for user inputs and runtime outputs:

```bash
export LITMINER_WORKSPACE_ROOT="/path/to/project"
export LITMINER_CONTACT_EMAIL="you@example.org"
```

Ignore runtime outputs:

```gitignore
.litminer/
```

## API Contact

| Variable | Purpose |
|----------|---------|
| `LITMINER_CONTACT_EMAIL` | General contact fallback. |
| `OPENALEX_MAILTO` | OpenAlex contact email. |
| `CROSSREF_MAILTO` | Crossref contact email. |
| `UNPAYWALL_EMAIL` | Unpaywall query email. |
| `OPENALEX_API_KEY` | Optional OpenAlex key. |
| `SEMANTIC_SCHOLAR_API_KEY` / `S2_API_KEY` | Optional Semantic Scholar key. |

## Run Modes

| Mode | Use |
|------|-----|
| `fast` | First pass; validates paths and concepts. |
| `balanced` | Default verification-oriented run. |
| `expanded` / `full` | Higher recall with more provider work. |

Useful controls:

- `--resume`
- `--stop-after-stage STAGE`
- `--time-budget-seconds N`
- `--max-crossref-rows N`
- `--max-unpaywall-rows N`
- `--no-cache`

## MCP

Self-test:

```bash
python -m litminer.sources.mcp.test_server
```

Codex example:

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

## Troubleshooting

- Inspect `api_discovery_trace.csv` for provider failures.
- If `skipped_cached_provider_failure` appears, wait for TTL or rerun with
  `--no-cache` after fixing the environment.
- If Unpaywall reports `skipped_missing_email`, set `UNPAYWALL_EMAIL` or
  `LITMINER_CONTACT_EMAIL`.
- If MCP rejects paths, keep inputs, outputs, and config under
  `LITMINER_WORKSPACE_ROOT`.
