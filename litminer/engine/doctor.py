#!/usr/bin/env python3
"""Installation, environment, and runtime-config checks for Litminer."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from litminer.engine import workspace as workspace_paths


PROJECT_ROOT = Path(__file__).resolve().parents[2]

EXPECTED_CONFIG: dict[str, dict[str, tuple[type, ...]]] = {
    "channels": {
        "openalex": (bool,),
        "semantic_scholar": (bool,),
        "arxiv": (bool,),
        "europe_pmc": (bool,),
        "crossref": (bool,),
        "unpaywall": (bool,),
        "journal_metrics": (bool,),
        "publisher_probe": (bool,),
    },
    "api": {
        "openalex_api_key_env": (str,),
        "openalex_mailto_env": (str,),
        "crossref_mailto_env": (str,),
        "unpaywall_email_env": (str,),
        "contact_email_env": (str,),
        "openalex_work_types": (str,),
    },
    "limits": {
        "max_results_per_query": (int,),
        "semantic_query_limit": (int, type(None)),
        "semantic_max_results": (int, type(None)),
        "publisher_probe_limit": (int, type(None)),
        "publisher_probe_sleep": (int, float),
        "strict_discovery": (bool,),
        "parallel_providers": (bool,),
        "provider_workers": (int, type(None)),
        "provider_failure_threshold": (int, type(None)),
        "provider_rate_limit_cooldown_seconds": (int, float),
        "unpaywall_sleep": (int, float),
        "crossref_checkpoint_interval": (int,),
        "unpaywall_checkpoint_interval": (int,),
        "time_budget_seconds": (int, float, type(None)),
        "max_crossref_rows": (int, type(None)),
        "max_unpaywall_rows": (int, type(None)),
        "max_publisher_probe_rows": (int, type(None)),
    },
    "outputs": {
        "default_output_dir": (str,),
        "screenshot_root": (str,),
    },
    "cache": {
        "enabled": (bool,),
        "cache_dir": (str,),
        "ttl_days": (int, float),
        "provider_failure_ttl_seconds": (int, float),
    },
    "evidence": {
        "require_doi_for_queue": (bool,),
        "queue_priorities": (str,),
        "include_metadata_blocked": (bool,),
        "queue_strict_only": (bool,),
        "unknown_value": (str,),
    },
}


@dataclass
class Check:
    name: str
    status: str
    message: str


def _status_order(status: str) -> int:
    return {"error": 0, "warning": 1, "ok": 2}.get(status, 3)


def load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise ValueError(f"config file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"config JSON parse failed: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("config root must be a JSON object")
    return data


def validate_config(path: Path) -> list[Check]:
    checks: list[Check] = []
    try:
        data = load_json(path)
    except ValueError as exc:
        return [Check("config", "error", str(exc))]

    checks.append(Check("config", "ok", f"loaded {path}"))

    for section, value in data.items():
        if section not in EXPECTED_CONFIG:
            checks.append(Check("config", "warning", f"unknown top-level section: {section}"))
            continue
        if not isinstance(value, dict):
            checks.append(Check("config", "error", f"section {section!r} must be an object"))
            continue
        expected_keys = EXPECTED_CONFIG[section]
        for key, item in value.items():
            if key not in expected_keys:
                checks.append(Check("config", "warning", f"unknown key: {section}.{key}"))
                continue
            allowed = expected_keys[key]
            if not isinstance(item, allowed):
                allowed_names = " or ".join(t.__name__ for t in allowed)
                checks.append(
                    Check("config", "error", f"{section}.{key} must be {allowed_names}, got {type(item).__name__}")
                )

    channels = data.get("channels", {})
    if isinstance(channels, dict) and not any(
        bool(channels.get(name)) for name in ("openalex", "semantic_scholar", "arxiv", "europe_pmc")
    ):
        checks.append(Check("config", "warning", "all discovery channels are disabled"))

    limits = data.get("limits", {})
    if isinstance(limits, dict):
        for key in (
            "max_results_per_query",
            "semantic_query_limit",
            "semantic_max_results",
            "publisher_probe_limit",
            "provider_workers",
            "provider_failure_threshold",
            "provider_rate_limit_cooldown_seconds",
            "crossref_checkpoint_interval",
            "unpaywall_checkpoint_interval",
            "time_budget_seconds",
            "max_crossref_rows",
            "max_unpaywall_rows",
            "max_publisher_probe_rows",
        ):
            value = limits.get(key)
            if value is not None and isinstance(value, int) and value < 0:
                checks.append(Check("config", "error", f"{key} must not be negative"))
        for key in ("publisher_probe_sleep", "unpaywall_sleep", "provider_rate_limit_cooldown_seconds"):
            value = limits.get(key)
            if value is not None and isinstance(value, (int, float)) and value < 0:
                checks.append(Check("config", "error", f"{key} must not be negative"))
    cache = data.get("cache", {})
    if isinstance(cache, dict):
        for key in ("ttl_days", "provider_failure_ttl_seconds"):
            value = cache.get(key)
            if value is not None and isinstance(value, (int, float)) and value < 0:
                checks.append(Check("config", "error", f"cache.{key} must not be negative"))

    return checks


def check_installation(skill_dir: Path | None = None) -> list[Check]:
    checks: list[Check] = []
    checks.append(
        Check(
            "python",
            "ok" if sys.version_info >= (3, 10) else "error",
            (
                f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}; "
                f"executable={sys.executable}; platform={platform.platform()}"
            ),
        )
    )
    available_launchers = [name for name in ("python", "py", "python3") if shutil.which(name)]
    if available_launchers:
        checks.append(Check("python", "ok", f"available command launchers: {', '.join(available_launchers)}"))
    elif os.name == "nt":
        checks.append(
            Check(
                "python",
                "warning",
                "no python launcher found on PATH; use `py -3 ...` or configure MCP command to sys.executable",
            )
        )

    root = (skill_dir or PROJECT_ROOT).resolve()
    required = [
        root / "SKILL.md",
        root / "litminer",
        root / "config" / "default.json",
    ]
    for path in required:
        if path.exists():
            checks.append(Check("install", "ok", f"found {path}"))
        else:
            checks.append(Check("install", "warning", f"missing {path}; direct module use may still work"))
    return checks


def _env_value(name: str) -> str:
    return os.environ.get(name, "").strip()


def check_environment(config: dict[str, Any] | None = None) -> list[Check]:
    checks: list[Check] = []
    api = (config or {}).get("api", {}) if isinstance((config or {}).get("api", {}), dict) else {}
    channels = (config or {}).get("channels", {}) if isinstance((config or {}).get("channels", {}), dict) else {}

    contact_env = str(api.get("contact_email_env") or "LITMINER_CONTACT_EMAIL")
    unpaywall_env = str(api.get("unpaywall_email_env") or "UNPAYWALL_EMAIL")
    openalex_env = str(api.get("openalex_mailto_env") or "OPENALEX_MAILTO")
    crossref_env = str(api.get("crossref_mailto_env") or "CROSSREF_MAILTO")

    contact = _env_value(contact_env)
    if contact:
        checks.append(Check("env", "ok", f"{contact_env} is set"))
    else:
        checks.append(Check("env", "warning", f"{contact_env} is not set; API polite contact fallback is unavailable"))

    for name in (openalex_env, crossref_env):
        if _env_value(name):
            checks.append(Check("env", "ok", f"{name} is set"))
        elif contact:
            checks.append(Check("env", "ok", f"{name} is not set; will fall back to {contact_env}"))
        else:
            checks.append(Check("env", "warning", f"{name} is not set"))

    unpaywall_enabled = bool(channels.get("unpaywall", True))
    if _env_value(unpaywall_env) or contact:
        checks.append(Check("env", "ok", f"{unpaywall_env} or {contact_env} is available"))
    elif unpaywall_enabled:
        checks.append(Check("env", "warning", f"{unpaywall_env} is not set; Unpaywall annotation will be skipped"))

    key_name = str(api.get("openalex_api_key_env") or "OPENALEX_API_KEY")
    checks.append(
        Check(
            "env",
            "ok" if _env_value(key_name) else "ok",
            f"{key_name} {'is set' if _env_value(key_name) else 'is not set; usually optional'}",
        )
    )
    return checks


def _workspace_root_for_arg(workspace: Path | None = None) -> Path:
    if workspace is not None:
        return workspace.expanduser().resolve(strict=False)
    configured = os.environ.get(workspace_paths.WORKSPACE_ENV, "").strip()
    if configured:
        return Path(configured).expanduser().resolve(strict=False)
    return workspace_paths.workspace_root()


def _explain_workspace_path(value: str | Path, root: Path) -> dict[str, Any]:
    requested = str(value)
    path = Path(requested).expanduser()
    candidate = path if path.is_absolute() else root / path
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root)
        inside_workspace = True
    except ValueError:
        inside_workspace = False
    return {
        "requested": requested,
        "resolved_path": str(resolved),
        "inside_workspace": inside_workspace,
        "exists": resolved.exists(),
        "is_file": resolved.is_file(),
        "is_dir": resolved.is_dir(),
        "suggestion": (
            "ok"
            if inside_workspace
            else (
                f"Move the file under {root}, pass a workspace-relative path, "
                f"or set {workspace_paths.WORKSPACE_ENV} to the project containing this path."
            )
        ),
    }


def workspace_report(workspace: Path | None = None,
                     explain_paths: list[str | Path] | None = None,
                     create: bool = False) -> dict[str, Any]:
    root = _workspace_root_for_arg(workspace)
    env_value = os.environ.get(workspace_paths.WORKSPACE_ENV, "")
    created = False
    create_error = ""
    if create and not root.exists():
        try:
            root.mkdir(parents=True, exist_ok=True)
            created = True
        except OSError as exc:
            create_error = str(exc)

    exists = root.exists()
    is_dir = root.is_dir()
    writable = False
    write_error = ""
    if exists and is_dir:
        try:
            with tempfile.NamedTemporaryFile(prefix=".litminer-doctor-", dir=root, delete=True):
                pass
            writable = True
        except OSError as exc:
            write_error = str(exc)
    elif exists:
        write_error = "workspace path exists but is not a directory"
    else:
        write_error = "workspace directory does not exist"

    default_output_dir = workspace_paths.resolve_workspace_path(workspace_paths.DEFAULT_RUN_DIR, root=root)
    default_screenshot_root = workspace_paths.resolve_workspace_path(workspace_paths.DEFAULT_SCREENSHOT_ROOT, root=root)
    return {
        "python_executable": sys.executable,
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "cwd": str(Path.cwd().resolve(strict=False)),
        "workspace_env": workspace_paths.WORKSPACE_ENV,
        "workspace_env_value": env_value,
        "workspace_root": str(root),
        "workspace_exists": exists,
        "workspace_is_dir": is_dir,
        "workspace_writable": writable,
        "workspace_write_error": write_error,
        "workspace_created": created,
        "workspace_create_error": create_error,
        "default_output_dir": str(default_output_dir),
        "default_screenshot_root": str(default_screenshot_root),
        "path_checks": [
            _explain_workspace_path(path, root)
            for path in (explain_paths or [])
        ],
    }


def check_workspace(workspace: Path | None = None,
                    explain_paths: list[str | Path] | None = None,
                    create: bool = False) -> list[Check]:
    report = workspace_report(workspace=workspace, explain_paths=explain_paths, create=create)
    root = report["workspace_root"]
    checks: list[Check] = []
    if report["workspace_created"]:
        checks.append(Check("workspace", "ok", f"created workspace: {root}"))
    if report["workspace_create_error"]:
        checks.append(Check("workspace", "error", f"failed to create workspace {root}: {report['workspace_create_error']}"))
    if not report["workspace_env_value"] and workspace is None:
        checks.append(
            Check(
                "workspace",
                "ok",
                f"{workspace_paths.WORKSPACE_ENV} is not set; using cwd as workspace root: {root}",
            )
        )
    if not report["workspace_exists"]:
        checks.append(Check("workspace", "warning", f"workspace directory does not exist yet: {root}"))
    elif not report["workspace_is_dir"]:
        checks.append(Check("workspace", "error", f"workspace path is not a directory: {root}"))
    elif not report["workspace_writable"]:
        checks.append(Check("workspace", "error", f"workspace is not writable: {root}; {report['workspace_write_error']}"))
    else:
        checks.append(
            Check(
                "workspace",
                "ok",
                f"workspace exists and is writable: {root}; default outputs use {report['default_output_dir']}",
            )
        )
    for path_check in report["path_checks"]:
        if path_check["inside_workspace"]:
            checks.append(Check("workspace-path", "ok", f"{path_check['requested']} -> {path_check['resolved_path']}"))
        else:
            checks.append(
                Check(
                    "workspace-path",
                    "error",
                    (
                        f"{path_check['requested']} escapes workspace root {root}; "
                        f"resolved to {path_check['resolved_path']}. {path_check['suggestion']}"
                    ),
                )
            )
    return checks


def run_checks(config_path: Path | None = None,
               skill_dir: Path | None = None,
               workspace: Path | None = None,
               explain_paths: list[str | Path] | None = None,
               create_workspace: bool = False) -> list[Check]:
    checks = check_installation(skill_dir)

    config_data: dict[str, Any] | None = None
    if config_path is not None:
        config_checks = validate_config(config_path)
        checks.extend(config_checks)
        if not any(check.status == "error" for check in config_checks):
            config_data = load_json(config_path)
    else:
        default_config = PROJECT_ROOT / "config" / "default.json"
        if default_config.exists():
            config_checks = validate_config(default_config)
            checks.extend(config_checks)
            if not any(check.status == "error" for check in config_checks):
                config_data = load_json(default_config)
        else:
            checks.append(Check("config", "warning", "config/default.json not found; runtime defaults will be used"))

    checks.extend(check_environment(config_data))
    checks.extend(check_workspace(workspace, explain_paths=explain_paths, create=create_workspace))
    checks.sort(key=lambda item: (_status_order(item.status), item.name, item.message))
    return checks


def print_checks(checks: list[Check], as_json: bool = False) -> None:
    if as_json:
        print(json.dumps([check.__dict__ for check in checks], indent=2))
        return
    for check in checks:
        print(f"[{check.status.upper()}] {check.name}: {check.message}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Check Litminer installation, environment, and config.")
    parser.add_argument("--config", type=Path, default=None, help="Runtime config JSON to validate")
    parser.add_argument("--skill-dir", type=Path, default=None, help="Skill install directory to inspect")
    parser.add_argument("--workspace", type=Path, default=None, help="Expected MCP workspace root")
    parser.add_argument("--explain-path", action="append", default=None,
                        help="Explain how a path resolves against the workspace root; repeatable")
    parser.add_argument("--create-workspace", action="store_true",
                        help="Create the workspace root before checking writability")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    args = parser.parse_args()

    checks = run_checks(
        args.config,
        skill_dir=args.skill_dir,
        workspace=args.workspace,
        explain_paths=args.explain_path,
        create_workspace=args.create_workspace,
    )
    print_checks(checks, as_json=args.json)
    if any(check.status == "error" for check in checks):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
