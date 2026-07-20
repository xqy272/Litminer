"""High-level MCP run, result, export, and recovery tools."""

from __future__ import annotations

import argparse
import json
import os
import threading
import time
import traceback
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from litminer.contracts.errors import classify_exception
from litminer.contracts.run_spec import RunSpec
from litminer.evidence.canonicalize import build_canonical_artifacts
from litminer.exporters.exporter import export_bibliography
from litminer.runtime.state_store import StateStore, default_state_store_path
from litminer.sources.mcp.job_registry import JobRegistry


@dataclass(frozen=True)
class WorkflowDependencies:
    workspace_root: Callable[[], Path]
    workspace_path: Callable[..., Path]
    optional_workspace_path: Callable[..., Path | None]
    runtime_store: Callable[[], StateStore]
    get_common: Callable[[], Any]
    get_engine_run_lit_search: Callable[[], Any]
    run_namespace: Callable[[dict[str, Any]], argparse.Namespace]
    read_csv_summary: Callable[[dict[str, Any]], dict[str, Any]]
    positive_int: Callable[..., int]
    next_actions: Callable[[dict[str, Any]], list[str]]


class WorkflowTools:
    def __init__(
        self,
        dependencies: WorkflowDependencies,
        jobs: JobRegistry,
    ) -> None:
        self.dependencies = dependencies
        self.jobs = jobs

    def read_results(self, args: dict[str, Any]) -> dict[str, Any]:
        output_dir = self.dependencies.workspace_path(
            args["output_dir"],
            "output_dir",
            must_exist=True,
        )
        artifact = str(args["artifact"])
        csv_artifacts = {
            "canonical_papers": "canonical_papers.csv",
            "triaged_candidates": "triaged_candidates.csv",
            "publisher_queue": "publisher_queue.csv",
        }
        if artifact in csv_artifacts:
            result = self.dependencies.read_csv_summary({
                "input_csv": str(output_dir / csv_artifacts[artifact]),
                "page": args.get("page", 1),
                "page_size": args.get("page_size", 20),
                "columns": args.get("columns") or [],
            })
            result.update({
                "artifact": artifact,
                "total_rows": result.get("filtered_count", 0),
                "has_more": bool(result.get("truncated")),
            })
            return result

        file_artifacts = {
            "coverage_report": "coverage_report.json",
            "canonical_provenance": "canonical_provenance.json",
            "agent_summary": "agent_summary.json",
            "run_outcome": "run_outcome.json",
            "processing_report": "processing_report.md",
            "search_audit_report": "search_audit_report.md",
            "export_manifest": "export_manifest.json",
        }
        path = output_dir / file_artifacts[artifact]
        if not path.exists():
            raise FileNotFoundError(f"artifact not found: {path}")
        max_chars = self.dependencies.positive_int(
            args.get("max_chars"),
            default=40000,
            minimum=100,
            maximum=200000,
        )
        raw = path.read_text(encoding="utf-8")
        truncated = len(raw) > max_chars
        payload: dict[str, Any] = {
            "ok": True,
            "artifact": artifact,
            "path": str(path),
            "characters": len(raw),
            "truncated": truncated,
            "has_more": truncated,
            "content_text": raw[:max_chars],
        }
        if path.suffix == ".json" and not truncated:
            payload["data"] = json.loads(raw)
        return payload

    def export(self, args: dict[str, Any]) -> dict[str, Any]:
        if args.get("output_dir"):
            output_dir = self.dependencies.workspace_path(
                args["output_dir"],
                "output_dir",
                must_exist=True,
            )
            input_csv = output_dir / "canonical_papers.csv"
            if not input_csv.exists():
                legacy = output_dir / "triaged_candidates.csv"
                if not legacy.exists():
                    raise FileNotFoundError(
                        "canonical or triaged bibliography not found under "
                        f"{output_dir}"
                    )
                input_csv, _provenance, _counts = (
                    build_canonical_artifacts(legacy, output_dir)
                )
        else:
            input_csv = self.dependencies.workspace_path(
                args["input_csv"],
                "input_csv",
                must_exist=True,
            )
            output_dir = input_csv.parent
            fieldnames, _rows = (
                self.dependencies.get_common().read_csv_rows(input_csv)
            )
            required = {
                "paper_id",
                "trusted_bibliography",
                "export_eligible",
            }
            if not required.issubset(set(fieldnames)):
                input_csv, _provenance, _counts = (
                    build_canonical_artifacts(input_csv, output_dir)
                )
        result = export_bibliography(
            input_csv,
            output_dir,
            formats=list(args.get("formats") or []),
            output_prefix=str(
                args.get("output_prefix") or "litminer_export"
            ),
            include_unverified=bool(
                args.get("include_unverified", False)
            ),
            ascii_latex=bool(args.get("ascii_latex", False)),
        )
        return {"ok": True, "status": "completed", **result}

    def run_lit_search(self, args: dict[str, Any]) -> dict[str, Any]:
        module = self.dependencies.get_engine_run_lit_search()
        result = module.run(self.dependencies.run_namespace(args))
        run_status = result.pop("status", "completed")
        summary_path = (
            Path(str(result.get("output_dir") or ""))
            / "agent_summary.json"
        )
        summary: dict[str, Any] = {}
        if summary_path.exists():
            try:
                summary = json.loads(
                    summary_path.read_text(encoding="utf-8")
                )
            except json.JSONDecodeError:
                summary = {}
        next_actions = self.dependencies.next_actions({
            "status": run_status,
            "agent_summary": summary,
            "agent_summary_path": str(summary_path),
        })
        return {
            "status": "ok",
            "run_status": run_status,
            "next_actions": next_actions,
            **result,
        }

    def job_snapshot(self, job_id: str) -> dict[str, Any]:
        job = self.jobs.snapshot(job_id)
        output_dir = job.get("output_dir")
        if output_dir:
            summary_path = Path(output_dir) / "agent_summary.json"
            if (
                job.get("status") in {"completed", "partial", "failed"}
                and summary_path.exists()
            ):
                try:
                    job["agent_summary"] = json.loads(
                        summary_path.read_text(encoding="utf-8")
                    )
                except json.JSONDecodeError:
                    job["agent_summary_path"] = str(summary_path)
            else:
                job["agent_summary_path"] = str(summary_path)
        job["next_actions"] = self.dependencies.next_actions(job)
        return job

    @staticmethod
    def read_json_object(path: Path) -> dict[str, Any]:
        if not path.exists() or not path.is_file():
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def run_view_from_output(self, output_dir: Path) -> dict[str, Any]:
        outcome = self.read_json_object(output_dir / "run_outcome.json")
        if outcome:
            return outcome
        manifest = self.read_json_object(output_dir / "run_manifest.json")
        coverage = self.read_json_object(
            output_dir / "coverage_report.json"
        )
        summary = self.read_json_object(output_dir / "agent_summary.json")
        index = self.read_json_object(output_dir / "artifacts_index.json")
        artifacts = {
            str(item.get("name") or ""): str(item.get("path") or "")
            for item in index.get("artifacts", [])
            if isinstance(item, dict) and item.get("exists")
        }
        if not (manifest or coverage or summary or artifacts):
            return {}
        status = str(
            manifest.get("run_status")
            or summary.get("run_status")
            or "unknown"
        )
        return {
            "ok": status != "failed",
            "run_id": str(manifest.get("run_id") or ""),
            "status": status,
            "quality": str(
                coverage.get("quality")
                or manifest.get("run_quality")
                or "inconclusive"
            ),
            "output_dir": str(output_dir),
            "artifacts": artifacts,
            "coverage": coverage,
            "warnings": summary.get("warnings") or [],
            "next_actions": summary.get("next_actions") or [],
            "compatibility_source": "legacy_artifacts",
        }

    def get_run(self, args: dict[str, Any]) -> dict[str, Any]:
        job_id = str(args.get("job_id") or "")
        if job_id:
            job = self.job_snapshot(job_id)
            job_status = str(job.get("status") or "")
            output_dir = (
                Path(str(job.get("output_dir") or ""))
                if job.get("output_dir")
                else None
            )
            outcome = (
                self.run_view_from_output(output_dir)
                if output_dir is not None
                and job_status not in {
                    "queued",
                    "running",
                    "cancelling",
                }
                else {}
            )
            if outcome:
                job_run_id = str(job.get("run_id") or "")
                job.update(outcome)
                if (
                    str(outcome.get("status") or "") in {"", "unknown"}
                    and job_status
                ):
                    job["status"] = job_status
                if (
                    not str(outcome.get("run_id") or "")
                    and job_run_id
                ):
                    job["run_id"] = job_run_id
                job["job_id"] = job_id
            job["ok"] = job.get("status") != "failed"
            job["next_actions"] = self.dependencies.next_actions(job)
            return job

        run_id = str(args.get("run_id") or "")
        output_value = args.get("output_dir")
        output_dir = (
            self.dependencies.workspace_path(
                output_value,
                "output_dir",
                must_exist=True,
            )
            if output_value
            else None
        )
        state_value = args.get("state_store")
        if state_value:
            store: StateStore | None = StateStore(
                self.dependencies.workspace_path(
                    state_value,
                    "state_store",
                    must_exist=True,
                )
            )
        else:
            default_path = default_state_store_path(
                self.dependencies.workspace_root()
            )
            store = (
                self.dependencies.runtime_store()
                if default_path.exists()
                else None
            )
        outcome = (
            store.get_run(
                run_id=run_id,
                output_dir=str(output_dir or ""),
            )
            if store is not None
            else {}
        )
        if not outcome and output_dir is not None:
            outcome = self.run_view_from_output(output_dir)
        if not outcome:
            raise ValueError(
                "no Litminer run was found for the supplied run_id or "
                "output_dir"
            )
        outcome.setdefault("ok", outcome.get("status") != "failed")
        outcome.setdefault(
            "next_actions",
            self.dependencies.next_actions(outcome),
        )
        return outcome

    def run_job(self, job_id: str, namespace: Any) -> None:
        module = self.dependencies.get_engine_run_lit_search()
        self.jobs.update(
            job_id,
            status="running",
            started_at=time.strftime(
                "%Y-%m-%dT%H:%M:%SZ",
                time.gmtime(),
            ),
        )
        try:
            result = module.run(namespace)
            self.jobs.update(
                job_id,
                status=result.get("status", "completed"),
                run_id=result.get(
                    "run_id",
                    self.job_snapshot(job_id).get("run_id", ""),
                ),
                quality=result.get("quality", "inconclusive"),
                result=result,
                output_dir=result.get(
                    "output_dir",
                    self.job_snapshot(job_id).get("output_dir", ""),
                ),
                ended_at=time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ",
                    time.gmtime(),
                ),
            )
        except (SystemExit, Exception) as exc:
            envelope = classify_exception(exc)
            self.jobs.update(
                job_id,
                status="failed",
                quality="inconclusive",
                error=envelope.to_dict(),
                traceback=(
                    traceback.format_exc()
                    if os.environ.get("LITMINER_MCP_DEBUG_ERRORS")
                    else ""
                ),
                ended_at=time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ",
                    time.gmtime(),
                ),
            )

    def start_run(self, args: dict[str, Any]) -> dict[str, Any]:
        queued_args = dict(args)
        queued_args["run_id"] = str(
            args.get("run_id")
            or (
                time.strftime(
                    "mcp_%Y%m%dT%H%M%SZ_",
                    time.gmtime(),
                )
                + uuid.uuid4().hex[:8]
            )
        )
        module = self.dependencies.get_engine_run_lit_search()
        namespace = module.normalize_args(
            self.dependencies.run_namespace(queued_args)
        )
        if bool(getattr(namespace, "resume", False)):
            prior = module.workflow_state.load_manifest(
                Path(namespace.output_dir)
            )
            if prior.get("run_id"):
                namespace.run_id = str(prior["run_id"])
        run_spec = RunSpec.from_namespace(namespace)
        job_id = str(uuid.uuid4())
        cancel_event = threading.Event()
        namespace.cancel_check = cancel_event.is_set
        self.jobs.create({
            "job_id": job_id,
            "run_id": str(namespace.run_id),
            "status": "queued",
            "quality": "inconclusive",
            "output_dir": str(
                getattr(namespace, "output_dir", "") or ""
            ),
            "state_store": str(
                getattr(namespace, "state_store_path", "") or ""
            ),
            "state_enabled": bool(
                getattr(namespace, "state_enabled", True)
            ),
            "run_spec": run_spec.to_dict(),
            "cancel_requested": False,
            "cancel_event": cancel_event,
            "created_at": time.strftime(
                "%Y-%m-%dT%H:%M:%SZ",
                time.gmtime(),
            ),
        })
        thread = threading.Thread(
            target=self.run_job,
            args=(job_id, namespace),
            daemon=False,
        )
        self.jobs.attach_thread(job_id, thread)
        thread.start()
        return {
            "status": "queued",
            "job_id": job_id,
            "run_id": str(namespace.run_id),
            "quality": "inconclusive",
            "output_dir": str(
                getattr(namespace, "output_dir", "") or ""
            ),
            "status_tool": "litminer_get_run",
            "next_actions": ["poll_litminer_get_run"],
        }

    def run_status(self, args: dict[str, Any]) -> dict[str, Any]:
        return self.get_run({"job_id": args.get("job_id")})

    def resume_run(self, args: dict[str, Any]) -> dict[str, Any]:
        resumed_args = dict(args)
        resumed_args["resume"] = True
        return self.start_run(resumed_args)

    def cancel_run(self, args: dict[str, Any]) -> dict[str, Any]:
        return self.jobs.request_cancel(str(args.get("job_id") or ""))
