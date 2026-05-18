import argparse
import csv
import json
import os
import sys
import tempfile
import time
import unittest
import urllib.error
from email.message import Message
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from litminer.engine import api_discovery
from litminer.engine import agent_summary
from litminer.engine import artifacts
from litminer.engine import bootstrap
from litminer.engine import build_publisher_queue
from litminer.engine import cache as cache_helpers
from litminer.engine import common
from litminer.engine import dedupe_papers
from litminer.engine import doctor
from litminer.engine import journal_metrics
from litminer.engine import offline_smoke
from litminer.engine import provenance
from litminer.engine import publisher_adapters
from litminer.engine import publisher_probe
from litminer.engine import query_plan
from litminer.engine import processing_report
from litminer.engine import run_lit_search
from litminer.engine import semantic_triage
from litminer.engine import source_strategy
from litminer.engine import websearch_import
from litminer.engine import workspace
from litminer.engine import workflow_state
from litminer.sources.api import arxiv_search
from litminer.sources.api import crossref_verify
from litminer.sources.api import europe_pmc_search
from litminer.sources.api import openalex_search
from litminer.sources.api import semantic_scholar_search
from litminer.sources.api import unpaywall_lookup
from litminer.sources.api.errors import ProviderSearchError
from litminer.sources.mcp import server as mcp_server


class LitminerCoreTests(unittest.TestCase):
    def test_api_discovery_records_provider_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            output = tmp_path / "candidates.csv"
            trace = tmp_path / "trace.csv"
            report = tmp_path / "report.md"

            with patch("litminer.engine.api_discovery.run_provider", side_effect=RuntimeError("boom")):
                result = api_discovery.discover_api(
                    ["query"],
                    output,
                    sources=["openalex"],
                    trace_csv=trace,
                    report_md=report,
                )

            self.assertEqual(result["candidate_count"], 0)
            with trace.open(encoding="utf-8", newline="") as handle:
                trace_rows = list(csv.DictReader(handle))
            self.assertEqual(trace_rows[0]["status"], "error")
            self.assertEqual(trace_rows[0]["status_class"], "error")
            self.assertIn("inspect_error", trace_rows[0]["next_action"])
            self.assertIn("boom", trace_rows[0]["error"])
            self.assertIn("Provider Statuses", report.read_text(encoding="utf-8"))

    def test_api_discovery_strict_mode_fails_on_provider_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            output = tmp_path / "candidates.csv"
            trace = tmp_path / "trace.csv"
            report = tmp_path / "report.md"

            with patch("litminer.engine.api_discovery.run_provider", side_effect=RuntimeError("boom")):
                with self.assertRaises(RuntimeError):
                    api_discovery.discover_api(
                        ["query"],
                        output,
                        sources=["openalex"],
                        trace_csv=trace,
                        report_md=report,
                        strict_discovery=True,
                    )

            self.assertTrue(output.exists())
            self.assertTrue(trace.exists())
            with trace.open(encoding="utf-8", newline="") as handle:
                trace_rows = list(csv.DictReader(handle))
            self.assertEqual(trace_rows[0]["status"], "error")

    def test_api_discovery_passes_year_to_to_providers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            output = tmp_path / "candidates.csv"
            trace = tmp_path / "trace.csv"

            with patch("litminer.engine.api_discovery.openalex_search.search", return_value=[]) as search:
                api_discovery.discover_api(
                    ["query"],
                    output,
                    sources=["openalex"],
                    year_from=2024,
                    year_to=2026,
                    trace_csv=trace,
                )

            self.assertEqual(search.call_args.kwargs["year_from"], 2024)
            self.assertEqual(search.call_args.kwargs["year_to"], 2026)
            with trace.open(encoding="utf-8", newline="") as handle:
                trace_rows = list(csv.DictReader(handle))
            self.assertEqual(trace_rows[0]["year_to"], "2026")

    def test_api_discovery_can_parallelize_provider_calls_preserving_trace_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            output = tmp_path / "candidates.csv"
            trace = tmp_path / "trace.csv"

            def fake_run_provider(provider, *args, **kwargs):
                return [{"title": provider, "doi": f"10.1234/{provider}"}]

            with patch("litminer.engine.api_discovery.run_provider", side_effect=fake_run_provider) as run_provider:
                result = api_discovery.discover_api(
                    ["query"],
                    output,
                    sources=["openalex", "arxiv"],
                    parallel_providers=True,
                    provider_workers=2,
                    trace_csv=trace,
                )

            self.assertEqual(run_provider.call_count, 2)
            self.assertEqual(result["candidate_count"], 2)
            with trace.open(encoding="utf-8", newline="") as handle:
                trace_rows = list(csv.DictReader(handle))
            self.assertEqual([row["provider"] for row in trace_rows], ["openalex", "arxiv"])

    def test_api_discovery_circuit_breaker_skips_failed_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            output = tmp_path / "candidates.csv"
            trace = tmp_path / "trace.csv"

            with patch("litminer.engine.api_discovery.run_provider", side_effect=RuntimeError("boom")) as run_provider:
                result = api_discovery.discover_api(
                    ["query one", "query two", "query three"],
                    output,
                    sources=["openalex"],
                    provider_failure_threshold=1,
                    trace_csv=trace,
                )

            self.assertEqual(run_provider.call_count, 1)
            self.assertEqual(result["provider_statuses"]["skipped_circuit_breaker"], 2)
            with trace.open(encoding="utf-8", newline="") as handle:
                trace_rows = list(csv.DictReader(handle))
            self.assertEqual(
                [row["status"] for row in trace_rows],
                ["error", "skipped_circuit_breaker", "skipped_circuit_breaker"],
            )
            self.assertEqual(trace_rows[1]["status_class"], "skipped")

    def test_api_discovery_cools_down_rate_limited_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            output = tmp_path / "candidates.csv"
            trace = tmp_path / "trace.csv"

            error = ProviderSearchError(
                "limited",
                status="rate_limited",
                retry_after_seconds=120,
                http_status=429,
                transient=True,
            )
            with patch("litminer.engine.api_discovery.run_provider", side_effect=error) as run_provider:
                result = api_discovery.discover_api(
                    ["query one", "query two"],
                    output,
                    sources=["semantic_scholar"],
                    provider_rate_limit_cooldown_seconds=30,
                    trace_csv=trace,
                )

            self.assertEqual(run_provider.call_count, 1)
            self.assertEqual(result["provider_status_classes"]["rate_limited"], 1)
            self.assertEqual(result["provider_status_classes"]["skipped"], 1)
            with trace.open(encoding="utf-8", newline="") as handle:
                trace_rows = list(csv.DictReader(handle))
            self.assertEqual(
                [row["status"] for row in trace_rows],
                ["rate_limited", "skipped_rate_limit_cooldown"],
            )
            self.assertEqual(trace_rows[0]["retry_after_seconds"], "120")
            self.assertIn("retry_provider", trace_rows[0]["next_action"])

    def test_openalex_work_type_filter_is_configurable(self) -> None:
        article_url = api_discovery.openalex_search._build_url(
            "query",
            None,
            None,
            1,
            10,
            work_types="article|review",
        )
        all_url = api_discovery.openalex_search._build_url(
            "query",
            None,
            None,
            1,
            10,
            work_types="all",
        )

        self.assertIn("type%3Aarticle%7Creview", article_url)
        self.assertNotIn("filter=", all_url)

    def test_provider_search_error_is_shared(self) -> None:
        self.assertIs(openalex_search.ProviderSearchError, ProviderSearchError)
        self.assertIs(semantic_scholar_search.ProviderSearchError, ProviderSearchError)
        self.assertIs(arxiv_search.ProviderSearchError, ProviderSearchError)
        self.assertIs(europe_pmc_search.ProviderSearchError, ProviderSearchError)

    def test_common_helpers_preserve_doi_parentheses_and_write_csv(self) -> None:
        self.assertEqual(
            common.normalize_doi("https://doi.org/10.1002/(SICI)1097-4571(199912)50:6)"),
            "10.1002/(sici)1097-4571(199912)50:6)",
        )
        self.assertEqual(common.normalize_doi("doi:10.1234/example)."), "10.1234/example")

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "out.csv"
            common.write_csv_atomic(
                [{"title": "Paper", "doi": "10.1234/example"}],
                output,
                fallback_fields=["title", "doi"],
            )
            fieldnames, rows = common.read_csv_rows(output)

        self.assertEqual(fieldnames, ["title", "doi"])
        self.assertEqual(rows[0]["doi"], "10.1234/example")

    def test_semantic_scholar_429_uses_rate_limited_status(self) -> None:
        headers = Message()
        headers["Retry-After"] = "0"
        error = urllib.error.HTTPError(
            url="https://api.semanticscholar.org/graph/v1/paper/search",
            code=429,
            msg="Too Many Requests",
            hdrs=headers,
            fp=None,
        )

        with (
            patch("litminer.sources.api.semantic_scholar_search.urllib.request.urlopen", side_effect=error),
            patch("litminer.sources.api.semantic_scholar_search.time.sleep") as sleep,
        ):
            with self.assertRaises(semantic_scholar_search.ProviderSearchError) as caught:
                semantic_scholar_search.search("clinical rag", year_from=2024, max_results=1)

        self.assertEqual(caught.exception.status, "rate_limited")
        self.assertEqual(caught.exception.retry_after_seconds, 0.0)
        self.assertEqual(caught.exception.http_status, 429)
        self.assertEqual(sleep.call_count, semantic_scholar_search.RATE_LIMIT_RETRIES - 1)

    def test_preflight_warnings_surface_configuration_gaps(self) -> None:
        args = argparse.Namespace(
            enrich_unpaywall=True,
            unpaywall_email=None,
            probe_publishers=False,
            fields_needed=["publisher evidence"],
            page_required_field=None,
            discovery_sources="semantic_scholar",
        )

        with patch.dict(os.environ, {}, clear=True):
            warnings = run_lit_search.preflight_warnings(args)

        self.assertTrue(any("Unpaywall is enabled" in warning for warning in warnings))
        self.assertTrue(any("Publisher-page fields" in warning for warning in warnings))
        self.assertTrue(any("Semantic Scholar is selected" in warning for warning in warnings))

    def test_run_report_marks_empty_candidate_set_not_feasible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_csv = tmp_path / "empty.csv"
            input_csv.write_text("title,doi,publication_year,journal,abstract\n", encoding="utf-8")
            out_dir = tmp_path / "run"

            args = argparse.Namespace(
                input_csv=input_csv,
                query=None,
                query_file=None,
                year_from=None,
                year_to=None,
                output_dir=out_dir,
                config=None,
                triage_profile=None,
                required_concept=[],
                optional_concept=[],
                negative_concept=[],
                exclude_article_type=[],
                queue_priorities="high,medium,needs_review",
                include_metadata_blocked=False,
                fields_needed=None,
                page_required_field=None,
                openalex_api_key=None,
                discovery_sources="openalex",
                max_results_per_query=100,
                skip_openalex=False,
                include_semantic_scholar=False,
                semantic_query_limit=3,
                semantic_max_results=50,
                skip_crossref=True,
                enrich_unpaywall=False,
                skip_unpaywall=True,
                unpaywall_email=None,
                unpaywall_sleep=0,
                metrics=None,
                min_if=None,
                target_count=None,
                queue_strict_only=False,
                allow_missing_doi=True,
                screenshot_root=tmp_path / "screens",
                probe_publishers=False,
                probe_limit=None,
                probe_sleep=0,
            )

            run_lit_search.run(args)
            report = (out_dir / "feasibility_report.md").read_text(encoding="utf-8")
            self.assertIn("Overall: `NOT_FEASIBLE`", report)
            self.assertIn("No candidates remained", report)
            self.assertTrue((out_dir / "processing_report.md").exists())

    def test_run_blocks_crossref_lookup_failures_before_queue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_csv = tmp_path / "input.csv"
            input_csv.write_text(
                "title,doi,publication_year,journal,abstract\n"
                "A precise paper,10.1234/missing,2026,Journal A,Reports external validation.\n",
                encoding="utf-8",
            )
            out_dir = tmp_path / "run"
            args = argparse.Namespace(
                input_csv=input_csv,
                query=None,
                query_file=None,
                year_from=2026,
                year_to=None,
                output_dir=out_dir,
                config=None,
                triage_profile=None,
                required_concept=["validation=external validation"],
                optional_concept=[],
                negative_concept=[],
                exclude_article_type=[],
                queue_priorities="high,medium,needs_review",
                include_metadata_blocked=False,
                fields_needed=None,
                page_required_field=None,
                openalex_api_key=None,
                discovery_sources="openalex",
                max_results_per_query=100,
                skip_openalex=False,
                include_semantic_scholar=False,
                semantic_query_limit=3,
                semantic_max_results=50,
                skip_crossref=False,
                enrich_unpaywall=False,
                skip_unpaywall=True,
                unpaywall_email=None,
                unpaywall_sleep=0,
                metrics=None,
                min_if=None,
                target_count=None,
                queue_strict_only=False,
                allow_missing_doi=None,
                screenshot_root=tmp_path / "screens",
                probe_publishers=False,
                probe_limit=None,
                probe_sleep=0,
            )

            with patch("litminer.sources.api.crossref_verify.verify_doi", return_value=None):
                run_lit_search.run(args)

            with (out_dir / "verified_candidates.csv").open(encoding="utf-8", newline="") as handle:
                verified = list(csv.DictReader(handle))
            self.assertEqual(verified[0]["crossref_status"], "lookup_failed")
            with (out_dir / "triaged_candidates.csv").open(encoding="utf-8", newline="") as handle:
                triaged = list(csv.DictReader(handle))
            self.assertEqual(triaged[0]["metadata_status"], "blocked")
            with (out_dir / "publisher_queue.csv").open(encoding="utf-8", newline="") as handle:
                queue = list(csv.DictReader(handle))
            self.assertEqual(queue, [])
            report = (out_dir / "feasibility_report.md").read_text(encoding="utf-8")
            self.assertIn("Overall: `NOT_FEASIBLE`", report)

    def test_crossref_title_recovery_runs_before_doi_required_triage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_csv = tmp_path / "input.csv"
            input_csv.write_text(
                "title,doi,publication_year,journal,abstract\n"
                "A precise paper title,,2026,Journal A,Reports external validation.\n",
                encoding="utf-8",
            )
            out_dir = tmp_path / "run"
            args = argparse.Namespace(
                input_csv=input_csv,
                query=None,
                query_file=None,
                year_from=2026,
                year_to=None,
                output_dir=out_dir,
                config=None,
                triage_profile=None,
                required_concept=["validation=external validation"],
                optional_concept=[],
                negative_concept=[],
                exclude_article_type=[],
                queue_priorities="high,medium,needs_review",
                include_metadata_blocked=False,
                fields_needed=None,
                page_required_field=None,
                openalex_api_key=None,
                discovery_sources="openalex",
                max_results_per_query=100,
                skip_openalex=False,
                include_semantic_scholar=False,
                semantic_query_limit=3,
                semantic_max_results=50,
                skip_crossref=False,
                enrich_unpaywall=False,
                skip_unpaywall=True,
                unpaywall_email=None,
                unpaywall_sleep=0,
                metrics=None,
                min_if=None,
                target_count=None,
                queue_strict_only=False,
                allow_missing_doi=None,
                screenshot_root=tmp_path / "screens",
                probe_publishers=False,
                probe_limit=None,
                probe_sleep=0,
            )
            candidates = [{
                "crossref_doi": "10.1234/recovered",
                "crossref_title": "A precise paper title",
                "crossref_container": "Journal A",
                "crossref_year": "2026",
            }]

            with patch("litminer.sources.api.crossref_verify.search_by_title", return_value=candidates):
                run_lit_search.run(args)

            with (out_dir / "publisher_queue.csv").open(encoding="utf-8", newline="") as handle:
                queue = list(csv.DictReader(handle))
            self.assertEqual(len(queue), 1)
            self.assertEqual(queue[0]["doi"], "10.1234/recovered")
            self.assertEqual(queue[0]["crossref_status"], "title_recovered")

    def test_semantic_triage_ignores_negated_required_concept(self) -> None:
        profile = semantic_triage.load_profile(
            required_specs=["external_validation=external validation"],
            optional_specs=["benchmark=benchmark data"],
        )
        row = {
            "title": "Benchmark dataset study",
            "abstract": "This paper reports benchmark data without any external validation.",
            "publication_year": "2026",
            "doi": "10.1234/example",
        }

        triaged = semantic_triage.triage_row(row, profile)
        self.assertEqual(triaged["matched_required"], "")
        self.assertIn("external_validation", triaged["missing_required"])

    def test_semantic_triage_pattern_cache_is_bounded(self) -> None:
        semantic_triage._PATTERN_CACHE.clear()
        with patch.object(semantic_triage, "MAX_PATTERN_CACHE_SIZE", 3):
            for index in range(5):
                semantic_triage.compile_pattern(f"pattern {index}")

        self.assertLessEqual(len(semantic_triage._PATTERN_CACHE), 3)
        self.assertNotIn(("pattern 0", True), semantic_triage._PATTERN_CACHE)

    def test_dedupe_merges_complementary_duplicate_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_csv = tmp_path / "dupes.csv"
            output_csv = tmp_path / "deduped.csv"
            input_csv.write_text(
                "title,doi,abstract,best_full_text_url,discovery_source\n"
                "Paper,10.1234/a,Abstract from OpenAlex,,openalex\n"
                "Paper,10.1234/a,,https://example.org/fulltext,semantic_scholar\n",
                encoding="utf-8",
            )

            dedupe_papers.dedupe(input_csv, output_csv, "doi", "title")

            with output_csv.open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["best_full_text_url"], "https://example.org/fulltext")
            self.assertEqual(rows[0]["duplicate_count"], "2")
            self.assertIn("openalex", rows[0]["merged_discovery_sources"])
            self.assertIn("semantic_scholar", rows[0]["merged_discovery_sources"])

    def test_journal_metrics_match_indexes_and_avoid_substring_matches(self) -> None:
        metric = journal_metrics.Metric(
            journal="Chemical Engineering Journal",
            aliases=["CEJ"],
            issns=["12345678"],
            impact_factor="12.3",
            metric_year="2026",
            metric_source="verified",
            source_url="https://example.org",
            last_checked="2026-05-14",
            confidence="high",
        )
        indexes = journal_metrics.build_indexes([metric])

        self.assertIs(
            journal_metrics.match_metric({"issn": "1234-5678"}, [metric], indexes=indexes),
            metric,
        )
        self.assertIs(
            journal_metrics.match_metric({"journal": "CEJ"}, [metric], indexes=indexes),
            metric,
        )
        self.assertIsNone(
            journal_metrics.match_metric(
                {"journal": "Chemical Engineering Journal Advances"},
                [metric],
                indexes=indexes,
            )
        )

    def test_build_publisher_queue_filters_metadata_and_keeps_requested_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_csv = tmp_path / "triaged.csv"
            output_csv = tmp_path / "queue.csv"
            input_csv.write_text(
                "title,doi,triage_priority,triage_score,metadata_status,candidate_status,crossref_status\n"
                "Ready,10.1234/ready,high,7.0,ok,ready_for_verification,verified\n"
                "Blocked,10.1234/blocked,high,6.0,blocked,metadata_blocked,lookup_failed\n"
                "No DOI,,high,5.0,ok,ready_for_verification,verified\n",
                encoding="utf-8",
            )

            counts = build_publisher_queue.build_queue(
                input_csv,
                output_csv,
                priorities={"high"},
                screenshot_root=str(tmp_path / "screens"),
                require_doi=True,
                fields_needed=["claim", "dataset"],
            )
            with output_csv.open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))

        self.assertEqual(counts["queued"], 1)
        self.assertEqual(counts["skipped_metadata_blocked"], 1)
        self.assertEqual(counts["skipped_missing_doi"], 1)
        self.assertEqual(rows[0]["doi"], "10.1234/ready")
        self.assertEqual(rows[0]["fields_needed"], "claim; dataset")

    def test_build_publisher_queue_rejects_priority_filter_without_triage_column(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_csv = tmp_path / "raw.csv"
            output_csv = tmp_path / "queue.csv"
            input_csv.write_text(
                "title,doi,publication_year\n"
                "Raw,10.1234/raw,2026\n",
                encoding="utf-8",
            )

            with self.assertRaises(SystemExit):
                build_publisher_queue.build_queue(
                    input_csv,
                    output_csv,
                    priorities={"high"},
                )

    def test_websearch_import_extracts_doi_from_url_and_marks_unverified(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_csv = tmp_path / "web.csv"
            output_csv = tmp_path / "out.csv"
            input_csv.write_text(
                "result_title,link,snippet\n"
                "A useful 2025 paper,https://doi.org/10.5555/example,Snippet text\n",
                encoding="utf-8",
            )

            counts = websearch_import.import_websearch(input_csv, output_csv, default_query="useful paper")
            with output_csv.open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))

        self.assertEqual(counts["with_doi"], 1)
        self.assertEqual(rows[0]["doi"], "10.5555/example")
        self.assertEqual(rows[0]["publication_year"], "2025")
        self.assertEqual(rows[0]["websearch_status"], "lead_unverified")

    def test_runtime_config_supplies_infrastructure_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "litminer_config.json"
            config_path.write_text(
                json.dumps({
                    "channels": {
                        "openalex": False,
                        "semantic_scholar": True,
                        "arxiv": True,
                        "europe_pmc": True,
                        "crossref": False,
                        "unpaywall": True,
                        "publisher_probe": True,
                    },
                    "limits": {
                        "max_results_per_query": 7,
                        "semantic_query_limit": 2,
                        "semantic_max_results": 11,
                        "publisher_probe_sleep": 0.1,
                        "unpaywall_sleep": 0.2,
                    },
                    "outputs": {
                        "default_output_dir": str(tmp_path / "configured_run"),
                        "screenshot_root": str(tmp_path / "shots"),
                    },
                    "evidence": {
                        "require_doi_for_queue": False,
                        "queue_priorities": "high,medium",
                        "include_metadata_blocked": True,
                    },
                }),
                encoding="utf-8",
            )
            args = argparse.Namespace(config=config_path)

            normalized = run_lit_search.normalize_args(args)

            self.assertTrue(normalized.skip_openalex)
            self.assertTrue(normalized.include_semantic_scholar)
            self.assertTrue(normalized.include_arxiv)
            self.assertTrue(normalized.include_europe_pmc)
            self.assertTrue(normalized.skip_crossref)
            self.assertTrue(normalized.enrich_unpaywall)
            self.assertTrue(normalized.probe_publishers)
            self.assertEqual(normalized.max_results_per_query, 7)
            self.assertEqual(normalized.semantic_query_limit, 2)
            self.assertEqual(normalized.semantic_max_results, 11)
            self.assertEqual(normalized.unpaywall_sleep, 0.2)
            self.assertEqual(normalized.queue_priorities, "high,medium")
            self.assertTrue(normalized.include_metadata_blocked)
            self.assertTrue(normalized.allow_missing_doi)
            self.assertFalse(normalized.queue_strict_only)
            self.assertEqual(normalized.output_dir, tmp_path / "configured_run")
            self.assertEqual(normalized.screenshot_root, tmp_path / "shots")

    def test_min_if_defaults_to_strict_metric_queue(self) -> None:
        args = argparse.Namespace(config=None, min_if=5.0)

        normalized = run_lit_search.normalize_args(args)

        self.assertTrue(normalized.queue_strict_only)

    def test_mcp_full_run_uses_configured_output_dir_when_omitted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace_root = Path(tmp)
            input_csv = workspace_root / "input.csv"
            input_csv.write_text(
                "title,doi,publication_year,journal,abstract,landing_page_url\n"
                "Paper,,2026,Journal A,Reports external validation,https://example.org/paper\n",
                encoding="utf-8",
            )
            config_path = workspace_root / "config.json"
            configured_run = workspace_root / "configured_run"
            config_path.write_text(
                json.dumps({
                    "channels": {
                        "crossref": False,
                        "unpaywall": False,
                        "publisher_probe": False,
                    },
                    "outputs": {
                        "default_output_dir": "configured_run",
                        "screenshot_root": "screens",
                    },
                    "evidence": {
                        "require_doi_for_queue": False,
                    },
                }),
                encoding="utf-8",
            )

            with patch.dict(os.environ, {"LITMINER_WORKSPACE_ROOT": str(workspace_root)}):
                result = mcp_server.tool_run_lit_search({
                    "input_csv": "input.csv",
                    "config": "config.json",
                    "required_concepts": ["validation=external validation"],
                    "allow_missing_doi": True,
                    "skip_crossref": True,
                    "skip_unpaywall": True,
                    "include_semantic_scholar": True,
                })

            self.assertEqual(Path(result["output_dir"]), configured_run)
            self.assertEqual(result["run_status"], "completed")
            self.assertIn("next_actions", result)
            self.assertIn("read_agent_summary_json", result["next_actions"])
            self.assertNotIn("use_as_verified_for_this_stage", result["next_actions"])
            self.assertTrue((configured_run / "processing_report.md").exists())
            self.assertTrue((configured_run / "run_manifest.json").exists())
            plan = json.loads((configured_run / "query_plan.json").read_text(encoding="utf-8"))
            self.assertEqual(plan["discovery_sources"], [])
            self.assertEqual(plan["source_strategy"]["source_selection"]["selection_origin"], "input_csv")
            self.assertEqual(plan["source_strategy"]["source_selection"]["configured_sources"], [])

    def test_run_resume_reuses_existing_stage_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_csv = tmp_path / "input.csv"
            input_csv.write_text(
                "title,doi,publication_year,journal,abstract\n"
                "Paper,10.1234/example,2026,Journal A,Reports external validation.\n",
                encoding="utf-8",
            )
            out_dir = tmp_path / "run"

            def make_args(resume: bool) -> argparse.Namespace:
                return argparse.Namespace(
                    input_csv=input_csv,
                    query=None,
                    query_file=None,
                    year_from=2026,
                    year_to=None,
                    output_dir=out_dir,
                    config=None,
                    mode="fast",
                    resume=resume,
                    triage_profile=None,
                    required_concept=["validation=external validation"],
                    optional_concept=[],
                    negative_concept=[],
                    exclude_article_type=[],
                    queue_priorities="high,medium,needs_review",
                    include_metadata_blocked=False,
                    fields_needed=None,
                    page_required_field=None,
                    openalex_api_key=None,
                    discovery_sources="openalex",
                    max_results_per_query=30,
                    skip_openalex=False,
                    include_semantic_scholar=False,
                    semantic_query_limit=3,
                    semantic_max_results=50,
                    skip_crossref=True,
                    strict_discovery=False,
                    parallel_providers=False,
                    provider_workers=None,
                    provider_failure_threshold=1,
                    enrich_unpaywall=False,
                    skip_unpaywall=True,
                    unpaywall_email=None,
                    unpaywall_sleep=0,
                    metrics=None,
                    min_if=None,
                    target_count=None,
                    queue_strict_only=False,
                    allow_missing_doi=False,
                    screenshot_root=tmp_path / "screens",
                    probe_publishers=False,
                    probe_limit=None,
                    probe_sleep=0,
                )

            run_lit_search.run(make_args(resume=False))
            summary = json.loads((out_dir / "agent_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["trust_tiers"]["crossref_trusted"], 0)
            self.assertEqual(agent_summary.build_summary(out_dir)["trust_tiers"]["crossref_trusted"], 0)
            self.assertTrue((out_dir / "processing_report.md").exists())

            with patch("litminer.engine.semantic_triage.triage_csv", side_effect=AssertionError("should resume")):
                run_lit_search.run(make_args(resume=True))

            manifest = json.loads((out_dir / "run_manifest.json").read_text(encoding="utf-8"))
            statuses = [stage["status"] for stage in manifest["stages"]]
            self.assertIn("skipped_existing", statuses)

            changed_args = make_args(resume=True)
            changed_args.required_concept = ["different=not present"]
            with self.assertRaises(SystemExit):
                run_lit_search.run(changed_args)

    def test_runtime_defaults_use_dot_litminer_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace_root = Path(tmp)
            args = argparse.Namespace(config=None)

            with patch.dict(os.environ, {"LITMINER_WORKSPACE_ROOT": str(workspace_root)}):
                normalized = run_lit_search.normalize_args(args)

            self.assertEqual(normalized.output_dir, workspace_root / ".litminer" / "runs" / "litminer_run")
            self.assertEqual(normalized.screenshot_root, workspace_root / ".litminer" / "screenshots")

    def test_workspace_path_falls_back_to_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            original_cwd = Path.cwd()
            try:
                os.chdir(tmp)
                with patch.dict(os.environ, {}, clear=True):
                    resolved = workspace.resolve_workspace_path(".litminer/runs/litminer_run")
            finally:
                os.chdir(original_cwd)

            self.assertEqual(resolved, Path(tmp).resolve() / ".litminer" / "runs" / "litminer_run")

    def test_fast_mode_supplies_first_pass_defaults(self) -> None:
        args = argparse.Namespace(config=None, mode="fast")

        normalized = run_lit_search.normalize_args(args)

        self.assertEqual(normalized.discovery_sources, "openalex")
        self.assertFalse(normalized.include_semantic_scholar)
        self.assertTrue(normalized.skip_crossref)
        self.assertFalse(normalized.enrich_unpaywall)
        self.assertFalse(normalized.probe_publishers)
        self.assertEqual(normalized.max_results_per_query, 30)

    def test_full_mode_keeps_domain_specific_sources_opt_in(self) -> None:
        args = argparse.Namespace(config=None, mode="full")

        normalized = run_lit_search.normalize_args(args)

        self.assertTrue(normalized.include_semantic_scholar)
        self.assertFalse(normalized.include_arxiv)
        self.assertFalse(normalized.include_europe_pmc)
        self.assertTrue(normalized.parallel_providers)

    def test_explicit_discovery_sources_override_config_channels(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "litminer_config.json"
            config_path.write_text(
                json.dumps({
                    "channels": {
                        "openalex": True,
                        "semantic_scholar": True,
                        "arxiv": True,
                        "europe_pmc": True,
                    }
                }),
                encoding="utf-8",
            )
            args = argparse.Namespace(config=config_path, discovery_sources="openalex")

            normalized = run_lit_search.normalize_args(args)

            self.assertEqual(normalized.discovery_sources, "openalex")
            self.assertFalse(normalized.include_semantic_scholar)
            self.assertFalse(normalized.include_arxiv)
            self.assertFalse(normalized.include_europe_pmc)

    def test_api_discovery_parses_registered_discovery_sources(self) -> None:
        parsed = api_discovery.parse_sources("oa,s2,arxiv,europe-pmc")

        self.assertEqual(parsed, ["openalex", "semantic_scholar", "arxiv", "europe_pmc"])
        capabilities = api_discovery.provider_capability_rows(["arxiv", "europe_pmc"])
        self.assertEqual(capabilities[0]["role"], "preprint_discovery")
        self.assertEqual(capabilities[1]["role"], "biomedical_fulltext_metadata_discovery")

    def test_arxiv_entry_maps_to_uniform_row(self) -> None:
        xml = """<?xml version="1.0" encoding="UTF-8"?>
        <entry xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
          <id>http://arxiv.org/abs/2501.01234v1</id>
          <updated>2025-01-04T00:00:00Z</updated>
          <published>2025-01-03T00:00:00Z</published>
          <title> A Useful Preprint </title>
          <summary> Line one.
          Line two. </summary>
          <author><name>Ada Lovelace</name></author>
          <author><name>Alan Turing</name></author>
          <link href="http://arxiv.org/abs/2501.01234v1" rel="alternate" type="text/html"/>
          <link title="pdf" href="http://arxiv.org/pdf/2501.01234v1" rel="related" type="application/pdf"/>
          <category term="cs.LG" scheme="http://arxiv.org/schemas/atom"/>
          <arxiv:doi>10.1234/example</arxiv:doi>
          <arxiv:journal_ref>Example Journal 1, 2</arxiv:journal_ref>
        </entry>"""
        entry = arxiv_search.ET.fromstring(xml)

        row = arxiv_search.entry_to_row(entry, source_query="all:example")

        self.assertEqual(row["title"], "A Useful Preprint")
        self.assertEqual(row["publication_year"], "2025")
        self.assertEqual(row["doi"], "10.1234/example")
        self.assertEqual(row["authors"], "Ada Lovelace; Alan Turing")
        self.assertEqual(row["arxiv_id"], "2501.01234v1")
        self.assertEqual(row["article_type"], "preprint")

    def test_europe_pmc_record_maps_to_uniform_row(self) -> None:
        record = {
            "source": "MED",
            "id": "12345678",
            "pmcid": "PMC123",
            "title": "A <i>useful</i> biomedical paper",
            "doi": "https://doi.org/10.5555/example",
            "pubYear": "2026",
            "journalTitle": "Example Medicine",
            "abstractText": "A <b>structured</b> abstract.",
            "pubType": "research-article",
            "citedByCount": 7,
            "authorString": "Doe J.; Roe R.",
            "isOpenAccess": "Y",
            "hasFullText": "Y",
            "inEPMC": "Y",
            "fullTextUrlList": {
                "fullTextUrl": [
                    {"availabilityCode": "S", "url": "https://example.org/landing"},
                    {"availabilityCode": "OA", "url": "https://example.org/fulltext"},
                ]
            },
        }

        row = europe_pmc_search.record_to_row(record, source_query="example")

        self.assertEqual(row["title"], "A useful biomedical paper")
        self.assertEqual(row["doi"], "10.5555/example")
        self.assertEqual(row["publication_year"], "2026")
        self.assertEqual(row["pmid"], "12345678")
        self.assertEqual(row["pmcid"], "PMC123")
        self.assertEqual(row["europe_pmc_id"], "MED:12345678")
        self.assertEqual(row["best_full_text_url"], "https://example.org/fulltext")

    def test_unpaywall_response_flattens_best_oa_location(self) -> None:
        result = {
            "status": "ok",
            "error": "",
            "data": {
                "is_oa": True,
                "oa_status": "green",
                "doi_url": "https://doi.org/10.1234/example",
                "oa_locations": [{"url": "https://repo.example/item"}],
                "best_oa_location": {
                    "url": "https://repo.example/item",
                    "url_for_landing_page": "https://repo.example/landing",
                    "url_for_pdf": "https://repo.example/paper.pdf",
                    "host_type": "repository",
                    "version": "acceptedVersion",
                    "license": "cc-by",
                    "evidence": "oa repository",
                    "repository_institution": "Example University",
                },
            },
        }

        flat = unpaywall_lookup.flatten_response(result, checked_at="2026-05-08T00:00:00Z")

        self.assertEqual(flat["unpaywall_status"], "ok")
        self.assertEqual(flat["is_oa"], "true")
        self.assertEqual(flat["oa_status"], "green")
        self.assertEqual(flat["oa_locations_count"], "1")
        self.assertEqual(flat["best_oa_pdf_url"], "https://repo.example/paper.pdf")
        self.assertEqual(flat["best_oa_host_type"], "repository")

    def test_unpaywall_429_returns_rate_limited_status(self) -> None:
        headers = Message()
        headers["Retry-After"] = "0"
        error = urllib.error.HTTPError(
            url="https://api.unpaywall.org/v2/10.1234/example",
            code=429,
            msg="Too Many Requests",
            hdrs=headers,
            fp=None,
        )

        with (
            patch("litminer.sources.api.unpaywall_lookup.urllib.request.urlopen", side_effect=error),
            patch("litminer.sources.api.unpaywall_lookup.time.sleep") as sleep,
        ):
            result = unpaywall_lookup.lookup_doi("10.1234/example", email="agent@example.org")

        flat = unpaywall_lookup.flatten_response(result, checked_at="2026-05-08T00:00:00Z")
        self.assertEqual(result["status"], "rate_limited")
        self.assertEqual(flat["unpaywall_retry_after_seconds"], "0.0")
        self.assertIn("rate limit", result["error"])
        self.assertEqual(sleep.call_count, unpaywall_lookup.MAX_RETRIES - 1)

    def test_unpaywall_rate_limited_rows_are_not_reused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "oa.csv"
            path.write_text(
                "title,doi,unpaywall_status\n"
                "Paper A,10.1234/a,ok\n"
                "Paper B,10.1234/b,rate_limited\n",
                encoding="utf-8",
            )

            cached = unpaywall_lookup._existing_annotated_rows(path)

            self.assertTrue(any("10.1234/a" in key for key in cached))
            self.assertFalse(any("10.1234/b" in key for key in cached))

    def test_processing_report_summarizes_workflow_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            triaged = tmp_path / "triaged_candidates.csv"
            triaged.write_text(
                "title,doi,publication_year,journal,discovery_source,triage_priority,metadata_status,candidate_status,llm_review_needed\n"
                "Paper A,10.1234/a,2026,Journal A,openalex,high,ok,ready,false\n"
                "Paper B,,2026,Journal B,websearch,needs_review,blocked,metadata_check,true\n",
                encoding="utf-8",
            )
            queue = tmp_path / "publisher_queue.csv"
            queue.write_text(
                "title,doi,doi_url,publisher_url,fields_needed,next_action,access_status,pdf_status\n"
                "Paper A,10.1234/a,https://doi.org/10.1234/a,https://doi.org/10.1234/a,abstract,Inspect page,pending,unknown\n",
                encoding="utf-8",
            )

            output = processing_report.write_report(tmp_path)
            text = output.read_text(encoding="utf-8")

            self.assertIn("Stage Counts", text)
            self.assertIn("Triage Summary", text)
            self.assertIn("Access And OA Hints", text)
            self.assertIn("Agent Guidance", text)

    def test_mcp_rejects_paths_outside_workspace(self) -> None:
        response = mcp_server.handle_request({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "litminer_dedupe",
                "arguments": {
                    "input_csv": "../outside.csv",
                    "output_csv": "check/out.csv",
                },
            },
        })

        self.assertIn("error", response)
        self.assertIn("escapes Litminer workspace", response["error"]["message"])
        self.assertIn("workspace_root=", response["error"]["message"])
        self.assertIn("resolved_path=", response["error"]["message"])
        self.assertNotIn("data", response["error"])

    def test_mcp_rejects_unsupported_protocol_version(self) -> None:
        response = mcp_server.handle_request({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "1900-01-01"},
        })

        self.assertIn("error", response)
        self.assertEqual(response["error"]["code"], -32602)

    def test_mcp_uses_configured_workspace_root_for_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            input_csv = workspace / "input.csv"
            input_csv.write_text(
                "title,doi,publication_year,journal\n"
                "Paper,10.1234/example,2026,Journal A\n",
                encoding="utf-8",
            )

            with patch.dict("os.environ", {"LITMINER_WORKSPACE_ROOT": str(workspace)}):
                response = mcp_server.handle_request({
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "litminer_dedupe",
                        "arguments": {
                            "input_csv": "input.csv",
                            "output_csv": "out/deduped.csv",
                        },
                    },
                })

            self.assertIn("result", response)
            self.assertTrue((workspace / "out" / "deduped.csv").exists())

    def test_mcp_default_profile_lists_workflow_tools_only(self) -> None:
        with patch.dict(os.environ, {"LITMINER_MCP_TOOL_PROFILE": "workflow"}):
            response = mcp_server.handle_request({
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/list",
                "params": {},
            })

        names = {tool["name"] for tool in response["result"]["tools"]}
        self.assertIn("litminer_run_lit_search", names)
        self.assertIn("litminer_agent_summary", names)
        self.assertIn("litminer_read_csv_summary", names)
        self.assertIn("litminer_workspace_doctor", names)
        self.assertNotIn("litminer_batch_verify_crossref", names)
        self.assertNotIn("litminer_search_openalex", names)

    def test_doctor_workspace_report_explains_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inside = root / "inside.csv"
            inside.write_text("title\n", encoding="utf-8")
            outside = root.parent / "outside.csv"

            report = doctor.workspace_report(
                workspace=root,
                explain_paths=["inside.csv", outside],
            )

        self.assertTrue(report["workspace_exists"])
        self.assertTrue(report["workspace_writable"])
        self.assertTrue(report["path_checks"][0]["inside_workspace"])
        self.assertFalse(report["path_checks"][1]["inside_workspace"])

    def test_mcp_workspace_doctor_reports_current_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "input.csv").write_text("title\n", encoding="utf-8")
            with patch.dict(os.environ, {"LITMINER_WORKSPACE_ROOT": str(root)}):
                result = mcp_server.tool_workspace_doctor({"paths": ["input.csv", "../outside.csv"]})

        self.assertEqual(result["status"], "ok")
        self.assertEqual(Path(result["workspace_root"]), root.resolve())
        self.assertTrue(result["path_checks"][0]["inside_workspace"])
        self.assertFalse(result["path_checks"][1]["inside_workspace"])

    def test_mcp_workspace_doctor_rejects_workspace_root_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            root.mkdir()
            outside = Path(tmp) / "outside"
            with patch.dict(os.environ, {"LITMINER_WORKSPACE_ROOT": str(root)}):
                with self.assertRaises(ValueError):
                    mcp_server.tool_workspace_doctor({
                        "workspace_root": str(outside),
                        "create_workspace": True,
                    })

    def test_mcp_batch_verify_crossref(self) -> None:
        with patch("litminer.sources.api.crossref_verify.verify_doi", return_value={"crossref_doi": "10.1234/a"}):
            result = mcp_server.tool_batch_verify_crossref({
                "dois": ["https://doi.org/10.1234/a", "10.1234/a", ""],
            })

        self.assertEqual(result["verified"], 1)
        self.assertEqual(result["skipped"], 2)
        self.assertEqual(result["results"][0]["doi"], "10.1234/a")

    def test_mcp_read_csv_summary_filters_and_pages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            csv_path = root / "triaged.csv"
            csv_path.write_text(
                "title,doi,triage_priority,candidate_status,metadata_status,triage_score\n"
                "A,10.1/a,high,ready,ok,5\n"
                "B,10.1/b,medium,ready,ok,4\n"
                "C,10.1/c,high,check,blocked,3\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"LITMINER_WORKSPACE_ROOT": str(root)}):
                result = mcp_server.tool_read_csv_summary({
                    "input_csv": "triaged.csv",
                    "priority": "high",
                    "page_size": 1,
                    "page": 2,
                    "columns": ["title", "triage_priority", "metadata_status"],
                })

        self.assertEqual(result["row_count"], 3)
        self.assertEqual(result["filtered_count"], 2)
        self.assertEqual(result["total_pages"], 2)
        self.assertEqual(result["rows"][0]["title"], "C")
        self.assertEqual(result["counts"]["triage_priority"]["high"], 2)

    def test_crossref_title_recovery_uses_context(self) -> None:
        candidates = [
            {
                "crossref_doi": "10.1234/wrong-year",
                "crossref_title": "A precise paper title",
                "crossref_container": "Journal A",
                "crossref_year": "2025",
            },
            {
                "crossref_doi": "10.1234/right",
                "crossref_title": "A precise paper title",
                "crossref_container": "Journal A",
                "crossref_year": "2026",
            },
        ]

        with patch("litminer.sources.api.crossref_verify.search_by_title", return_value=candidates):
            match = crossref_verify._best_title_match(
                "A precise paper title",
                input_row={"publication_year": "2026", "journal": "Journal A"},
            )

        self.assertIsNotNone(match)
        self.assertEqual(match["crossref_doi"], "10.1234/right")
        self.assertIn("crossref_recovered_doi_confidence", match)

    def test_crossref_title_lookup_failures_are_rate_limited(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_csv = tmp_path / "input.csv"
            output_csv = tmp_path / "output.csv"
            lines = ["title,doi,publication_year,journal"]
            lines.extend(f"Missing DOI paper {index},,2026,Journal A" for index in range(10))
            input_csv.write_text("\n".join(lines) + "\n", encoding="utf-8")

            with (
                patch("litminer.sources.api.crossref_verify.search_by_title", return_value=[]),
                patch("litminer.sources.api.crossref_verify.time.sleep") as sleep,
            ):
                counts = crossref_verify.verify_csv(input_csv, output_csv, title_lookup=True)

            self.assertEqual(counts["title_lookup_failed"], 10)
            sleep.assert_called_once_with(0.5)

    def test_crossref_retry_respects_retry_after_header(self) -> None:
        headers = Message()
        headers["Retry-After"] = "0"
        first = urllib.error.HTTPError(
            url="https://api.crossref.org/works/10.1234/example",
            code=429,
            msg="Too Many Requests",
            hdrs=headers,
            fp=None,
        )

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b'{"message": {"DOI": "10.1234/example", "title": ["Example"]}}'

        with (
            patch("litminer.sources.api.crossref_verify.urllib.request.urlopen", side_effect=[first, Response()]),
            patch("litminer.sources.api.crossref_verify.time.sleep") as sleep,
        ):
            data = crossref_verify._fetch_json("https://api.crossref.org/works/10.1234/example")

        self.assertEqual(data["message"]["DOI"], "10.1234/example")
        sleep.assert_called_once_with(0.0)

    def test_crossref_rate_limited_rows_are_not_reused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_csv = tmp_path / "input.csv"
            output_csv = tmp_path / "output.csv"
            input_csv.write_text("title,doi\nPaper,10.1234/a\n", encoding="utf-8")

            with patch(
                "litminer.sources.api.crossref_verify.verify_doi",
                side_effect=crossref_verify.CrossrefRateLimitError("limited", retry_after_seconds=0.0),
            ):
                counts = crossref_verify.verify_csv(input_csv, output_csv)

            self.assertEqual(counts["rate_limited"], 1)
            with output_csv.open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows[0]["crossref_status"], "rate_limited")
            self.assertEqual(rows[0]["crossref_retry_after_seconds"], "0.0")
            self.assertEqual(crossref_verify._existing_verified_rows(output_csv), {})

    def test_publisher_probe_marks_heuristic_status(self) -> None:
        row = publisher_probe.probe_row({})
        self.assertEqual(row["access_status"], "missing_url")
        self.assertEqual(row["publisher_probe_method"], "doi_or_url_http_heuristic")
        self.assertIn("Heuristic", row["publisher_probe_note"])

    def test_publisher_probe_blocks_private_urls(self) -> None:
        row = publisher_probe.probe_row({"publisher_url": "http://127.0.0.1:9/private"})
        self.assertEqual(row["access_status"], "blocked_url")
        self.assertIn("Blocked", row["publisher_probe_error"])

    def test_publisher_probe_caches_dns_resolution(self) -> None:
        publisher_probe._DNS_CACHE.clear()
        infos = [(0, 0, 0, "", ("93.184.216.34", 0))]
        with patch("litminer.engine.publisher_probe.socket.getaddrinfo", return_value=infos) as getaddrinfo:
            publisher_probe.validate_public_http_url("https://example.org/a")
            publisher_probe.validate_public_http_url("https://example.org/b")

        getaddrinfo.assert_called_once_with("example.org", None)

    def test_doctor_validates_config_types(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad_config.json"
            path.write_text(
                json.dumps({"limits": {"max_results_per_query": "many"}}),
                encoding="utf-8",
            )

            checks = doctor.validate_config(path)

        self.assertTrue(any(check.status == "error" for check in checks))
        self.assertTrue(any("max_results_per_query" in check.message for check in checks))

    def test_doctor_accepts_example_user_config(self) -> None:
        checks = doctor.validate_config(PROJECT_ROOT / "config" / "example.user.json")

        self.assertFalse([check for check in checks if check.status == "error"])

    def test_runner_rejects_invalid_runtime_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad_config.json"
            path.write_text(
                json.dumps({"limits": {"max_results_per_query": "many"}}),
                encoding="utf-8",
            )

            with self.assertRaises(SystemExit):
                run_lit_search.normalize_args(argparse.Namespace(config=path))

    def test_offline_smoke_generates_expected_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "smoke"

            result = offline_smoke.run(output_dir)

            self.assertGreaterEqual(int(result["publisher_queue_rows"]), 1)
            self.assertTrue((output_dir / "processing_report.md").exists())
            self.assertTrue((output_dir / "publisher_queue.csv").exists())

    def test_semantic_triage_supports_expression_concepts(self) -> None:
        profile = semantic_triage.load_profile(
            required_specs=[
                json.dumps({
                    "name": "photocatalytic_h2",
                    "all_of": [
                        "photocatalytic",
                        {"name": "h2_production", "near": ["hydrogen", "production"], "window": 8},
                    ],
                })
            ],
            negative_specs=[
                json.dumps({"name": "h2o2_only", "any_of": ["hydrogen peroxide", "H2O2"]})
            ],
        )

        true_row = {
            "title": "Photocatalytic hydrogen production with pollutant degradation",
            "abstract": "The catalyst couples hydrogen production and organic degradation.",
            "doi": "10.1234/ok",
            "publication_year": "2026",
        }
        peroxide_row = {
            "title": "Photocatalytic hydrogen peroxide production for degradation",
            "abstract": "The work focuses on H2O2 formation.",
            "doi": "10.1234/h2o2",
            "publication_year": "2026",
        }

        triaged_true = semantic_triage.triage_row(true_row, profile)
        triaged_peroxide = semantic_triage.triage_row(peroxide_row, profile)

        self.assertEqual(triaged_true["matched_required"], "photocatalytic_h2")
        self.assertEqual(triaged_true["matched_negative"], "")
        self.assertEqual(triaged_peroxide["matched_negative"], "h2o2_only")

    def test_semantic_triage_regex_concepts_are_opt_in(self) -> None:
        row = {"title": "Alpha beta", "abstract": ""}
        profile = semantic_triage.load_profile(required_specs=["alpha_regex=re:alpha"])

        with self.assertRaises(ValueError):
            semantic_triage.triage_row(row, profile)

        regex_profile = semantic_triage.load_profile(
            required_specs=["alpha_regex=re:alpha"],
            allow_regex=True,
        )
        triaged = semantic_triage.triage_row(row, regex_profile)
        self.assertEqual(triaged["matched_required"], "alpha_regex")

    def test_dedupe_records_key_confidence_and_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_csv = tmp_path / "dupes.csv"
            output_csv = tmp_path / "deduped.csv"
            input_csv.write_text(
                "title,doi,publication_year,journal\n"
                "Paper,10.1234/a,2026,Journal A\n"
                "Paper,https://doi.org/10.1234/a,2026,Journal A\n",
                encoding="utf-8",
            )

            dedupe_papers.dedupe(input_csv, output_csv, "doi", "title")

            with output_csv.open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows[0]["dedupe_confidence"], "high")
            self.assertEqual(rows[0]["dedupe_reason"], "exact DOI match")
            self.assertTrue(rows[0]["dedupe_key"].startswith("doi:10.1234/a"))

    def test_journal_metrics_validator_flags_governance_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "metrics.csv"
            path.write_text(
                "journal,aliases,issn,impact_factor,metric_year,metric_source,source_url,last_checked,confidence\n"
                "Journal A,Shared Alias,1234-5678,12.3,2024,JCR,https://example.org/a,2026-05-01,high\n"
                "Journal B,Shared Alias,8765-4321,not-a-number,2024,JCR,,2026-05-01,medium\n",
                encoding="utf-8",
            )

            result = journal_metrics.validate_metrics(path, require_numeric_if=True)

        self.assertEqual(result["status"], "error")
        self.assertTrue(any("impact_factor" in error for error in result["errors"]))
        self.assertTrue(any("maps to both" in error for error in result["errors"]))
        self.assertTrue(any("source_url is blank" in warning for warning in result["warnings"]))

    def test_query_plan_provenance_bootstrap_and_adapters(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            plan = query_plan.build_plan(
                queries=["photocatalytic hydrogen"],
                year_from=2026,
                required_concepts=["main=photocatalytic"],
                discovery_sources=["openalex"],
                controls={"time_budget_seconds": 60},
                mode="fast",
            )
            query_plan.write_plan(tmp_path, plan)
            input_csv = tmp_path / "queue.csv"
            input_csv.write_text(
                "title,doi,journal,publication_year,crossref_title,crossref_doi,crossref_container,crossref_year,journal_metric,journal_metric_verified,journal_metric_source\n"
                "Candidate,10.1234/a,Journal A,2026,Verified Candidate,10.1234/a,Journal A,2026,12.3,true,JCR\n",
                encoding="utf-8",
            )
            provenance_path = provenance.write_from_csv(input_csv, tmp_path / "field_provenance.json")
            report = bootstrap.build_report(workspace_root=tmp_path)

            self.assertEqual(plan["run_controls"]["time_budget_seconds"], 60)
            self.assertTrue((tmp_path / "query_plan.json").exists())
            self.assertEqual(
                json.loads(provenance_path.read_text(encoding="utf-8"))["records"][0]["fields"]["doi"]["source"],
                "crossref",
            )
            self.assertEqual(report["workspace_root"], str(tmp_path.resolve()))
        self.assertTrue(any(row["name"] == "http_heuristic" for row in publisher_adapters.adapter_rows()))

    def test_artifacts_index_groups_agent_primary_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "agent_summary.json").write_text("{}\n", encoding="utf-8")
            (tmp_path / "processing_report.md").write_text("# Report\n", encoding="utf-8")
            (tmp_path / "query_plan.json").write_text("{}\n", encoding="utf-8")

            index_path = artifacts.write_index(tmp_path)
            index = json.loads(index_path.read_text(encoding="utf-8"))

        self.assertIn("primary", index["by_tier"])
        self.assertIn("agent_summary", index["by_tier"]["primary"])
        self.assertTrue(any(item["name"] == "query_plan" and item["exists"] for item in index["artifacts"]))

    def test_query_plan_includes_advisory_source_strategy(self) -> None:
        plan = query_plan.build_plan(
            queries=["clinical enzyme stability validation"],
            year_from=2026,
            required_concepts=["validation=external validation"],
            discovery_sources=["openalex"],
            controls={
                "max_results_per_query": 50,
                "semantic_query_limit": 1,
                "discovery_sources_origin": "explicit",
                "configured_discovery_sources": "openalex",
            },
            mode="fast",
        )

        strategy = plan["source_strategy"]
        self.assertIn("biomedical", strategy["domain_tags"])
        self.assertIn("europe_pmc", strategy["missing_recommended_sources"])
        self.assertIn("semantic_scholar", strategy["missing_recommended_sources"])
        self.assertIn("single_query_low_recall_risk", strategy["risk_flags"])
        self.assertEqual(strategy["request_estimate"]["provider_calls"], 1)
        self.assertEqual(strategy["source_selection"]["selection_origin"], "explicit")
        self.assertFalse(strategy["source_selection"]["automatic_expansion"])
        self.assertIn("europe_pmc", strategy["source_selection"]["not_enabled_reasons"])

    def test_source_strategy_configured_sources_include_flags(self) -> None:
        strategy = source_strategy.build_strategy(
            queries=["clinical validation llm"],
            selected_sources=["semantic_scholar"],
            required_concepts=["validation=external validation"],
            controls={
                "discovery_sources_origin": "explicit",
                "configured_discovery_sources": "openalex",
                "skip_openalex": True,
                "include_semantic_scholar": True,
            },
            mode="fast",
        )

        selection = strategy["source_selection"]
        self.assertEqual(selection["raw_configured_sources"], ["openalex"])
        self.assertEqual(selection["configured_sources"], ["semantic_scholar"])
        self.assertIn("openalex", selection["recommended_not_selected"])
        self.assertEqual(selection["not_enabled_reasons"]["openalex"], "disabled by skip_openalex")

    def test_agent_summary_surfaces_source_strategy_and_primary_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            query_plan.write_plan(
                tmp_path,
                query_plan.build_plan(
                    queries=["machine learning enzyme stability"],
                    required_concepts=["validation=external validation"],
                    discovery_sources=["openalex"],
                    mode="fast",
                ),
            )

            summary = agent_summary.build_summary(tmp_path)

        self.assertIn("source_strategy", summary)
        self.assertIn("primary_artifacts", summary)
        self.assertIn("query_plan", summary["primary_artifacts"])
        self.assertIn("query_plan", summary["artifacts"])
        self.assertNotIn("artifacts_index", summary)
        self.assertIn("missing_recommended_sources", summary["source_strategy"])

    def test_api_discovery_classifies_network_provider_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            output = tmp_path / "candidates.csv"
            trace = tmp_path / "trace.csv"
            error = ProviderSearchError("certificate verify failed", status="network_error", transient=True)

            with patch("litminer.engine.api_discovery.run_provider", side_effect=error):
                result = api_discovery.discover_api(
                    ["query"],
                    output,
                    sources=["openalex"],
                    trace_csv=trace,
                )

            self.assertEqual(result["provider_status_classes"]["network"], 1)
            with trace.open(encoding="utf-8", newline="") as handle:
                trace_rows = list(csv.DictReader(handle))
            self.assertEqual(trace_rows[0]["status_class"], "network")
            self.assertIn("network", trace_rows[0]["next_action"])

    def test_api_discovery_uses_short_lived_provider_failure_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            cache_dir = tmp_path / "cache"
            first_trace = tmp_path / "trace1.csv"
            second_trace = tmp_path / "trace2.csv"
            error = ProviderSearchError("Too Many Requests", status="rate_limited", retry_after_seconds=30)

            with patch("litminer.engine.api_discovery.run_provider", side_effect=error) as provider:
                api_discovery.discover_api(
                    ["query"],
                    tmp_path / "candidates1.csv",
                    sources=["semantic_scholar"],
                    trace_csv=first_trace,
                    provider_failure_cache_dir=cache_dir,
                    provider_failure_cache_ttl_seconds=300,
                )
                api_discovery.discover_api(
                    ["query"],
                    tmp_path / "candidates2.csv",
                    sources=["semantic_scholar"],
                    trace_csv=second_trace,
                    provider_failure_cache_dir=cache_dir,
                    provider_failure_cache_ttl_seconds=300,
                )

            self.assertEqual(provider.call_count, 1)
            with second_trace.open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows[0]["status"], "skipped_cached_provider_failure")
            self.assertEqual(rows[0]["cache_status"], "hit")

    def test_api_discovery_does_not_cache_auth_failures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            cache_dir = tmp_path / "cache"
            first_trace = tmp_path / "trace1.csv"
            second_trace = tmp_path / "trace2.csv"
            error = ProviderSearchError("Forbidden", status="auth_error", http_status=403, transient=False)

            with patch("litminer.engine.api_discovery.run_provider", side_effect=error) as provider:
                api_discovery.discover_api(
                    ["query"],
                    tmp_path / "candidates1.csv",
                    sources=["openalex"],
                    trace_csv=first_trace,
                    provider_failure_cache_dir=cache_dir,
                    provider_failure_cache_ttl_seconds=300,
                )
                api_discovery.discover_api(
                    ["query"],
                    tmp_path / "candidates2.csv",
                    sources=["openalex"],
                    trace_csv=second_trace,
                    provider_failure_cache_dir=cache_dir,
                    provider_failure_cache_ttl_seconds=300,
                )

            self.assertEqual(provider.call_count, 2)
            with second_trace.open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows[0]["status"], "auth_error")
            self.assertEqual(rows[0]["cache_status"], "")

    def test_unpaywall_missing_email_rows_are_not_reused_when_email_is_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_csv = tmp_path / "input.csv"
            output_csv = tmp_path / "oa.csv"
            input_csv.write_text(
                "title,doi\nPaper,10.1234/a\n",
                encoding="utf-8",
            )

            with patch.dict(os.environ, {}, clear=True):
                unpaywall_lookup.annotate_csv(input_csv, output_csv, email=None, sleep_s=0)

            fake_result = {
                "status": "ok",
                "error": "",
                "data": {
                    "is_oa": False,
                    "oa_status": "closed",
                    "oa_locations": [],
                    "best_oa_location": None,
                    "doi_url": "https://doi.org/10.1234/a",
                },
            }
            with patch("litminer.sources.api.unpaywall_lookup.lookup_doi", return_value=fake_result) as lookup:
                unpaywall_lookup.annotate_csv(input_csv, output_csv, email="agent@example.org", sleep_s=0)

            self.assertEqual(lookup.call_count, 1)
            with output_csv.open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows[0]["unpaywall_status"], "ok")

    def test_unpaywall_cache_reuses_stable_doi_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_csv = tmp_path / "input.csv"
            output_csv = tmp_path / "oa.csv"
            cache_dir = tmp_path / "cache"
            input_csv.write_text("title,doi\nPaper,10.1234/a\n", encoding="utf-8")
            fake_result = {
                "status": "ok",
                "error": "",
                "data": {
                    "is_oa": True,
                    "oa_status": "green",
                    "oa_locations": [{"url": "https://repo.example/item"}],
                    "best_oa_location": {"url": "https://repo.example/item"},
                    "doi_url": "https://doi.org/10.1234/a",
                },
            }

            with patch("litminer.sources.api.unpaywall_lookup.lookup_doi", return_value=fake_result) as lookup:
                first = unpaywall_lookup.annotate_csv(
                    input_csv,
                    output_csv,
                    email="agent@example.org",
                    sleep_s=0,
                    cache_dir=cache_dir,
                    cache_ttl_days=30,
                )
                second = unpaywall_lookup.annotate_csv(
                    input_csv,
                    output_csv.with_name("oa2.csv"),
                    email=None,
                    sleep_s=0,
                    cache_dir=cache_dir,
                    cache_ttl_days=30,
                )

            self.assertEqual(lookup.call_count, 1)
            self.assertEqual(first["cache_store"], 1)
            self.assertEqual(second["cache_hit"], 1)

    def test_crossref_missing_doi_rows_are_retried_when_title_lookup_is_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_csv = tmp_path / "input.csv"
            output_csv = tmp_path / "verified.csv"
            input_csv.write_text(
                "title,doi,publication_year,journal\n"
                "Recoverable Paper,,2026,Journal A\n",
                encoding="utf-8",
            )
            output_csv.write_text(
                "title,doi,publication_year,journal,crossref_status,crossref_verified,crossref_mismatches\n"
                "Recoverable Paper,,2026,Journal A,missing_doi,false,NO_DOI\n",
                encoding="utf-8",
            )
            candidates = [{
                "crossref_doi": "10.1234/recovered",
                "crossref_title": "Recoverable Paper",
                "crossref_container": "Journal A",
                "crossref_year": "2026",
            }]

            with patch("litminer.sources.api.crossref_verify.search_by_title", return_value=candidates) as search:
                crossref_verify.verify_csv(input_csv, output_csv, title_lookup=True, checkpoint_interval=0)

            self.assertEqual(search.call_count, 1)
            with output_csv.open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows[0]["crossref_status"], "title_recovered")
            self.assertEqual(rows[0]["doi"], "10.1234/recovered")

    def test_crossref_batch_marks_network_errors_explicitly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_csv = tmp_path / "input.csv"
            output_csv = tmp_path / "verified.csv"
            input_csv.write_text(
                "title,doi\nPaper,10.1234/a\n",
                encoding="utf-8",
            )

            with patch(
                "litminer.sources.api.crossref_verify.verify_doi",
                side_effect=crossref_verify.CrossrefRequestError("certificate failed", status="network_error"),
            ):
                counts = crossref_verify.verify_csv(input_csv, output_csv, checkpoint_interval=0)

            self.assertEqual(counts["network_error"], 1)
            with output_csv.open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows[0]["crossref_status"], "network_error")
            self.assertEqual(rows[0]["crossref_verified"], "false")

    def test_crossref_cache_reuses_doi_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_csv = tmp_path / "input.csv"
            output_csv = tmp_path / "verified.csv"
            cache_dir = tmp_path / "cache"
            input_csv.write_text("title,doi\nPaper,10.1234/a\n", encoding="utf-8")
            meta = {
                "crossref_doi": "10.1234/a",
                "crossref_title": "Paper",
                "crossref_container": "",
                "crossref_year": "",
            }

            with patch("litminer.sources.api.crossref_verify.verify_doi", return_value=meta) as verify:
                first = crossref_verify.verify_csv(
                    input_csv,
                    output_csv,
                    checkpoint_interval=0,
                    cache_dir=cache_dir,
                    cache_ttl_days=30,
                )
                second = crossref_verify.verify_csv(
                    input_csv,
                    output_csv.with_name("verified2.csv"),
                    checkpoint_interval=0,
                    cache_dir=cache_dir,
                    cache_ttl_days=30,
                )

            self.assertEqual(verify.call_count, 1)
            self.assertEqual(first["cache_store"], 1)
            self.assertEqual(second["cache_hit"], 1)

    def test_manifest_does_not_treat_json_as_csv_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            output = tmp_path / "query_plan.json"
            output.write_text('{"schema_version": 1}\n', encoding="utf-8")
            manifest = {"stages": []}

            workflow_state.record_stage(manifest, "query_plan", "completed", output_path=output)

            self.assertEqual(manifest["stages"][0]["output_fields"], [])
            self.assertIn("output_path", manifest["stages"][0])
            self.assertEqual(manifest["stages"][0]["output"], manifest["stages"][0]["output_path"])

    def test_json_cache_read_modify_write_preserves_other_writers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "cache"
            first = cache_helpers.JsonCache(cache_dir, "shared")
            second = cache_helpers.JsonCache(cache_dir, "shared")

            self.assertIsNone(second.get("b"))
            first.set("a", {"value": "A"})
            second.set("b", {"value": "B"})
            fresh = cache_helpers.JsonCache(cache_dir, "shared")

            self.assertEqual(fresh.get("a").value["value"], "A")
            self.assertEqual(fresh.get("b").value["value"], "B")

    def test_run_stop_after_triage_writes_partial_control_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_csv = tmp_path / "input.csv"
            input_csv.write_text(
                "title,doi,publication_year,journal,abstract\n"
                "A precise paper,10.1234/a,2026,Journal A,Reports external validation.\n",
                encoding="utf-8",
            )
            out_dir = tmp_path / "run"
            args = argparse.Namespace(
                input_csv=input_csv,
                query=None,
                query_file=None,
                year_from=2026,
                year_to=None,
                output_dir=out_dir,
                config=None,
                mode="fast",
                resume=False,
                resume_allow_mismatch=False,
                resume_mismatch_reason="",
                time_budget_seconds=None,
                stop_after_stage="triage",
                triage_profile=None,
                required_concept=["validation=external validation"],
                optional_concept=[],
                negative_concept=[],
                exclude_article_type=[],
                queue_priorities="high,medium,needs_review",
                include_metadata_blocked=False,
                fields_needed=None,
                page_required_field=None,
                openalex_api_key=None,
                openalex_mailto=None,
                openalex_work_types=None,
                discovery_sources="openalex",
                max_results_per_query=100,
                skip_openalex=False,
                include_semantic_scholar=False,
                include_arxiv=False,
                include_europe_pmc=False,
                semantic_query_limit=3,
                semantic_max_results=50,
                skip_crossref=True,
                strict_discovery=False,
                parallel_providers=False,
                provider_workers=None,
                provider_failure_threshold=1,
                enrich_unpaywall=False,
                skip_unpaywall=True,
                unpaywall_email=None,
                unpaywall_sleep=0,
                crossref_checkpoint_interval=25,
                unpaywall_checkpoint_interval=25,
                max_crossref_rows=None,
                max_unpaywall_rows=None,
                metrics=None,
                min_if=None,
                skip_journal_metrics=True,
                target_count=None,
                queue_strict_only=False,
                allow_missing_doi=True,
                screenshot_root=tmp_path / "screens",
                probe_publishers=False,
                probe_limit=None,
                max_publisher_probe_rows=None,
                probe_sleep=0,
            )

            result = run_lit_search.run(args)

            self.assertEqual(result["status"], "partial")
            self.assertTrue((out_dir / "query_plan.json").exists())
            self.assertTrue((out_dir / "triaged_candidates.csv").exists())
            self.assertTrue((out_dir / "artifacts_index.json").exists())
            self.assertEqual(result["artifacts_index"], str(out_dir / "artifacts_index.json"))
            manifest = json.loads((out_dir / "run_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["run_status"], "partial")
            self.assertIn("started_at", manifest)
            self.assertIn("stop_reason", manifest)
            self.assertIn("output_path", manifest["stages"][0])
            self.assertTrue(manifest["cache"]["enabled"])
            summary = json.loads((out_dir / "agent_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["run_status"], "partial")
            self.assertTrue(summary["partial"])
            self.assertIn("artifact_tiers", summary)
            self.assertIn("Resume the run", summary["next_actions"][0])

    def test_run_marks_rate_limited_discovery_as_partial_stage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            out_dir = tmp_path / "run"

            def fake_discover(_queries, output, **_kwargs):
                output.write_text(
                    "title,doi,publication_year,journal,abstract\n"
                    "A precise paper,10.1234/a,2026,Journal A,Reports external validation.\n",
                    encoding="utf-8",
                )
                return {
                    "provider_status_classes": {"rate_limited": 1},
                    "provider_statuses": {"rate_limited": 1},
                }

            args = argparse.Namespace(
                input_csv=None,
                query=["external validation"],
                query_file=None,
                year_from=2026,
                year_to=None,
                output_dir=out_dir,
                config=None,
                mode="fast",
                resume=False,
                resume_allow_mismatch=False,
                resume_mismatch_reason="",
                time_budget_seconds=None,
                stop_after_stage=None,
                triage_profile=None,
                required_concept=[],
                optional_concept=[],
                negative_concept=[],
                exclude_article_type=[],
                queue_priorities="high,medium,needs_review",
                include_metadata_blocked=False,
                fields_needed=None,
                page_required_field=None,
                openalex_api_key=None,
                openalex_mailto=None,
                openalex_work_types=None,
                discovery_sources="openalex",
                max_results_per_query=100,
                skip_openalex=False,
                include_semantic_scholar=False,
                include_arxiv=False,
                include_europe_pmc=False,
                semantic_query_limit=3,
                semantic_max_results=50,
                skip_crossref=True,
                strict_discovery=False,
                parallel_providers=False,
                provider_workers=None,
                provider_failure_threshold=1,
                provider_rate_limit_cooldown_seconds=60,
                enrich_unpaywall=False,
                skip_unpaywall=True,
                unpaywall_email=None,
                unpaywall_sleep=0,
                crossref_checkpoint_interval=25,
                unpaywall_checkpoint_interval=25,
                max_crossref_rows=None,
                max_unpaywall_rows=None,
                metrics=None,
                min_if=None,
                skip_journal_metrics=True,
                target_count=None,
                queue_strict_only=False,
                allow_missing_doi=True,
                screenshot_root=tmp_path / "screens",
                probe_publishers=False,
                probe_limit=None,
                max_publisher_probe_rows=None,
                probe_sleep=0,
            )

            with patch("litminer.engine.api_discovery.discover_api", side_effect=fake_discover):
                result = run_lit_search.run(args)

            manifest = json.loads((out_dir / "run_manifest.json").read_text(encoding="utf-8"))
            discovery = [stage for stage in manifest["stages"] if stage["name"] == "discovery"][-1]
            self.assertEqual(result["status"], "partial")
            self.assertEqual(manifest["run_status"], "partial")
            self.assertEqual(discovery["status"], "partial_rate_limited")
            self.assertIn("rate limited", discovery["message"])

    def test_crossref_provider_failure_marks_stage_and_run_partial(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_csv = tmp_path / "input.csv"
            input_csv.write_text(
                "title,doi,publication_year,journal\n"
                "A precise paper,10.1234/a,2026,Journal A\n",
                encoding="utf-8",
            )
            out_dir = tmp_path / "run"
            out_dir.mkdir()
            manifest = {"stages": []}
            counts: dict[str, int] = {}

            def fake_verify(_input_path, output_path, **_kwargs):
                output_path.write_text(
                    "title,doi,crossref_status,crossref_verified\n"
                    "A precise paper,10.1234/a,network_error,false\n",
                    encoding="utf-8",
                )
                return {"rows": 1, "network_error": 1}

            args = argparse.Namespace(
                skip_crossref=False,
                resume=False,
                crossref_checkpoint_interval=25,
                max_crossref_rows=None,
                cache_dir=None,
                cache_ttl_days=30.0,
                cache_enabled=True,
            )
            with patch("litminer.engine.run_lit_search.crossref_verify.verify_csv", side_effect=fake_verify):
                run_lit_search.run_crossref_stage(input_csv, out_dir, args, counts, manifest=manifest)

            crossref = [stage for stage in manifest["stages"] if stage["name"] == "crossref"][-1]
            self.assertEqual(crossref["status"], "partial_network")
            self.assertEqual(run_lit_search.aggregate_run_status(manifest, "completed"), "partial")

    def test_unpaywall_missing_email_marks_stage_and_run_partial(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_csv = tmp_path / "input.csv"
            input_csv.write_text(
                "title,doi,publication_year,journal\n"
                "A precise paper,10.1234/a,2026,Journal A\n",
                encoding="utf-8",
            )
            out_dir = tmp_path / "run"
            out_dir.mkdir()
            manifest = {"stages": []}
            counts: dict[str, int] = {}
            args = argparse.Namespace(
                enrich_unpaywall=True,
                resume=False,
                unpaywall_email=None,
                unpaywall_sleep=0,
                unpaywall_checkpoint_interval=25,
                max_unpaywall_rows=None,
                cache_dir=None,
                cache_ttl_days=30.0,
                cache_enabled=True,
            )

            run_lit_search.run_unpaywall_stage(input_csv, out_dir, args, counts, manifest=manifest)

            unpaywall = [stage for stage in manifest["stages"] if stage["name"] == "unpaywall"][-1]
            self.assertEqual(unpaywall["status"], "partial_auth")
            self.assertEqual(run_lit_search.aggregate_run_status(manifest, "completed"), "partial")

    def test_resume_ignores_run_control_signature_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_csv = tmp_path / "input.csv"
            input_csv.write_text(
                "title,doi,publication_year,journal,abstract\n"
                "A precise paper,10.1234/a,2026,Journal A,Reports external validation.\n",
                encoding="utf-8",
            )
            out_dir = tmp_path / "run"

            base = dict(
                input_csv=input_csv,
                query=None,
                query_file=None,
                year_from=2026,
                year_to=None,
                output_dir=out_dir,
                config=None,
                mode="fast",
                resume=False,
                resume_allow_mismatch=False,
                resume_mismatch_reason="",
                time_budget_seconds=0,
                stop_after_stage="triage",
                triage_profile=None,
                required_concept=["validation=external validation"],
                optional_concept=[],
                negative_concept=[],
                exclude_article_type=[],
                queue_priorities="high,medium,needs_review",
                include_metadata_blocked=False,
                fields_needed=None,
                page_required_field=None,
                openalex_api_key=None,
                openalex_mailto=None,
                openalex_work_types=None,
                discovery_sources="openalex",
                max_results_per_query=100,
                skip_openalex=False,
                include_semantic_scholar=False,
                include_arxiv=False,
                include_europe_pmc=False,
                semantic_query_limit=3,
                semantic_max_results=50,
                skip_crossref=True,
                strict_discovery=False,
                parallel_providers=False,
                provider_workers=None,
                provider_failure_threshold=1,
                enrich_unpaywall=False,
                skip_unpaywall=True,
                unpaywall_email=None,
                unpaywall_sleep=0,
                crossref_checkpoint_interval=25,
                unpaywall_checkpoint_interval=25,
                max_crossref_rows=1,
                max_unpaywall_rows=1,
                metrics=None,
                min_if=None,
                skip_journal_metrics=True,
                target_count=None,
                queue_strict_only=False,
                allow_missing_doi=True,
                screenshot_root=tmp_path / "screens",
                probe_publishers=False,
                probe_limit=None,
                max_publisher_probe_rows=1,
                probe_sleep=0,
            )
            first = run_lit_search.run(argparse.Namespace(**base))

            resumed_args = argparse.Namespace(**{
                **base,
                "resume": True,
                "time_budget_seconds": None,
                "stop_after_stage": "queue",
                "max_crossref_rows": None,
                "max_unpaywall_rows": None,
                "max_publisher_probe_rows": None,
            })
            second = run_lit_search.run(resumed_args)

            self.assertEqual(first["status"], "partial")
            self.assertEqual(second["status"], "partial")
            self.assertTrue((out_dir / "publisher_queue.csv").exists())

    def test_budgeted_crossref_and_unpaywall_stages_are_not_reused_as_complete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_csv = tmp_path / "input.csv"
            input_csv.write_text(
                "title,doi,publication_year,journal\n"
                "A,10.1234/a,2026,Journal A\n"
                "B,10.1234/b,2026,Journal B\n",
                encoding="utf-8",
            )
            crossref_out = tmp_path / "verified.csv"
            unpaywall_out = tmp_path / "oa.csv"

            with patch("litminer.sources.api.crossref_verify.verify_doi", return_value={"crossref_doi": "10.1234/a"}):
                crossref_verify.verify_csv(input_csv, crossref_out, max_rows=1)
            crossref_cached = crossref_verify._existing_verified_rows(crossref_out)

            unpaywall_lookup.annotate_csv(input_csv, unpaywall_out, email=None, max_rows=1)
            unpaywall_cached = unpaywall_lookup._existing_annotated_rows(unpaywall_out)

            with crossref_out.open(encoding="utf-8", newline="") as handle:
                crossref_rows = list(csv.DictReader(handle))
            with unpaywall_out.open(encoding="utf-8", newline="") as handle:
                unpaywall_rows = list(csv.DictReader(handle))
            self.assertEqual(crossref_rows[1]["crossref_status"], "skipped_budget")
            self.assertFalse(any("10.1234/b" in key for key in crossref_cached))
            self.assertEqual(unpaywall_rows[1]["unpaywall_status"], "skipped_budget")
            self.assertFalse(any("10.1234/b" in key for key in unpaywall_cached))

    def test_mcp_all_profile_lists_advanced_tools(self) -> None:
        with patch.dict(os.environ, {"LITMINER_MCP_TOOL_PROFILE": "all"}):
            response = mcp_server.handle_request({
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/list",
                "params": {},
            })

        names = {tool["name"] for tool in response["result"]["tools"]}
        for name in {
            "litminer_bootstrap",
            "litminer_start_run",
            "litminer_run_status",
            "litminer_resume_run",
            "litminer_cancel_run",
            "litminer_validate_journal_metrics",
            "litminer_field_provenance",
            "litminer_publisher_adapters",
            "litminer_search_openalex",
            "litminer_batch_verify_crossref",
        }:
            self.assertIn(name, names)
        start_tool = next(tool for tool in response["result"]["tools"] if tool["name"] == "litminer_start_run")
        self.assertIn("input_csv", start_tool["inputSchema"]["properties"])

    def test_mcp_background_run_completes_partial_job(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "input.csv").write_text(
                "title,doi,publication_year,journal\n"
                "Paper,10.1234/a,2026,Journal A\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"LITMINER_WORKSPACE_ROOT": str(root)}):
                start = mcp_server.tool_start_run({
                    "input_csv": "input.csv",
                    "output_dir": "run",
                    "mode": "fast",
                    "stop_after_stage": "dedupe",
                })
                self.assertIn("poll_litminer_run_status", start["next_actions"])
                for _ in range(50):
                    status = mcp_server.tool_run_status({"job_id": start["job_id"]})
                    if status["status"] in {"completed", "partial", "failed"}:
                        break
                    time.sleep(0.05)

                self.assertEqual(status["status"], "partial")
                self.assertIn("read_agent_summary_json", status["next_actions"])
                self.assertIn("inspect_agent_summary_next_actions_before_resume", status["next_actions"])
                self.assertNotIn("use_as_verified_for_this_stage", status["next_actions"])
                self.assertTrue((root / "run" / "query_plan.json").exists())

                with mcp_server._jobs_lock:
                    mcp_server.JOBS.pop(start["job_id"], None)
                persisted = mcp_server.tool_run_status({"job_id": start["job_id"]})
                self.assertEqual(persisted["status"], "partial")
                self.assertEqual(Path(persisted["output_dir"]), root / "run")

    def test_mcp_cancel_run_stops_at_stage_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "input.csv").write_text(
                "title,doi,publication_year,journal\n"
                "Paper,10.1234/a,2026,Journal A\n",
                encoding="utf-8",
            )
            original = run_lit_search.write_query_plan_artifact

            def slow_query_plan(*args, **kwargs):
                result = original(*args, **kwargs)
                time.sleep(0.2)
                return result

            with (
                patch.dict(os.environ, {"LITMINER_WORKSPACE_ROOT": str(root)}),
                patch("litminer.engine.run_lit_search.write_query_plan_artifact", side_effect=slow_query_plan),
            ):
                start = mcp_server.tool_start_run({
                    "input_csv": "input.csv",
                    "output_dir": "cancelled",
                    "mode": "fast",
                })
                cancel = mcp_server.tool_cancel_run({"job_id": start["job_id"]})
                for _ in range(50):
                    status = mcp_server.tool_run_status({"job_id": start["job_id"]})
                    if status["status"] in {"completed", "partial", "failed"}:
                        break
                    time.sleep(0.05)

            self.assertEqual(cancel["status"], "cancel_requested")
            self.assertEqual(status["status"], "partial")
            manifest = json.loads((root / "cancelled" / "run_manifest.json").read_text(encoding="utf-8"))
            self.assertIn("Cancelled by background job request", manifest["stop_reason"])


if __name__ == "__main__":
    unittest.main()
