# Litminer Release Checklist

Litminer uses a lightweight open-source release process. A release is a Git tag
on a tested repository checkout.

## Before Tagging

- Release from native Windows first and native macOS second. Do not substitute
  Linux or Docker evidence for either platform.
- When no physical Mac is available, successful GitHub Actions
  `test-macos` plus release-appropriate `live-macos`/`soak-macos`
  jobs on `macos-latest` are the native macOS evidence.
- Confirm Codex and Claude Code adapters still list the same default nine MCP
  tools and artifact order.
- Update `CHANGELOG.md`.
- Confirm `pyproject.toml` version if the release changes package metadata.
- Check README examples for stale commands or nonexistent tags.
- Run:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run_ci.ps1 full
```

On macOS:

```bash
sh scripts/run_ci.sh full
```

The full profile includes compile, unit tests, MCP, offline/known-issue Agent
scenarios, deterministic Codex/Claude acceptance, architecture probes, runtime
resilience, and quick soak.

- If dev tools are installed, also run:

```bash
python -m ruff check litminer test scripts
python -m mypy litminer scripts
```

- Run controlled live provider acceptance on native Windows:

```powershell
python -m litminer.engine.provider_acceptance --profile full --output-dir .litminer/acceptance/providers --allow-skipped
```

- Run at least the core provider profile on native macOS.
- Run standard soak on Windows and quick or standard soak on macOS.
- Optionally run installed real clients:

```bash
python -m litminer.engine.agent_client_acceptance --agent all --real --allow-missing-client --output-dir .litminer/acceptance/real-agents
```

- Import one generated RIS and BibTeX file into Zotero and/or JabRef before a
  release that changes exporters.

## Tag

```bash
git tag vX.Y.Z
git push origin vX.Y.Z
```

## After Tagging

- Create a GitHub release from the tag.
- Paste the relevant `CHANGELOG.md` section.
- Mention that the install method is Git clone:

```bash
git clone --branch vX.Y.Z --depth 1 https://github.com/xqy272/Litminer.git ~/.agents/skills/litminer
```

- Do not describe PyPI or wheel installation as the full Agent skill install
  unless the skill asset distribution model changes.
