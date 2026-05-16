#!/usr/bin/env python3
"""Generate a first-run bootstrap report for Windows-heavy Agent environments."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import sys
from pathlib import Path
from typing import Any

from litminer.engine import workspace
from litminer.engine.common import write_text_atomic


def build_report(workspace_root: Path | None = None) -> dict[str, Any]:
    root = (workspace_root or workspace.workspace_root()).resolve(strict=False)
    python_commands = [name for name in ("python", "py", "python3") if shutil.which(name)]
    contact_email = os.environ.get("LITMINER_CONTACT_EMAIL", "")
    recommended_commands = [
        "python -m litminer.engine.doctor",
        "python -m litminer.engine.offline_smoke",
        "python -m litminer.sources.mcp.test_server",
    ]
    if os.name == "nt" and "py" in python_commands:
        recommended_commands = [
            command.replace("python -m", "py -3 -m") for command in recommended_commands
        ]
    report: dict[str, Any] = {
        "schema_version": 1,
        "platform": platform.platform(),
        "python_executable": sys.executable,
        "python_version": platform.python_version(),
        "available_python_commands": python_commands,
        "workspace_root": str(root),
        "workspace_exists": root.exists(),
        "workspace_writable": os.access(root, os.W_OK) if root.exists() else False,
        "contact_email_configured": bool(contact_email),
        "recommended_environment": {
            "LITMINER_WORKSPACE_ROOT": str(root),
            "LITMINER_CONTACT_EMAIL": "you@example.org" if not contact_email else contact_email,
            "PowerShell_OutputEncoding": "UTF-8",
        },
        "recommended_commands": recommended_commands,
    }
    return report


def write_reports(output_dir: Path, workspace_root: Path | None = None) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    report = build_report(workspace_root)
    json_path = output_dir / "bootstrap_report.json"
    md_path = output_dir / "bootstrap_report.md"
    write_text_atomic(json_path, json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    lines = [
        "# Litminer Bootstrap Report",
        "",
        f"- platform: `{report['platform']}`",
        f"- python: `{report['python_version']}` at `{report['python_executable']}`",
        f"- workspace_root: `{report['workspace_root']}`",
        f"- workspace_writable: `{report['workspace_writable']}`",
        f"- contact_email_configured: `{report['contact_email_configured']}`",
        "",
        "## Recommended PowerShell Environment",
        "",
        "```powershell",
        f"$env:LITMINER_WORKSPACE_ROOT = \"{report['recommended_environment']['LITMINER_WORKSPACE_ROOT']}\"",
        "$env:LITMINER_CONTACT_EMAIL = \"you@example.org\"",
        "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8",
        "```",
        "",
        "## Recommended Checks",
        "",
        *[f"- `{command}`" for command in report["recommended_commands"]],
        "",
    ]
    write_text_atomic(md_path, "\n".join(lines))
    return {"json": str(json_path), "markdown": str(md_path)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Litminer first-run bootstrap reports.")
    parser.add_argument("--workspace", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=Path(".litminer/bootstrap"))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    outputs = write_reports(args.output_dir, workspace_root=args.workspace)
    if args.json:
        print(json.dumps(outputs, indent=2))
    else:
        print(f"Bootstrap report: {outputs['markdown']}")
        print(f"Bootstrap JSON: {outputs['json']}")


if __name__ == "__main__":
    main()
