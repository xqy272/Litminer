"""Validate Codex and Claude Code adapters against the live Litminer contract."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import time
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from litminer.contracts import tool_contracts
from litminer.sources.mcp import server as mcp_server


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = PROJECT_ROOT / "config" / "agent_clients.json"


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _check(condition: bool, code: str, message: str, checks: list[dict[str, Any]]) -> None:
    checks.append({"code": code, "passed": bool(condition), "message": message})


def _validate_template(path: Path) -> dict[str, Any]:
    if path.suffix == ".json":
        payload = _load_json(path)
    elif path.suffix == ".toml":
        payload = tomllib.loads(path.read_text(encoding="utf-8"))
    else:
        raise ValueError(f"unsupported Agent template: {path}")
    raw = path.read_text(encoding="utf-8")
    if "sk-" in raw or "api_key=" in raw.lower():
        raise ValueError(f"possible committed secret in {path}")
    return payload


def deterministic_acceptance(agent: str, output_dir: Path) -> dict[str, Any]:
    manifest = _load_json(MANIFEST_PATH)
    client = manifest["primary_clients"][agent]
    guide_path = PROJECT_ROOT / client["guide"]
    contract_path = PROJECT_ROOT / manifest["shared_contract"]
    skill_path = PROJECT_ROOT / "SKILL.md"
    template_path = PROJECT_ROOT / client["mcp_template"]
    expected_tools = list(manifest["default_mcp_tools"])
    expected_artifacts = list(manifest["artifact_read_order"])
    checks: list[dict[str, Any]] = []

    _check(platform.system() in manifest["supported_systems"], "supported_system",
           f"native platform is {platform.system()}", checks)
    for path, code in (
        (guide_path, "guide_exists"),
        (contract_path, "shared_contract_exists"),
        (skill_path, "skill_exists"),
        (template_path, "mcp_template_exists"),
    ):
        _check(path.exists(), code, str(path), checks)

    guide = guide_path.read_text(encoding="utf-8") if guide_path.exists() else ""
    contract = contract_path.read_text(encoding="utf-8") if contract_path.exists() else ""
    skill = skill_path.read_text(encoding="utf-8") if skill_path.exists() else ""
    _check("Contract-Version: 1" in guide, "guide_contract_version", str(guide_path), checks)
    _check("Contract-Version: 1" in contract, "shared_contract_version", str(contract_path), checks)
    _check("Codex" in skill and "Claude Code" in skill, "skill_clients", "SKILL.md names both clients", checks)

    tools_response = mcp_server.handle_request({
        "jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {},
    }) or {}
    actual_tools = [
        item["name"]
        for item in (tools_response.get("result") or {}).get("tools", [])
    ]
    _check(actual_tools == expected_tools, "default_tools_match",
           f"expected={expected_tools}; actual={actual_tools}", checks)
    for tool_name in expected_tools:
        _check(tool_name in guide, f"guide_tool_{tool_name}", tool_name, checks)
        _check(tool_name in contract, f"contract_tool_{tool_name}", tool_name, checks)
        _check(tool_contracts.schema_for(tool_name) is not None or tool_name == "litminer_workspace_doctor",
               f"schema_{tool_name}", tool_name, checks)
    for artifact in expected_artifacts:
        _check(artifact in guide, f"guide_artifact_{artifact}", artifact, checks)
        _check(artifact in contract, f"contract_artifact_{artifact}", artifact, checks)

    try:
        template = _validate_template(template_path)
        template_ok = bool(template)
        template_message = str(template_path)
    except Exception as exc:
        template_ok = False
        template_message = f"{type(exc).__name__}: {exc}"
    _check(template_ok, "template_parses_without_secret", template_message, checks)

    invalid = mcp_server.handle_request({
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {"name": "litminer_start_run", "arguments": {}},
    }) or {}
    structured = (invalid.get("result") or {}).get("structuredContent") or {}
    _check(
        (invalid.get("result") or {}).get("isError") is True
        and (structured.get("error") or {}).get("class") == "validation",
        "structured_invalid_input",
        json.dumps(structured, ensure_ascii=False),
        checks,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    result = {
        "schema_version": 1,
        "agent": agent,
        "mode": "deterministic",
        "platform": platform.system(),
        "python_version": platform.python_version(),
        "passed": all(item["passed"] for item in checks),
        "checks": checks,
    }
    (output_dir / f"{agent}_deterministic.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return result


def _real_prompt(agent: str) -> str:
    guide = "AGENTS.md" if agent == "codex" else "CLAUDE.md"
    return (
        f"Read {guide}, SKILL.md, and references/agent-operating-contract.md. "
        "Do not modify files and do not use network tools. Return only a JSON object with "
        "client, contract_version, default_tool_count, first_artifact, and supported_systems. "
        f"Set client to {agent}, contract_version to 1, default_tool_count to 9, "
        "first_artifact to run_outcome.json, and supported_systems to Windows and macOS "
        "if and only if those values are supported by the files."
    )


def _executable_command(executable: str, arguments: list[str]) -> list[str]:
    suffix = Path(executable).suffix.lower()
    if os.name == "nt" and suffix in {".cmd", ".bat"}:
        command_line = subprocess.list2cmdline([executable, *arguments])
        return [
            os.environ.get("COMSPEC", "cmd.exe"),
            "/d",
            "/s",
            "/c",
            command_line,
        ]
    if os.name == "nt" and suffix == ".ps1":
        return [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            executable,
            *arguments,
        ]
    return [executable, *arguments]


def _codex_arguments(
    schema_path: Path,
    response_path: Path,
) -> list[str]:
    return [
        "-a",
        "never",
        "exec",
        "--ephemeral",
        "-C",
        str(PROJECT_ROOT),
        "-s",
        "read-only",
        "--output-schema",
        str(schema_path),
        "-o",
        str(response_path),
        "-",
    ]


def _contract_payload(value: Any) -> dict[str, Any] | None:
    required = {
        "client",
        "contract_version",
        "default_tool_count",
        "first_artifact",
        "supported_systems",
    }
    if isinstance(value, dict):
        if required.issubset(value):
            return value
        for key in (
            "structured_output",
            "structuredOutput",
            "result",
            "output",
            "content",
        ):
            if key in value:
                found = _contract_payload(value[key])
                if found is not None:
                    return found
        for item in value.values():
            if isinstance(item, (dict, list)):
                found = _contract_payload(item)
                if found is not None:
                    return found
        return None
    if isinstance(value, list):
        for item in value:
            found = _contract_payload(item)
            if found is not None:
                return found
        return None
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return None
        return _contract_payload(decoded)
    return None


def _parse_contract_payload(raw: str) -> dict[str, Any] | None:
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        decoded = None
    found = _contract_payload(decoded)
    if found is not None:
        return found
    for line in reversed(raw.splitlines()):
        try:
            decoded = json.loads(line)
        except json.JSONDecodeError:
            continue
        found = _contract_payload(decoded)
        if found is not None:
            return found
    return None


def _valid_contract_payload(agent: str, payload: dict[str, Any] | None) -> bool:
    if payload is None:
        return False
    systems = {
        str(item).strip().lower()
        for item in payload.get("supported_systems") or []
    }
    return (
        str(payload.get("client") or "").strip().lower() == agent
        and payload.get("contract_version") == 1
        and payload.get("default_tool_count") == 9
        and payload.get("first_artifact") == "run_outcome.json"
        and systems == {"windows", "macos"}
    )


def real_acceptance(agent: str, output_dir: Path, timeout: float, allow_missing: bool) -> dict[str, Any]:
    command_name = "codex" if agent == "codex" else "claude"
    executable = shutil.which(command_name)
    output_dir.mkdir(parents=True, exist_ok=True)
    if not executable:
        result = {
            "schema_version": 1,
            "agent": agent,
            "mode": "real",
            "status": "skipped",
            "passed": bool(allow_missing),
            "reason": f"{command_name} CLI is not installed",
        }
    else:
        schema = {
            "type": "object",
            "properties": {
                "client": {"type": "string"},
                "contract_version": {"type": "integer"},
                "default_tool_count": {"type": "integer"},
                "first_artifact": {"type": "string"},
                "supported_systems": {"type": "array", "items": {"type": "string"}},
            },
            "required": [
                "client", "contract_version", "default_tool_count",
                "first_artifact", "supported_systems",
            ],
            "additionalProperties": False,
        }
        schema_path = output_dir / f"{agent}_response_schema.json"
        schema_path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
        response_path = output_dir / f"{agent}_response.json"
        prompt = _real_prompt(agent)
        stdin_text: str | None = None
        if agent == "codex":
            command = _executable_command(
                executable,
                _codex_arguments(schema_path, response_path),
            )
            stdin_text = prompt
        else:
            command = _executable_command(executable, [
                "-p", "--output-format", "json",
                "--json-schema", json.dumps(schema), "--permission-mode", "plan",
                "--tools", "Read,Glob", "--no-session-persistence",
                "--max-budget-usd", "0.20", prompt,
            ])
        started = time.monotonic()
        try:
            completed = subprocess.run(
                command,
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                input=stdin_text,
                timeout=timeout,
                check=False,
            )
            raw = response_path.read_text(encoding="utf-8") if response_path.exists() else completed.stdout
            parsed = _parse_contract_payload(raw)
            passed = (
                completed.returncode == 0
                and _valid_contract_payload(agent, parsed)
            )
            result = {
                "schema_version": 1,
                "agent": agent,
                "mode": "real",
                "status": "passed" if passed else "failed",
                "passed": passed,
                "duration_seconds": round(time.monotonic() - started, 6),
                "command": command,
                "exit_code": completed.returncode,
                "stdout_tail": completed.stdout[-12000:],
                "stderr_tail": completed.stderr[-12000:],
                "response": raw[-12000:],
                "parsed_response": parsed,
            }
        except subprocess.TimeoutExpired as exc:
            result = {
                "schema_version": 1,
                "agent": agent,
                "mode": "real",
                "status": "failed",
                "passed": False,
                "reason": f"timeout after {timeout:g}s",
                "stdout_tail": str(exc.stdout or "")[-12000:],
                "stderr_tail": str(exc.stderr or "")[-12000:],
            }
    (output_dir / f"{agent}_real.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", choices=["codex", "claude", "all"], default="all")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--real", action="store_true")
    parser.add_argument("--allow-missing-client", action="store_true")
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    args = parser.parse_args()

    agents = ["codex", "claude"] if args.agent == "all" else [args.agent]
    results: list[dict[str, Any]] = []
    for agent in agents:
        results.append(deterministic_acceptance(agent, args.output_dir))
        if args.real:
            results.append(real_acceptance(
                agent, args.output_dir, args.timeout_seconds, args.allow_missing_client,
            ))
    summary = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "passed": all(item["passed"] for item in results),
        "results": results,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = args.output_dir / "agent_client_acceptance.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({
        "passed": summary["passed"],
        "report": str(summary_path),
        "agents": agents,
        "real": args.real,
    }, ensure_ascii=False))
    raise SystemExit(0 if summary["passed"] else 1)


if __name__ == "__main__":
    main()
