"""Workspace path helpers for Litminer runtime outputs."""

from __future__ import annotations

import os
from pathlib import Path


WORKSPACE_ENV = "LITMINER_WORKSPACE_ROOT"
DEFAULT_RUN_DIR = ".litminer/runs/litminer_run"
DEFAULT_SMOKE_DIR = ".litminer/runs/offline_smoke"
DEFAULT_SCREENSHOT_ROOT = ".litminer/screenshots"


def workspace_root() -> Path:
    """Return the configured user workspace, falling back to the process cwd."""
    configured = os.environ.get(WORKSPACE_ENV, "").strip()
    if configured:
        return Path(configured).expanduser().resolve(strict=False)
    return Path.cwd().resolve(strict=False)


def resolve_workspace_path(value: str | Path, root: Path | None = None) -> Path:
    """Resolve a relative runtime path under the Litminer workspace root."""
    path = Path(value).expanduser()
    if path.is_absolute():
        return path.resolve(strict=False)
    base = root or workspace_root()
    return (base / path).resolve(strict=False)
