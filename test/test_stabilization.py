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

    def test_contract_payload_parser_handles_claude_result_envelope(self) -> None:
        raw = json.dumps({
            "type": "result",
            "structured_output": {
                "client": "claude",
                "contract_version": 1,
                "default_tool_count": 9,
                "first_artifact": "run_outcome.json",
                "supported_systems": ["Windows", "macOS"],
            },
        })
        parsed = agent_client_acceptance._parse_contract_payload(raw)
        self.assertTrue(
            agent_client_acceptance._valid_contract_payload(
                "claude",
                parsed,
            )
        )

    def test_windows_batch_client_uses_comspec_wrapper(self) -> None:
        with patch.object(agent_client_acceptance.os, "name", "nt"), patch.dict(
            os.environ,
            {"COMSPEC": "cmd.exe"},
            clear=False,
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
        )
        self.assertEqual(arguments[:3], ["-a", "never", "exec"])
        self.assertIn("--ephemeral", arguments)
        self.assertIn("read-only", arguments)
        self.assertEqual(arguments[-1], "-")


if __name__ == "__main__":
    unittest.main()
