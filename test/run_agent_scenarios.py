#!/usr/bin/env python3
"""Run executable Litminer Agent scenarios.

The scenarios are deterministic checks for the Agent-facing skill contract.
They do not require an LLM. Each scenario stores a natural-language user prompt,
the expected Agent decision, the concrete tool invocation, and assertions over
stdout/stderr/artifacts.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCENARIOS = PROJECT_ROOT / "test" / "agent_scenarios.json"


class ScenarioError(AssertionError):
    pass


def _load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _json_path(data: Any, dotted: str) -> Any:
    current = data
    for part in dotted.split("."):
        if isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError) as exc:
                raise ScenarioError(f"JSON path segment {part!r} not found in list for {dotted!r}") from exc
        elif isinstance(current, dict):
            if part not in current:
                raise ScenarioError(f"JSON path {dotted!r} missing at segment {part!r}")
            current = current[part]
        else:
            raise ScenarioError(f"JSON path {dotted!r} reached non-container at {part!r}")
    return current


def _format_value(value: Any, context: dict[str, str]) -> Any:
    if isinstance(value, str):
        return value.format(**context)
    if isinstance(value, list):
        return [_format_value(item, context) for item in value]
    if isinstance(value, dict):
        return {key: _format_value(item, context) for key, item in value.items()}
    return value


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def _count_values(rows: list[dict[str, str]], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = row.get(field, "")
        counts[value] = counts.get(value, 0) + 1
    return counts


def _assert_equal(label: str, actual: Any, expected: Any, errors: list[str]) -> None:
    if actual != expected:
        errors.append(f"{label}: expected {expected!r}, got {actual!r}")


def _check_text(expect: dict[str, Any], stdout: str, stderr: str, errors: list[str]) -> None:
    for needle in expect.get("stdout_contains", []):
        if needle not in stdout:
            errors.append(f"stdout does not contain {needle!r}")
    for needle in expect.get("stdout_not_contains", []):
        if needle in stdout:
            errors.append(f"stdout unexpectedly contains {needle!r}")
    for needle in expect.get("stderr_contains", []):
        if needle not in stderr:
            errors.append(f"stderr does not contain {needle!r}")
    for needle in expect.get("stderr_not_contains", []):
        if needle in stderr:
            errors.append(f"stderr unexpectedly contains {needle!r}")


def _check_files(expect: dict[str, Any], errors: list[str]) -> None:
    for raw in expect.get("files_exist", []):
        path = Path(raw)
        if not path.exists():
            errors.append(f"expected file missing: {path}")
    for raw in expect.get("files_not_exist", []):
        path = Path(raw)
        if path.exists():
            errors.append(f"file should not exist: {path}")


def _check_json(expect: dict[str, Any], errors: list[str]) -> None:
    for spec in expect.get("json", []):
        path = Path(spec["path"])
        if not path.exists():
            errors.append(f"JSON file missing: {path}")
            continue
        try:
            data = _load_json(path)
        except Exception as exc:  # pragma: no cover - exercised by malformed artifacts
            errors.append(f"failed reading JSON {path}: {exc}")
            continue
        for check in spec.get("checks", []):
            label = f"{path}:{check.get('path')}"
            try:
                value = _json_path(data, check["path"])
            except ScenarioError as exc:
                errors.append(str(exc))
                continue
            if "equals" in check:
                _assert_equal(label, value, check["equals"], errors)
            if "contains" in check:
                expected = check["contains"]
                if isinstance(value, str):
                    ok = expected in value
                else:
                    ok = expected in value
                if not ok:
                    errors.append(f"{label}: expected to contain {expected!r}, got {value!r}")
            if "length" in check:
                try:
                    actual_len = len(value)
                except TypeError:
                    errors.append(f"{label}: value has no length: {value!r}")
                else:
                    _assert_equal(f"{label} length", actual_len, check["length"], errors)


def _check_csv(expect: dict[str, Any], errors: list[str]) -> None:
    for spec in expect.get("csv", []):
        path = Path(spec["path"])
        if not path.exists():
            errors.append(f"CSV file missing: {path}")
            continue
        try:
            fieldnames, rows = _read_csv(path)
        except Exception as exc:  # pragma: no cover - exercised by malformed artifacts
            errors.append(f"failed reading CSV {path}: {exc}")
            continue
        if "row_count" in spec:
            _assert_equal(f"{path} row_count", len(rows), spec["row_count"], errors)
        for field, expected_counts in spec.get("counts", {}).items():
            if field not in fieldnames:
                errors.append(f"{path}: field {field!r} missing")
                continue
            _assert_equal(f"{path} counts[{field}]", _count_values(rows, field), expected_counts, errors)
        for field in spec.get("unique", []):
            if field not in fieldnames:
                errors.append(f"{path}: unique field {field!r} missing")
                continue
            values = [row.get(field, "") for row in rows if row.get(field, "")]
            seen: set[str] = set()
            duplicates: set[str] = set()
            for v in values:
                if v in seen:
                    duplicates.add(v)
                else:
                    seen.add(v)
            if duplicates:
                errors.append(f"{path}: duplicate values for {field!r}: {sorted(duplicates)}")


def _run_command(command: list[str], env: dict[str, str], cwd: Path, timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=str(cwd),
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def _scenario_context(output_root: Path, scenario_id: str) -> dict[str, str]:
    output_dir = output_root / scenario_id
    return {
        "python": sys.executable,
        "project_root": str(PROJECT_ROOT),
        "output_root": str(output_root),
        "output_dir": str(output_dir),
        "fixture_mixed": str(PROJECT_ROOT / "test" / "fixtures" / "agent_mixed_candidates.csv"),
        "fixture_duplicate_doi": str(PROJECT_ROOT / "test" / "fixtures" / "triaged_duplicate_doi.csv"),
        "fixture_websearch": str(PROJECT_ROOT / "test" / "fixtures" / "websearch_candidates.csv"),
        "fixture_empty": str(PROJECT_ROOT / "test" / "fixtures" / "empty_candidates.csv"),
        "fixture_live_doi": str(PROJECT_ROOT / "test" / "fixtures" / "live_crossref_doi.csv"),
    }


def _scenario_env(scenario: dict[str, Any], context: dict[str, str]) -> dict[str, str]:
    env = dict(os.environ)
    env.setdefault("LITMINER_CONTACT_EMAIL", "agent-scenarios@example.org")
    env.setdefault("OPENALEX_MAILTO", "agent-scenarios@example.org")
    env.setdefault("CROSSREF_MAILTO", "agent-scenarios@example.org")
    env.setdefault("UNPAYWALL_EMAIL", "agent-scenarios@example.org")
    env.setdefault("LITMINER_MCP_TOOL_PROFILE", "workflow")
    scenario_env = scenario.get("env", {})
    for key in scenario_env.get("unset", []):
        env.pop(key, None)
    for key, value in scenario_env.get("set", {}).items():
        env[key] = str(value).format(**context)
    return env


def _execute_scenario(scenario: dict[str, Any], output_root: Path) -> dict[str, Any]:
    scenario_id = scenario["id"]
    context = _scenario_context(output_root, scenario_id)
    Path(context["output_dir"]).mkdir(parents=True, exist_ok=True)
    env = _scenario_env(scenario, context)
    timeout = int(scenario.get("timeout_seconds", 60))

    setup_results = []
    for raw_setup in scenario.get("setup_commands", []):
        command = _format_value(raw_setup, context)
        setup = _run_command(command, env, PROJECT_ROOT, timeout)
        setup_results.append({
            "command": command,
            "returncode": setup.returncode,
            "stdout": setup.stdout,
            "stderr": setup.stderr,
        })
        if setup.returncode != 0:
            return {
                "id": scenario_id,
                "status": "fail",
                "expected_failure": bool(scenario.get("expected_failure", False)),
                "errors": [f"setup command failed with exit code {setup.returncode}"],
                "setup_results": setup_results,
            }

    command = _format_value(scenario["command"], context)
    expect = _format_value(scenario.get("expect", {}), context)
    completed = _run_command(command, env, PROJECT_ROOT, timeout)

    errors: list[str] = []
    if "exit_code" in expect:
        _assert_equal("exit_code", completed.returncode, expect["exit_code"], errors)
    _check_text(expect, completed.stdout, completed.stderr, errors)
    _check_files(expect, errors)
    _check_json(expect, errors)
    _check_csv(expect, errors)

    expected_failure = bool(scenario.get("expected_failure", False))
    if errors and expected_failure:
        status = "xfail"
    elif errors:
        status = "fail"
    elif expected_failure:
        status = "xpass"
    else:
        status = "pass"

    return {
        "id": scenario_id,
        "status": status,
        "expected_failure": expected_failure,
        "expected_failure_reason": scenario.get("expected_failure_reason", ""),
        "profiles": scenario.get("profiles", []),
        "dimensions": scenario.get("dimensions", []),
        "user_prompt": scenario.get("user_prompt", ""),
        "command": command,
        "returncode": completed.returncode,
        "errors": errors,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "setup_results": setup_results,
        "output_dir": context["output_dir"],
    }


def _select_scenarios(all_scenarios: list[dict[str, Any]], profiles: set[str], ids: set[str]) -> list[dict[str, Any]]:
    selected = []
    for scenario in all_scenarios:
        scenario_profiles = set(scenario.get("profiles", []))
        if ids and scenario["id"] not in ids:
            continue
        if not ids and profiles and not scenario_profiles.intersection(profiles):
            continue
        selected.append(scenario)
    return selected


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenarios", type=Path, default=DEFAULT_SCENARIOS)
    parser.add_argument("--profile", action="append", default=None,
                        help="Scenario profile to run. Repeatable. Use 'all' for every scenario.")
    parser.add_argument("--scenario", action="append", default=[],
                        help="Run a specific scenario id. Repeatable.")
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument("--report-json", type=Path, default=None)
    parser.add_argument("--list", action="store_true", help="List selected scenarios and exit.")
    parser.add_argument("--allow-xpass", action="store_true",
                        help="Do not fail if an expected-failure scenario unexpectedly passes.")
    args = parser.parse_args(argv)

    data = _load_json(args.scenarios)
    all_scenarios = data.get("scenarios", [])
    profiles = set(args.profile or ["offline"])
    if "all" in profiles:
        profiles = set()
    scenario_ids = set(args.scenario or [])
    selected = _select_scenarios(all_scenarios, profiles, scenario_ids)

    if args.list:
        for scenario in selected:
            print(f"{scenario['id']}\t{','.join(scenario.get('profiles', []))}\t{scenario.get('user_prompt', '')}")
        return 0

    timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_root = args.output_root or (PROJECT_ROOT / ".litminer" / "test" / "agent_scenarios" / timestamp)
    output_root = output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    results = [_execute_scenario(scenario, output_root) for scenario in selected]
    counts: dict[str, int] = {}
    for result in results:
        counts[result["status"]] = counts.get(result["status"], 0) + 1

    report = {
        "schema_version": 1,
        "scenario_file": str(args.scenarios),
        "output_root": str(output_root),
        "selected_profiles": sorted(profiles) if profiles else ["all"],
        "selected_ids": sorted(scenario_ids),
        "counts": counts,
        "results": results,
    }
    report_json = args.report_json or (output_root / "agent_scenario_report.json")
    _write_report(report_json, report)

    for result in results:
        marker = result["status"].upper()
        print(f"[{marker}] {result['id']}")
        for error in result.get("errors", []):
            print(f"  - {error}")
        if result["status"] == "xfail" and result.get("expected_failure_reason"):
            print(f"  expected failure: {result['expected_failure_reason']}")
    print(f"Report: {report_json}")

    hard_fail = counts.get("fail", 0) > 0
    xpass_fail = counts.get("xpass", 0) > 0 and not args.allow_xpass
    return 1 if hard_fail or xpass_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
