#!/usr/bin/env python3
"""Agent-facing Litminer workflow runner.

The runner is a stable substrate for literature-search Agents:

1. discover candidates through APIs or start from a CSV
2. merge and deduplicate
3. annotate/rank with caller-supplied semantic concepts
4. verify metadata through Crossref
5. annotate journal metrics when requested
6. build a DOI/publisher-page evidence queue

It does not perform final literature-review judgement and does not parse PDFs.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from litminer.engine import api_discovery
from litminer.engine import build_publisher_queue
from litminer.engine import dedupe_papers
from litminer.engine import journal_metrics
from litminer.engine import merge_csv
from litminer.engine import publisher_probe
from litminer.engine import processing_report
from litminer.engine import semantic_triage
from litminer.engine import validate_stage
from litminer.engine import workspace
from litminer.engine.common import read_csv_rows, write_csv_atomic, write_text_atomic
from litminer.sources.api import crossref_verify
from litminer.sources.api import unpaywall_lookup


DEFAULT_QUEUE_PRIORITIES = {"high", "medium", "needs_review"}
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "default.json"

RUNTIME_DEFAULTS = {
    "channels": {
        "openalex": True,
        "semantic_scholar": False,
        "arxiv": False,
        "europe_pmc": False,
        "crossref": True,
        "unpaywall": True,
        "journal_metrics": True,
        "publisher_probe": False,
    },
    "limits": {
        "max_results_per_query": 100,
        "semantic_query_limit": 3,
        "semantic_max_results": 50,
        "publisher_probe_limit": None,
        "publisher_probe_sleep": 0.5,
        "strict_discovery": False,
        "parallel_providers": False,
        "provider_workers": None,
        "unpaywall_sleep": 0.1,
    },
    "outputs": {
        "default_output_dir": workspace.DEFAULT_RUN_DIR,
        "screenshot_root": workspace.DEFAULT_SCREENSHOT_ROOT,
    },
    "evidence": {
        "require_doi_for_queue": True,
        "queue_priorities": "high,medium,needs_review",
        "include_metadata_blocked": False,
        "queue_strict_only": True,
    },
    "api": {
        "openalex_api_key_env": "OPENALEX_API_KEY",
        "openalex_mailto_env": "OPENALEX_MAILTO",
        "crossref_mailto_env": "CROSSREF_MAILTO",
        "unpaywall_email_env": "UNPAYWALL_EMAIL",
        "contact_email_env": "LITMINER_CONTACT_EMAIL",
        "openalex_work_types": "article",
    },
}


@dataclass
class RuntimeConfig:
    """Typed view over the runtime infrastructure config."""

    raw: dict[str, Any] = field(default_factory=dict)
    channels: dict[str, Any] = field(default_factory=dict)
    limits: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    evidence: dict[str, Any] = field(default_factory=dict)
    api: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_path(cls, path: Path | None = None) -> "RuntimeConfig":
        raw = load_runtime_config(path)
        return cls(
            raw=raw,
            channels=dict(raw.get("channels", {})),
            limits=dict(raw.get("limits", {})),
            outputs=dict(raw.get("outputs", {})),
            evidence=dict(raw.get("evidence", {})),
            api=dict(raw.get("api", {})),
        )

    def output_path(self, key: str, default: str) -> Path:
        return workspace.resolve_workspace_path(self.outputs.get(key) or default)


def write_rows(rows: list[dict[str, str]], output: Path,
               fallback_fields: list[str] | None = None) -> None:
    write_csv_atomic(rows, output, fallback_fields=fallback_fields)


def read_rows(path: Path) -> list[dict[str, str]]:
    _fieldnames, rows = read_csv_rows(path)
    return rows


def deep_merge(base: dict, override: dict) -> dict:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_runtime_config(path: Path | None = None) -> dict:
    config = dict(RUNTIME_DEFAULTS)
    if DEFAULT_CONFIG.exists():
        config = deep_merge(config, json.loads(DEFAULT_CONFIG.read_text(encoding="utf-8")))
    if path:
        config = deep_merge(config, json.loads(path.read_text(encoding="utf-8")))
    return config


def normalize_args(args: argparse.Namespace) -> argparse.Namespace:
    config = RuntimeConfig.from_path(getattr(args, "config", None))
    channels = config.channels
    limits = config.limits
    evidence = config.evidence
    api = config.api

    if getattr(args, "output_dir", None) is None:
        args.output_dir = config.output_path("default_output_dir", workspace.DEFAULT_RUN_DIR)
    if getattr(args, "screenshot_root", None) is None:
        args.screenshot_root = config.output_path("screenshot_root", workspace.DEFAULT_SCREENSHOT_ROOT)
    discovery_sources_from_config = getattr(args, "discovery_sources", None) is None
    if discovery_sources_from_config:
        sources = []
        if channels.get("openalex", True):
            sources.append("openalex")
        if channels.get("semantic_scholar", False):
            sources.append("semantic_scholar")
        if channels.get("arxiv", False):
            sources.append("arxiv")
        if channels.get("europe_pmc", False):
            sources.append("europe_pmc")
        args.discovery_sources = ",".join(sources) or "openalex"
    if getattr(args, "include_semantic_scholar", None) is None:
        args.include_semantic_scholar = bool(channels.get("semantic_scholar", False)) if discovery_sources_from_config else False
    if getattr(args, "include_arxiv", None) is None:
        args.include_arxiv = bool(channels.get("arxiv", False)) if discovery_sources_from_config else False
    if getattr(args, "include_europe_pmc", None) is None:
        args.include_europe_pmc = bool(channels.get("europe_pmc", False)) if discovery_sources_from_config else False
    if getattr(args, "skip_openalex", None) is None:
        args.skip_openalex = not bool(channels.get("openalex", True))
    if getattr(args, "skip_crossref", None) is None:
        args.skip_crossref = not bool(channels.get("crossref", True))
    if getattr(args, "skip_journal_metrics", None) is None:
        args.skip_journal_metrics = not bool(channels.get("journal_metrics", True))
    if getattr(args, "probe_publishers", None) is None:
        args.probe_publishers = bool(channels.get("publisher_probe", False))
    if getattr(args, "skip_unpaywall", None):
        args.enrich_unpaywall = False
    elif getattr(args, "enrich_unpaywall", None) is None:
        args.enrich_unpaywall = bool(channels.get("unpaywall", True))

    if getattr(args, "max_results_per_query", None) is None:
        args.max_results_per_query = int(limits.get("max_results_per_query") or 100)
    if getattr(args, "semantic_query_limit", None) is None:
        args.semantic_query_limit = limits.get("semantic_query_limit")
    if getattr(args, "semantic_max_results", None) is None:
        args.semantic_max_results = limits.get("semantic_max_results")
    if getattr(args, "probe_limit", None) is None:
        args.probe_limit = limits.get("publisher_probe_limit")
    if getattr(args, "probe_sleep", None) is None:
        args.probe_sleep = float(limits.get("publisher_probe_sleep", 0.5))
    if getattr(args, "unpaywall_sleep", None) is None:
        args.unpaywall_sleep = float(limits.get("unpaywall_sleep", 0.1))
    if getattr(args, "strict_discovery", None) is None:
        args.strict_discovery = bool(limits.get("strict_discovery", False))
    if getattr(args, "parallel_providers", None) is None:
        args.parallel_providers = bool(limits.get("parallel_providers", False))
    if getattr(args, "provider_workers", None) is None:
        args.provider_workers = limits.get("provider_workers")

    if getattr(args, "queue_priorities", None) is None:
        args.queue_priorities = evidence.get("queue_priorities") or "high,medium,needs_review"
    if getattr(args, "include_metadata_blocked", None) is None:
        args.include_metadata_blocked = bool(evidence.get("include_metadata_blocked", False))
    if getattr(args, "allow_missing_doi", None) is None:
        args.allow_missing_doi = not bool(evidence.get("require_doi_for_queue", True))
    if getattr(args, "queue_strict_only", None) is None:
        min_if = getattr(args, "min_if", None)
        args.queue_strict_only = bool(
            min_if is not None and evidence.get("queue_strict_only", True)
        )

    if getattr(args, "openalex_api_key", None) is None:
        key_env = api.get("openalex_api_key_env") or "OPENALEX_API_KEY"
        args.openalex_api_key = os.environ.get(str(key_env))
    if getattr(args, "openalex_mailto", None) is None:
        mailto_env = api.get("openalex_mailto_env") or "OPENALEX_MAILTO"
        contact_env = api.get("contact_email_env") or "LITMINER_CONTACT_EMAIL"
        args.openalex_mailto = (
            os.environ.get(str(mailto_env))
            or os.environ.get(str(contact_env))
            or None
        )
    if getattr(args, "openalex_work_types", None) is None:
        args.openalex_work_types = api.get("openalex_work_types", "article")

    crossref_env = api.get("crossref_mailto_env") or "CROSSREF_MAILTO"
    contact_env = api.get("contact_email_env") or "LITMINER_CONTACT_EMAIL"
    crossref_contact = os.environ.get(str(crossref_env)) or os.environ.get(str(contact_env))
    if crossref_contact and not os.environ.get("CROSSREF_MAILTO"):
        os.environ["CROSSREF_MAILTO"] = crossref_contact
    if getattr(args, "unpaywall_email", None) is None:
        unpaywall_env = api.get("unpaywall_email_env") or "UNPAYWALL_EMAIL"
        args.unpaywall_email = (
            os.environ.get(str(unpaywall_env))
            or os.environ.get(str(contact_env))
            or None
        )
    return args


def parse_set(value: str | list[str] | set[str] | None,
              default: set[str] | None = None) -> set[str]:
    if value is None:
        return set(default or set())
    if isinstance(value, set):
        return {str(item).strip() for item in value if str(item).strip()}
    if isinstance(value, list):
        raw = []
        for item in value:
            raw.extend(re.split(r"[,;]", str(item)))
    else:
        raw = re.split(r"[,;]", value)
    parsed = {item.strip() for item in raw if item.strip()}
    return parsed or set(default or set())


def load_queries(args: argparse.Namespace) -> list[str]:
    return api_discovery.load_queries(args.query or [], args.query_file)


def discover(args: argparse.Namespace, out_dir: Path) -> list[Path]:
    queries = load_queries(args)
    if not queries:
        return []

    query_file = out_dir / "queries.txt"
    write_text_atomic(query_file, "\n".join(queries) + "\n")

    sources = api_discovery.parse_sources(args.discovery_sources)
    if args.skip_openalex:
        sources = [source for source in sources if source != "openalex"]
    if args.include_semantic_scholar and "semantic_scholar" not in sources:
        sources.append("semantic_scholar")
    if args.include_arxiv and "arxiv" not in sources:
        sources.append("arxiv")
    if args.include_europe_pmc and "europe_pmc" not in sources:
        sources.append("europe_pmc")
    if not sources:
        raise SystemExit("No API discovery sources selected.")

    output = out_dir / "api_candidates.csv"
    api_discovery.discover_api(
        queries,
        output,
        sources=sources,
        year_from=args.year_from,
        year_to=args.year_to,
        max_results_per_query=args.max_results_per_query,
        semantic_query_limit=args.semantic_query_limit,
        semantic_max_results=args.semantic_max_results,
        openalex_api_key=args.openalex_api_key,
        openalex_mailto=args.openalex_mailto,
        openalex_work_types=args.openalex_work_types,
        strict_discovery=args.strict_discovery,
        parallel_providers=args.parallel_providers,
        provider_workers=args.provider_workers,
        trace_csv=out_dir / "api_discovery_trace.csv",
        report_md=out_dir / "api_discovery_report.md",
    )
    return [output]


def select_by_priority(input_path: Path, output_path: Path,
                       priorities: set[str],
                       include_metadata_blocked: bool = False) -> int:
    fields, rows = read_csv_rows(input_path)
    if not fields:
        raise SystemExit("Input CSV has no header")
    if priorities and "triage_priority" not in fields:
        raise SystemExit("Input CSV has no triage_priority column for priority selection")

    selected = []
    for row in rows:
        if not include_metadata_blocked and row.get("metadata_status") == "blocked":
            continue
        priority = (row.get("triage_priority") or "").strip()
        if not priority or priority in priorities:
            selected.append(row)
    write_rows(selected, output_path, fallback_fields=fields)
    return len(selected)


def count_trusted_crossref_rows(path: Path) -> int:
    trusted = {"verified", "title_recovered"}
    rows = read_rows(path)
    if not rows:
        return 0
    if "crossref_status" not in rows[0]:
        return len(rows)
    return sum(1 for row in rows if (row.get("crossref_status") or "").strip() in trusted)


def profile_path(args: argparse.Namespace) -> Path | None:
    return args.triage_profile


def preflight_warnings(args: argparse.Namespace) -> list[str]:
    warnings: list[str] = []
    if args.enrich_unpaywall and not args.unpaywall_email:
        warnings.append(
            "Unpaywall is enabled but no email was resolved from --unpaywall-email, "
            "UNPAYWALL_EMAIL, or LITMINER_CONTACT_EMAIL; Unpaywall rows will be skipped."
        )
    if not args.probe_publishers and (args.fields_needed or args.page_required_field):
        warnings.append(
            "Publisher-page fields were requested but publisher probing is disabled; "
            "publisher_queue.csv will be created without resolved access/html/pdf status."
        )
    try:
        sources = api_discovery.parse_sources(args.discovery_sources)
    except SystemExit:
        sources = []
    if "semantic_scholar" in sources and not (
        os.environ.get("SEMANTIC_SCHOLAR_API_KEY") or os.environ.get("S2_API_KEY")
    ):
        warnings.append(
            "Semantic Scholar is selected without SEMANTIC_SCHOLAR_API_KEY/S2_API_KEY; "
            "the free unauthenticated API is more likely to return HTTP 429 rate limits."
        )
    metrics_requested = getattr(args, "min_if", None) is not None or getattr(args, "metrics", None) is not None
    if metrics_requested and getattr(args, "skip_journal_metrics", False):
        warnings.append(
            "Journal metrics were requested but journal_metrics is disabled; "
            "metric annotation/filtering will be skipped."
        )
    if getattr(args, "min_if", None) is not None and not getattr(args, "queue_strict_only", False):
        warnings.append(
            "A minimum impact-factor threshold is set, but queue_strict_only is disabled; "
            "metric-fail and unverified rows may still enter the publisher queue."
        )
    return warnings


def make_report(out_dir: Path, counts: dict[str, int],
                args: argparse.Namespace,
                strict_path: Path | None,
                backup_path: Path | None,
                queue_priorities: set[str],
                warnings: list[str] | None = None) -> None:
    target = args.target_count
    feasible_count = counts.get("publisher_queue", 0)
    blocking_reasons: list[str] = []
    if counts.get("deduped", 0) == 0:
        blocking_reasons.append("No candidates remained after discovery/merge/deduplication.")
    if counts.get("triaged", 0) == 0:
        blocking_reasons.append("No rows reached semantic triage.")
    if feasible_count == 0:
        if args.min_if is not None and getattr(args, "skip_journal_metrics", False):
            blocking_reasons.append("Metric filtering was requested but journal metrics are disabled.")
        elif args.min_if is not None and counts.get("metric_pass", 0) == 0:
            blocking_reasons.append("No metric-pass candidates are available under the current IF threshold.")
        else:
            blocking_reasons.append("No candidates reached the publisher evidence queue under the current constraints.")
    if target is not None and feasible_count < target:
        blocking_reasons.append(
            f"Current feasible count {feasible_count} is below requested target {target}."
        )
    feasible = not blocking_reasons

    lines = [
        "# Litminer Feasibility Report",
        "",
        f"Output directory: `{out_dir}`",
        f"Year from: `{args.year_from or 'none'}`",
        f"Target count: `{target if target is not None else 'not specified'}`",
        f"Minimum IF: `{args.min_if if args.min_if is not None else 'not specified'}`",
        f"Metric queue mode: `{'strict-pass-only' if args.queue_strict_only else 'annotate-only'}`",
        f"Queued triage priorities: `{', '.join(sorted(queue_priorities))}`",
        f"Overall: `{'FEASIBLE' if feasible else 'NOT_FEASIBLE'}`",
        "",
        "## Counts",
        "",
    ]
    for key in [
        "discovery_files",
        "deduped",
        "crossref_verified",
        "crossref_title_recovered",
        "crossref_mismatch",
        "crossref_lookup_failed",
        "crossref_missing_doi",
        "crossref_title_lookup_failed",
        "triaged",
        "triage_high",
        "triage_medium",
        "triage_needs_review",
        "triage_low",
        "metadata_blocked",
        "selected_for_verification",
        "verified",
        "unpaywall_ok",
        "unpaywall_skipped_missing_email",
        "unpaywall_missing_doi",
        "unpaywall_not_found",
        "unpaywall_error",
        "metric_pass",
        "metric_backup",
        "publisher_queue",
        "publisher_probed",
    ]:
        if key in counts:
            lines.append(f"- {key}: {counts[key]}")

    lines.extend([
        "",
        "## Feasibility",
        "",
    ])
    if feasible:
        lines.append("The current constraints appear feasible from the available candidate set.")
    else:
        lines.append(
            "The current constraints do not reach the requested count. "
            "Do not fabricate rows; inspect lower-priority candidates or ask to relax constraints."
        )
        lines.append("")
        lines.append("Blocking reasons:")
        for reason in blocking_reasons:
            lines.append(f"- {reason}")
    if strict_path:
        lines.append(f"- Metric-pass table: `{strict_path.name}`")
    if backup_path:
        lines.append(f"- Metric backup table: `{backup_path.name}`")

    if warnings:
        lines.extend(["", "## Configuration Warnings", ""])
        for warning in warnings:
            lines.append(f"- {warning}")

    lines.extend([
        "",
        "## Next Actions",
        "",
        "- Use `triaged_candidates.csv` as the Agent review surface; scripts rank and tag but do not make final scientific judgement.",
        "- Use `publisher_queue.csv` to inspect DOI landing pages and publisher-visible article pages.",
        "- Use Unpaywall OA links as structured access hints when available; verify article-level claims on publisher-visible pages.",
        "- Record PDF/SI URLs when publisher pages expose them; PDF parsing is outside Litminer core.",
        "- Treat WebSearch as supplemental only; metadata and publisher pages remain the primary evidence path.",
    ])
    write_text_atomic(out_dir / "feasibility_report.md", "\n".join(lines) + "\n")


def run_crossref_stage(input_path: Path, out_dir: Path,
                       args: argparse.Namespace,
                       counts: dict[str, int]) -> Path:
    if args.skip_crossref:
        return input_path
    output_path = out_dir / "verified_candidates.csv"
    crossref_counts = crossref_verify.verify_csv(
        input_path,
        output_path,
        strict=False,
        title_lookup=True,
    )
    counts["crossref_verified"] = crossref_counts.get("verified", 0)
    counts["crossref_title_recovered"] = crossref_counts.get("title_recovered", 0)
    counts["crossref_mismatch"] = crossref_counts.get("mismatch", 0)
    counts["crossref_lookup_failed"] = crossref_counts.get("lookup_failed", 0)
    counts["crossref_missing_doi"] = crossref_counts.get("missing_doi", 0)
    counts["crossref_title_lookup_failed"] = crossref_counts.get("title_lookup_failed", 0)
    return output_path


def run_triage_stage(input_path: Path, out_dir: Path,
                     args: argparse.Namespace,
                     counts: dict[str, int]) -> Path:
    output_path = out_dir / "triaged_candidates.csv"
    triage_counts = semantic_triage.triage_csv(
        input_path,
        output_path,
        profile_path=profile_path(args),
        required_concepts=args.required_concept,
        optional_concepts=args.optional_concept,
        negative_concepts=args.negative_concept,
        year_from=args.year_from,
        year_to=args.year_to,
        require_doi=not args.allow_missing_doi,
        exclude_article_types=args.exclude_article_type,
    )
    counts["triaged"] = triage_counts["rows"]
    counts["triage_high"] = triage_counts["high"]
    counts["triage_medium"] = triage_counts["medium"]
    counts["triage_needs_review"] = triage_counts["needs_review"]
    counts["triage_low"] = triage_counts["low"]
    counts["metadata_blocked"] = triage_counts["metadata_blocked"]
    validate_stage.validate_stage(
        output_path,
        out_dir / "triage_validation.md",
        "triage",
    )
    return output_path


def run_unpaywall_stage(input_path: Path, out_dir: Path,
                        args: argparse.Namespace,
                        counts: dict[str, int]) -> Path:
    if not args.enrich_unpaywall:
        return input_path
    output_path = out_dir / "oa_annotated_candidates.csv"
    unpaywall_counts = unpaywall_lookup.annotate_csv(
        input_path,
        output_path,
        email=args.unpaywall_email,
        sleep_s=args.unpaywall_sleep,
    )
    counts["unpaywall_ok"] = unpaywall_counts.get("ok", 0)
    counts["unpaywall_skipped_missing_email"] = unpaywall_counts.get("skipped_missing_email", 0)
    counts["unpaywall_missing_doi"] = unpaywall_counts.get("missing_doi", 0)
    counts["unpaywall_not_found"] = unpaywall_counts.get("not_found", 0)
    counts["unpaywall_error"] = unpaywall_counts.get("error", 0)
    return output_path


def run_metrics_stage(input_path: Path, out_dir: Path,
                      args: argparse.Namespace,
                      counts: dict[str, int]) -> tuple[Path, Path | None, Path | None]:
    strict_path: Path | None = None
    backup_path: Path | None = None
    metrics_requested = args.min_if is not None or args.metrics
    if not metrics_requested or args.skip_journal_metrics:
        return input_path, strict_path, backup_path

    annotated = out_dir / "metrics_annotated_candidates.csv"
    strict_path = out_dir / "strict_candidates.csv"
    backup_path = out_dir / "backup_candidates.csv"
    journal_metrics.filter_csv(
        input_path,
        annotated,
        metrics_path=args.metrics or journal_metrics.DEFAULT_METRICS,
        min_if=args.min_if,
        pass_output=strict_path,
        backup_output=backup_path,
    )
    counts["metric_pass"] = len(read_rows(strict_path))
    counts["metric_backup"] = len(read_rows(backup_path))
    return (strict_path if args.queue_strict_only else annotated), strict_path, backup_path


def run_queue_stage(input_path: Path, out_dir: Path,
                    args: argparse.Namespace,
                    counts: dict[str, int],
                    queue_priorities: set[str]) -> Path:
    output_path = out_dir / "publisher_queue.csv"
    queue_counts = build_publisher_queue.build_queue(
        input_path,
        output_path,
        decisions=None,
        priorities=queue_priorities,
        screenshot_root=str(args.screenshot_root),
        require_doi=not args.allow_missing_doi,
        include_metadata_blocked=args.include_metadata_blocked,
        fields_needed=args.fields_needed,
        page_required_fields=args.page_required_field,
    )
    counts["publisher_queue"] = queue_counts["queued"]
    validate_stage.validate_stage(
        output_path,
        out_dir / "publisher_queue_validation.md",
        "queue",
    )
    return output_path


def run_publisher_probe_stage(input_path: Path, out_dir: Path,
                              args: argparse.Namespace,
                              counts: dict[str, int]) -> None:
    if not args.probe_publishers:
        return
    probed = out_dir / "publisher_queue_probed.csv"
    publisher_counts = publisher_probe.probe_csv(
        input_path,
        probed,
        limit=args.probe_limit,
        sleep_s=args.probe_sleep,
    )
    counts["publisher_probed"] = sum(publisher_counts.values())


def run(args: argparse.Namespace) -> dict[str, str]:
    args = normalize_args(args)
    warnings = preflight_warnings(args)
    for warning in warnings:
        print(f"WARNING: {warning}", file=sys.stderr)
    out_dir = args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}

    if args.input_csv:
        discovery_inputs = [args.input_csv]
    else:
        discovery_inputs = discover(args, out_dir)
    counts["discovery_files"] = len(discovery_inputs)
    if not discovery_inputs:
        raise SystemExit("No discovery inputs produced. Provide --input-csv or at least one --query.")

    merged = out_dir / "merged_candidates.csv"
    if len(discovery_inputs) == 1:
        merged = discovery_inputs[0]
    else:
        merge_csv.merge_csv(discovery_inputs, merged, allow_missing=True)

    deduped = out_dir / "deduped_candidates.csv"
    dedupe_papers.dedupe(merged, deduped, "doi", "title")
    counts["deduped"] = len(read_rows(deduped))

    triage_input = run_crossref_stage(deduped, out_dir, args, counts)
    triaged = run_triage_stage(triage_input, out_dir, args, counts)

    queue_priorities = parse_set(args.queue_priorities, DEFAULT_QUEUE_PRIORITIES)
    selected = out_dir / "selected_candidates.csv"
    counts["selected_for_verification"] = select_by_priority(
        triaged,
        selected,
        queue_priorities,
        include_metadata_blocked=args.include_metadata_blocked,
    )

    verified = selected
    counts["verified"] = (
        count_trusted_crossref_rows(selected)
        if not args.skip_crossref else len(read_rows(selected))
    )

    verified = run_unpaywall_stage(verified, out_dir, args, counts)
    metric_input, strict_path, backup_path = run_metrics_stage(verified, out_dir, args, counts)
    publisher_queue = run_queue_stage(metric_input, out_dir, args, counts, queue_priorities)
    run_publisher_probe_stage(publisher_queue, out_dir, args, counts)

    make_report(out_dir, counts, args, strict_path, backup_path, queue_priorities, warnings=warnings)
    processing_report.write_report(out_dir, out_dir / "processing_report.md")
    return {
        "output_dir": str(out_dir),
        "triaged_candidates": str(triaged),
        "feasibility_report": str(out_dir / "feasibility_report.md"),
        "processing_report": str(out_dir / "processing_report.md"),
        "publisher_queue": str(publisher_queue),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run an Agent-facing Litminer search workflow.")
    parser.add_argument("--input-csv", type=Path, default=None,
                        help="Start from an existing candidate CSV instead of running discovery")
    parser.add_argument("--query", action="append", default=None,
                        help="Search query; can be repeated")
    parser.add_argument("--query-file", type=Path, default=None)
    parser.add_argument("--year-from", type=int, default=None)
    parser.add_argument("--year-to", type=int, default=None)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--config", type=Path, default=None,
                        help="Runtime infrastructure config JSON: channels, API env names, limits, and outputs.")
    parser.add_argument("--triage-profile", type=Path, default=None,
                        help="JSON semantic triage profile")
    parser.add_argument("--required-concept", action="append", default=[],
                        help="Caller-supplied required concept, e.g. name=term1|term2")
    parser.add_argument("--optional-concept", action="append", default=[],
                        help="Caller-supplied optional concept")
    parser.add_argument("--negative-concept", action="append", default=[],
                        help="Caller-supplied negative tag. Rows are tagged, not deleted.")
    parser.add_argument("--exclude-article-type", action="append", default=[],
                        help="Metadata article type to mark as blocked, e.g. review")
    parser.add_argument("--queue-priorities", default=None,
                        help="Comma-separated triage priorities selected for verification/queue")
    parser.add_argument("--include-metadata-blocked", action="store_true", default=None,
                        help="Also verify/queue rows marked metadata_status=blocked")
    parser.add_argument("--fields-needed", action="append", default=None,
                        help="Task-specific field requested from publisher page; repeat or comma-separate")
    parser.add_argument("--page-required-field", action="append", default=None,
                        help="Generic publisher-page evidence field; repeat or comma-separate")
    parser.add_argument("--openalex-api-key", default=None)
    parser.add_argument("--openalex-mailto", default=None,
                        help="Contact email for OpenAlex polite pool")
    parser.add_argument("--openalex-work-types", default=None,
                        help="OpenAlex work type filter; comma/pipe-separated, or 'all' to disable")
    parser.add_argument("--discovery-sources", default=None,
                        help="Comma-separated API providers: openalex, semantic_scholar, arxiv, europe_pmc")
    parser.add_argument("--max-results-per-query", type=int, default=None)
    parser.add_argument("--skip-openalex", action="store_true", default=None)
    parser.add_argument("--include-semantic-scholar", action="store_true", default=None)
    parser.add_argument("--include-arxiv", action="store_true", default=None)
    parser.add_argument("--include-europe-pmc", action="store_true", default=None)
    parser.add_argument("--semantic-query-limit", type=int, default=None)
    parser.add_argument("--semantic-max-results", type=int, default=None)
    parser.add_argument("--skip-crossref", action="store_true", default=None)
    parser.add_argument("--strict-discovery", action="store_true", default=None,
                        help="Fail when provider errors prevent a reliable candidate set")
    parser.add_argument("--parallel-providers", action="store_true", default=None,
                        help="Run different discovery providers for the same query concurrently")
    parser.add_argument("--serial-providers", dest="parallel_providers",
                        action="store_false",
                        help="Disable provider concurrency even if config enables it")
    parser.add_argument("--provider-workers", type=int, default=None,
                        help="Max provider worker threads when --parallel-providers is set")
    parser.add_argument("--enrich-unpaywall", action="store_true", default=None,
                        help="Annotate verified DOI rows with Unpaywall OA links")
    parser.add_argument("--skip-unpaywall", action="store_true", default=None,
                        help="Disable Unpaywall annotation even when config enables it")
    parser.add_argument("--unpaywall-email", default=None,
                        help="Unpaywall email; also reads UNPAYWALL_EMAIL or LITMINER_CONTACT_EMAIL")
    parser.add_argument("--unpaywall-sleep", type=float, default=None)
    parser.add_argument("--metrics", type=Path, default=None)
    parser.add_argument("--min-if", type=float, default=None)
    parser.add_argument("--skip-journal-metrics", dest="skip_journal_metrics",
                        action="store_true", default=None,
                        help="Disable journal metric annotation/filtering")
    parser.add_argument("--enable-journal-metrics", dest="skip_journal_metrics",
                        action="store_false",
                        help="Enable journal metric annotation/filtering even if config disables it")
    parser.add_argument("--target-count", type=int, default=None)
    parser.add_argument("--queue-strict-only", dest="queue_strict_only",
                        action="store_true", default=None,
                        help="When metrics filtering is active, queue only metric-pass rows")
    parser.add_argument("--queue-all-metric-statuses", dest="queue_strict_only",
                        action="store_false",
                        help="When metrics filtering is active, keep fail/unverified rows in queue candidates")
    parser.add_argument("--allow-missing-doi", action="store_true", default=None)
    parser.add_argument("--screenshot-root", type=Path, default=None)
    parser.add_argument("--probe-publishers", action="store_true", default=None)
    parser.add_argument("--probe-limit", type=int, default=None)
    parser.add_argument("--probe-sleep", type=float, default=None)
    args = parser.parse_args()

    result = run(args)
    print(f"Litminer run complete: {result['output_dir']}", file=sys.stderr)
    print(f"Triaged candidates: {result['triaged_candidates']}", file=sys.stderr)
    print(f"Feasibility report: {result['feasibility_report']}", file=sys.stderr)
    print(f"Processing report: {result['processing_report']}", file=sys.stderr)


if __name__ == "__main__":
    main()
