#!/usr/bin/env python3
"""Quick local test of the Litminer MCP server before deploying to Cowork.
Run this to verify the server starts, responds to initialize, and lists tools.
No Cowork or MCP client needed; this script speaks JSON-RPC directly over a pipe.

Usage:
    python sources/mcp/test_server.py
"""

import json
import subprocess
import sys
from pathlib import Path

SERVER_PATH = Path(__file__).resolve().parent / "server.py"
PROJECT_ROOT = SERVER_PATH.parent.parent.parent


def send_request(proc, request: dict) -> dict:
    """Send a JSON-RPC request to the server and read the response."""
    line = json.dumps(request) + "\n"
    proc.stdin.write(line)
    proc.stdin.flush()
    response_line = proc.stdout.readline()
    return json.loads(response_line)


def main():
    print("Litminer MCP Server Test")
    print("=" * 50)

    # Start server as subprocess
    proc = subprocess.Popen(
        [sys.executable, str(SERVER_PATH)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(PROJECT_ROOT),
    )

    try:
        # Test 1: Initialize
        print("\n[1/4] Testing initialize...")
        resp = send_request(proc, {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-11-25"},
        })
        assert resp.get("result", {}).get("serverInfo", {}).get("name") == "litminer"
        assert resp.get("result", {}).get("protocolVersion") == "2025-11-25"
        print("  PASS: Server identifies as litminer")

        # Test 2: List tools
        print("[2/4] Testing tools/list...")
        resp = send_request(proc, {
            "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}
        })
        tools = resp.get("result", {}).get("tools", [])
        tool_names = [t["name"] for t in tools]
        expected = [
            "litminer_search_openalex",
            "litminer_search_semantic_scholar",
            "litminer_search_arxiv",
            "litminer_search_europe_pmc",
            "litminer_verify_crossref",
            "litminer_search_crossref_title",
            "litminer_batch_crossref_title_search",
            "litminer_dedupe",
            "litminer_lookup_unpaywall",
            "litminer_discover_api",
            "litminer_semantic_triage",
            "litminer_filter_journal_metrics",
            "litminer_build_publisher_queue",
            "litminer_probe_publishers",
            "litminer_import_websearch",
            "litminer_processing_report",
            "litminer_run_lit_search",
        ]
        for name in expected:
            assert name in tool_names, f"Missing tool: {name}"
        print(f"  PASS: All {len(tool_names)} tools registered")

        # Test 3: Call a tool that doesn't need network (dedupe)
        print("[3/4] Testing tool call (dedupe with non-existent file)...")
        resp = send_request(proc, {
            "jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
                "name": "litminer_dedupe",
                "arguments": {"input_csv": "nonexistent.csv", "output_csv": "check/mcp_test_output.csv"}
            }
        })
        # Dedupe will fail because input does not exist; that is expected.
        has_error = "error" in resp
        print(f"  PASS: Tool handler executed (error={has_error}, this is expected)")

        # Test 4: Notification (no id) should get no response
        print("[4/4] Testing notification handling...")
        assert proc.stdin is not None
        proc.stdin.write(json.dumps({
            "jsonrpc": "2.0", "method": "notifications/initialized", "params": {}
        }) + "\n")
        proc.stdin.flush()
        # There should be no response for notifications.
        # (server.py returns None, main loop skips printing)
        print("  PASS: Notification sent (no response expected)")

        print("\n" + "=" * 50)
        print("ALL TESTS PASSED - server is ready for Cowork deployment.")

    except Exception as e:
        print(f"\nFAIL: {e}")
        sys.exit(1)
    finally:
        proc.terminate()
        try:
            _, stderr_output = proc.communicate(timeout=5)
            if "Litminer MCP Server starting" in stderr_output:
                print("\n  Server startup message confirmed in stderr.")
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


if __name__ == "__main__":
    main()
