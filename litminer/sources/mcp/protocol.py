"""JSON-RPC/MCP response construction and stdio transport."""

from __future__ import annotations

import json
import os
import sys
import traceback
from typing import Any, Callable

from litminer import __version__
from litminer.contracts import tool_contracts
from litminer.contracts.errors import classify_exception, error_result
from litminer.contracts.schema_validation import validate_json_schema


DEFAULT_PROTOCOL_VERSION = "2025-11-25"
SUPPORTED_PROTOCOL_VERSIONS = {
    DEFAULT_PROTOCOL_VERSION,
    "2024-11-05",
    "2025-03-26",
    "2025-06-18",
}
DEFAULT_MAX_STDIN_LINE_BYTES = 16 * 1024 * 1024


def jsonrpc_error(
    request: dict[str, Any],
    code: int,
    message: str,
    exc: Exception | None = None,
) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if exc is not None:
        error["data"] = {"type": type(exc).__name__}
    return {
        "jsonrpc": "2.0",
        "id": request.get("id"),
        "error": error,
    }


def legacy_input_schema(tool: dict[str, Any]) -> dict[str, Any]:
    parameters = tool.get("parameters") or tool.get("args") or {}
    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, item in parameters.items():
        schema = {
            key: value
            for key, value in item.items()
            if key != "required"
        }
        properties[name] = schema
        if item.get("required"):
            required.append(name)
    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }


def input_schema(
    tool_name: str,
    tool: dict[str, Any],
) -> dict[str, Any]:
    """Return the client-compatible schema advertised by ``tools/list``."""
    return (
        tool_contracts.client_schema_for(tool_name)
        or legacy_input_schema(tool)
    )


def validation_schema(
    tool_name: str,
    tool: dict[str, Any],
) -> dict[str, Any]:
    """Return the strict schema used before a tool handler is invoked."""
    return (
        tool_contracts.schema_for(tool_name)
        or legacy_input_schema(tool)
    )


def mcp_tool_response(
    request: dict[str, Any],
    payload: Any,
    *,
    is_error: bool = False,
) -> dict[str, Any]:
    if isinstance(payload, dict):
        structured = dict(payload)
        structured.setdefault("ok", not is_error)
    else:
        structured = {"ok": not is_error, "result": payload}
    return {
        "jsonrpc": "2.0",
        "id": request.get("id"),
        "result": {
            "content": [{
                "type": "text",
                "text": json.dumps(
                    structured,
                    indent=2,
                    ensure_ascii=False,
                ),
            }],
            "structuredContent": structured,
            "isError": bool(is_error),
        },
    }


def handle_request(
    request: dict[str, Any],
    *,
    tools: dict[str, dict[str, Any]],
    visible_tool_names: Callable[[], list[str]],
) -> dict[str, Any] | None:
    method = request.get("method", "")
    if method == "tools/list":
        tools_list = []
        for name in visible_tool_names():
            tool = tools[name]
            tools_list.append({
                "name": name,
                "description": tool_contracts.description_for(
                    name,
                    tool["description"],
                ),
                "inputSchema": input_schema(name, tool),
            })
        return {
            "jsonrpc": "2.0",
            "id": request.get("id"),
            "result": {"tools": tools_list},
        }

    if method == "tools/call":
        tool_name = request.get("params", {}).get("name", "")
        arguments = request.get("params", {}).get("arguments", {})
        if tool_name not in tools:
            envelope = classify_exception(
                ValueError(f"Unknown tool: {tool_name}"),
                code="unknown_tool",
            )
            return mcp_tool_response(
                request,
                error_result(envelope),
                is_error=True,
            )
        try:
            tool = tools[tool_name]
            validate_json_schema(
                arguments,
                validation_schema(tool_name, tool),
            )
            result = tool["handler"](arguments)
            return mcp_tool_response(request, result)
        except (SystemExit, Exception) as exc:
            envelope = classify_exception(exc)
            debug_trace = (
                traceback.format_exc()
                if os.environ.get("LITMINER_MCP_DEBUG_ERRORS")
                else ""
            )
            return mcp_tool_response(
                request,
                error_result(envelope, debug_trace=debug_trace),
                is_error=True,
            )

    if method == "initialize":
        requested_version = (
            request.get("params", {}).get("protocolVersion")
            or DEFAULT_PROTOCOL_VERSION
        )
        if requested_version not in SUPPORTED_PROTOCOL_VERSIONS:
            return jsonrpc_error(
                request,
                -32602,
                f"Unsupported protocolVersion: {requested_version}. "
                f"Supported: {', '.join(sorted(SUPPORTED_PROTOCOL_VERSIONS))}",
            )
        return {
            "jsonrpc": "2.0",
            "id": request.get("id"),
            "result": {
                "protocolVersion": requested_version,
                "serverInfo": {
                    "name": "litminer",
                    "version": __version__,
                },
                "capabilities": {"tools": {"listChanged": False}},
            },
        }

    if request.get("id") is None:
        return None
    return jsonrpc_error(
        request,
        -32601,
        f"Unknown method: {method}",
    )


def serve_stdio(
    handler: Callable[[dict[str, Any]], dict[str, Any] | None],
    *,
    max_line_bytes: int = DEFAULT_MAX_STDIN_LINE_BYTES,
) -> None:
    print("Litminer MCP Server starting on stdio", file=sys.stderr)
    for raw_line in sys.stdin.buffer:
        if len(raw_line) > max_line_bytes:
            print(json.dumps({
                "jsonrpc": "2.0",
                "id": None,
                "error": {
                    "code": -32700,
                    "message": (
                        f"Input line exceeds {max_line_bytes} bytes"
                    ),
                },
            }), flush=True)
            continue
        line = raw_line.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            response = handler(request)
            if response is not None:
                print(json.dumps(response), flush=True)
        except json.JSONDecodeError as exc:
            print(json.dumps({
                "jsonrpc": "2.0",
                "id": None,
                "error": {
                    "code": -32700,
                    "message": f"Parse error: {exc}",
                },
            }), flush=True)
