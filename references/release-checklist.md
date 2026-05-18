# Litminer Release Checklist

Litminer uses a lightweight open-source release process. A release is a Git tag
on a tested repository checkout.

## Before Tagging

- Update `CHANGELOG.md`.
- Confirm `pyproject.toml` version if the release changes package metadata.
- Check README examples for stale commands or nonexistent tags.
- Run:

```bash
python -m compileall litminer -q
python -m unittest discover -s test -p "test_*.py"
python -m litminer.sources.mcp.test_server
python -m litminer.engine.bootstrap --output-dir .litminer/bootstrap
python -m litminer.engine.offline_smoke
```

- If dev tools are installed, also run:

```bash
python -m ruff check litminer test
python -m mypy litminer
```

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
