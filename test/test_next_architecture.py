from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from litminer.contracts.errors import LitminerValidationError, ProviderCooldownError, classify_exception
from litminer.contracts.run_spec import RunSpec
from litminer.contracts.schema_validation import validate_json_schema
from litminer.contracts import tool_contracts
from litminer.engine import common
from litminer.engine.common import write_csv_atomic
from litminer.evidence.canonicalize import canonicalize_row
from litminer.evidence.coverage import build_coverage_report
from litminer.exporters import bibtex
from litminer.exporters.exporter import export_bibliography
from litminer.runtime import state_store as state_store_module
from litminer.runtime.provider_runtime import ProviderRuntime
from litminer.runtime.provider_scheduler import ProviderScheduler
from litminer.runtime.state_store import StateStore
from litminer.sources.api.http_client import RetryPolicy, fetch_json
from litminer.sources.mcp import server as mcp_server


class _Response:
    status = 200

    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return self.payload


class NextArchitectureTests(unittest.TestCase):
    def test_atomic_write_retries_transient_windows_replace_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            real_replace = common.os.replace
            attempts = 0

            def flaky_replace(source, destination):
                nonlocal attempts
                attempts += 1
                if attempts < 3:
                    raise PermissionError(32, "target is temporarily open")
                return real_replace(source, destination)

            with patch("litminer.engine.common.os.replace", side_effect=flaky_replace):
                common.write_text_atomic(path, '{"ok": true}\n')
            self.assertEqual(attempts, 3)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"ok": True})

    def test_run_spec_input_modes_and_nonserializable_extras(self) -> None:
        spec = RunSpec.from_mapping({
            "queries": ["clinical retrieval"],
            "output_dir": "run",
            "cancel_check": threading.Event().is_set,
            "export": ["ris", "bibtex"],
        })
        self.assertEqual(spec.input.mode, "discover")
        self.assertEqual(spec.output.export_formats, ("ris", "bibtex"))
        self.assertNotIn("cancel_check", spec.to_dict())
        with self.assertRaises(LitminerValidationError):
            RunSpec.from_mapping({"queries": ["q"], "input_csv": "in.csv"})
        with self.assertRaises(LitminerValidationError):
            RunSpec.from_mapping({"queries": ["q"], "year_from": 2026, "year_to": 2020})

    def test_contract_schema_rejects_missing_or_mixed_input(self) -> None:
        schema = tool_contracts.schema_for("litminer_start_run")
        self.assertIsNotNone(schema)
        with self.assertRaises(LitminerValidationError):
            validate_json_schema({}, schema or {})
        with self.assertRaises(LitminerValidationError):
            validate_json_schema({"queries": ["q"], "input_csv": "in.csv"}, schema or {})
        validate_json_schema({"queries": ["q"]}, schema or {})

    def test_mcp_tools_list_uses_contract_and_invalid_input_is_structured(self) -> None:
        response = mcp_server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
        tools = {item["name"]: item for item in response["result"]["tools"]}
        self.assertEqual(
            tools["litminer_start_run"]["inputSchema"],
            tool_contracts.client_schema_for("litminer_start_run"),
        )
        self.assertNotIn("oneOf", tools["litminer_start_run"]["inputSchema"])
        self.assertIn(
            "oneOf",
            tool_contracts.schema_for("litminer_start_run") or {},
        )
        invalid = mcp_server.handle_request({
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "litminer_plan_run", "arguments": {}},
        })
        self.assertTrue(invalid["result"]["isError"])
        self.assertEqual(invalid["result"]["structuredContent"]["error"]["class"], "validation")

    def test_mcp_plan_run_does_not_create_run_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "input.csv").write_text("title,doi\nPaper,10.1/a\n", encoding="utf-8")
            with patch.dict("os.environ", {"LITMINER_WORKSPACE_ROOT": str(root)}, clear=False):
                plan = mcp_server.tool_plan_run({"input_csv": "input.csv", "output_dir": "planned", "mode": "fast"})
            self.assertEqual(plan["input_mode"], "import")
            self.assertFalse(plan["will_call_network"])
            self.assertFalse((root / "planned").exists())

    def test_state_store_transaction_migration_rollback_and_export(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.sqlite3"
            store = StateStore(path)
            with self.assertRaises(RuntimeError):
                with store.connect() as db:
                    db.execute("INSERT INTO provider_health(provider, updated_at) VALUES ('rollback', 'now')")
                    raise RuntimeError("rollback")
            with store.connect() as db:
                self.assertIsNone(db.execute("SELECT provider FROM provider_health WHERE provider='rollback'").fetchone())

            broken_version = state_store_module.CURRENT_SCHEMA_VERSION + 1
            broken = state_store_module.MIGRATIONS + ((
                broken_version,
                "CREATE TABLE rollback_probe(id INTEGER); INVALID SQL;",
            ),)
            with patch.object(state_store_module, "MIGRATIONS", broken):
                with self.assertRaises(sqlite3.Error):
                    StateStore(path)
            db = sqlite3.connect(path)
            try:
                self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='rollback_probe'").fetchone())
                self.assertIsNone(db.execute(
                    "SELECT version FROM schema_migrations WHERE version=?",
                    (broken_version,),
                ).fetchone())
            finally:
                db.close()

            exported = store.export_state(Path(tmp) / "state.json")
            payload = json.loads(exported.read_text(encoding="utf-8"))
            self.assertIn("provider_requests", payload["tables"])
            self.assertIn("runtime_events", payload["tables"])
            self.assertEqual(
                payload["schema_version"],
                state_store_module.CURRENT_SCHEMA_VERSION,
            )

    def test_state_store_v1_fixture_upgrades_to_v2_without_data_loss(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state-v1.sqlite3"
            db = sqlite3.connect(path)
            try:
                db.executescript(state_store_module.MIGRATIONS[0][1])
                db.execute(
                    "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
                )
                db.execute("INSERT INTO schema_migrations VALUES (1, '2026-01-01T00:00:00Z')")
                db.execute(
                    "INSERT INTO research_sessions VALUES (?, ?, ?, ?, ?)",
                    ("session-v1", str(Path(tmp)), str(Path(tmp) / "run"), "created", "updated"),
                )
                db.execute(
                    "INSERT INTO iterations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        "iteration_001",
                        "session-v1",
                        "run-v1",
                        "import",
                        "completed",
                        "healthy",
                        '{"schema_version": 1}',
                        "created",
                        "completed",
                    ),
                )
                db.execute(
                    "INSERT INTO jobs VALUES (?, ?, ?, ?, ?)",
                    ("job-v1", "run-v1", "completed", '{"job_id":"job-v1","status":"completed"}', "updated"),
                )
                db.execute(
                    "INSERT INTO run_outcomes VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        "run-v1",
                        str(Path(tmp) / "run"),
                        "completed",
                        "healthy",
                        '{"run_id":"run-v1","status":"completed","quality":"healthy"}',
                        "updated",
                    ),
                )
                db.commit()
            finally:
                db.close()

            store = StateStore(path)
            self.assertEqual(store.get_job("job-v1")["status"], "completed")
            self.assertEqual(store.get_run(run_id="run-v1")["quality"], "healthy")
            with store.connect() as upgraded:
                versions = [
                    row[0]
                    for row in upgraded.execute(
                        "SELECT version FROM schema_migrations ORDER BY version"
                    )
                ]
                self.assertEqual(versions, [1, 2])
                self.assertIsNotNone(upgraded.execute(
                    "SELECT name FROM sqlite_master WHERE name='runtime_events'"
                ).fetchone())

            StateStore(path)
            with store.connect() as upgraded:
                self.assertEqual(
                    upgraded.execute(
                        "SELECT COUNT(*) FROM schema_migrations WHERE version=2"
                    ).fetchone()[0],
                    1,
                )

    def test_runtime_event_ledger_is_append_only_across_transitions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.sqlite3"
            store = StateStore(path)
            store.upsert_session(
                "session-events",
                workspace_root=tmp,
                output_dir=str(Path(tmp) / "run"),
            )
            store.start_iteration(
                session_id="session-events",
                iteration_id="iteration_001",
                run_id="run-events",
                input_mode="import",
                spec={"schema_version": 1},
            )
            store.record_stage(
                run_id="run-events",
                stage_name="dedupe",
                status="completed",
                status_class="ok",
            )
            store.complete_iteration(
                "run-events",
                status="completed",
                quality="healthy",
            )
            events = store.list_events(run_id="run-events")
            self.assertEqual(
                [item["event_type"] for item in events],
                ["iteration_started", "stage_recorded", "iteration_completed"],
            )

    def test_state_store_get_run_reports_in_progress_iteration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            run_dir.mkdir()
            state_path = root / "state.sqlite3"
            store = StateStore(state_path)
            aliased_run_dir = root / "unused" / ".." / "run"
            store.upsert_session(
                "session-1",
                workspace_root=str(root / "unused" / ".."),
                output_dir=str(aliased_run_dir),
            )
            store.start_iteration(
                session_id="session-1", iteration_id="iteration_001", run_id="run-live",
                input_mode="discover", spec={"schema_version": 1},
            )
            (run_dir / "run_manifest.json").write_text(
                json.dumps({"schema_version": 1, "run_id": "run-live"}),
                encoding="utf-8",
            )
            current = store.get_run(run_id="run-live")
            self.assertEqual(current["status"], "running")
            self.assertEqual(current["quality"], "inconclusive")
            self.assertEqual(current["state_source"], "sqlite_runtime")
            self.assertEqual(
                Path(current["output_dir"]).resolve(strict=False),
                run_dir.resolve(strict=False),
            )
            env = {
                "LITMINER_WORKSPACE_ROOT": str(root),
                "LITMINER_STATE_STORE": str(state_path),
            }
            with patch.dict("os.environ", env, clear=False):
                mcp_current = mcp_server.tool_get_run({"output_dir": "run"})
            self.assertEqual(mcp_current["status"], "running")
            self.assertEqual(mcp_current["state_source"], "sqlite_runtime")

    def test_persisted_job_is_interrupted_after_worker_loss(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_path = root / ".litminer" / "state" / "litminer.sqlite3"
            StateStore(state_path).upsert_job({"job_id": "lost-job", "run_id": "run-1", "status": "running"})
            env = {"LITMINER_WORKSPACE_ROOT": str(root), "LITMINER_STATE_STORE": str(state_path)}
            with patch.dict("os.environ", env, clear=False):
                loaded = mcp_server._load_persisted_job("lost-job")
            self.assertEqual(loaded["status"], "interrupted")

    def test_provider_cooldown_persists_and_scheduler_skip_is_ledgered(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.sqlite3"
            first = StateStore(path)
            ProviderScheduler(first).record("openalex", status_class="rate_limited", retry_after_seconds=60)
            runtime = ProviderRuntime(StateStore(path), run_id="second-run")
            with self.assertRaises(ProviderCooldownError):
                runtime.execute("openalex", "search", "q", lambda: [])
            summary = StateStore(path).request_summary("second-run")
            self.assertEqual(summary["requests"], 1)
            self.assertEqual(summary["by_provider_status"][0]["status_class"], "rate_limited")

    def test_http_attempt_is_recorded_without_sensitive_url(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "state.sqlite3")
            runtime = ProviderRuntime(store, run_id="http-run")
            with patch("urllib.request.urlopen", return_value=_Response(b'{"ok": true}')):
                result = runtime.execute(
                    "openalex", "search", "sensitive query",
                    lambda: fetch_json("https://example.test/?token=secret", retry=RetryPolicy(max_retries=1)),
                )
            self.assertTrue(result["ok"])
            summary = store.request_summary("http-run")
            self.assertEqual(summary["requests"], 1)
            exported = json.loads(store.export_state(Path(tmp) / "state.json").read_text(encoding="utf-8"))
            metadata = exported["tables"]["provider_requests"][0]["metadata_json"]
            self.assertNotIn("secret", metadata)

    def test_coverage_distinguishes_degraded_inconclusive_and_healthy_empty(self) -> None:
        degraded = build_coverage_report(
            configured_sources=["openalex", "arxiv"], query_count=1, candidate_count=1,
            input_mode="discover", trace_rows=[
                {"provider": "openalex", "status": "ok", "status_class": "ok", "returned_count": "1"},
                {"provider": "arxiv", "status": "network_error", "status_class": "network", "returned_count": "0"},
            ],
        )
        self.assertEqual(degraded["quality"], "degraded")
        inconclusive = build_coverage_report(
            configured_sources=["openalex"], query_count=1, candidate_count=0, input_mode="discover",
            trace_rows=[{"provider": "openalex", "status": "network_error", "status_class": "network"}],
        )
        self.assertEqual(inconclusive["quality"], "inconclusive")
        healthy_empty = build_coverage_report(
            configured_sources=["openalex"], query_count=1, candidate_count=0, input_mode="discover",
            trace_rows=[{"provider": "openalex", "status": "empty_result", "status_class": "empty_or_missing"}],
        )
        self.assertEqual(healthy_empty["quality"], "healthy")

    def test_canonical_projection_prefers_trusted_crossref_and_records_reason(self) -> None:
        canonical, provenance, _values = canonicalize_row({
            "title": "Discovery title", "doi": "10.1/a", "publication_year": "2020",
            "crossref_status": "verified", "crossref_title": "Canonical title",
            "crossref_doi": "10.1/a", "crossref_year": "2021", "crossref_authors": "Doe, Jane",
        })
        self.assertEqual(canonical["title"], "Canonical title")
        self.assertEqual(canonical["publication_year"], "2021")
        self.assertEqual(canonical["trusted_bibliography"], "true")
        self.assertEqual(provenance["title"]["source"], "crossref")
        self.assertIn("Crossref", provenance["title"]["reason"])

    def test_bibliography_export_is_audited_deterministic_and_excludes_untrusted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            canonical = root / "canonical.csv"
            rows = [
                {"paper_id": "p1", "entry_type": "article", "title": "A & B", "authors": "Doe, Jane", "publication_year": "2024", "trusted_bibliography": "true", "export_eligible": "true", "retraction_status": "unknown"},
                {"paper_id": "p2", "entry_type": "article", "title": "A & C", "authors": "Doe, John", "publication_year": "2024", "trusted_bibliography": "true", "export_eligible": "true", "retraction_status": "unknown"},
                {"paper_id": "p3", "entry_type": "article", "title": "Unverified", "authors": "Roe, Ray", "publication_year": "2024", "trusted_bibliography": "false", "export_eligible": "false", "retraction_status": "unknown"},
                {"paper_id": "p4", "entry_type": "article", "title": "Inconsistent trust flags", "authors": "Roe, Ray", "publication_year": "2024", "trusted_bibliography": "false", "export_eligible": "true", "retraction_status": "unknown"},
            ]
            write_csv_atomic(rows, canonical)
            first = export_bibliography(canonical, root, formats=["ris", "bibtex"])
            bib_first = (root / "litminer_export.bib").read_text(encoding="utf-8")
            second = export_bibliography(canonical, root, formats=["ris", "bibtex"])
            self.assertEqual(bib_first, (root / "litminer_export.bib").read_text(encoding="utf-8"))
            self.assertEqual(first["exported_rows"], 2)
            self.assertEqual(first["excluded_reasons"]["unverified"], 2)
            self.assertEqual(first["outputs"]["bibtex"]["sha256"], second["outputs"]["bibtex"]["sha256"])
            self.assertIn(r"A \& B", bib_first)
            self.assertIn("Doe2024", bib_first)

    def test_export_output_prefix_cannot_escape_output_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            run_dir.mkdir()
            write_csv_atomic([{
                "paper_id": "doi:10.1/a", "entry_type": "article", "title": "Paper",
                "authors": "Doe, Jane", "publication_year": "2024", "doi": "10.1/a",
                "trusted_bibliography": "true", "export_eligible": "true",
                "retraction_status": "unknown",
            }], run_dir / "canonical_papers.csv")
            env = {
                "LITMINER_WORKSPACE_ROOT": str(root),
                "LITMINER_STATE_STORE": str(root / "state.sqlite3"),
            }
            with patch.dict("os.environ", env, clear=False):
                for unsafe_prefix in ("../escape", r"subdir\escape", "stream:escape"):
                    with self.subTest(output_prefix=unsafe_prefix):
                        with self.assertRaisesRegex(ValueError, "plain file prefix"):
                            mcp_server.tool_export({
                                "output_dir": "run",
                                "formats": ["ris"],
                                "output_prefix": unsafe_prefix,
                            })
                        with self.assertRaises(LitminerValidationError):
                            validate_json_schema(
                                {
                                    "output_dir": "run",
                                    "formats": ["ris"],
                                    "output_prefix": unsafe_prefix,
                                },
                                tool_contracts.schema_for("litminer_export") or {},
                            )
            self.assertFalse((root / "escape.ris").exists())

    def test_bibtex_duplicate_paper_ids_still_get_unique_stable_keys(self) -> None:
        rows = [
            {"paper_id": "same", "entry_type": "article", "title": "Alpha work", "authors": "Doe, Jane", "publication_year": "2024"},
            {"paper_id": "same", "entry_type": "article", "title": "Alpha study", "authors": "Doe, John", "publication_year": "2024"},
        ]
        first, conflicts = bibtex.serialize(rows)
        second, _ = bibtex.serialize(rows)
        self.assertEqual(first, second)
        self.assertEqual(conflicts, 2)
        self.assertIn("Doe2024Alphaa", first)
        self.assertIn("Doe2024Alphab", first)

    def test_mcp_provider_pagination_and_high_level_read_export_get(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = {"LITMINER_WORKSPACE_ROOT": str(root), "LITMINER_STATE_STORE": str(root / "state.sqlite3")}
            with patch.dict("os.environ", env, clear=False), patch(
                "litminer.sources.api.openalex_search.search",
                return_value=[{"title": f"Paper {i}"} for i in range(45)],
            ):
                page = mcp_server.tool_search_openalex({"query": "q", "page": 2, "page_size": 20})
            self.assertEqual(page["count"], 20)
            self.assertEqual(page["total_found"], 45)
            self.assertTrue(page["has_more"])

            run_dir = root / "run"
            run_dir.mkdir()
            write_csv_atomic([{
                "paper_id": "doi:10.1/a", "entry_type": "article", "title": "Paper",
                "authors": "Doe, Jane", "publication_year": "2024", "doi": "10.1/a",
                "trusted_bibliography": "true", "export_eligible": "true", "retraction_status": "unknown",
            }], run_dir / "canonical_papers.csv")
            outcome = {"run_id": "run-1", "status": "completed", "quality": "healthy", "output_dir": str(run_dir), "artifacts": {}}
            (run_dir / "run_outcome.json").write_text(json.dumps(outcome), encoding="utf-8")
            with patch.dict("os.environ", env, clear=False):
                read = mcp_server.tool_read_results({"output_dir": "run", "artifact": "canonical_papers", "page": 1, "page_size": 1})
                exported = mcp_server.tool_export({"output_dir": "run", "formats": ["ris", "bibtex"]})
                loaded = mcp_server.tool_get_run({"output_dir": "run"})
            self.assertEqual(read["total_rows"], 1)
            self.assertFalse(read["has_more"])
            self.assertEqual(exported["exported_rows"], 1)
            self.assertEqual(loaded["quality"], "healthy")

    def test_mcp_resume_returns_persisted_run_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            run_dir.mkdir()
            persisted_run_id = "workflow_existing_run"
            (run_dir / "run_manifest.json").write_text(
                json.dumps({"schema_version": 1, "run_id": persisted_run_id}),
                encoding="utf-8",
            )
            env = {
                "LITMINER_WORKSPACE_ROOT": str(root),
                "LITMINER_STATE_STORE": str(root / "state.sqlite3"),
            }
            with patch.dict("os.environ", env, clear=False), patch(
                "threading.Thread.start", return_value=None,
            ):
                resumed = mcp_server.tool_resume_run({
                    "queries": ["resume contract"],
                    "output_dir": "run",
                })
            try:
                self.assertEqual(resumed["run_id"], persisted_run_id)
                self.assertEqual(
                    mcp_server.JOBS[resumed["job_id"]]["run_id"],
                    persisted_run_id,
                )
            finally:
                mcp_server.JOBS.pop(resumed["job_id"], None)

    def test_error_classification_carries_provider_retry_metadata(self) -> None:
        exc = urllib.error.HTTPError("https://example.test", 429, "Too Many Requests", None, None)
        self.addCleanup(exc.close)
        exc.retry_after_seconds = 12
        envelope = classify_exception(exc, provider="openalex", stage="search")
        self.assertEqual(envelope.error_class, "rate_limited")
        self.assertEqual(envelope.provider, "openalex")
        self.assertEqual(envelope.http_status, 429)


if __name__ == "__main__":
    unittest.main()
