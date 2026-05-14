#!/usr/bin/env python3
"""Installation, environment, and runtime-config checks for Litminer."""

from __future__ import annotations

import argparse
import json
import os
import sys
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
        "unpaywall_sleep": (int, float),
    },
    "outputs": {
        "default_output_dir": (str,),
        "screenshot_root": (str,),
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
        for key in ("max_results_per_query", "semantic_query_limit", "semantic_max_results", "publisher_probe_limit"):
            value = limits.get(key)
            if value is not None and isinstance(value, int) and value < 0:
                checks.append(Check("config", "error", f"{key} must not be negative"))
        for key in ("publisher_probe_sleep", "unpaywall_sleep"):
            value = limits.get(key)
            if value is not None and isinstance(value, (int, float)) and value < 0:
                checks.append(Check("config", "error", f"{key} must not be negative"))

    return checks


def check_installation(skill_dir: Path | None = None) -> list[Check]:
    checks: list[Check] = []
    checks.append(
        Check(
            "python",
            "ok" if sys.version_info >= (3, 10) else "error",
            f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
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


def check_workspace(workspace: Path | None = None) -> list[Check]:
    configured = (
        workspace
        or (
            Path(os.environ[workspace_paths.WORKSPACE_ENV])
            if os.environ.get(workspace_paths.WORKSPACE_ENV)
            else None
        )
    )
    if configured is None:
        root = workspace_paths.workspace_root()
        return [
            Check(
                "workspace",
                "ok",
                f"{workspace_paths.WORKSPACE_ENV} is not set; default runtime outputs use {root / '.litminer'}",
            )
        ]
    resolved = configured.expanduser().resolve(strict=False)
    if resolved.exists() and resolved.is_dir():
        return [Check("workspace", "ok", f"workspace exists: {resolved}; default outputs use {resolved / '.litminer'}")]
    return [Check("workspace", "warning", f"workspace directory does not exist yet: {resolved}")]


def run_checks(config_path: Path | None = None,
               skill_dir: Path | None = None,
               workspace: Path | None = None) -> list[Check]:
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
    checks.extend(check_workspace(workspace))
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
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    args = parser.parse_args()

    checks = run_checks(args.config, skill_dir=args.skill_dir, workspace=args.workspace)
    print_checks(checks, as_json=args.json)
    if any(check.status == "error" for check in checks):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
