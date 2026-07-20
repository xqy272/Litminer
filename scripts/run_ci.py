#!/usr/bin/env python3
"""Cross-platform Litminer validation entry point.

This script sequences public commands instead of importing their internals.
GitHub Actions, Windows PowerShell, macOS shells, and local developers
therefore exercise the same surfaces.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT_ROOT = PROJECT_ROOT / ".litminer" / "ci"
ARCHITECTURE_SCENARIOS = (
    "degraded_coverage",
    "inconclusive_coverage",
    "persisted_cooldown",
    "invalid_mcp_input",
    "export_exclusion",
)


@dataclass(frozen=True)
class Step:
    name: str
    args: tuple[str, ...]
    category: str


def _python(*args: str) -> tuple[str, ...]:
    return (sys.executable, *args)


def quality_steps() -> list[Step]:
    return [
        Step("compile", _python("-m", "compileall", "-q", "litminer", "test", "scripts"), "quality"),
        Step("ruff", _python("-m", "ruff", "check", "litminer", "test", "scripts"), "quality"),
        Step("mypy", _python("-m", "mypy", "litminer", "scripts"), "quality"),
    ]


def test_steps(run_root: Path) -> list[Step]:
    steps = [
        Step("compile", _python("-m", "compileall", "-q", "litminer", "test", "scripts"), "test"),
        Step(
            "unittest",
            _python("-m", "unittest", "discover", "-s", "test", "-p", "test_*.py", "-q"),
            "test",
        ),
        Step("mcp_self_test", _python("-m", "litminer.sources.mcp.test_server"), "test"),
        Step(
            "doctor",
            _python("-m", "litminer.engine.doctor", "--config", "config/example.user.json"),
            "test",
        ),
        Step(
            "offline_smoke",
            _python(
                "-m",
                "litminer.engine.offline_smoke",
                "--output-dir",
                str(run_root / "offline_smoke"),
            ),
            "test",
        ),
        Step(
            "agent_scenarios_offline",
            _python(
                "test/run_agent_scenarios.py",
                "--profile",
                "offline",
                "--output-root",
                str(run_root / "agent_offline"),
                "--report-json",
                str(run_root / "agent_offline.json"),
            ),
            "agent",
        ),
        Step(
            "agent_scenarios_known_issue",
            _python(
                "test/run_agent_scenarios.py",
                "--profile",
                "known_issue",
                "--output-root",
                str(run_root / "agent_known_issue"),
                "--report-json",
                str(run_root / "agent_known_issue.json"),
            ),
            "agent",
        ),
        Step(
            "agent_adapter_acceptance",
            _python(
                "-m",
                "litminer.engine.agent_client_acceptance",
                "--agent",
                "all",
                "--output-dir",
                str(run_root / "agent_clients"),
            ),
            "agent",
        ),
        Step(
            "runtime_resilience",
            _python(
                "-m",
                "litminer.engine.runtime_resilience",
                "--profile",
                "quick",
                "--output-dir",
                str(run_root / "runtime_resilience"),
            ),
            "resilience",
        ),
        Step(
            "runtime_soak_quick",
            _python(
                "-m",
                "litminer.engine.runtime_soak",
                "--profile",
                "quick",
                "--output-dir",
                str(run_root / "runtime_soak"),
            ),
            "resilience",
        ),
    ]
    for scenario in ARCHITECTURE_SCENARIOS:
        steps.append(Step(
            f"architecture_{scenario}",
            _python(
                "-m",
                "litminer.engine.architecture_acceptance",
                "--scenario",
                scenario,
                "--output-dir",
                str(run_root / "architecture" / scenario),
            ),
            "architecture",
        ))
    return steps


def live_steps(run_root: Path, provider_profile: str) -> list[Step]:
    return [
        Step(
            f"provider_live_{provider_profile}",
            _python(
                "-m",
                "litminer.engine.provider_acceptance",
                "--profile",
                provider_profile,
                "--output-dir",
                str(run_root / "provider_live"),
                "--allow-skipped",
            ),
            "live",
        )
    ]


def soak_steps(run_root: Path, soak_profile: str) -> list[Step]:
    return [
        Step(
            f"runtime_soak_{soak_profile}",
            _python(
                "-m",
                "litminer.engine.runtime_soak",
                "--profile",
                soak_profile,
                "--output-dir",
                str(run_root / "runtime_soak"),
            ),
            "soak",
        )
    ]


def _tail(value: str, limit: int = 12000) -> str:
    return value if len(value) <= limit else value[-limit:]


def _tool_available(module: str) -> bool:
    probe = subprocess.run(
        _python("-c", f"import {module}"),
        cwd=PROJECT_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return probe.returncode == 0


def _preflight(steps: Iterable[Step]) -> list[str]:
    required_modules: list[str] = []
    names = {step.name for step in steps}
    if "ruff" in names:
        required_modules.append("ruff")
    if "mypy" in names:
        required_modules.append("mypy")
    return [module for module in required_modules if not _tool_available(module)]


def run_step(step: Step, env: dict[str, str]) -> dict[str, object]:
    print(f"\n[{step.category}] {step.name}")
    print(" ".join(step.args))
    started = time.monotonic()
    completed = subprocess.run(
        step.args,
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    duration = time.monotonic() - started
    if completed.stdout:
        print(completed.stdout, end="" if completed.stdout.endswith("\n") else "\n")
    if completed.stderr:
        print(completed.stderr, file=sys.stderr, end="" if completed.stderr.endswith("\n") else "\n")
    return {
        "name": step.name,
        "category": step.category,
        "command": list(step.args),
        "exit_code": completed.returncode,
        "passed": completed.returncode == 0,
        "duration_seconds": round(duration, 6),
        "stdout_tail": _tail(completed.stdout),
        "stderr_tail": _tail(completed.stderr),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile",
        choices=["quick", "quality", "test", "full", "live", "soak"],
        default="quick",
    )
    parser.add_argument("--provider-profile", choices=["core", "full"], default="full")
    parser.add_argument("--soak-profile", choices=["quick", "standard", "long"], default="standard")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_root = DEFAULT_REPORT_ROOT / f"{args.profile}_{timestamp}"
    if args.profile == "quality":
        steps = quality_steps()
    elif args.profile == "test":
        steps = test_steps(run_root)
    elif args.profile == "full":
        steps = [*quality_steps(), *test_steps(run_root)]
    elif args.profile == "live":
        steps = live_steps(run_root, args.provider_profile)
    elif args.profile == "soak":
        steps = soak_steps(run_root, args.soak_profile)
    else:
        steps = [
            quality_steps()[0],
            Step(
                "new_architecture_tests",
                _python("-m", "unittest", "discover", "-s", "test", "-p", "test_next_architecture.py", "-q"),
                "quick",
            ),
            Step("mcp_self_test", _python("-m", "litminer.sources.mcp.test_server"), "quick"),
        ]

    if args.list:
        for step in steps:
            print(f"{step.category}\t{step.name}\t{' '.join(step.args)}")
        return

    missing = _preflight(steps)
    if missing:
        print(
            "Missing development modules: "
            + ", ".join(missing)
            + '. Create a project virtual environment and install: python -m pip install -e ".[dev]"',
            file=sys.stderr,
        )
        raise SystemExit(2)

    run_root.mkdir(parents=True, exist_ok=True)
    report_path = args.report or run_root / "ci_report.json"
    env = {
        **os.environ,
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "LITMINER_WORKSPACE_ROOT": str(PROJECT_ROOT),
    }
    if os.name == "nt":
        default_temp = PROJECT_ROOT / ".litminer" / "tmp"
        env.setdefault("TEMP", str(Path(os.environ.get("TEMP") or default_temp)))
        env.setdefault("TMP", env["TEMP"])

    started = time.monotonic()
    results: list[dict[str, object]] = []
    for step in steps:
        result = run_step(step, env)
        results.append(result)
        if not result["passed"]:
            break

    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "profile": args.profile,
        "platform": platform.platform(),
        "system": platform.system(),
        "python_version": platform.python_version(),
        "python_executable": sys.executable,
        "passed": len(results) == len(steps) and all(item["passed"] for item in results),
        "duration_seconds": round(time.monotonic() - started, 6),
        "steps": results,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\nCI report: {report_path}")
    raise SystemExit(0 if payload["passed"] else 1)


if __name__ == "__main__":
    main()
