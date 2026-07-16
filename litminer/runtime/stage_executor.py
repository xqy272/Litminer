"""Explicit stage-state coordinator used by the compatibility runner."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from litminer.contracts.errors import ErrorEnvelope
from litminer.contracts.run_spec import RunSpec
from litminer.engine import status_policy
from litminer.runtime.state_store import StateStore, utc_now


@dataclass(frozen=True)
class StageResult:
    name: str
    status: str
    status_class: str
    input_count: int = 0
    output_count: int = 0
    input_path: str = ""
    output_path: str = ""
    message: str = ""
    warnings: tuple[str, ...] = ()
    error: ErrorEnvelope | None = None
    coverage_impact: dict[str, Any] = field(default_factory=dict)


class Stage(Protocol):
    name: str

    def fingerprint(self, context: "RunContext") -> str: ...
    def preconditions(self, context: "RunContext") -> list[Any]: ...
    def execute(self, context: "RunContext") -> StageResult: ...


@dataclass
class RunContext:
    run_spec: RunSpec
    output_dir: Path
    run_id: str
    iteration_id: str
    session_id: str
    state_store: StateStore
    started_monotonic: float = field(default_factory=time.monotonic)
    cancel_check: Callable[[], bool] | None = None


class PipelineExecutor:
    def __init__(self, context: RunContext) -> None:
        self.context = context

    def record_stage(self, result: StageResult) -> None:
        self.context.state_store.record_stage(
            run_id=self.context.run_id,
            stage_name=result.name,
            status=result.status,
            status_class=result.status_class,
            input_path=result.input_path,
            output_path=result.output_path,
            input_count=result.input_count,
            output_count=result.output_count,
            message=result.message,
            error=result.error.to_dict() if result.error else None,
            completed_at=utc_now() if result.status not in {"queued", "running"} else "",
        )

    def record_legacy_stage(
        self,
        *,
        name: str,
        status: str,
        input_path: Path | None = None,
        output_path: Path | None = None,
        input_count: int = 0,
        output_count: int = 0,
        message: str = "",
    ) -> StageResult:
        result = StageResult(
            name=name,
            status=status,
            status_class=status_policy.classify_status(status),
            input_count=input_count,
            output_count=output_count,
            input_path=str(input_path or ""),
            output_path=str(output_path or ""),
            message=message,
        )
        self.record_stage(result)
        return result

    def should_stop(self, stage_name: str) -> tuple[bool, str]:
        spec = self.context.run_spec
        if self.context.cancel_check and self.context.cancel_check():
            return True, f"Cancelled by background job request after stage: {stage_name}"
        if spec.controls.stop_after_stage and spec.controls.stop_after_stage == stage_name:
            return True, f"Stopped after requested stage: {stage_name}"
        budget = spec.controls.time_budget_seconds
        if budget is not None and time.monotonic() - self.context.started_monotonic >= max(0.0, float(budget)):
            return True, f"Stopped after stage {stage_name}: time budget {float(budget):g}s exhausted"
        return False, ""
