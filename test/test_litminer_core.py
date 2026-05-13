import argparse
import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from litminer.engine import api_discovery
from litminer.engine import dedupe_papers
from litminer.engine import publisher_probe
from litminer.engine import processing_report
from litminer.engine import run_lit_search
from litminer.engine import semantic_triage
from litminer.sources.api import arxiv_search
from litminer.sources.api import crossref_verify
from litminer.sources.api import europe_pmc_search
from litminer.sources.api import unpaywall_lookup
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
            self.assertIn("boom", trace_rows[0]["error"])
            self.assertIn("Provider Statuses", report.read_text(encoding="utf-8"))

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
        self.assertNotIn("data", response["error"])

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

    def test_publisher_probe_marks_heuristic_status(self) -> None:
        row = publisher_probe.probe_row({})
        self.assertEqual(row["access_status"], "missing_url")
        self.assertEqual(row["publisher_probe_method"], "doi_or_url_http_heuristic")
        self.assertIn("Heuristic", row["publisher_probe_note"])

    def test_publisher_probe_blocks_private_urls(self) -> None:
        row = publisher_probe.probe_row({"publisher_url": "http://127.0.0.1:9/private"})
        self.assertEqual(row["access_status"], "blocked_url")
        self.assertIn("Blocked", row["publisher_probe_error"])


if __name__ == "__main__":
    unittest.main()
