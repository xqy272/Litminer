"""Stable run-outcome model shared by CLI, MCP, and artifacts."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from litminer.contracts.errors import ErrorEnvelope
from litminer.engine.common import write_text_atomic


RUN_STATUSES = {"queued", "running", "partial", "completed", "cancelled", "failed"}
QUALITY_STATUSES = {"healthy", "degraded", "inconclusive"}


@dataclass(frozen=True)
class RunOutcome:
    run_id: str
    status: str
    quality: str
    output_dir: str = ""
    artifacts: dict[str, str] = field(default_factory=dict)
    coverage: dict[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = field(default_factory=tuple)
    next_actions: tuple[str, ...] = field(default_factory=tuple)
    error: ErrorEnvelope | None = None
    schema_version: int = 1

    def __post_init__(self) -> None:
        if self.status not in RUN_STATUSES:
            raise ValueError(f"unknown run status: {self.status}")
        if self.quality not in QUALITY_STATUSES:
            raise ValueError(f"unknown run quality: {self.quality}")

    @property
    def ok(self) -> bool:
        return self.status not in {"failed"}

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["ok"] = self.ok
        data["warnings"] = list(self.warnings)
        data["next_actions"] = list(self.next_actions)
        if self.error is not None:
            data["error"] = self.error.to_dict()
        else:
            data.pop("error", None)
        return data

    def write(self, path: Path) -> Path:
        write_text_atomic(path, json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n")
        return path
