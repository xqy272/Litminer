"""Validate Codex and Claude Code adapters against the live Litminer contract."""

from __future__ import annotations

import argparse
import ast
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

try:
    import tomllib as _tomllib
except ModuleNotFoundError:  # Python 3.10
    _tomllib = None

from litminer.contracts import tool_contracts
from litminer.sources.mcp import server as mcp_server


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = PROJECT_ROOT / "config" / "agent_clients.json"
MCP_SERVER_PATH = PROJECT_ROOT / "litminer" / "sources" / "mcp" / "server.py"
REAL_ACCEPTANCE_TOOLS = (
    "litminer_workspace_doctor",
    "litminer_plan_run",
)
SENSITIVE_PROVIDER_ENV_SUFFIXES = (
    "_API_KEY",
    "_EMAIL",
    "_MAILTO",
)
CLAUDE_DEBUG_EVIDENCE_MARKERS = (
    'MCP server "litminer": Starting connection',
    'MCP server "litminer": Successfully connected',
    'MCP server "litminer": Connection established',
    'MCP server "litminer": Connection failed',
    'MCP server "litminer": Calling MCP tool:',
    "MCP server \"litminer\": Tool 'litminer_",
    'MCP server "litminer" Skipping tool "litminer_',
    "tool=mcp__litminer__",
)


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _check(condition: bool, code: str, message: str, checks: list[dict[str, Any]]) -> None:
    checks.append({"code": code, "passed": bool(condition), "message": message})


def _balanced_toml_value(value: str) -> bool:
    return (
        value.count("[") == value.count("]")
        and value.count("{") == value.count("}")
    )


def _toml_assignment_map(raw: str, section_name: str) -> dict[str, str]:
    """Read the small MCP template subset used by Litminer on Python 3.10."""
    assignments: dict[str, str] = {}
    lines = raw.splitlines()
    current_section = ""
    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        index += 1
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            current_section = stripped[1:-1].strip()
            continue
        if current_section != section_name:
            continue
        if "=" not in stripped:
            raise ValueError(f"invalid TOML assignment in [{section_name}]: {stripped}")
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip()
        while not _balanced_toml_value(value) and index < len(lines):
            value += "\n" + lines[index].strip()
            index += 1
        if not _balanced_toml_value(value):
            raise ValueError(f"unbalanced TOML value for {key}")
        assignments[key] = value
    return assignments


def _parse_inline_string_table(value: str) -> dict[str, str]:
    stripped = value.strip()
    if not (stripped.startswith("{") and stripped.endswith("}")):
        raise ValueError("expected TOML inline table")
    body = stripped[1:-1]
    pattern = re.compile(
        r"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(\"(?:\\.|[^\"])*\")"
    )
    parsed: dict[str, str] = {}
    consumed: list[tuple[int, int]] = []
    for match in pattern.finditer(body):
        parsed[match.group(1)] = ast.literal_eval(match.group(2))
        consumed.append(match.span())
    remainder_parts: list[str] = []
    cursor = 0
    for start, end in consumed:
        remainder_parts.append(body[cursor:start])
        cursor = end
    remainder_parts.append(body[cursor:])
    remainder = "".join(remainder_parts).replace(",", "").strip()
    if remainder or not parsed:
        raise ValueError("unsupported TOML inline table value")
    return parsed


def _load_toml_template_compat(raw: str) -> dict[str, Any]:
    section = "mcp_servers.litminer"
    assignments = _toml_assignment_map(raw, section)
    required = {"command", "args", "cwd", "env"}
    missing = sorted(required - assignments.keys())
    if missing:
        raise ValueError(f"missing TOML keys in [{section}]: {', '.join(missing)}")
    command = ast.literal_eval(assignments["command"])
    args = ast.literal_eval(assignments["args"])
    cwd = ast.literal_eval(assignments["cwd"])
    env = _parse_inline_string_table(assignments["env"])
    env_vars = ast.literal_eval(assignments.get("env_vars", "[]"))
    if not isinstance(command, str) or not command:
        raise ValueError("MCP command must be a non-empty string")
    if not isinstance(cwd, str) or not cwd:
        raise ValueError("MCP cwd must be a non-empty string")
    if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
        raise ValueError("MCP args must be a string list")
    if not isinstance(env_vars, list) or not all(isinstance(item, str) for item in env_vars):
        raise ValueError("MCP env_vars must be a string list")
    return {
        "mcp_servers": {
            "litminer": {
                "command": command,
                "args": args,
                "cwd": cwd,
                "env": env,
                "env_vars": env_vars,
            }
        }
    }


def _validate_template(path: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        payload = _load_json(path)
    elif path.suffix == ".toml":
        payload = (
            _tomllib.loads(raw)
            if _tomllib is not None
            else _load_toml_template_compat(raw)
        )
    else:
        raise ValueError(f"unsupported Agent template: {path}")
    if "sk-" in raw or "api_key=" in raw.lower():
        raise ValueError(f"possible committed secret in {path}")
    if path.suffix == ".toml":
        servers = payload.get("mcp_servers")
    else:
        servers = payload.get("mcpServers")
    if not isinstance(servers, dict):
        raise ValueError(f"missing MCP server map in {path}")
    server = servers.get("litminer")
    if not isinstance(server, dict):
        raise ValueError(f"missing Litminer MCP server entry in {path}")
    if not isinstance(server.get("command"), str) or not server["command"]:
        raise ValueError(f"Litminer MCP command is missing in {path}")
    args = server.get("args")
    if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
        raise ValueError(f"Litminer MCP args must be a string list in {path}")
    if not isinstance(server.get("cwd"), str) or not server["cwd"]:
        raise ValueError(f"Litminer MCP cwd is missing in {path}")
    if path.suffix == ".json" and server.get("type") != "stdio":
        raise ValueError(f"Claude Litminer MCP transport must be stdio in {path}")
    env = server.get("env") or {}
    if not isinstance(env, dict):
        raise ValueError(f"Litminer MCP env must be an object in {path}")
    if not str(env.get("LITMINER_WORKSPACE_ROOT") or "").strip():
        raise ValueError(f"LITMINER_WORKSPACE_ROOT is missing in {path}")
    if env.get("LITMINER_MCP_TOOL_PROFILE") != "workflow":
        raise ValueError(f"Litminer MCP template must use workflow profile in {path}")
    persisted_sensitive = sorted(
        str(name)
        for name in env
        if str(name).upper().endswith(SENSITIVE_PROVIDER_ENV_SUFFIXES)
    )
    if persisted_sensitive:
        raise ValueError(
            "provider credentials/contact values must be inherited from the "
            f"launch environment, not persisted in {path}: "
            + ", ".join(persisted_sensitive)
        )
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
    expected_protocols = set(manifest["supported_mcp_protocol_versions"])
    forbidden_schema_keywords = set(
        manifest["client_schema_forbidden_top_level_keywords"]
    )
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
    tool_entries = {
        item["name"]: item
        for item in (tools_response.get("result") or {}).get("tools", [])
    }
    for tool_name in expected_tools:
        _check(tool_name in guide, f"guide_tool_{tool_name}", tool_name, checks)
        _check(tool_name in contract, f"contract_tool_{tool_name}", tool_name, checks)
        _check(tool_contracts.schema_for(tool_name) is not None or tool_name == "litminer_workspace_doctor",
               f"schema_{tool_name}", tool_name, checks)
        advertised_schema = (tool_entries.get(tool_name) or {}).get("inputSchema") or {}
        incompatible = sorted(forbidden_schema_keywords.intersection(advertised_schema))
        _check(
            not incompatible,
            f"client_schema_{tool_name}",
            f"forbidden_top_level_keywords={incompatible}",
            checks,
        )
    actual_protocols = set(mcp_server.SUPPORTED_PROTOCOL_VERSIONS)
    _check(
        actual_protocols == expected_protocols,
        "protocol_manifest_match",
        f"expected={sorted(expected_protocols)}; actual={sorted(actual_protocols)}",
        checks,
    )
    for version in sorted(expected_protocols):
        initialized = mcp_server.handle_request({
            "jsonrpc": "2.0",
            "id": f"protocol-{version}",
            "method": "initialize",
            "params": {"protocolVersion": version},
        }) or {}
        negotiated = (initialized.get("result") or {}).get("protocolVersion")
        _check(
            negotiated == version,
            f"protocol_{version}",
            json.dumps(initialized, ensure_ascii=False),
            checks,
        )
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
    return (
        "Use the Litminer MCP server configured for this invocation. Do not use shell, "
        "filesystem, browser, web, or any non-Litminer tool. First call "
        "litminer_workspace_doctor with an empty object. Then call litminer_plan_run "
        "with queries=[\"litminer agent acceptance\"], mode=\"fast\", and "
        "output_dir=\".litminer/acceptance/real-agent-plan\". Do not start a run. "
        "If either MCP tool is deferred, use the built-in ToolSearch tool to load it. "
        "Return exactly one raw JSON object without Markdown fences or commentary. "
        "The object must contain only client, tools_called, doctor_ok, and plan_ok. "
        "Set tools_called to the two exact tool names in call order. Set doctor_ok and "
        "plan_ok from the actual tool results, never from intent. "
        f"Set client to {agent}."
    )


def _executable_command(executable: str, arguments: list[str]) -> list[str]:
    suffix = os.path.splitext(executable)[1].lower()
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


def _client_version(executable: str) -> str:
    try:
        completed = subprocess.run(
            _executable_command(executable, ["--version"]),
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    value = (completed.stdout or completed.stderr).strip()
    return value.splitlines()[0] if value else ""


def _codex_config_mcp_server_names() -> list[str]:
    config_home = Path(
        os.environ.get("CODEX_HOME") or Path.home() / ".codex"
    )
    config_path = config_home / "config.toml"
    try:
        raw = config_path.read_text(encoding="utf-8")
    except OSError:
        return []
    if _tomllib is not None:
        try:
            payload = _tomllib.loads(raw)
        except Exception:
            payload = {}
        servers = payload.get("mcp_servers")
        if isinstance(servers, dict):
            return [str(name) for name in servers]
    names: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped.startswith("[mcp_servers.") or not stripped.endswith("]"):
            continue
        suffix = stripped[len("[mcp_servers."):-1]
        name_token = suffix.split(".", 1)[0]
        try:
            name = ast.literal_eval(name_token) if name_token.startswith(('"', "'")) else name_token
        except (SyntaxError, ValueError):
            continue
        if isinstance(name, str) and name and name not in names:
            names.append(name)
    return names


def _codex_config_key(name: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_-]+", name):
        return name
    return json.dumps(name)


def _codex_arguments(
    schema_path: Path,
    response_path: Path,
    disabled_servers: Sequence[str] = (),
) -> list[str]:
    def literal_path(path: Path) -> str:
        value = path.resolve().as_posix()
        if "'" in value:
            raise ValueError("Codex acceptance paths may not contain a single quote")
        return f"'{value}'"

    python_value = literal_path(Path(sys.executable))
    server_value = f"[{literal_path(MCP_SERVER_PATH)}]"
    cwd_value = literal_path(PROJECT_ROOT)
    workspace_value = literal_path(PROJECT_ROOT)
    arguments = [
        "-a",
        "never",
    ]
    for server_name in disabled_servers:
        if server_name == "litminer":
            continue
        arguments.extend([
            "-c",
            (
                "mcp_servers."
                f"{_codex_config_key(server_name)}.enabled=false"
            ),
        ])
    arguments.extend([
        "-c",
        "mcp_servers.litminer.enabled=true",
        "-c",
        f"mcp_servers.litminer.command={python_value}",
        "-c",
        f"mcp_servers.litminer.args={server_value}",
        "-c",
        f"mcp_servers.litminer.cwd={cwd_value}",
        "-c",
        (
            "mcp_servers.litminer.env={"
            f"LITMINER_WORKSPACE_ROOT={workspace_value},"
            "LITMINER_MCP_TOOL_PROFILE='workflow'}"
        ),
        "-c",
        "mcp_servers.litminer.default_tools_approval_mode='prompt'",
        "-c",
        "mcp_servers.litminer.tools.litminer_workspace_doctor.approval_mode='approve'",
        "-c",
        "mcp_servers.litminer.tools.litminer_plan_run.approval_mode='approve'",
        "exec",
        "--ephemeral",
        "-C",
        str(PROJECT_ROOT),
        "-s",
        "read-only",
        "--json",
        "--output-schema",
        str(schema_path),
        "-o",
        str(response_path),
        "-",
    ])
    return arguments


def _claude_mcp_config(output_dir: Path) -> Path:
    path = output_dir / "claude_mcp.json"
    payload = {
        "mcpServers": {
            "litminer": {
                "type": "stdio",
                "command": str(Path(sys.executable).resolve()),
                "args": [str(MCP_SERVER_PATH.resolve())],
                "cwd": str(PROJECT_ROOT.resolve()),
                "env": {
                    "LITMINER_WORKSPACE_ROOT": str(PROJECT_ROOT.resolve()),
                    "LITMINER_MCP_TOOL_PROFILE": "workflow",
                },
            }
        }
    }
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


def _claude_arguments(
    mcp_config_path: Path,
    debug_path: Path,
    prompt: str,
) -> list[str]:
    allowed_tools = [
        "ToolSearch",
        *(f"mcp__litminer__{name}" for name in REAL_ACCEPTANCE_TOOLS),
    ]
    return [
        "-p",
        "--output-format",
        "json",
        "--permission-mode",
        "dontAsk",
        "--allowedTools",
        ",".join(allowed_tools),
        "--tools",
        "ToolSearch",
        "--mcp-config",
        str(mcp_config_path),
        "--strict-mcp-config",
        "--debug",
        "mcp",
        "--debug-file",
        str(debug_path),
        "--no-session-persistence",
        "--max-budget-usd",
        "0.20",
        prompt,
    ]


def _real_response_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "client": {"type": "string"},
            "tools_called": {
                "type": "array",
                "items": {"type": "string"},
            },
            "doctor_ok": {"type": "boolean"},
            "plan_ok": {"type": "boolean"},
        },
        "required": [
            "client",
            "tools_called",
            "doctor_ok",
            "plan_ok",
        ],
        "additionalProperties": False,
    }


def _contract_payload(value: Any) -> dict[str, Any] | None:
    required = {
        "client",
        "tools_called",
        "doctor_ok",
        "plan_ok",
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
        stripped = value.strip()
        if stripped.startswith("```") and stripped.endswith("```"):
            lines = stripped.splitlines()
            if len(lines) >= 3:
                stripped = "\n".join(lines[1:-1]).strip()
        try:
            decoded = json.loads(stripped)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", stripped, flags=re.DOTALL)
            if match is None:
                return None
            try:
                decoded = json.loads(match.group(0))
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


def _canonical_response_tool_name(value: Any) -> str:
    name = str(value or "")
    prefix = "mcp__litminer__"
    return name[len(prefix):] if name.startswith(prefix) else name


def _valid_contract_payload(agent: str, payload: dict[str, Any] | None) -> bool:
    if payload is None:
        return False
    tools_called = payload.get("tools_called")
    if not isinstance(tools_called, list):
        return False
    return (
        str(payload.get("client") or "").strip().lower() == agent
        and [
            _canonical_response_tool_name(item)
            for item in tools_called
        ] == list(REAL_ACCEPTANCE_TOOLS)
        and payload.get("doctor_ok") is True
        and payload.get("plan_ok") is True
    )


def _sanitized_stderr_tail(value: str, limit: int = 12000) -> str:
    sanitized = re.sub(
        r"\\u003c(?:!doctype|html)\b.*?\\u003c/html\\u003e",
        "<html response omitted>",
        value,
        flags=re.IGNORECASE | re.DOTALL,
    )
    sanitized = re.sub(
        r"<html\b.*?</html>",
        "<html response omitted>",
        sanitized,
        flags=re.IGNORECASE | re.DOTALL,
    )
    sanitized = re.sub(
        r"\[IP:[^\]]+\]",
        "[gateway metadata omitted]",
        sanitized,
        flags=re.IGNORECASE,
    )
    sanitized = re.sub(
        r"\bRay ID\s*[:=]\s*[A-Za-z0-9_-]+",
        "Ray ID:[omitted]",
        sanitized,
        flags=re.IGNORECASE,
    )
    return sanitized[-limit:]


def _mcp_result_ok(result: Any) -> bool:
    if not isinstance(result, dict):
        return False
    if result.get("isError") is True or result.get("is_error") is True:
        return False
    structured = (
        result.get("structuredContent")
        or result.get("structured_content")
    )
    if isinstance(structured, dict):
        return structured.get("ok") is True
    content = result.get("content")
    if isinstance(content, list):
        for item in content:
            if not isinstance(item, dict) or item.get("type") != "text":
                continue
            try:
                payload = json.loads(str(item.get("text") or ""))
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                return payload.get("ok") is True
    return False


def _codex_mcp_tool_events(raw: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in raw.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "item.completed":
            continue
        item = event.get("item") or {}
        if item.get("type") != "mcp_tool_call":
            continue
        tool_name = str(item.get("tool") or item.get("name") or "")
        if not tool_name:
            continue
        events.append({
            "server": str(item.get("server") or ""),
            "tool": tool_name,
            "status": str(item.get("status") or ""),
            "error": item.get("error"),
            "result_ok": _mcp_result_ok(item.get("result")),
        })
    return events


def _codex_completed_mcp_tools(raw: str) -> list[str]:
    completed: list[str] = []
    for event in _codex_mcp_tool_events(raw):
        tool_name = event["tool"]
        if (
            event["status"] in {"completed", "success"}
            and not event["error"]
            and event["server"] == "litminer"
            and event["result_ok"] is True
            and tool_name in REAL_ACCEPTANCE_TOOLS
            and tool_name not in completed
        ):
            completed.append(tool_name)
    return completed


def _claude_debug_evidence_lines(debug_text: str) -> list[str]:
    return [
        _sanitized_stderr_tail(line, 2000)
        for line in debug_text.splitlines()
        if any(marker in line for marker in CLAUDE_DEBUG_EVIDENCE_MARKERS)
    ][-100:]


def _mcp_evidence(
    agent: str,
    *,
    stdout: str,
    stderr: str,
    debug_path: Path | None,
) -> dict[str, Any]:
    startup_errors = [
        marker
        for marker in (
            "Unsupported protocolVersion",
            'MCP server "litminer" Connection failed',
        )
        if marker in stdout or marker in stderr
    ]
    skipped_tools: list[str] = []
    completed_tools: list[str] = []
    attempted_tools: list[str] = []
    failed_tools: list[str] = []
    unexpected_servers: list[str] = []
    connected = False
    debug_text = ""
    debug_evidence_lines: list[str] = []
    if debug_path is not None and debug_path.exists():
        debug_text = debug_path.read_text(encoding="utf-8", errors="replace")
    if agent == "claude":
        debug_evidence_lines = _claude_debug_evidence_lines(debug_text)
        connected = 'MCP server "litminer": Successfully connected' in debug_text
        skipped_tools = re.findall(r'Skipping tool "([^"]+)"', debug_text)
        attempted_tools = re.findall(r"Tool '([^']+)' completed successfully", debug_text)
        failed_tools = re.findall(r"Tool '([^']+)' (?:failed|errored)", debug_text)
        for tool_name in attempted_tools:
            if tool_name in REAL_ACCEPTANCE_TOOLS and tool_name not in completed_tools:
                completed_tools.append(tool_name)
        if 'MCP server "litminer" Connection failed' in debug_text:
            startup_errors.append('MCP server "litminer" Connection failed')
    else:
        codex_events = _codex_mcp_tool_events(stdout)
        attempted_tools = [event["tool"] for event in codex_events]
        failed_tools = [
            event["tool"]
            for event in codex_events
            if (
                event["status"] not in {"completed", "success"}
                or event["error"]
                or event["server"] != "litminer"
                or event["result_ok"] is not True
            )
        ]
        unexpected_servers = sorted({
            event["server"] or "<missing>"
            for event in codex_events
            if event["server"] != "litminer"
        })
        completed_tools = _codex_completed_mcp_tools(stdout)
        connected = not startup_errors and bool(attempted_tools)
    unexpected_tools = sorted({
        tool_name
        for tool_name in attempted_tools
        if tool_name not in REAL_ACCEPTANCE_TOOLS
    })
    return {
        "connected": connected,
        "completed_tools": completed_tools,
        "attempted_tools": attempted_tools,
        "failed_tools": sorted(set(failed_tools)),
        "unexpected_tools": unexpected_tools,
        "unexpected_servers": unexpected_servers,
        "skipped_tools": sorted(set(skipped_tools)),
        "startup_errors": sorted(set(startup_errors)),
        "debug_evidence_lines": debug_evidence_lines,
        "passed": (
            connected
            and attempted_tools == list(REAL_ACCEPTANCE_TOOLS)
            and completed_tools == list(REAL_ACCEPTANCE_TOOLS)
            and not failed_tools
            and not unexpected_tools
            and not unexpected_servers
            and not skipped_tools
            and not startup_errors
        ),
    }


def _real_failure_class(
    *,
    returncode: int,
    stdout: str,
    stderr: str,
    parsed: dict[str, Any] | None,
    evidence: dict[str, Any],
    agent: str,
) -> str:
    if (
        returncode == 0
        and _valid_contract_payload(agent, parsed)
        and evidence["passed"]
    ):
        return ""
    combined = f"{stdout}\n{stderr}".lower()
    if (
        "unsupported_country_region_territory" in combined
        or "country, region, or territory not supported" in combined
    ):
        return "client_auth_or_region"
    if any(
        marker in combined
        for marker in (
            "operation not permitted",
            "attempt to write a readonly database",
            "access is denied",
        )
    ):
        return "client_process_environment"
    if evidence.get("startup_errors"):
        return "mcp_startup"
    if (
        evidence.get("failed_tools")
        or evidence.get("unexpected_tools")
        or evidence.get("unexpected_servers")
    ):
        return "mcp_tool_execution"
    if returncode != 0:
        return "client_process"
    if parsed is None or not _valid_contract_payload(agent, parsed):
        return "agent_response_contract"
    if not evidence["passed"]:
        return "mcp_evidence"
    return ""


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
            "client_version": "",
            "failure_class": "client_missing",
            "reason": f"{command_name} CLI is not installed",
        }
    else:
        client_version = _client_version(executable)
        schema = _real_response_schema()
        schema_path = output_dir / f"{agent}_response_schema.json"
        schema_path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
        response_path = output_dir / f"{agent}_response.json"
        response_path.unlink(missing_ok=True)
        prompt = _real_prompt(agent)
        stdin_text: str | None = None
        debug_path: Path | None = None
        debug_cleanup_error = ""
        disabled_user_mcp_servers: list[str] = []
        if agent == "codex":
            disabled_user_mcp_servers = [
                name
                for name in _codex_config_mcp_server_names()
                if name != "litminer"
            ]
            command = _executable_command(
                executable,
                _codex_arguments(
                    schema_path,
                    response_path,
                    disabled_user_mcp_servers,
                ),
            )
            stdin_text = prompt
        else:
            mcp_config_path = _claude_mcp_config(output_dir)
            debug_path = (
                output_dir
                / f"claude_mcp_debug_{uuid.uuid4().hex}.log"
            )
            command = _executable_command(
                executable,
                _claude_arguments(mcp_config_path, debug_path, prompt),
            )
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
            evidence = _mcp_evidence(
                agent,
                stdout=completed.stdout,
                stderr=completed.stderr,
                debug_path=debug_path,
            )
            passed = (
                completed.returncode == 0
                and _valid_contract_payload(agent, parsed)
                and evidence["passed"]
            )
            failure_class = _real_failure_class(
                returncode=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
                parsed=parsed,
                evidence=evidence,
                agent=agent,
            )
            result = {
                "schema_version": 1,
                "agent": agent,
                "mode": "real",
                "status": "passed" if passed else "failed",
                "passed": passed,
                "client_version": client_version,
                "failure_class": failure_class,
                "disabled_user_mcp_servers": disabled_user_mcp_servers,
                "duration_seconds": round(time.monotonic() - started, 6),
                "command": command,
                "exit_code": completed.returncode,
                "stdout_tail": _sanitized_stderr_tail(completed.stdout),
                "stderr_tail": _sanitized_stderr_tail(completed.stderr),
                "response": _sanitized_stderr_tail(raw),
                "parsed_response": parsed,
                "mcp_evidence": evidence,
            }
        except subprocess.TimeoutExpired as exc:
            timeout_stdout = (
                exc.stdout.decode("utf-8", errors="replace")
                if isinstance(exc.stdout, bytes)
                else str(exc.stdout or "")
            )
            timeout_stderr = (
                exc.stderr.decode("utf-8", errors="replace")
                if isinstance(exc.stderr, bytes)
                else str(exc.stderr or "")
            )
            raw = (
                response_path.read_text(encoding="utf-8")
                if response_path.exists()
                else timeout_stdout
            )
            parsed = _parse_contract_payload(raw)
            evidence = _mcp_evidence(
                agent,
                stdout=timeout_stdout,
                stderr=timeout_stderr,
                debug_path=debug_path,
            )
            result = {
                "schema_version": 1,
                "agent": agent,
                "mode": "real",
                "status": "failed",
                "passed": False,
                "client_version": client_version,
                "failure_class": "timeout",
                "disabled_user_mcp_servers": disabled_user_mcp_servers,
                "reason": f"timeout after {timeout:g}s",
                "command": command,
                "stdout_tail": _sanitized_stderr_tail(timeout_stdout),
                "stderr_tail": _sanitized_stderr_tail(timeout_stderr),
                "response": _sanitized_stderr_tail(raw),
                "parsed_response": parsed,
                "mcp_evidence": evidence,
            }
        except OSError as exc:
            evidence = _mcp_evidence(
                agent,
                stdout="",
                stderr=str(exc),
                debug_path=debug_path,
            )
            result = {
                "schema_version": 1,
                "agent": agent,
                "mode": "real",
                "status": "failed",
                "passed": False,
                "client_version": client_version,
                "failure_class": "client_process_environment",
                "disabled_user_mcp_servers": disabled_user_mcp_servers,
                "duration_seconds": round(time.monotonic() - started, 6),
                "reason": f"{type(exc).__name__}: {exc}",
                "command": command,
                "stdout_tail": "",
                "stderr_tail": _sanitized_stderr_tail(str(exc)),
                "response": "",
                "parsed_response": None,
                "mcp_evidence": evidence,
            }
        finally:
            if debug_path is not None:
                try:
                    debug_path.unlink(missing_ok=True)
                except OSError as exc:
                    debug_cleanup_error = f"{type(exc).__name__}: {exc}"
        if debug_cleanup_error:
            result["status"] = "failed"
            result["passed"] = False
            result["failure_class"] = "acceptance_artifact_cleanup"
            result["debug_cleanup_error"] = _sanitized_stderr_tail(
                debug_cleanup_error,
            )
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
