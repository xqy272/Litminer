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
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from litminer.engine import api_discovery
from litminer.engine import agent_summary
from litminer.engine import artifacts
from litminer.engine import build_publisher_queue
from litminer.engine import cache as cache_helpers
from litminer.engine import dedupe_papers
from litminer.engine import doctor
from litminer.engine import journal_metrics
from litminer.engine import merge_csv
from litminer.engine import publisher_probe
from litminer.engine import processing_report
from litminer.engine import provenance
from litminer.engine import publisher_adapters
from litminer.engine import semantic_triage
from litminer.engine import query_plan
from litminer.engine import status_policy
from litminer.engine import validate_stage
from litminer.engine import workspace
from litminer.engine import workflow_state
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
        "provider_failure_threshold": 2,
        "provider_rate_limit_cooldown_seconds": 60.0,
        "unpaywall_sleep": 0.1,
        "crossref_checkpoint_interval": 25,
        "unpaywall_checkpoint_interval": 25,
        "time_budget_seconds": None,
        "max_crossref_rows": None,
        "max_unpaywall_rows": None,
        "max_publisher_probe_rows": None,
    },
    "outputs": {
        "default_output_dir": workspace.DEFAULT_RUN_DIR,
        "screenshot_root": workspace.DEFAULT_SCREENSHOT_ROOT,
    },
    "cache": {
        "enabled": True,
        "cache_dir": cache_helpers.DEFAULT_CACHE_DIR,
        "ttl_days": cache_helpers.DEFAULT_TTL_DAYS,
        "provider_failure_ttl_seconds": cache_helpers.DEFAULT_PROVIDER_FAILURE_TTL_SECONDS,
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

RUN_MODE_PRESETS = {
    "fast": {
        "channels": {
            "openalex": True,
            "semantic_scholar": False,
            "arxiv": False,
            "europe_pmc": False,
            "crossref": False,
            "unpaywall": False,
            "publisher_probe": False,
        },
        "limits": {
            "max_results_per_query": 30,
            "parallel_providers": False,
            "provider_failure_threshold": 1,
        },
    },
    "balanced": {
        "channels": {
            "openalex": True,
            "semantic_scholar": False,
            "arxiv": False,
            "europe_pmc": False,
            "crossref": True,
            "unpaywall": True,
            "publisher_probe": False,
        },
        "limits": {
            "max_results_per_query": 100,
            "semantic_query_limit": 3,
            "semantic_max_results": 50,
            "parallel_providers": False,
            "provider_failure_threshold": 2,
        },
    },
    "full": {
        "channels": {
            "openalex": True,
            "semantic_scholar": True,
            "arxiv": False,
            "europe_pmc": False,
            "crossref": True,
            "unpaywall": True,
            "publisher_probe": False,
        },
        "limits": {
            "max_results_per_query": 100,
            "semantic_query_limit": 3,
            "semantic_max_results": 50,
            "parallel_providers": True,
            "provider_failure_threshold": 2,
        },
    },
    "expanded": {
        "channels": {
            "openalex": True,
            "semantic_scholar": True,
            "arxiv": False,
            "europe_pmc": False,
            "crossref": True,
            "unpaywall": True,
            "publisher_probe": False,
        },
        "limits": {
            "max_results_per_query": 100,
            "semantic_query_limit": 3,
            "semantic_max_results": 50,
            "parallel_providers": True,
            "provider_failure_threshold": 2,
        },
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
    cache: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_path(cls, path: Path | None = None, mode: str | None = None) -> "RuntimeConfig":
        raw = load_runtime_config(path)
        if mode:
            if mode not in RUN_MODE_PRESETS:
                raise ValueError(f"unknown run mode: {mode}")
            raw = deep_merge(raw, RUN_MODE_PRESETS[mode])
        return cls(
            raw=raw,
            channels=dict(raw.get("channels", {})),
            limits=dict(raw.get("limits", {})),
            outputs=dict(raw.get("outputs", {})),
            evidence=dict(raw.get("evidence", {})),
            api=dict(raw.get("api", {})),
            cache=dict(raw.get("cache", {})),
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


def load_checked_runtime_config(path: Path) -> dict:
    checks = doctor.validate_config(path)
    errors = [check.message for check in checks if check.status == "error"]
    if errors:
        raise SystemExit(f"Runtime config validation failed for {path}: {'; '.join(errors)}")
    return doctor.load_json(path)


def load_runtime_config(path: Path | None = None) -> dict:
    config = dict(RUNTIME_DEFAULTS)
    if DEFAULT_CONFIG.exists():
        config = deep_merge(config, load_checked_runtime_config(DEFAULT_CONFIG))
    if path:
        config = deep_merge(config, load_checked_runtime_config(path))
    return config


def normalize_args(args: argparse.Namespace) -> argparse.Namespace:
    config = RuntimeConfig.from_path(getattr(args, "config", None), mode=getattr(args, "mode", None))
    channels = config.channels
    limits = config.limits
    evidence = config.evidence
    api = config.api
    cache_config = config.cache

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
    if getattr(args, "crossref_checkpoint_interval", None) is None:
        args.crossref_checkpoint_interval = int(limits.get("crossref_checkpoint_interval", 25) or 0)
    if getattr(args, "unpaywall_checkpoint_interval", None) is None:
        args.unpaywall_checkpoint_interval = int(limits.get("unpaywall_checkpoint_interval", 25) or 0)
    if getattr(args, "time_budget_seconds", None) is None:
        args.time_budget_seconds = limits.get("time_budget_seconds")
    if getattr(args, "max_crossref_rows", None) is None:
        args.max_crossref_rows = limits.get("max_crossref_rows")
    if getattr(args, "max_unpaywall_rows", None) is None:
        args.max_unpaywall_rows = limits.get("max_unpaywall_rows")
    if getattr(args, "max_publisher_probe_rows", None) is None:
        args.max_publisher_probe_rows = limits.get("max_publisher_probe_rows")
    if getattr(args, "probe_limit", None) is None and getattr(args, "max_publisher_probe_rows", None) is not None:
        args.probe_limit = args.max_publisher_probe_rows
    if getattr(args, "strict_discovery", None) is None:
        args.strict_discovery = bool(limits.get("strict_discovery", False))
    if getattr(args, "parallel_providers", None) is None:
        args.parallel_providers = bool(limits.get("parallel_providers", False))
    if getattr(args, "provider_workers", None) is None:
        args.provider_workers = limits.get("provider_workers")
    if getattr(args, "provider_failure_threshold", None) is None:
        args.provider_failure_threshold = limits.get("provider_failure_threshold")
    if getattr(args, "provider_rate_limit_cooldown_seconds", None) is None:
        args.provider_rate_limit_cooldown_seconds = float(limits.get("provider_rate_limit_cooldown_seconds", 60.0))
    if getattr(args, "cache_enabled", None) is None:
        args.cache_enabled = bool(cache_config.get("enabled", True))
    if getattr(args, "cache_dir", None) is None:
        args.cache_dir = workspace.resolve_workspace_path(
            cache_config.get("cache_dir") or cache_helpers.DEFAULT_CACHE_DIR
        )
    if getattr(args, "cache_ttl_days", None) is None:
        args.cache_ttl_days = float(cache_config.get("ttl_days", cache_helpers.DEFAULT_TTL_DAYS))
    if getattr(args, "provider_failure_cache_ttl_seconds", None) is None:
        args.provider_failure_cache_ttl_seconds = float(
            cache_config.get(
                "provider_failure_ttl_seconds",
                cache_helpers.DEFAULT_PROVIDER_FAILURE_TTL_SECONDS,
            )
        )

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
    if getattr(args, "allow_regex_concepts", None) is None:
        args.allow_regex_concepts = False

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


def run_signature_payload(args: argparse.Namespace, queries: list[str]) -> dict[str, Any]:
    return {
        "input_csv": str(args.input_csv.resolve(strict=False)) if args.input_csv else "",
        "queries": queries,
        "year_from": args.year_from,
        "year_to": args.year_to,
        "mode": getattr(args, "mode", None) or "custom/default",
        "discovery_sources": args.discovery_sources,
        "max_results_per_query": args.max_results_per_query,
        "skip_openalex": args.skip_openalex,
        "include_semantic_scholar": args.include_semantic_scholar,
        "include_arxiv": args.include_arxiv,
        "include_europe_pmc": args.include_europe_pmc,
        "semantic_query_limit": args.semantic_query_limit,
        "semantic_max_results": args.semantic_max_results,
        "strict_discovery": args.strict_discovery,
        "parallel_providers": args.parallel_providers,
        "provider_workers": args.provider_workers,
        "provider_failure_threshold": args.provider_failure_threshold,
        "provider_rate_limit_cooldown_seconds": args.provider_rate_limit_cooldown_seconds,
        "openalex_work_types": args.openalex_work_types,
        "skip_crossref": args.skip_crossref,
        "enrich_unpaywall": args.enrich_unpaywall,
        "skip_unpaywall": args.skip_unpaywall,
        "skip_journal_metrics": args.skip_journal_metrics,
        "metrics": str(args.metrics.resolve(strict=False)) if args.metrics else "",
        "min_if": args.min_if,
        "queue_strict_only": args.queue_strict_only,
        "allow_missing_doi": args.allow_missing_doi,
        "queue_priorities": args.queue_priorities,
        "include_metadata_blocked": args.include_metadata_blocked,
        "fields_needed": args.fields_needed or [],
        "page_required_field": args.page_required_field or [],
        "probe_publishers": args.probe_publishers,
        "required_concept": args.required_concept or [],
        "optional_concept": args.optional_concept or [],
        "negative_concept": args.negative_concept or [],
        "exclude_article_type": args.exclude_article_type or [],
        "triage_profile": str(args.triage_profile.resolve(strict=False)) if args.triage_profile else "",
        "allow_regex_concepts": bool(getattr(args, "allow_regex_concepts", False)),
    }


def stage_files_exist(out_dir: Path) -> bool:
    stage_names = [
        "api_candidates.csv",
        "merged_candidates.csv",
        "deduped_candidates.csv",
        "verified_candidates.csv",
        "triaged_candidates.csv",
        "selected_candidates.csv",
        "oa_annotated_candidates.csv",
        "metrics_annotated_candidates.csv",
        "strict_candidates.csv",
        "backup_candidates.csv",
        "publisher_queue.csv",
        "publisher_queue_probed.csv",
    ]
    return any((out_dir / name).exists() for name in stage_names)


def validate_resume_manifest(
    out_dir: Path,
    args: argparse.Namespace,
    existing_manifest: dict[str, Any],
    signature: str,
) -> None:
    if not getattr(args, "resume", False):
        return
    if getattr(args, "resume_allow_mismatch", False):
        reason = str(getattr(args, "resume_mismatch_reason", "") or "").strip()
        if not reason:
            raise SystemExit(
                "--resume-allow-mismatch requires --resume-mismatch-reason so the unsafe reuse is auditable."
            )
        return
    existing_signature = str(existing_manifest.get("run_signature") or "")
    if existing_signature:
        if existing_signature != signature:
            raise SystemExit(
                "Cannot resume because current request parameters differ from run_manifest.json. "
                "Use a new --output-dir, remove --resume, or pass --resume-allow-mismatch only after manual review."
            )
        return
    if existing_manifest or stage_files_exist(out_dir):
        raise SystemExit(
            "Cannot safely resume: existing outputs have no run signature. "
            "Use a new --output-dir, remove --resume, or pass --resume-allow-mismatch only after manual review."
        )


def selected_discovery_sources(args: argparse.Namespace) -> list[str]:
    sources = api_discovery.parse_sources(args.discovery_sources)
    if args.skip_openalex:
        sources = [source for source in sources if source != "openalex"]
    if args.include_semantic_scholar and "semantic_scholar" not in sources:
        sources.append("semantic_scholar")
    if args.include_arxiv and "arxiv" not in sources:
        sources.append("arxiv")
    if args.include_europe_pmc and "europe_pmc" not in sources:
        sources.append("europe_pmc")
    return sources


def discover(args: argparse.Namespace, out_dir: Path,
             manifest: dict[str, Any] | None = None) -> list[Path]:
    queries = load_queries(args)
    if not queries:
        return []

    query_file = out_dir / "queries.txt"
    write_text_atomic(query_file, "\n".join(queries) + "\n")

    sources = selected_discovery_sources(args)
    if not sources:
        raise SystemExit("No API discovery sources selected.")

    output = out_dir / "api_candidates.csv"
    if getattr(args, "resume", False) and workflow_state.reusable_stage(manifest, "discovery", output):
        record_manifest_stage(
            out_dir,
            manifest,
            "discovery",
            "skipped_existing",
            output_path=output,
            row_count_value=workflow_state.row_count(output),
            message="Reused existing api_candidates.csv",
        )
        return [output]

    discovery_result = api_discovery.discover_api(
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
        provider_failure_threshold=args.provider_failure_threshold,
        provider_rate_limit_cooldown_seconds=args.provider_rate_limit_cooldown_seconds,
        provider_failure_cache_dir=args.cache_dir,
        provider_failure_cache_enabled=args.cache_enabled,
        provider_failure_cache_ttl_seconds=args.provider_failure_cache_ttl_seconds,
        trace_csv=out_dir / "api_discovery_trace.csv",
        report_md=out_dir / "api_discovery_report.md",
        run_id=manifest.get("run_id") if manifest else None,
    )
    status_classes = discovery_result.get("provider_status_classes", {})
    if isinstance(status_classes, dict) and status_classes.get("rate_limited"):
        discovery_status = "partial_rate_limited"
        discovery_message = "One or more discovery providers were rate limited; resume later with the same output_dir."
    elif isinstance(status_classes, dict) and (
        status_classes.get("auth")
        or status_classes.get("network")
        or status_classes.get("error")
        or status_classes.get("partial")
        or status_classes.get("skipped")
    ):
        discovery_status = "partial_source_failure"
        discovery_message = "One or more discovery providers failed or were skipped; inspect api_discovery_trace.csv."
    else:
        discovery_status = "completed"
        discovery_message = ""
    record_manifest_stage(
        out_dir,
        manifest,
        "discovery",
        discovery_status,
        output_path=output,
        row_count_value=workflow_state.row_count(output),
        message=discovery_message,
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
        return 0
    return sum(1 for row in rows if (row.get("crossref_status") or "").strip() in trusted)


def count_field_values(path: Path, field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in read_rows(path):
        value = (row.get(field) or "").strip() or "<blank>"
        counts[value] = counts.get(value, 0) + 1
    return counts


def profile_path(args: argparse.Namespace) -> Path | None:
    return args.triage_profile


def preflight_warnings(args: argparse.Namespace) -> list[str]:
    warnings: list[str] = []
    if getattr(args, "mode", None) == "fast":
        warnings.append(
            "Fast mode is enabled: Crossref verification, Unpaywall annotation, "
            "Semantic Scholar, and publisher probing are disabled unless explicitly re-enabled."
        )
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
    if getattr(args, "time_budget_seconds", None) not in (None, ""):
        warnings.append(
            "A run time budget is set; Litminer will stop only at stage boundaries and will write partial artifacts."
        )
    if getattr(args, "stop_after_stage", None):
        warnings.append(f"Run will stop after stage: {args.stop_after_stage}.")
    return warnings


def refresh_processing_report(out_dir: Path, warnings: list[str] | None = None) -> None:
    """Write best-effort Agent-facing reports after resumable stages."""
    try:
        processing_report.write_report(out_dir, out_dir / "processing_report.md")
    except Exception as exc:
        print(f"WARNING: failed to refresh processing_report.md: {exc}", file=sys.stderr)
    try:
        agent_summary.write_summary(out_dir, warnings=warnings)
    except Exception as exc:
        print(f"WARNING: failed to refresh agent_summary.json: {exc}", file=sys.stderr)


def record_manifest_stage(out_dir: Path,
                          manifest: dict[str, Any] | None,
                          name: str,
                          status: str,
                          *,
                          input_path: Path | None = None,
                          output_path: Path | None = None,
                          row_count_value: int | None = None,
                          message: str = "") -> None:
    if manifest is None:
        return
    workflow_state.record_stage(
        manifest,
        name,
        status,
        input_path=input_path,
        output_path=output_path,
        row_count_value=row_count_value,
        message=message,
    )
    workflow_state.write_manifest(out_dir, manifest)


def budget_seconds(args: argparse.Namespace) -> float | None:
    value = getattr(args, "time_budget_seconds", None)
    if value in (None, ""):
        return None
    return max(0.0, float(str(value)))


def budget_exceeded(args: argparse.Namespace, started_at: float) -> bool:
    budget = budget_seconds(args)
    return budget is not None and (time.monotonic() - started_at) >= budget


def cancellation_requested(args: argparse.Namespace) -> bool:
    cancel_check = getattr(args, "cancel_check", None)
    if callable(cancel_check):
        return bool(cancel_check())
    return bool(getattr(args, "cancel_requested", False))


def should_stop_after(args: argparse.Namespace, stage_name: str, started_at: float) -> tuple[bool, str]:
    if cancellation_requested(args):
        return True, f"Cancelled by background job request after stage: {stage_name}"
    requested = (getattr(args, "stop_after_stage", None) or "").strip()
    if requested and requested == stage_name:
        return True, f"Stopped after requested stage: {stage_name}"
    if budget_exceeded(args, started_at):
        budget = budget_seconds(args)
        return True, f"Stopped after stage {stage_name}: time budget {budget:g}s exhausted"
    return False, ""


PARTIAL_RUN_STATUS_CLASSES = {"auth", "budget_limited", "error", "network", "partial", "rate_limited"}


def aggregate_run_status(manifest: dict[str, Any], requested_status: str) -> str:
    if requested_status != "completed":
        return requested_status
    stages = manifest.get("stages", [])
    if not isinstance(stages, list):
        return requested_status
    for stage in stages:
        if not isinstance(stage, dict):
            continue
        status_class = status_policy.classify_status(str(stage.get("status") or ""))
        if status_class in PARTIAL_RUN_STATUS_CLASSES:
            return "partial"
    return requested_status


def write_query_plan_artifact(
    out_dir: Path,
    args: argparse.Namespace,
    queries: list[str],
    sources: list[str],
    manifest: dict[str, Any] | None = None,
) -> Path:
    plan = query_plan.build_plan(
        queries=queries,
        year_from=args.year_from,
        year_to=args.year_to,
        required_concepts=args.required_concept,
        optional_concepts=args.optional_concept,
        negative_concepts=args.negative_concept,
        discovery_sources=sources,
        mode=getattr(args, "mode", None) or "custom/default",
        controls={
            "max_results_per_query": getattr(args, "max_results_per_query", None),
            "semantic_query_limit": getattr(args, "semantic_query_limit", None),
            "semantic_max_results": getattr(args, "semantic_max_results", None),
            "parallel_providers": getattr(args, "parallel_providers", None),
            "provider_failure_threshold": getattr(args, "provider_failure_threshold", None),
            "provider_rate_limit_cooldown_seconds": getattr(args, "provider_rate_limit_cooldown_seconds", None),
            "time_budget_seconds": getattr(args, "time_budget_seconds", None),
            "stop_after_stage": getattr(args, "stop_after_stage", None),
            "max_crossref_rows": getattr(args, "max_crossref_rows", None),
            "max_unpaywall_rows": getattr(args, "max_unpaywall_rows", None),
            "max_publisher_probe_rows": getattr(args, "max_publisher_probe_rows", None),
            "cache_enabled": getattr(args, "cache_enabled", None),
            "cache_ttl_days": getattr(args, "cache_ttl_days", None),
            "provider_failure_cache_ttl_seconds": getattr(args, "provider_failure_cache_ttl_seconds", None),
        },
    )
    path = query_plan.write_plan(out_dir, plan)
    record_manifest_stage(
        out_dir,
        manifest,
        "query_plan",
        "completed",
        output_path=path,
        row_count_value=0,
    )
    return path


def write_provenance_artifact(
    input_path: Path,
    out_dir: Path,
    manifest: dict[str, Any] | None = None,
) -> Path:
    path = out_dir / provenance.PROVENANCE_NAME
    provenance.write_from_csv(input_path, path)
    record_manifest_stage(
        out_dir,
        manifest,
        "field_provenance",
        "completed",
        input_path=input_path,
        output_path=path,
        row_count_value=workflow_state.row_count(input_path),
    )
    return path


def write_publisher_adapters_artifact(out_dir: Path) -> Path:
    path = out_dir / "publisher_adapters.json"
    write_text_atomic(
        path,
        json.dumps({"schema_version": 1, "adapters": publisher_adapters.adapter_rows()}, indent=2) + "\n",
    )
    return path


def finalize_run(
    out_dir: Path,
    manifest: dict[str, Any],
    counts: dict[str, int],
    args: argparse.Namespace,
    strict_path: Path | None,
    backup_path: Path | None,
    queue_priorities: set[str],
    warnings: list[str],
    *,
    run_status: str = "completed",
    stop_reason: str = "",
    triaged: Path | None = None,
    publisher_queue: Path | None = None,
) -> dict[str, str]:
    if stop_reason:
        warnings = [*warnings, stop_reason]
        record_manifest_stage(
            out_dir,
            manifest,
            "run_control",
            "stopped",
            message=stop_reason,
        )
    final_status = aggregate_run_status(manifest, run_status)
    if final_status == "partial" and run_status == "completed":
        warnings = [*warnings, "One or more stages completed with partial, rate-limit, budget, or error status."]
    make_report(out_dir, counts, args, strict_path, backup_path, queue_priorities, warnings=warnings)
    write_publisher_adapters_artifact(out_dir)
    manifest["run_status"] = final_status
    if stop_reason:
        manifest["stop_reason"] = stop_reason
    manifest["completed_at"] = workflow_state.utc_now()
    workflow_state.write_manifest(out_dir, manifest)
    artifact_index_path = artifacts.write_index(out_dir)
    refresh_processing_report(out_dir, warnings=warnings)
    artifact_index_path = artifacts.write_index(out_dir)
    return {
        "status": final_status,
        "output_dir": str(out_dir),
        "triaged_candidates": str(triaged or out_dir / "triaged_candidates.csv"),
        "feasibility_report": str(out_dir / "feasibility_report.md"),
        "processing_report": str(out_dir / "processing_report.md"),
        "agent_summary": str(out_dir / agent_summary.SUMMARY_NAME),
        "query_plan": str(out_dir / query_plan.PLAN_NAME),
        "field_provenance": str(out_dir / provenance.PROVENANCE_NAME),
        "publisher_adapters": str(out_dir / "publisher_adapters.json"),
        "publisher_queue": str(publisher_queue or out_dir / "publisher_queue.csv"),
        "run_manifest": str(workflow_state.manifest_path(out_dir)),
        "artifacts_index": str(artifact_index_path),
    }


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
        f"Run mode: `{getattr(args, 'mode', None) or 'custom/default'}`",
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
        "crossref_rate_limited",
        "crossref_network_error",
        "crossref_auth_error",
        "crossref_response_parse_error",
        "crossref_provider_error",
        "crossref_skipped_budget",
        "triaged",
        "triage_high",
        "triage_medium",
        "triage_needs_review",
        "triage_low",
        "metadata_blocked",
        "selected_for_verification",
        "verified",
        "selected_unverified",
        "unpaywall_ok",
        "unpaywall_skipped_missing_email",
        "unpaywall_missing_doi",
        "unpaywall_not_found",
        "unpaywall_rate_limited",
        "unpaywall_network_error",
        "unpaywall_response_parse_error",
        "unpaywall_error",
        "unpaywall_skipped_budget",
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
                       counts: dict[str, int],
                       manifest: dict[str, Any] | None = None) -> Path:
    if args.skip_crossref:
        record_manifest_stage(
            out_dir,
            manifest,
            "crossref",
            "skipped_disabled",
            input_path=input_path,
            output_path=input_path,
            row_count_value=workflow_state.row_count(input_path),
        )
        return input_path
    output_path = out_dir / "verified_candidates.csv"
    if getattr(args, "resume", False) and workflow_state.reusable_stage(
        manifest,
        "crossref",
        output_path,
        input_path=input_path,
    ):
        status_counts = count_field_values(output_path, "crossref_status")
        counts["crossref_verified"] = status_counts.get("verified", 0)
        counts["crossref_title_recovered"] = status_counts.get("title_recovered", 0)
        counts["crossref_mismatch"] = status_counts.get("mismatch", 0)
        counts["crossref_lookup_failed"] = status_counts.get("lookup_failed", 0)
        counts["crossref_missing_doi"] = status_counts.get("missing_doi", 0)
        counts["crossref_title_lookup_failed"] = status_counts.get("title_lookup_failed", 0)
        counts["crossref_rate_limited"] = status_counts.get("rate_limited", 0)
        counts["crossref_network_error"] = status_counts.get("network_error", 0)
        counts["crossref_auth_error"] = status_counts.get("auth_error", 0)
        counts["crossref_response_parse_error"] = status_counts.get("response_parse_error", 0)
        counts["crossref_provider_error"] = status_counts.get("provider_error", 0)
        counts["crossref_skipped_budget"] = status_counts.get("skipped_budget", 0)
        cache_counts = count_field_values(output_path, "crossref_cache_status")
        counts["crossref_cache_hit"] = cache_counts.get("hit", 0)
        counts["crossref_cache_store"] = cache_counts.get("store", 0)
        record_manifest_stage(
            out_dir,
            manifest,
            "crossref",
            "skipped_existing",
            input_path=input_path,
            output_path=output_path,
            row_count_value=workflow_state.row_count(output_path),
            message="Reused existing verified_candidates.csv",
        )
        return output_path
    crossref_counts = crossref_verify.verify_csv(
        input_path,
        output_path,
        strict=False,
        title_lookup=True,
        checkpoint_interval=args.crossref_checkpoint_interval,
        max_rows=getattr(args, "max_crossref_rows", None),
        cache_dir=args.cache_dir,
        cache_ttl_days=args.cache_ttl_days,
        cache_enabled=args.cache_enabled,
    )
    counts["crossref_verified"] = crossref_counts.get("verified", 0)
    counts["crossref_title_recovered"] = crossref_counts.get("title_recovered", 0)
    counts["crossref_mismatch"] = crossref_counts.get("mismatch", 0)
    counts["crossref_lookup_failed"] = crossref_counts.get("lookup_failed", 0)
    counts["crossref_missing_doi"] = crossref_counts.get("missing_doi", 0)
    counts["crossref_title_lookup_failed"] = crossref_counts.get("title_lookup_failed", 0)
    counts["crossref_rate_limited"] = crossref_counts.get("rate_limited", 0)
    counts["crossref_network_error"] = crossref_counts.get("network_error", 0)
    counts["crossref_auth_error"] = crossref_counts.get("auth_error", 0)
    counts["crossref_response_parse_error"] = crossref_counts.get("response_parse_error", 0)
    counts["crossref_provider_error"] = crossref_counts.get("provider_error", 0)
    counts["crossref_skipped_budget"] = crossref_counts.get("skipped_budget", 0)
    counts["crossref_cache_hit"] = crossref_counts.get("cache_hit", 0)
    counts["crossref_cache_store"] = crossref_counts.get("cache_store", 0)
    if counts["crossref_skipped_budget"]:
        crossref_stage_status = "partial_budget"
        crossref_message = "Rows beyond --max-crossref-rows were marked skipped_budget"
    elif counts["crossref_rate_limited"]:
        crossref_stage_status = "partial_rate_limited"
        crossref_message = "One or more Crossref rows were rate limited; rerun with --resume later."
    elif counts["crossref_auth_error"]:
        crossref_stage_status = "partial_auth"
        crossref_message = "One or more Crossref rows failed with an auth or access-policy error."
    elif counts["crossref_network_error"]:
        crossref_stage_status = "partial_network"
        crossref_message = "One or more Crossref rows failed with a network error; inspect verified_candidates.csv."
    elif counts["crossref_response_parse_error"] or counts["crossref_provider_error"]:
        crossref_stage_status = "partial_provider_error"
        crossref_message = "One or more Crossref rows failed with provider or response parsing errors."
    else:
        crossref_stage_status = "completed"
        crossref_message = ""
    record_manifest_stage(
        out_dir,
        manifest,
        "crossref",
        crossref_stage_status,
        input_path=input_path,
        output_path=output_path,
        row_count_value=workflow_state.row_count(output_path),
        message=crossref_message,
    )
    return output_path


def run_triage_stage(input_path: Path, out_dir: Path,
                     args: argparse.Namespace,
                     counts: dict[str, int],
                     manifest: dict[str, Any] | None = None) -> Path:
    output_path = out_dir / "triaged_candidates.csv"
    if getattr(args, "resume", False) and workflow_state.reusable_stage(
        manifest,
        "triage",
        output_path,
        input_path=input_path,
    ):
        priority_counts = count_field_values(output_path, "triage_priority")
        metadata_counts = count_field_values(output_path, "metadata_status")
        counts["triaged"] = workflow_state.row_count(output_path)
        counts["triage_high"] = priority_counts.get("high", 0)
        counts["triage_medium"] = priority_counts.get("medium", 0)
        counts["triage_needs_review"] = priority_counts.get("needs_review", 0)
        counts["triage_low"] = priority_counts.get("low", 0)
        counts["metadata_blocked"] = metadata_counts.get("blocked", 0)
        record_manifest_stage(
            out_dir,
            manifest,
            "triage",
            "skipped_existing",
            input_path=input_path,
            output_path=output_path,
            row_count_value=counts["triaged"],
            message="Reused existing triaged_candidates.csv",
        )
        return output_path
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
        allow_regex=bool(getattr(args, "allow_regex_concepts", False)),
    )
    counts["triaged"] = triage_counts["rows"]
    counts["triage_high"] = triage_counts["high"]
    counts["triage_medium"] = triage_counts["medium"]
    counts["triage_needs_review"] = triage_counts["needs_review"]
    counts["triage_low"] = triage_counts["low"]
    counts["metadata_blocked"] = triage_counts["metadata_blocked"]
    validation_failures = validate_stage.validate_stage(
        output_path,
        out_dir / "triage_validation.md",
        "triage",
    )
    stage_status = "partial_validation_failed" if validation_failures else "completed"
    record_manifest_stage(
        out_dir,
        manifest,
        "triage",
        stage_status,
        input_path=input_path,
        output_path=output_path,
        row_count_value=counts["triaged"],
        message=f"Stage validation failed with {validation_failures} failure(s)"
        if validation_failures
        else "",
    )
    return output_path


def run_unpaywall_stage(input_path: Path, out_dir: Path,
                        args: argparse.Namespace,
                        counts: dict[str, int],
                        manifest: dict[str, Any] | None = None) -> Path:
    if not args.enrich_unpaywall:
        record_manifest_stage(
            out_dir,
            manifest,
            "unpaywall",
            "skipped_disabled",
            input_path=input_path,
            output_path=input_path,
            row_count_value=workflow_state.row_count(input_path),
        )
        return input_path
    output_path = out_dir / "oa_annotated_candidates.csv"
    if getattr(args, "resume", False) and workflow_state.reusable_stage(
        manifest,
        "unpaywall",
        output_path,
        input_path=input_path,
    ):
        status_counts = count_field_values(output_path, "unpaywall_status")
        counts["unpaywall_ok"] = status_counts.get("ok", 0)
        counts["unpaywall_skipped_missing_email"] = status_counts.get("skipped_missing_email", 0)
        counts["unpaywall_missing_doi"] = status_counts.get("missing_doi", 0)
        counts["unpaywall_not_found"] = status_counts.get("not_found", 0)
        counts["unpaywall_rate_limited"] = status_counts.get("rate_limited", 0)
        counts["unpaywall_network_error"] = status_counts.get("network_error", 0)
        counts["unpaywall_response_parse_error"] = status_counts.get("response_parse_error", 0)
        counts["unpaywall_error"] = status_counts.get("error", 0)
        counts["unpaywall_skipped_budget"] = status_counts.get("skipped_budget", 0)
        cache_counts = count_field_values(output_path, "unpaywall_cache_status")
        counts["unpaywall_cache_hit"] = cache_counts.get("hit", 0)
        counts["unpaywall_cache_store"] = cache_counts.get("store", 0)
        record_manifest_stage(
            out_dir,
            manifest,
            "unpaywall",
            "skipped_existing",
            input_path=input_path,
            output_path=output_path,
            row_count_value=workflow_state.row_count(output_path),
            message="Reused existing oa_annotated_candidates.csv",
        )
        return output_path
    unpaywall_counts = unpaywall_lookup.annotate_csv(
        input_path,
        output_path,
        email=args.unpaywall_email,
        sleep_s=args.unpaywall_sleep,
        checkpoint_interval=args.unpaywall_checkpoint_interval,
        max_rows=getattr(args, "max_unpaywall_rows", None),
        cache_dir=args.cache_dir,
        cache_ttl_days=args.cache_ttl_days,
        cache_enabled=args.cache_enabled,
    )
    counts["unpaywall_ok"] = unpaywall_counts.get("ok", 0)
    counts["unpaywall_skipped_missing_email"] = unpaywall_counts.get("skipped_missing_email", 0)
    counts["unpaywall_missing_doi"] = unpaywall_counts.get("missing_doi", 0)
    counts["unpaywall_not_found"] = unpaywall_counts.get("not_found", 0)
    counts["unpaywall_rate_limited"] = unpaywall_counts.get("rate_limited", 0)
    counts["unpaywall_network_error"] = unpaywall_counts.get("network_error", 0)
    counts["unpaywall_response_parse_error"] = unpaywall_counts.get("response_parse_error", 0)
    counts["unpaywall_error"] = unpaywall_counts.get("error", 0)
    counts["unpaywall_skipped_budget"] = unpaywall_counts.get("skipped_budget", 0)
    counts["unpaywall_cache_hit"] = unpaywall_counts.get("cache_hit", 0)
    counts["unpaywall_cache_store"] = unpaywall_counts.get("cache_store", 0)
    if counts["unpaywall_skipped_budget"]:
        unpaywall_stage_status = "partial_budget"
        unpaywall_message = "Rows beyond --max-unpaywall-rows were marked skipped_budget"
    elif counts["unpaywall_rate_limited"]:
        unpaywall_stage_status = "partial_rate_limited"
        unpaywall_message = "One or more Unpaywall rows were rate limited; rerun with --resume later."
    elif counts["unpaywall_skipped_missing_email"]:
        unpaywall_stage_status = "partial_auth"
        unpaywall_message = "Unpaywall was enabled but no contact email was available; rows were skipped."
    elif counts["unpaywall_network_error"]:
        unpaywall_stage_status = "partial_network"
        unpaywall_message = (
            "One or more Unpaywall rows failed with a network error; "
            "inspect oa_annotated_candidates.csv."
        )
    elif counts["unpaywall_response_parse_error"] or counts["unpaywall_error"]:
        unpaywall_stage_status = "partial_provider_error"
        unpaywall_message = "One or more Unpaywall rows failed with provider or response parsing errors."
    else:
        unpaywall_stage_status = "completed"
        unpaywall_message = ""
    record_manifest_stage(
        out_dir,
        manifest,
        "unpaywall",
        unpaywall_stage_status,
        input_path=input_path,
        output_path=output_path,
        row_count_value=workflow_state.row_count(output_path),
        message=unpaywall_message,
    )
    return output_path


def run_metrics_stage(input_path: Path, out_dir: Path,
                      args: argparse.Namespace,
                      counts: dict[str, int],
                      manifest: dict[str, Any] | None = None) -> tuple[Path, Path | None, Path | None]:
    strict_path: Path | None = None
    backup_path: Path | None = None
    metrics_requested = args.min_if is not None or args.metrics
    if not metrics_requested or args.skip_journal_metrics:
        record_manifest_stage(
            out_dir,
            manifest,
            "metrics",
            "skipped_disabled",
            input_path=input_path,
            output_path=input_path,
            row_count_value=workflow_state.row_count(input_path),
        )
        return input_path, strict_path, backup_path

    annotated = out_dir / "metrics_annotated_candidates.csv"
    strict_path = out_dir / "strict_candidates.csv"
    backup_path = out_dir / "backup_candidates.csv"
    expected_metric_output = strict_path if args.queue_strict_only else annotated
    if (
        getattr(args, "resume", False)
        and workflow_state.reusable_stage(manifest, "metrics", expected_metric_output, input_path=input_path)
        and workflow_state.reusable_csv(strict_path)
        and workflow_state.reusable_csv(backup_path)
    ):
        counts["metric_pass"] = workflow_state.row_count(strict_path)
        counts["metric_backup"] = workflow_state.row_count(backup_path)
        output_path = expected_metric_output
        record_manifest_stage(
            out_dir,
            manifest,
            "metrics",
            "skipped_existing",
            input_path=input_path,
            output_path=output_path,
            row_count_value=workflow_state.row_count(output_path),
            message="Reused existing metric CSV outputs",
        )
        return output_path, strict_path, backup_path
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
    output_path = strict_path if args.queue_strict_only else annotated
    record_manifest_stage(
        out_dir,
        manifest,
        "metrics",
        "completed",
        input_path=input_path,
        output_path=output_path,
        row_count_value=workflow_state.row_count(output_path),
    )
    return output_path, strict_path, backup_path


def run_queue_stage(input_path: Path, out_dir: Path,
                    args: argparse.Namespace,
                    counts: dict[str, int],
                    queue_priorities: set[str],
                    manifest: dict[str, Any] | None = None) -> Path:
    output_path = out_dir / "publisher_queue.csv"
    if getattr(args, "resume", False) and workflow_state.reusable_stage(
        manifest,
        "publisher_queue",
        output_path,
        input_path=input_path,
    ):
        counts["publisher_queue"] = workflow_state.row_count(output_path)
        record_manifest_stage(
            out_dir,
            manifest,
            "publisher_queue",
            "skipped_existing",
            input_path=input_path,
            output_path=output_path,
            row_count_value=counts["publisher_queue"],
            message="Reused existing publisher_queue.csv",
        )
        return output_path
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
    optional_empty_fields = {"doi", "doi_url"} if args.allow_missing_doi else None
    validation_failures = validate_stage.validate_stage(
        output_path,
        out_dir / "publisher_queue_validation.md",
        "queue",
        optional_empty_fields=optional_empty_fields,
    )
    stage_status = "partial_validation_failed" if validation_failures else "completed"
    record_manifest_stage(
        out_dir,
        manifest,
        "publisher_queue",
        stage_status,
        input_path=input_path,
        output_path=output_path,
        row_count_value=counts["publisher_queue"],
        message=f"Stage validation failed with {validation_failures} failure(s)"
        if validation_failures
        else "",
    )
    return output_path


def run_publisher_probe_stage(input_path: Path, out_dir: Path,
                              args: argparse.Namespace,
                              counts: dict[str, int],
                              manifest: dict[str, Any] | None = None) -> Path:
    if not args.probe_publishers:
        record_manifest_stage(
            out_dir,
            manifest,
            "publisher_probe",
            "skipped_disabled",
            input_path=input_path,
            row_count_value=workflow_state.row_count(input_path),
        )
        return input_path
    probed = out_dir / "publisher_queue_probed.csv"
    if getattr(args, "resume", False) and workflow_state.reusable_stage(
        manifest,
        "publisher_probe",
        probed,
        input_path=input_path,
    ):
        counts["publisher_probed"] = workflow_state.row_count(probed)
        record_manifest_stage(
            out_dir,
            manifest,
            "publisher_probe",
            "skipped_existing",
            input_path=input_path,
            output_path=probed,
            row_count_value=counts["publisher_probed"],
            message="Reused existing publisher_queue_probed.csv",
        )
        return probed
    publisher_counts = publisher_probe.probe_csv(
        input_path,
        probed,
        limit=args.probe_limit,
        sleep_s=args.probe_sleep,
    )
    counts["publisher_probed"] = sum(publisher_counts.values())
    input_rows = workflow_state.row_count(input_path)
    partial_probe = counts["publisher_probed"] < input_rows
    record_manifest_stage(
        out_dir,
        manifest,
        "publisher_probe",
        "partial_budget" if partial_probe else "completed",
        input_path=input_path,
        output_path=probed,
        row_count_value=workflow_state.row_count(probed),
        message="Only a subset of publisher queue rows were probed"
        if partial_probe
        else "",
    )
    return probed


def run(args: argparse.Namespace) -> dict[str, str]:
    started_at = time.monotonic()
    args = normalize_args(args)
    warnings = preflight_warnings(args)
    for warning in warnings:
        print(f"WARNING: {warning}", file=sys.stderr)
    out_dir = args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    queries = load_queries(args)
    signature_payload = run_signature_payload(args, queries)
    signature = workflow_state.stable_fingerprint(signature_payload)
    existing_manifest = workflow_state.load_manifest(out_dir) if getattr(args, "resume", False) else {}
    validate_resume_manifest(out_dir, args, existing_manifest, signature)
    manifest = workflow_state.new_manifest(
        args,
        existing=existing_manifest,
        signature=signature,
        signature_payload=signature_payload,
    )
    manifest["cache"] = {
        "enabled": bool(getattr(args, "cache_enabled", True)),
        "cache_dir": str(getattr(args, "cache_dir", "")),
        "ttl_days": getattr(args, "cache_ttl_days", None),
        "provider_failure_ttl_seconds": getattr(args, "provider_failure_cache_ttl_seconds", None),
        "scope": "workspace_local_metadata_and_short_lived_provider_failures",
    }
    if getattr(args, "resume_allow_mismatch", False):
        manifest["resume_mismatch_allowed"] = True
        manifest["resume_mismatch_reason"] = str(getattr(args, "resume_mismatch_reason", "") or "").strip()
    workflow_state.write_manifest(out_dir, manifest)
    refresh_processing_report(out_dir, warnings=warnings)
    counts: dict[str, int] = {}
    queue_priorities = parse_set(args.queue_priorities, DEFAULT_QUEUE_PRIORITIES)
    strict_path: Path | None = None
    backup_path: Path | None = None

    try:
        sources = selected_discovery_sources(args)
    except SystemExit:
        sources = []
    write_query_plan_artifact(out_dir, args, queries, sources, manifest=manifest)
    should_stop, stop_reason = should_stop_after(args, "query_plan", started_at)
    if should_stop:
        return finalize_run(
            out_dir,
            manifest,
            counts,
            args,
            strict_path,
            backup_path,
            queue_priorities,
            warnings,
            run_status="partial",
            stop_reason=stop_reason,
        )

    if args.input_csv:
        discovery_inputs = [args.input_csv]
        record_manifest_stage(
            out_dir,
            manifest,
            "discovery",
            "skipped_input_csv",
            output_path=args.input_csv,
            row_count_value=workflow_state.row_count(args.input_csv),
            message="Started from --input-csv",
        )
    else:
        discovery_inputs = discover(args, out_dir, manifest=manifest)
    counts["discovery_files"] = len(discovery_inputs)
    if not discovery_inputs:
        raise SystemExit("No discovery inputs produced. Provide --input-csv or at least one --query.")
    should_stop, stop_reason = should_stop_after(args, "discovery", started_at)
    if should_stop:
        return finalize_run(
            out_dir,
            manifest,
            counts,
            args,
            strict_path,
            backup_path,
            queue_priorities,
            warnings,
            run_status="partial",
            stop_reason=stop_reason,
        )

    merged = out_dir / "merged_candidates.csv"
    if len(discovery_inputs) == 1:
        merged = discovery_inputs[0]
        record_manifest_stage(
            out_dir,
            manifest,
            "merge",
            "skipped_single_input",
            input_path=merged,
            output_path=merged,
            row_count_value=workflow_state.row_count(merged),
        )
    else:
        if getattr(args, "resume", False) and workflow_state.reusable_stage(manifest, "merge", merged):
            record_manifest_stage(
                out_dir,
                manifest,
                "merge",
                "skipped_existing",
                output_path=merged,
                row_count_value=workflow_state.row_count(merged),
                message="Reused existing merged_candidates.csv",
            )
        else:
            merge_csv.merge_csv(discovery_inputs, merged, allow_missing=True)
            record_manifest_stage(
                out_dir,
                manifest,
                "merge",
                "completed",
                output_path=merged,
                row_count_value=workflow_state.row_count(merged),
            )
    should_stop, stop_reason = should_stop_after(args, "merge", started_at)
    if should_stop:
        return finalize_run(
            out_dir,
            manifest,
            counts,
            args,
            strict_path,
            backup_path,
            queue_priorities,
            warnings,
            run_status="partial",
            stop_reason=stop_reason,
        )

    deduped = out_dir / "deduped_candidates.csv"
    if getattr(args, "resume", False) and workflow_state.reusable_stage(
        manifest,
        "dedupe",
        deduped,
        input_path=merged,
    ):
        record_manifest_stage(
            out_dir,
            manifest,
            "dedupe",
            "skipped_existing",
            input_path=merged,
            output_path=deduped,
            row_count_value=workflow_state.row_count(deduped),
            message="Reused existing deduped_candidates.csv",
        )
    else:
        dedupe_papers.dedupe(merged, deduped, "doi", "title")
        record_manifest_stage(
            out_dir,
            manifest,
            "dedupe",
            "completed",
            input_path=merged,
            output_path=deduped,
            row_count_value=workflow_state.row_count(deduped),
        )
    counts["deduped"] = len(read_rows(deduped))
    refresh_processing_report(out_dir, warnings=warnings)
    should_stop, stop_reason = should_stop_after(args, "dedupe", started_at)
    if should_stop:
        return finalize_run(
            out_dir,
            manifest,
            counts,
            args,
            strict_path,
            backup_path,
            queue_priorities,
            warnings,
            run_status="partial",
            stop_reason=stop_reason,
        )

    triage_input = run_crossref_stage(deduped, out_dir, args, counts, manifest=manifest)
    refresh_processing_report(out_dir, warnings=warnings)
    should_stop, stop_reason = should_stop_after(args, "crossref", started_at)
    if should_stop:
        return finalize_run(
            out_dir,
            manifest,
            counts,
            args,
            strict_path,
            backup_path,
            queue_priorities,
            warnings,
            run_status="partial",
            stop_reason=stop_reason,
        )
    triaged = run_triage_stage(triage_input, out_dir, args, counts, manifest=manifest)
    refresh_processing_report(out_dir, warnings=warnings)
    should_stop, stop_reason = should_stop_after(args, "triage", started_at)
    if should_stop:
        return finalize_run(
            out_dir,
            manifest,
            counts,
            args,
            strict_path,
            backup_path,
            queue_priorities,
            warnings,
            run_status="partial",
            stop_reason=stop_reason,
            triaged=triaged,
        )

    selected = out_dir / "selected_candidates.csv"
    if getattr(args, "resume", False) and workflow_state.reusable_stage(
        manifest,
        "priority_selection",
        selected,
        input_path=triaged,
    ):
        counts["selected_for_verification"] = workflow_state.row_count(selected)
        record_manifest_stage(
            out_dir,
            manifest,
            "priority_selection",
            "skipped_existing",
            input_path=triaged,
            output_path=selected,
            row_count_value=counts["selected_for_verification"],
            message="Reused existing selected_candidates.csv",
        )
    else:
        counts["selected_for_verification"] = select_by_priority(
            triaged,
            selected,
            queue_priorities,
            include_metadata_blocked=args.include_metadata_blocked,
        )
        record_manifest_stage(
            out_dir,
            manifest,
            "priority_selection",
            "completed",
            input_path=triaged,
            output_path=selected,
            row_count_value=counts["selected_for_verification"],
        )
    refresh_processing_report(out_dir, warnings=warnings)
    should_stop, stop_reason = should_stop_after(args, "selection", started_at)
    if should_stop:
        return finalize_run(
            out_dir,
            manifest,
            counts,
            args,
            strict_path,
            backup_path,
            queue_priorities,
            warnings,
            run_status="partial",
            stop_reason=stop_reason,
            triaged=triaged,
        )

    verified = selected
    counts["verified"] = count_trusted_crossref_rows(selected)
    if args.skip_crossref:
        counts["selected_unverified"] = len(read_rows(selected))

    verified = run_unpaywall_stage(verified, out_dir, args, counts, manifest=manifest)
    refresh_processing_report(out_dir, warnings=warnings)
    should_stop, stop_reason = should_stop_after(args, "unpaywall", started_at)
    if should_stop:
        return finalize_run(
            out_dir,
            manifest,
            counts,
            args,
            strict_path,
            backup_path,
            queue_priorities,
            warnings,
            run_status="partial",
            stop_reason=stop_reason,
            triaged=triaged,
        )
    metric_input, strict_path, backup_path = run_metrics_stage(verified, out_dir, args, counts, manifest=manifest)
    refresh_processing_report(out_dir, warnings=warnings)
    should_stop, stop_reason = should_stop_after(args, "metrics", started_at)
    if should_stop:
        return finalize_run(
            out_dir,
            manifest,
            counts,
            args,
            strict_path,
            backup_path,
            queue_priorities,
            warnings,
            run_status="partial",
            stop_reason=stop_reason,
            triaged=triaged,
        )
    publisher_queue = run_queue_stage(metric_input, out_dir, args, counts, queue_priorities, manifest=manifest)
    refresh_processing_report(out_dir, warnings=warnings)
    write_provenance_artifact(publisher_queue, out_dir, manifest=manifest)
    refresh_processing_report(out_dir, warnings=warnings)
    should_stop, stop_reason = should_stop_after(args, "queue", started_at)
    if should_stop:
        return finalize_run(
            out_dir,
            manifest,
            counts,
            args,
            strict_path,
            backup_path,
            queue_priorities,
            warnings,
            run_status="partial",
            stop_reason=stop_reason,
            triaged=triaged,
            publisher_queue=publisher_queue,
        )
    final_queue = run_publisher_probe_stage(publisher_queue, out_dir, args, counts, manifest=manifest)
    if final_queue != publisher_queue:
        write_provenance_artifact(final_queue, out_dir, manifest=manifest)
    refresh_processing_report(out_dir, warnings=warnings)
    should_stop, stop_reason = should_stop_after(args, "probe", started_at)
    if should_stop:
        return finalize_run(
            out_dir,
            manifest,
            counts,
            args,
            strict_path,
            backup_path,
            queue_priorities,
            warnings,
            run_status="partial",
            stop_reason=stop_reason,
            triaged=triaged,
            publisher_queue=final_queue,
        )

    return finalize_run(
        out_dir,
        manifest,
        counts,
        args,
        strict_path,
        backup_path,
        queue_priorities,
        warnings,
        triaged=triaged,
        publisher_queue=final_queue,
    )


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
    parser.add_argument("--mode", choices=sorted(RUN_MODE_PRESETS),
                        default=None,
                        help="Runtime preset: fast for first pass, balanced for default verification, expanded/full for semantic recall.")
    parser.add_argument("--resume", action="store_true",
                        help="Reuse existing stage CSVs in the output directory when they are present")
    parser.add_argument("--resume-allow-mismatch", action="store_true",
                        help="Allow --resume when run_manifest.json has a different or missing run signature")
    parser.add_argument("--resume-mismatch-reason", default="",
                        help="Required audit note when --resume-allow-mismatch is used")
    parser.add_argument("--time-budget-seconds", type=float, default=None,
                        help="Stop cleanly after a stage once this run-level time budget is exhausted")
    parser.add_argument("--stop-after-stage",
                        choices=[
                            "query_plan", "discovery", "merge", "dedupe", "crossref", "triage",
                            "selection", "unpaywall", "metrics", "queue", "probe",
                        ],
                        default=None,
                        help="Stop after a named stage and still write reports/manifest")
    parser.add_argument("--triage-profile", type=Path, default=None,
                        help="JSON semantic triage profile")
    parser.add_argument("--required-concept", action="append", default=[],
                        help="Caller-supplied required concept, e.g. name=term1|term2")
    parser.add_argument("--optional-concept", action="append", default=[],
                        help="Caller-supplied optional concept")
    parser.add_argument("--negative-concept", action="append", default=[],
                        help="Caller-supplied negative tag. Rows are tagged, not deleted.")
    parser.add_argument("--enable-regex-concepts", dest="allow_regex_concepts",
                        action="store_true", default=None,
                        help="Allow re: semantic concept patterns. Disabled by default.")
    parser.add_argument("--disable-regex-concepts", dest="allow_regex_concepts",
                        action="store_false",
                        help="Reject re: semantic concept patterns")
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
    parser.add_argument("--provider-failure-threshold", type=int, default=None,
                        help="Skip remaining calls for a provider after this many failed calls in one discovery run")
    parser.add_argument("--provider-rate-limit-cooldown-seconds", type=float, default=None,
                        help="Default cooldown for repeated calls to a rate-limited discovery provider")
    parser.add_argument("--cache-dir", type=Path, default=None,
                        help="Workspace-local JSON cache directory for metadata and short-lived provider failure state")
    parser.add_argument("--cache-ttl-days", type=float, default=None,
                        help="TTL in days for Crossref/Unpaywall metadata cache")
    parser.add_argument("--provider-failure-cache-ttl-seconds", type=float, default=None,
                        help="TTL in seconds for discovery provider failure cache")
    parser.add_argument("--no-cache", dest="cache_enabled", action="store_false", default=None,
                        help="Disable Litminer cache for this run")
    parser.add_argument("--cache", dest="cache_enabled", action="store_true",
                        help="Enable Litminer cache for this run")
    parser.add_argument("--enrich-unpaywall", action="store_true", default=None,
                        help="Annotate verified DOI rows with Unpaywall OA links")
    parser.add_argument("--skip-unpaywall", action="store_true", default=None,
                        help="Disable Unpaywall annotation even when config enables it")
    parser.add_argument("--unpaywall-email", default=None,
                        help="Unpaywall email; also reads UNPAYWALL_EMAIL or LITMINER_CONTACT_EMAIL")
    parser.add_argument("--unpaywall-sleep", type=float, default=None)
    parser.add_argument("--crossref-checkpoint-interval", type=int, default=None,
                        help="Write Crossref batch progress every N rows; 0 disables checkpoints")
    parser.add_argument("--unpaywall-checkpoint-interval", type=int, default=None,
                        help="Write Unpaywall batch progress every N rows; 0 disables checkpoints")
    parser.add_argument("--max-crossref-rows", type=int, default=None,
                        help="Only verify the first N rows in Crossref; remaining rows are marked skipped_budget")
    parser.add_argument("--max-unpaywall-rows", type=int, default=None,
                        help="Only annotate the first N rows in Unpaywall; remaining rows are marked skipped_budget")
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
    parser.add_argument("--max-publisher-probe-rows", type=int, default=None,
                        help="Alias-style budget for publisher probing; used when --probe-limit is not set")
    parser.add_argument("--probe-sleep", type=float, default=None)
    args = parser.parse_args()

    result = run(args)
    print(f"Litminer run complete: {result['output_dir']}", file=sys.stderr)
    print(f"Triaged candidates: {result['triaged_candidates']}", file=sys.stderr)
    print(f"Feasibility report: {result['feasibility_report']}", file=sys.stderr)
    print(f"Processing report: {result['processing_report']}", file=sys.stderr)
    print(f"Agent summary: {result['agent_summary']}", file=sys.stderr)
    print(f"Query plan: {result['query_plan']}", file=sys.stderr)
    print(f"Field provenance: {result['field_provenance']}", file=sys.stderr)
    print(f"Run manifest: {result['run_manifest']}", file=sys.stderr)
    print(f"Artifacts index: {result['artifacts_index']}", file=sys.stderr)


if __name__ == "__main__":
    main()
