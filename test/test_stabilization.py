from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from litminer.engine import agent_client_acceptance, provider_acceptance, runtime_soak


class ProviderAcceptanceTests(unittest.TestCase):
    def test_each_discovery_provider_uses_parser_entry_and_validates_shape(self) -> None:
        for provider in provider_acceptance.DISCOVERY_QUERIES:
            with self.subTest(provider=provider), tempfile.TemporaryDirectory() as tmp, patch(
                "litminer.engine.provider_acceptance.api_discovery.run_provider",
                return_value=[{"title": f"{provider} result", "doi": "10.1/example"}],
            ) as mocked:
                report = provider_acceptance.run_acceptance(
                    providers=[provider],
                    output_dir=Path(tmp),
                )
                self.assertTrue(report["passed"])
                self.assertEqual(report["results"][0]["status"], "success")
                self.assertEqual(mocked.call_args.args[0], provider)
                self.assertTrue((Path(tmp) / "provider_acceptance.sqlite3").exists())

    def test_crossref_probe_validates_real_parser_result_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch(
            "litminer.engine.provider_acceptance.crossref_verify.verify_doi",
            return_value={
                "crossref_doi": provider_acceptance.ACCEPTANCE_DOI,
                "crossref_title": "Acceptance paper",
            },
        ) as mocked:
            report = provider_acceptance.run_acceptance(
                providers=["crossref"],
                output_dir=Path(tmp),
            )
            self.assertTrue(report["passed"])
            mocked.assert_called_once_with(
                provider_acceptance.ACCEPTANCE_DOI,
                raise_transient=True,
            )

    def test_unpaywall_missing_email_is_explicit_skip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {"UNPAYWALL_EMAIL": "", "LITMINER_CONTACT_EMAIL": ""},
            clear=False,
        ):
            failed = provider_acceptance.run_acceptance(
                providers=["unpaywall"],
                output_dir=Path(tmp) / "strict",
            )
            allowed = provider_acceptance.run_acceptance(
                providers=["unpaywall"],
                output_dir=Path(tmp) / "allowed",
                allow_skipped=True,
            )
            self.assertFalse(failed["passed"])
            self.assertEqual(failed["results"][0]["status"], "skipped")
            self.assertTrue(allowed["passed"])

    def test_unpaywall_success_shape_and_report_are_bounded(self) -> None:
        payload = {
            "status": "ok",
            "error": "",
            "data": {
                "doi": provider_acceptance.ACCEPTANCE_DOI,
                "title": "x" * 1000,
            },
        }
        with tempfile.TemporaryDirectory() as tmp, patch(
            "litminer.engine.provider_acceptance.unpaywall_lookup.resolve_email",
            return_value="acceptance@example.test",
        ), patch(
            "litminer.engine.provider_acceptance.unpaywall_lookup.lookup_doi",
            return_value=payload,
        ):
            report = provider_acceptance.run_acceptance(
                providers=["unpaywall"],
                output_dir=Path(tmp),
            )
            self.assertTrue(report["passed"])
            written = json.loads(
                (Path(tmp) / "provider_acceptance.json").read_text(encoding="utf-8")
            )
            sample = written["results"][0]["response_shape"]["sample"]
            self.assertLess(len(sample["data"]["title"]), 300)

    def test_unpaywall_transient_result_can_degrade_release_gate(self) -> None:
        class StubRuntime:
            def execute(self, *_args, **_kwargs):
                return {
                    "status": "rate_limited",
                    "error": "temporary limit",
                    "retry_after_seconds": 30,
                    "attempts": 4,
                    "request_count": 4,
                    "data": None,
                }

        with patch(
            "litminer.engine.provider_acceptance.unpaywall_lookup.resolve_email",
            return_value="acceptance@example.test",
        ):
            result = provider_acceptance.probe_provider(
                "unpaywall",
                StubRuntime(),
            )
        self.assertEqual(result["error"]["class"], "rate_limited")
        self.assertTrue(result["error"]["transient"])
        self.assertEqual(result["error"]["retry_after_seconds"], 30.0)
        gate = provider_acceptance._apply_gate(
            [
                {"provider": "openalex", "status": "success", "passed": True},
                {"provider": "crossref", "status": "success", "passed": True},
                result,
            ],
            policy="release",
            allow_skipped=False,
        )
        self.assertTrue(gate["passed"])
        self.assertEqual(gate["degraded_providers"], ["unpaywall"])

    def test_provider_failure_is_structured_and_nonzero_equivalent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch(
            "litminer.engine.provider_acceptance.api_discovery.run_provider",
            side_effect=OSError("offline"),
        ):
            report = provider_acceptance.run_acceptance(
                providers=["openalex"],
                output_dir=Path(tmp),
            )
            self.assertFalse(report["passed"])
            self.assertEqual(report["results"][0]["status"], "failed")
            self.assertIn("error", report["results"][0])

    def test_release_gate_accepts_only_structured_optional_transient_failure(self) -> None:
        results = [
            {"provider": "openalex", "status": "success", "passed": True},
            {"provider": "crossref", "status": "success", "passed": True},
            {
                "provider": "semantic_scholar",
                "status": "failed",
                "passed": False,
                "error": {
                    "class": "rate_limited",
                    "code": "provider_rate_limited",
                    "transient": True,
                    "http_status": 429,
                },
            },
        ]
        gate = provider_acceptance._apply_gate(
            results,
            policy="release",
            allow_skipped=False,
        )
        self.assertTrue(gate["passed"])
        self.assertFalse(gate["strict_passed"])
        self.assertEqual(gate["quality"], "degraded")
        self.assertEqual(gate["degraded_providers"], ["semantic_scholar"])

    def test_release_gate_rejects_required_transient_and_optional_parser_errors(self) -> None:
        required_failure = [
            {
                "provider": "openalex",
                "status": "failed",
                "passed": False,
                "error": {
                    "class": "network",
                    "code": "network_error",
                    "transient": True,
                },
            },
            {"provider": "crossref", "status": "success", "passed": True},
        ]
        parser_failure = [
            {"provider": "openalex", "status": "success", "passed": True},
            {"provider": "crossref", "status": "success", "passed": True},
            {
                "provider": "semantic_scholar",
                "status": "failed",
                "passed": False,
                "error": {
                    "class": "provider_response",
                    "code": "provider_response_invalid",
                    "transient": True,
                },
            },
        ]
        self.assertFalse(provider_acceptance._apply_gate(
            required_failure,
            policy="release",
            allow_skipped=False,
        )["passed"])
        self.assertFalse(provider_acceptance._apply_gate(
            parser_failure,
            policy="release",
            allow_skipped=False,
        )["passed"])

    def test_release_policy_requires_complete_set_and_never_allows_skips(self) -> None:
        with self.assertRaisesRegex(ValueError, "complete provider set"):
            provider_acceptance.run_acceptance(
                providers=list(provider_acceptance.CORE_PROVIDERS),
                output_dir=Path("unused"),
                policy="release",
            )
        with self.assertRaisesRegex(ValueError, "duplicates"):
            provider_acceptance.run_acceptance(
                providers=[
                    *provider_acceptance.FULL_PROVIDERS,
                    provider_acceptance.FULL_PROVIDERS[0],
                ],
                output_dir=Path("unused"),
                policy="release",
            )
        with self.assertRaisesRegex(ValueError, "never allows"):
            provider_acceptance.run_acceptance(
                providers=list(provider_acceptance.FULL_PROVIDERS),
                output_dir=Path("unused"),
                policy="release",
                allow_skipped=True,
            )


class RuntimeSoakPathTests(unittest.TestCase):
    def test_relative_output_dir_is_absolutized_before_pipeline_children(self) -> None:
        original_cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            os.chdir(tmp)
            try:
                with patch.object(
                    runtime_soak,
                    "runtime_iteration",
                    return_value={"passed": True},
                ) as iteration_mock, patch.object(
                    runtime_soak,
                    "pipeline_cycle",
                    return_value={"passed": True},
                ) as pipeline_mock:
                    report = runtime_soak.run_soak(
                        profile_name="quick",
                        output_dir=Path("relative-soak"),
                        iterations=1,
                    )
            finally:
                os.chdir(original_cwd)
        self.assertTrue(report["passed"])
        self.assertTrue(iteration_mock.call_args.args[0].is_absolute())
        self.assertTrue(pipeline_mock.call_args.args[0].is_absolute())


class AgentClientAcceptanceTests(unittest.TestCase):
    def test_python_310_toml_fallback_validates_codex_template(self) -> None:
        template = agent_client_acceptance.PROJECT_ROOT / "config" / "mcp.codex.example.toml"
        with patch.object(agent_client_acceptance, "_tomllib", None):
            payload = agent_client_acceptance._validate_template(template)
        litminer = payload["mcp_servers"]["litminer"]
        self.assertEqual(litminer["command"], "python")
        self.assertIn("LITMINER_WORKSPACE_ROOT", litminer["env"])
        self.assertIn("SEMANTIC_SCHOLAR_API_KEY", litminer["env_vars"])
        self.assertIn("S2_API_KEY", litminer["env_vars"])
        self.assertIn("UNPAYWALL_EMAIL", litminer["env_vars"])

    def test_python_310_toml_fallback_rejects_incomplete_template(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            template = Path(tmp) / "incomplete.toml"
            template.write_text(
                '[mcp_servers.litminer]\ncommand = "python"\n',
                encoding="utf-8",
            )
            with patch.object(agent_client_acceptance, "_tomllib", None):
                with self.assertRaisesRegex(ValueError, "missing TOML keys"):
                    agent_client_acceptance._validate_template(template)

    def test_claude_template_has_real_mcp_file_shape_without_contact_values(self) -> None:
        template = (
            agent_client_acceptance.PROJECT_ROOT
            / "config"
            / "mcp.claude.example.json"
        )
        payload = agent_client_acceptance._validate_template(template)
        server = payload["mcpServers"]["litminer"]
        self.assertEqual(server["type"], "stdio")
        self.assertEqual(server["env"]["LITMINER_MCP_TOOL_PROFILE"], "workflow")
        self.assertNotIn("LITMINER_CONTACT_EMAIL", server["env"])

    def test_claude_registration_examples_place_name_before_env_arguments(self) -> None:
        paths = (
            "CLAUDE.md",
            "README.md",
            "README.en.md",
            "references/user-guide.md",
            "references/user-guide.en.md",
            "references/mcp-surface.md",
        )
        for relative in paths:
            with self.subTest(path=relative):
                raw = (
                    agent_client_acceptance.PROJECT_ROOT / relative
                ).read_text(encoding="utf-8")
                self.assertIn(
                    "claude mcp add --scope user litminer `",
                    raw,
                )
                self.assertNotIn(
                    "claude mcp add --scope user `\n",
                    raw,
                )

    def test_template_validation_rejects_persisted_provider_contact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            template = Path(tmp) / "mcp.json"
            template.write_text(
                json.dumps({
                    "mcpServers": {
                        "litminer": {
                            "type": "stdio",
                            "command": "python",
                            "args": ["server.py"],
                            "cwd": "D:/workspace",
                            "env": {
                                "LITMINER_WORKSPACE_ROOT": "D:/workspace",
                                "LITMINER_MCP_TOOL_PROFILE": "workflow",
                                "LITMINER_CONTACT_EMAIL": "user@example.test",
                            },
                        },
                    },
                }),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "must be inherited"):
                agent_client_acceptance._validate_template(template)

    def test_contract_payload_parser_handles_claude_result_envelope(self) -> None:
        raw = json.dumps({
            "type": "result",
            "structured_output": {
                "client": "claude",
                "contract_version": 1,
                "default_tool_count": 9,
                "first_artifact": "run_outcome.json",
                "supported_systems": ["Windows", "macOS"],
                "tools_called": [
                    "litminer_workspace_doctor",
                    "litminer_plan_run",
                ],
                "doctor_ok": True,
                "plan_ok": True,
            },
        })
        parsed = agent_client_acceptance._parse_contract_payload(raw)
        self.assertTrue(
            agent_client_acceptance._valid_contract_payload(
                "claude",
                parsed,
            )
        )

    def test_contract_payload_accepts_claude_qualified_tool_names(self) -> None:
        payload = {
            "client": "claude",
            "tools_called": [
                "mcp__litminer__litminer_workspace_doctor",
                "mcp__litminer__litminer_plan_run",
            ],
            "doctor_ok": True,
            "plan_ok": True,
        }
        self.assertTrue(
            agent_client_acceptance._valid_contract_payload(
                "claude",
                payload,
            )
        )

    def test_acceptance_stderr_omits_gateway_html_and_network_metadata(self) -> None:
        raw = (
            "refresh failed\n"
            "<html><body>Unable to load site [IP:192.0.2.1 | Ray ID:test]</body></html>\n"
            "\\u003chtml\\u003eescaped gateway Ray ID:escaped123"
            "\\u003c/html\\u003e\n"
            "MCP failed Ray ID:outside456\n"
            "unsupported_country_region_territory\n"
        )
        sanitized = agent_client_acceptance._sanitized_stderr_tail(raw)
        self.assertIn("<html response omitted>", sanitized)
        self.assertIn("unsupported_country_region_territory", sanitized)
        self.assertNotIn("192.0.2.1", sanitized)
        self.assertNotIn("Unable to load site", sanitized)
        self.assertNotIn("escaped123", sanitized)
        self.assertNotIn("outside456", sanitized)

    def test_windows_batch_client_uses_comspec_wrapper(self) -> None:
        with patch.object(agent_client_acceptance.os, "name", "nt"), patch.dict(
            os.environ,
            {"COMSPEC": "cmd.exe"},
            clear=False,
        ), patch.object(
            agent_client_acceptance,
            "Path",
            side_effect=AssertionError("launcher suffix detection must be host-neutral"),
        ):
            command = agent_client_acceptance._executable_command(
                r"C:\tools\codex.CMD",
                ["exec", "--help"],
            )
        self.assertEqual(command[:4], ["cmd.exe", "/d", "/s", "/c"])
        self.assertIn("codex.CMD", command[4])

    def test_codex_approval_flag_precedes_exec_subcommand(self) -> None:
        arguments = agent_client_acceptance._codex_arguments(
            Path("schema.json"),
            Path("response.json"),
            ["github", "server.with.dot", "litminer", "LitMiner"],
        )
        self.assertEqual(arguments[:2], ["-a", "never"])
        exec_index = arguments.index("exec")
        self.assertGreater(exec_index, 2)
        self.assertIn("-c", arguments[:exec_index])
        self.assertIn("--ephemeral", arguments)
        self.assertNotIn("--ignore-user-config", arguments)
        self.assertIn("read-only", arguments)
        self.assertIn("--json", arguments)
        self.assertIn(
            "mcp_servers.litminer.default_tools_approval_mode='prompt'",
            arguments,
        )
        self.assertIn(
            "mcp_servers.litminer.tools.litminer_workspace_doctor.approval_mode='approve'",
            arguments,
        )
        self.assertIn(
            "mcp_servers.litminer.tools.litminer_plan_run.approval_mode='approve'",
            arguments,
        )
        self.assertIn("mcp_servers.litminer.enabled=true", arguments)
        self.assertNotIn("mcp_servers.litminer.enabled=false", arguments)
        self.assertIn("mcp_servers.LitMiner.enabled=false", arguments)
        self.assertIn("mcp_servers.github.enabled=false", arguments)
        self.assertIn(
            'mcp_servers."server.with.dot".enabled=false',
            arguments,
        )
        self.assertEqual(arguments[-1], "-")

    def test_codex_config_parser_discovers_servers_without_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_home = Path(tmp)
            (config_home / "config.toml").write_text(
                '[mcp_servers.alpha]\ncommand = "python"\n'
                '[mcp_servers.beta]\nurl = "https://example.test"\n',
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {"CODEX_HOME": str(config_home)},
                clear=False,
            ):
                names = agent_client_acceptance._codex_config_mcp_server_names()
        self.assertEqual(names, ["alpha", "beta"])

    def test_claude_transient_mcp_config_uses_current_python_without_contact_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = agent_client_acceptance._claude_mcp_config(Path(tmp))
            payload = json.loads(path.read_text(encoding="utf-8"))
        server = payload["mcpServers"]["litminer"]
        self.assertEqual(server["command"], str(Path(agent_client_acceptance.sys.executable).resolve()))
        self.assertEqual(server["env"]["LITMINER_MCP_TOOL_PROFILE"], "workflow")
        self.assertNotIn("LITMINER_CONTACT_EMAIL", server["env"])
        self.assertNotIn("UNPAYWALL_EMAIL", server["env"])

    def test_claude_arguments_keep_tool_search_and_only_auto_allow_acceptance_tools(self) -> None:
        arguments = agent_client_acceptance._claude_arguments(
            Path("mcp.json"),
            Path("debug.log"),
            "prompt",
        )
        self.assertIn("--strict-mcp-config", arguments)
        self.assertNotIn("--json-schema", arguments)
        self.assertEqual(
            arguments[arguments.index("--permission-mode") + 1],
            "dontAsk",
        )
        self.assertEqual(arguments[arguments.index("--tools") + 1], "ToolSearch")
        allowed = arguments[arguments.index("--allowedTools") + 1].split(",")
        self.assertEqual(allowed[0], "ToolSearch")
        self.assertEqual(
            allowed[1:],
            [
                f"mcp__litminer__{name}"
                for name in agent_client_acceptance.REAL_ACCEPTANCE_TOOLS
            ],
        )

    def test_codex_event_parser_requires_completed_mcp_items(self) -> None:
        raw = "\n".join([
            json.dumps({
                "type": "item.completed",
                "item": {
                    "type": "mcp_tool_call",
                    "server": "litminer",
                    "tool": "litminer_workspace_doctor",
                    "status": "completed",
                    "error": None,
                    "result": {
                        "structured_content": {"ok": True},
                    },
                },
            }),
            json.dumps({
                "type": "item.completed",
                "item": {
                    "type": "mcp_tool_call",
                    "server": "litminer",
                    "tool": "litminer_plan_run",
                    "status": "completed",
                    "error": None,
                    "result": {
                        "structured_content": {"ok": True},
                    },
                },
            }),
        ])
        self.assertEqual(
            agent_client_acceptance._codex_completed_mcp_tools(raw),
            list(agent_client_acceptance.REAL_ACCEPTANCE_TOOLS),
        )

    def test_codex_mcp_evidence_rejects_failed_or_unexpected_calls(self) -> None:
        raw = "\n".join([
            json.dumps({
                "type": "item.completed",
                "item": {
                    "type": "mcp_tool_call",
                    "server": "litminer",
                    "tool": "litminer_workspace_doctor",
                    "status": "completed",
                    "error": None,
                    "result": {
                        "structured_content": {"ok": True},
                    },
                },
            }),
            json.dumps({
                "type": "item.completed",
                "item": {
                    "type": "mcp_tool_call",
                    "server": "litminer",
                    "tool": "litminer_start_run",
                    "status": "failed",
                    "error": {"message": "denied"},
                    "result": {
                        "structured_content": {"ok": False},
                    },
                },
            }),
        ])
        evidence = agent_client_acceptance._mcp_evidence(
            "codex",
            stdout=raw,
            stderr="",
            debug_path=None,
        )
        self.assertFalse(evidence["passed"])
        self.assertEqual(evidence["failed_tools"], ["litminer_start_run"])
        self.assertEqual(evidence["unexpected_tools"], ["litminer_start_run"])

    def test_codex_mcp_evidence_rejects_duplicate_or_false_result_calls(self) -> None:
        def event(tool: str, *, ok: bool = True, server: str = "litminer") -> str:
            return json.dumps({
                "type": "item.completed",
                "item": {
                    "type": "mcp_tool_call",
                    "server": server,
                    "tool": tool,
                    "status": "completed",
                    "error": None,
                    "result": {
                        "structured_content": {"ok": ok},
                    },
                },
            })

        duplicate = "\n".join([
            event("litminer_workspace_doctor"),
            event("litminer_workspace_doctor"),
            event("litminer_plan_run"),
        ])
        false_result = "\n".join([
            event("litminer_workspace_doctor", ok=False),
            event("litminer_plan_run"),
        ])
        wrong_server = "\n".join([
            event("litminer_workspace_doctor", server="other"),
            event("litminer_plan_run"),
        ])

        duplicate_evidence = agent_client_acceptance._mcp_evidence(
            "codex",
            stdout=duplicate,
            stderr="",
            debug_path=None,
        )
        false_evidence = agent_client_acceptance._mcp_evidence(
            "codex",
            stdout=false_result,
            stderr="",
            debug_path=None,
        )
        server_evidence = agent_client_acceptance._mcp_evidence(
            "codex",
            stdout=wrong_server,
            stderr="",
            debug_path=None,
        )

        self.assertFalse(duplicate_evidence["passed"])
        self.assertFalse(false_evidence["passed"])
        self.assertEqual(
            false_evidence["failed_tools"],
            ["litminer_workspace_doctor"],
        )
        self.assertFalse(server_evidence["passed"])
        self.assertEqual(server_evidence["unexpected_servers"], ["other"])

    def test_claude_debug_evidence_excludes_unrelated_user_settings(self) -> None:
        debug_text = "\n".join([
            "Applying permission update: API_KEY=should-not-survive",
            'MCP server "litminer": Successfully connected',
            "MCP server \"litminer\": Tool 'litminer_workspace_doctor' "
            "completed successfully Ray ID:debug123",
        ])
        evidence_lines = agent_client_acceptance._claude_debug_evidence_lines(
            debug_text,
        )
        self.assertEqual(len(evidence_lines), 2)
        self.assertNotIn("API_KEY", "\n".join(evidence_lines))
        self.assertNotIn("debug123", "\n".join(evidence_lines))

    def test_claude_mcp_evidence_fails_when_client_skips_a_workflow_tool(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            debug_path = Path(tmp) / "debug.log"
            debug_path.write_text(
                'MCP server "litminer": Successfully connected\n'
                'MCP server "litminer" Skipping tool "litminer_plan_run"\n'
                "Tool 'litminer_workspace_doctor' completed successfully\n",
                encoding="utf-8",
            )
            evidence = agent_client_acceptance._mcp_evidence(
                "claude",
                stdout="",
                stderr="",
                debug_path=debug_path,
            )
        self.assertFalse(evidence["passed"])
        self.assertEqual(evidence["skipped_tools"], ["litminer_plan_run"])

    def test_real_failure_class_separates_region_and_mcp_failures(self) -> None:
        region = agent_client_acceptance._real_failure_class(
            returncode=1,
            stdout="",
            stderr="unsupported_country_region_territory",
            parsed=None,
            evidence={
                "startup_errors": [],
                "failed_tools": [],
                "unexpected_tools": [],
                "passed": False,
            },
            agent="codex",
        )
        mcp = agent_client_acceptance._real_failure_class(
            returncode=0,
            stdout="",
            stderr="",
            parsed={
                "client": "codex",
                "tools_called": list(
                    agent_client_acceptance.REAL_ACCEPTANCE_TOOLS
                ),
                "doctor_ok": False,
                "plan_ok": False,
            },
            evidence={
                "startup_errors": [],
                "failed_tools": ["litminer_workspace_doctor"],
                "unexpected_tools": [],
                "passed": False,
            },
            agent="codex",
        )
        self.assertEqual(region, "client_auth_or_region")
        self.assertEqual(mcp, "mcp_tool_execution")


if __name__ == "__main__":
    unittest.main()
