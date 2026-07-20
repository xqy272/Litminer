"""Native subprocess acceptance for Litminer migration and restart semantics."""

from __future__ import annotations

import argparse
import json
import os
import platform
import queue
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from litminer.runtime import state_store as state_store_module
from litminer.runtime.state_store import CURRENT_SCHEMA_VERSION, StateStore


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CRASH_HELPER = PROJECT_ROOT / "test" / "helpers" / "crash_run.py"
MCP_SLOW_HELPER = PROJECT_ROOT / "test" / "helpers" / "mcp_slow_server.py"
CRASH_EXIT_CODE = 91


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _tail(value: str, limit: int = 12000) -> str:
    return value if len(value) <= limit else value[-limit:]


def _command_result(completed: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    return {
        "command": [str(item) for item in completed.args],
        "exit_code": completed.returncode,
        "stdout_tail": _tail(completed.stdout or ""),
        "stderr_tail": _tail(completed.stderr or ""),
    }


def _run(
    command: list[str],
    *,
    env: dict[str, str],
    timeout: float = 120.0,
) -> subprocess.CompletedProcess[str]:
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


def _write_input(path: Path) -> None:
    path.write_text(
        "title,doi,publication_year,journal,abstract\n"
        "Crash Recovery Paper,10.1000/recovery,2025,Test Journal,Recovery fixture\n"
        "Crash Recovery Paper,10.1000/recovery,2025,Test Journal,Duplicate fixture\n",
        encoding="utf-8",
    )


def _base_env(workspace: Path) -> dict[str, str]:
    temp_dir = workspace / ".litminer" / "tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    return {
        **os.environ,
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "LITMINER_WORKSPACE_ROOT": str(workspace),
        "LITMINER_STATE_STORE": str(
            workspace / ".litminer" / "state" / "litminer.sqlite3"
        ),
        "TEMP": str(temp_dir),
        "TMP": str(temp_dir),
    }


def migration_scenario(root: Path) -> dict[str, Any]:
    scenario_root = root / "migration"
    scenario_root.mkdir(parents=True, exist_ok=True)
    path = scenario_root / "state-v1.sqlite3"
    db = sqlite3.connect(path)
    try:
        db.executescript(state_store_module.MIGRATIONS[0][1])
        db.execute(
            "CREATE TABLE schema_migrations ("
            "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        db.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (1, ?)",
            ("2026-01-01T00:00:00Z",),
        )
        db.execute(
            "INSERT INTO research_sessions VALUES (?, ?, ?, ?, ?)",
            (
                "session-v1",
                str(scenario_root),
                str(scenario_root / "run"),
                "created",
                "updated",
            ),
        )
        db.execute(
            "INSERT INTO iterations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "iteration_001",
                "session-v1",
                "run-v1",
                "import",
                "completed",
                "healthy",
                '{"schema_version": 1}',
                "created",
                "completed",
            ),
        )
        db.execute(
            "INSERT INTO jobs VALUES (?, ?, ?, ?, ?)",
            (
                "job-v1",
                "run-v1",
                "completed",
                '{"job_id":"job-v1","run_id":"run-v1","status":"completed"}',
                "updated",
            ),
        )
        db.execute(
            "INSERT INTO run_outcomes VALUES (?, ?, ?, ?, ?, ?)",
            (
                "run-v1",
                str(scenario_root / "run"),
                "completed",
                "healthy",
                '{"run_id":"run-v1","status":"completed","quality":"healthy"}',
                "updated",
            ),
        )
        db.commit()
    finally:
        db.close()

    store = StateStore(path)
    first_job = store.get_job("job-v1")
    first_run = store.get_run(run_id="run-v1")
    StateStore(path)
    with store.connect() as upgraded:
        versions = [
            int(row[0])
            for row in upgraded.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            )
        ]
        runtime_events_exists = upgraded.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type='table' AND name='runtime_events'"
        ).fetchone() is not None

    broken_version = CURRENT_SCHEMA_VERSION + 1
    original_migrations = state_store_module.MIGRATIONS
    rollback_ok = False
    try:
        state_store_module.MIGRATIONS = original_migrations + ((
            broken_version,
            "CREATE TABLE rollback_probe(id INTEGER); INVALID SQL;",
        ),)
        try:
            StateStore(path)
        except sqlite3.Error:
            pass
        with sqlite3.connect(path) as check:
            rollback_ok = (
                check.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='rollback_probe'"
                ).fetchone()
                is None
                and check.execute(
                    "SELECT 1 FROM schema_migrations WHERE version=?",
                    (broken_version,),
                ).fetchone()
                is None
            )
    finally:
        state_store_module.MIGRATIONS = original_migrations

    export_path = store.export_state(scenario_root / "state-export.json")
    exported = json.loads(export_path.read_text(encoding="utf-8"))
    passed = (
        versions == list(range(1, CURRENT_SCHEMA_VERSION + 1))
        and runtime_events_exists
        and first_job.get("status") == "completed"
        and first_run.get("quality") == "healthy"
        and rollback_ok
        and "runtime_events" in exported.get("tables", {})
    )
    return {
        "name": "sqlite_migration_v1_to_current",
        "passed": passed,
        "versions": versions,
        "runtime_events_exists": runtime_events_exists,
        "preserved_job": first_job,
        "preserved_run": first_run,
        "broken_migration_rolled_back": rollback_ok,
        "state_store": str(path),
        "state_export": str(export_path),
    }


def cli_crash_resume_scenario(root: Path) -> dict[str, Any]:
    workspace = root / "cli-crash"
    workspace.mkdir(parents=True, exist_ok=True)
    input_path = workspace / "input.csv"
    output_dir = workspace / "run"
    _write_input(input_path)
    env = _base_env(workspace)
    env["LITMINER_CRASH_AFTER_STAGE"] = "dedupe"
    common = [
        "--input-csv",
        str(input_path),
        "--output-dir",
        str(output_dir),
        "--mode",
        "fast",
        "--skip-crossref",
        "--skip-unpaywall",
        "--skip-journal-metrics",
    ]
    crashed = _run([sys.executable, str(CRASH_HELPER), *common], env=env)
    manifest_path = output_dir / "run_manifest.json"
    before = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.exists()
        else {}
    )
    original_run_id = str(before.get("run_id") or "")
    dedupe_before = [
        item
        for item in before.get("stages", [])
        if isinstance(item, dict) and item.get("name") == "dedupe"
    ]

    resume_env = dict(env)
    resume_env.pop("LITMINER_CRASH_AFTER_STAGE", None)
    resumed = _run(
        [
            sys.executable,
            "-m",
            "litminer.engine.run_lit_search",
            *common,
            "--resume",
        ],
        env=resume_env,
    )
    after = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.exists()
        else {}
    )
    outcome_path = output_dir / "run_outcome.json"
    outcome = (
        json.loads(outcome_path.read_text(encoding="utf-8"))
        if outcome_path.exists()
        else {}
    )
    dedupe_after = [
        item
        for item in after.get("stages", [])
        if isinstance(item, dict) and item.get("name") == "dedupe"
    ]
    reused = any(item.get("status") == "skipped_existing" for item in dedupe_after)
    passed = (
        crashed.returncode == CRASH_EXIT_CODE
        and bool(original_run_id)
        and bool(dedupe_before)
        and resumed.returncode == 0
        and str(after.get("run_id") or "") == original_run_id
        and str(outcome.get("run_id") or "") == original_run_id
        and outcome.get("status") in {"completed", "partial"}
        and reused
    )
    return {
        "name": "cli_real_crash_and_resume",
        "passed": passed,
        "crash": _command_result(crashed),
        "resume": _command_result(resumed),
        "run_id": original_run_id,
        "outcome_status": outcome.get("status"),
        "dedupe_reused": reused,
        "manifest": str(manifest_path),
        "outcome": str(outcome_path),
    }


def _readline_with_timeout(stream, timeout: float) -> str:
    lines: queue.Queue[str | BaseException] = queue.Queue(maxsize=1)

    def read() -> None:
        try:
            lines.put(stream.readline())
        except BaseException as exc:
            lines.put(exc)

    thread = threading.Thread(target=read, daemon=True)
    thread.start()
    try:
        value = lines.get(timeout=timeout)
    except queue.Empty as exc:
        raise TimeoutError(f"timed out waiting {timeout:g}s for MCP response") from exc
    if isinstance(value, BaseException):
        raise value
    return value


def _mcp_call_once(
    env: dict[str, str],
    request: dict[str, Any],
    timeout: float = 30.0,
) -> tuple[dict[str, Any], subprocess.CompletedProcess[str]]:
    completed = subprocess.run(
        [sys.executable, "-m", "litminer.sources.mcp.server"],
        cwd=PROJECT_ROOT,
        env=env,
        input=json.dumps(request) + "\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    response: dict[str, Any] = {}
    for line in completed.stdout.splitlines():
        try:
            candidate = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict):
            response = candidate
    return response, completed


def _structured_content(response: dict[str, Any]) -> dict[str, Any]:
    result = response.get("result")
    if not isinstance(result, dict):
        return {}
    structured = result.get("structuredContent")
    return structured if isinstance(structured, dict) else {}


def mcp_worker_loss_scenario(root: Path) -> dict[str, Any]:
    workspace = root / "mcp-worker-loss"
    workspace.mkdir(parents=True, exist_ok=True)
    _write_input(workspace / "input.csv")
    env = _base_env(workspace)
    process = subprocess.Popen(
        [sys.executable, str(MCP_SLOW_HELPER)],
        cwd=PROJECT_ROOT,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    job_id = ""
    start_response: dict[str, Any] = {}
    persisted_before: dict[str, Any] = {}
    try:
        if process.stdin is None or process.stdout is None:
            raise RuntimeError("MCP subprocess pipes were not created")
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "litminer_start_run",
                "arguments": {
                    "input_csv": "input.csv",
                    "output_dir": "run",
                    "mode": "fast",
                },
            },
        }
        process.stdin.write(json.dumps(request) + "\n")
        process.stdin.flush()
        line = _readline_with_timeout(process.stdout, 20.0)
        start_response = json.loads(line)
        started = _structured_content(start_response)
        job_id = str(started.get("job_id") or "")
        store = StateStore(Path(env["LITMINER_STATE_STORE"]))
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            persisted_before = store.get_job(job_id)
            if persisted_before.get("status") == "running":
                break
            time.sleep(0.05)
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=10)
        if process.stdin:
            process.stdin.close()
        if process.stdout:
            process.stdout.close()
        if process.stderr:
            process.stderr.close()

    get_request = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {
            "name": "litminer_get_run",
            "arguments": {"job_id": job_id},
        },
    }
    loaded_response, loaded_process = _mcp_call_once(env, get_request)
    loaded = _structured_content(loaded_response)
    store = StateStore(Path(env["LITMINER_STATE_STORE"]))
    persisted_after = store.get_job(job_id)
    events = store.list_events(entity_type="job", entity_id=job_id)

    completed_id = "completed-" + uuid.uuid4().hex[:8]
    store.upsert_job({
        "job_id": completed_id,
        "run_id": "completed-run",
        "status": "completed",
        "quality": "healthy",
    })
    completed_response, completed_process = _mcp_call_once(
        env,
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "litminer_get_run",
                "arguments": {"job_id": completed_id},
            },
        },
    )
    completed_loaded = _structured_content(completed_response)
    event_types = [str(item.get("event_type") or "") for item in events]
    passed = (
        bool(job_id)
        and persisted_before.get("status") == "running"
        and process.returncode not in (None, 0)
        and loaded_process.returncode == 0
        and loaded.get("status") == "interrupted"
        and persisted_after.get("status") == "interrupted"
        and "job_running" in event_types
        and "job_interrupted" in event_types
        and completed_process.returncode == 0
        and completed_loaded.get("status") == "completed"
    )
    return {
        "name": "mcp_worker_loss_and_restart",
        "passed": passed,
        "job_id": job_id,
        "killed_exit_code": process.returncode,
        "persisted_before": persisted_before,
        "loaded_after_restart": loaded,
        "persisted_after": persisted_after,
        "job_event_types": event_types,
        "completed_job_preserved": completed_loaded.get("status") == "completed",
        "restart": _command_result(loaded_process),
    }


def _run_scenario(
    name: str,
    callback: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    started = time.monotonic()
    try:
        result = callback()
        result.setdefault("name", name)
        result.setdefault("passed", False)
    except BaseException as exc:
        result = {
            "name": name,
            "passed": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
    result["duration_seconds"] = round(time.monotonic() - started, 6)
    return result


def run_acceptance(profile: str, output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    scenarios = [
        _run_scenario(
            "sqlite_migration_v1_to_current",
            lambda: migration_scenario(output_dir),
        ),
        _run_scenario(
            "cli_real_crash_and_resume",
            lambda: cli_crash_resume_scenario(output_dir),
        ),
        _run_scenario(
            "mcp_worker_loss_and_restart",
            lambda: mcp_worker_loss_scenario(output_dir),
        ),
    ]
    report = {
        "schema_version": 1,
        "generated_at": utc_now(),
        "profile": profile,
        "platform": platform.system(),
        "platform_detail": platform.platform(),
        "python_version": platform.python_version(),
        "passed": all(bool(item.get("passed")) for item in scenarios),
        "duration_seconds": round(time.monotonic() - started, 6),
        "scenarios": scenarios,
    }
    report_path = output_dir / "runtime_resilience.json"
    report["report"] = str(report_path)
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=["quick", "full"], default="quick")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    report = run_acceptance(args.profile, args.output_dir)
    print(json.dumps({
        "passed": report["passed"],
        "profile": args.profile,
        "report": report["report"],
    }, ensure_ascii=False))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
