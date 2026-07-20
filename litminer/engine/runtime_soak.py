"""Repeat native Litminer runtime operations and report long-run integrity."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sqlite3
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from litminer.contracts.errors import ProviderCooldownError
from litminer.engine.common import write_text_atomic
from litminer.evidence.canonicalize import build_canonical_artifacts
from litminer.exporters.exporter import export_bibliography
from litminer.runtime.provider_scheduler import ProviderScheduler
from litminer.runtime.state_store import StateStore


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class SoakProfile:
    iterations: int | None
    duration_seconds: float | None
    pipeline_every: int
    pause_seconds: float


PROFILES = {
    "quick": SoakProfile(
        iterations=2,
        duration_seconds=None,
        pipeline_every=1,
        pause_seconds=0.0,
    ),
    "standard": SoakProfile(
        iterations=None,
        duration_seconds=180.0,
        pipeline_every=15,
        pause_seconds=1.0,
    ),
    "long": SoakProfile(
        iterations=None,
        duration_seconds=1800.0,
        pipeline_every=60,
        pause_seconds=2.0,
    ),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _base_env(root: Path) -> dict[str, str]:
    temp_dir = root / "tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    return {
        **os.environ,
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "LITMINER_WORKSPACE_ROOT": str(root),
        "LITMINER_STATE_STORE": str(root / "state" / "litminer.sqlite3"),
        "TEMP": str(temp_dir),
        "TMP": str(temp_dir),
    }


def _run(command: list[str], env: dict[str, str], timeout: float = 120.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def _write_pipeline_input(path: Path, suffix: str, *, extra: bool = False) -> None:
    rows = [
        (
            f"Soak Pipeline Paper {suffix}",
            f"10.1000/soak.{suffix}.1",
            "2025",
            "Soak Journal",
            "A deterministic offline soak fixture with validation evidence.",
        ),
        (
            f"Soak Pipeline Paper {suffix}",
            f"10.1000/soak.{suffix}.1",
            "2025",
            "Soak Journal",
            "Duplicate row for deduplication and resume checks.",
        ),
    ]
    if extra:
        rows = [(
            f"Soak Merge Paper {suffix}",
            f"10.1000/soak.{suffix}.2",
            "2026",
            "Soak Journal",
            "A new row for merge iteration delta checks.",
        )]
    text = "title,doi,publication_year,journal,abstract\n"
    text += "".join(",".join(row) + "\n" for row in rows)
    path.write_text(text, encoding="utf-8")


def pipeline_cycle(root: Path, cycle: int) -> dict[str, Any]:
    cycle_root = root / "pipeline" / f"cycle_{cycle:04d}"
    cycle_root.mkdir(parents=True, exist_ok=True)
    input_path = cycle_root / "input.csv"
    merge_input = cycle_root / "merge.csv"
    output_dir = cycle_root / "run"
    _write_pipeline_input(input_path, str(cycle))
    _write_pipeline_input(merge_input, str(cycle), extra=True)
    env = _base_env(cycle_root)
    common = [
        "--mode",
        "fast",
        "--skip-crossref",
        "--skip-unpaywall",
        "--skip-journal-metrics",
    ]
    first = _run([
        sys.executable,
        "-m",
        "litminer.engine.run_lit_search",
        "--input-csv",
        str(input_path),
        "--output-dir",
        str(output_dir),
        *common,
    ], env)
    first_outcome_path = output_dir / "run_outcome.json"
    first_outcome = (
        json.loads(first_outcome_path.read_text(encoding="utf-8"))
        if first_outcome_path.exists()
        else {}
    )
    first_run_id = str(first_outcome.get("run_id") or "")
    resumed = _run([
        sys.executable,
        "-m",
        "litminer.engine.run_lit_search",
        "--input-csv",
        str(input_path),
        "--output-dir",
        str(output_dir),
        *common,
        "--resume",
    ], env)
    resumed_outcome = (
        json.loads(first_outcome_path.read_text(encoding="utf-8"))
        if first_outcome_path.exists()
        else {}
    )
    merged = _run([
        sys.executable,
        "-m",
        "litminer.engine.run_lit_search",
        "--input-csv",
        str(merge_input),
        "--merge-into",
        str(output_dir),
        *common,
    ], env)
    merged_outcome = (
        json.loads(first_outcome_path.read_text(encoding="utf-8"))
        if first_outcome_path.exists()
        else {}
    )
    session_path = output_dir / "research_session_manifest.json"
    delta_path = output_dir / "delta_profile.json"
    session = (
        json.loads(session_path.read_text(encoding="utf-8"))
        if session_path.exists()
        else {}
    )
    delta = (
        json.loads(delta_path.read_text(encoding="utf-8"))
        if delta_path.exists()
        else {}
    )
    iterations = session.get("iterations")
    passed = (
        first.returncode == 0
        and resumed.returncode == 0
        and merged.returncode == 0
        and bool(first_run_id)
        and str(resumed_outcome.get("run_id") or "") == first_run_id
        and str(merged_outcome.get("run_id") or "") != first_run_id
        and isinstance(iterations, list)
        and len(iterations) >= 2
        and int(delta.get("new_rows") or 0) >= 1
    )
    return {
        "passed": passed,
        "first_exit_code": first.returncode,
        "resume_exit_code": resumed.returncode,
        "merge_exit_code": merged.returncode,
        "first_run_id": first_run_id,
        "merged_run_id": merged_outcome.get("run_id"),
        "session_iterations": len(iterations) if isinstance(iterations, list) else 0,
        "delta_new_rows": int(delta.get("new_rows") or 0),
        "output_dir": str(output_dir),
        "first_stderr_tail": first.stderr[-4000:],
        "resume_stderr_tail": resumed.stderr[-4000:],
        "merge_stderr_tail": merged.stderr[-4000:],
    }


def _write_canonical_input(path: Path, iteration: int) -> None:
    path.write_text(
        "title,doi,publication_year,journal,article_type,crossref_status,"
        "crossref_title,crossref_doi,crossref_year,crossref_container,crossref_type\n"
        f"Canonical Soak {iteration},10.1000/canonical.{iteration},2025,"
        "Soak Journal,journal-article,verified,"
        f"Canonical Soak {iteration},10.1000/canonical.{iteration},2025,"
        "Soak Journal,journal-article\n",
        encoding="utf-8",
    )


def runtime_iteration(root: Path, store: StateStore, iteration: int) -> dict[str, Any]:
    iteration_root = root / "iterations" / f"iteration_{iteration:06d}"
    iteration_root.mkdir(parents=True, exist_ok=True)
    run_id = f"soak-run-{iteration:06d}"
    job_id = f"soak-job-{iteration:06d}"

    atomic_path = root / "atomic-state.json"
    atomic_payload = {
        "iteration": iteration,
        "nonce": uuid.uuid4().hex,
        "written_at": utc_now(),
    }
    write_text_atomic(
        atomic_path,
        json.dumps(atomic_payload, sort_keys=True) + "\n",
    )
    atomic_loaded = json.loads(atomic_path.read_text(encoding="utf-8"))

    store.upsert_job({"job_id": job_id, "run_id": run_id, "status": "queued"})
    store.upsert_job({"job_id": job_id, "run_id": run_id, "status": "running"})
    store.upsert_job({
        "job_id": job_id,
        "run_id": run_id,
        "status": "completed",
        "quality": "healthy",
    })
    persisted_job = StateStore(store.path).get_job(job_id)

    provider = f"soak_provider_{iteration:06d}"
    ProviderScheduler(store).record(
        provider,
        status_class="rate_limited",
        retry_after_seconds=60.0,
    )
    cooldown_observed = False
    try:
        ProviderScheduler(StateStore(store.path)).acquire(provider)
    except ProviderCooldownError:
        cooldown_observed = True
    store.update_provider_health(
        provider,
        not_before="",
        last_retry_after_seconds=None,
    )
    cooldown_released = (
        ProviderScheduler(StateStore(store.path)).acquire(provider)
        >= 0
    )

    canonical_input = iteration_root / "verified.csv"
    _write_canonical_input(canonical_input, iteration)
    canonical_path, provenance_path, canonical_counts = build_canonical_artifacts(
        canonical_input,
        iteration_root,
        state_store=store,
    )
    export = export_bibliography(
        canonical_path,
        iteration_root,
        formats=["ris", "bibtex"],
        output_prefix="soak_export",
    )
    artifacts = {
        "atomic": file_sha256(atomic_path),
        "canonical": file_sha256(canonical_path),
        "provenance": file_sha256(provenance_path),
        "ris": str((export.get("outputs") or {}).get("ris", {}).get("sha256") or ""),
        "bibtex": str((export.get("outputs") or {}).get("bibtex", {}).get("sha256") or ""),
    }
    passed = (
        atomic_loaded == atomic_payload
        and persisted_job.get("status") == "completed"
        and cooldown_observed
        and cooldown_released
        and canonical_counts.get("trusted") == 1
        and canonical_counts.get("export_eligible") == 1
        and export.get("exported_rows") == 1
        and all(artifacts.values())
    )
    return {
        "iteration": iteration,
        "passed": passed,
        "job_persisted": persisted_job.get("status") == "completed",
        "cooldown_observed": cooldown_observed,
        "cooldown_released": cooldown_released,
        "canonical_counts": canonical_counts,
        "exported_rows": export.get("exported_rows"),
        "artifact_hashes": artifacts,
    }


def integrity_snapshot(store: StateStore) -> dict[str, Any]:
    with store.connect() as db:
        integrity = str(db.execute("PRAGMA integrity_check").fetchone()[0])
        journal_mode = str(db.execute("PRAGMA journal_mode").fetchone()[0])
        event_count = int(db.execute("SELECT COUNT(*) FROM runtime_events").fetchone()[0])
        job_count = int(db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0])
    return {
        "integrity_check": integrity,
        "journal_mode": journal_mode,
        "runtime_events": event_count,
        "jobs": job_count,
    }


def run_soak(
    *,
    profile_name: str,
    output_dir: Path,
    iterations: int | None = None,
    duration_seconds: float | None = None,
) -> dict[str, Any]:
    profile = PROFILES[profile_name]
    output_dir = output_dir.expanduser().resolve(strict=False)
    iteration_limit = iterations if iterations is not None else profile.iterations
    duration_limit = (
        duration_seconds
        if duration_seconds is not None
        else profile.duration_seconds
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    store = StateStore(output_dir / "runtime_soak.sqlite3")
    started = time.monotonic()
    iteration_results: list[dict[str, Any]] = []
    pipeline_results: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    lock_retries = 0
    iteration = 0

    while True:
        if iteration_limit is not None and iteration >= iteration_limit:
            break
        if (
            duration_limit is not None
            and iteration > 0
            and time.monotonic() - started >= duration_limit
        ):
            break
        iteration += 1
        try:
            result = runtime_iteration(output_dir, store, iteration)
            iteration_results.append(result)
            if not result["passed"]:
                failures.append({"iteration": iteration, "stage": "runtime_iteration"})
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc).lower():
                lock_retries += 1
            failures.append({
                "iteration": iteration,
                "stage": "runtime_iteration",
                "error": f"{type(exc).__name__}: {exc}",
            })
        except BaseException as exc:
            failures.append({
                "iteration": iteration,
                "stage": "runtime_iteration",
                "error": f"{type(exc).__name__}: {exc}",
            })

        if iteration == 1 or iteration % profile.pipeline_every == 0:
            try:
                pipeline = pipeline_cycle(output_dir, iteration)
                pipeline_results.append(pipeline)
                if not pipeline["passed"]:
                    failure: dict[str, Any] = {
                        "iteration": iteration,
                        "stage": "pipeline_cycle",
                        "first_exit_code": pipeline.get("first_exit_code"),
                        "resume_exit_code": pipeline.get("resume_exit_code"),
                        "merge_exit_code": pipeline.get("merge_exit_code"),
                    }
                    for field in (
                        "first_stderr_tail",
                        "resume_stderr_tail",
                        "merge_stderr_tail",
                    ):
                        if pipeline.get(field):
                            failure[field] = pipeline[field]
                    failures.append(failure)
            except BaseException as exc:
                failures.append({
                    "iteration": iteration,
                    "stage": "pipeline_cycle",
                    "error": f"{type(exc).__name__}: {exc}",
                })
        if profile.pause_seconds:
            time.sleep(profile.pause_seconds)

    integrity = integrity_snapshot(store)
    passed = (
        not failures
        and bool(iteration_results)
        and integrity["integrity_check"] == "ok"
        and integrity["journal_mode"].lower() == "wal"
    )
    report = {
        "schema_version": 1,
        "generated_at": utc_now(),
        "profile": profile_name,
        "platform": platform.system(),
        "platform_detail": platform.platform(),
        "python_version": platform.python_version(),
        "passed": passed,
        "duration_seconds": round(time.monotonic() - started, 6),
        "requested_iterations": iteration_limit,
        "requested_duration_seconds": duration_limit,
        "iteration_pause_seconds": profile.pause_seconds,
        "completed_iterations": len(iteration_results),
        "pipeline_cycles": len(pipeline_results),
        "lock_retries": lock_retries,
        "failure_count": len(failures),
        "failures": failures,
        "integrity": integrity,
        "iterations": iteration_results,
        "pipelines": pipeline_results,
        "state_store": str(store.path),
    }
    report_path = output_dir / "runtime_soak.json"
    report["report"] = str(report_path)
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=sorted(PROFILES), default="quick")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--iterations", type=int, default=None)
    parser.add_argument("--duration-seconds", type=float, default=None)
    args = parser.parse_args()
    if args.iterations is not None and args.iterations < 1:
        parser.error("--iterations must be at least 1")
    if args.duration_seconds is not None and args.duration_seconds <= 0:
        parser.error("--duration-seconds must be positive")
    report = run_soak(
        profile_name=args.profile,
        output_dir=args.output_dir,
        iterations=args.iterations,
        duration_seconds=args.duration_seconds,
    )
    print(json.dumps({
        "passed": report["passed"],
        "profile": args.profile,
        "iterations": report["completed_iterations"],
        "report": report["report"],
    }, ensure_ascii=False))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
