"""Run initialization, resume validation, and manifest/state-store wiring."""

from __future__ import annotations

import argparse
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from litminer.contracts.run_spec import RunSpec
from litminer.engine import research_session, status_policy, workflow_state, workspace
from litminer.engine.common import write_text_atomic
from litminer.runtime.provider_runtime import ProviderRuntime
from litminer.runtime.stage_executor import PipelineExecutor, RunContext
from litminer.runtime.state_store import StateStore, default_state_store_path


@dataclass(frozen=True)
class RunLifecycleState:
    args: argparse.Namespace
    output_dir: Path
    run_spec: RunSpec
    manifest: dict[str, Any]
    state_store: StateStore
    merge_base_path: Path | None


def run_signature_payload(
    args: argparse.Namespace,
    queries: list[str],
) -> dict[str, Any]:
    return {
        "input_csv": str(args.input_csv.resolve(strict=False)) if args.input_csv else "",
        "merge_into": str(args.merge_into.resolve(strict=False))
        if getattr(args, "merge_into", None)
        else "",
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
        "provider_rate_limit_cooldown_seconds": (
            args.provider_rate_limit_cooldown_seconds
        ),
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
        "triage_profile": str(args.triage_profile.resolve(strict=False))
        if args.triage_profile
        else "",
        "allow_regex_concepts": bool(
            getattr(args, "allow_regex_concepts", False)
        ),
    }


def stage_files_exist(out_dir: Path) -> bool:
    stage_names = [
        "api_candidates.csv",
        "merged_candidates.csv",
        "deduped_candidates.csv",
        "pretriaged_candidates.csv",
        "verification_queue.csv",
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
        reason = str(
            getattr(args, "resume_mismatch_reason", "") or ""
        ).strip()
        if not reason:
            raise SystemExit(
                "--resume-allow-mismatch requires --resume-mismatch-reason "
                "so the unsafe reuse is auditable."
            )
        return
    existing_signature = str(existing_manifest.get("run_signature") or "")
    if existing_signature:
        if existing_signature != signature:
            raise SystemExit(
                "Cannot resume because current request parameters differ from "
                "run_manifest.json. Use a new --output-dir, remove --resume, "
                "or pass --resume-allow-mismatch only after manual review."
            )
        return
    if existing_manifest or stage_files_exist(out_dir):
        raise SystemExit(
            "Cannot safely resume: existing outputs have no run signature. "
            "Use a new --output-dir, remove --resume, or pass "
            "--resume-allow-mismatch only after manual review."
        )


def record_manifest_stage(
    out_dir: Path,
    manifest: dict[str, Any] | None,
    name: str,
    status: str,
    *,
    input_path: Path | None = None,
    output_path: Path | None = None,
    row_count_value: int | None = None,
    message: str = "",
) -> None:
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
    state_info = (
        manifest.get("state_store")
        if isinstance(manifest.get("state_store"), dict)
        else {}
    )
    if state_info and state_info.get("enabled") and state_info.get("path"):
        StateStore(Path(str(state_info["path"]))).record_stage(
            run_id=str(manifest.get("run_id") or ""),
            stage_name=name,
            status=status,
            status_class=status_policy.classify_status(status),
            input_path=str(input_path or ""),
            output_path=str(output_path or ""),
            input_count=workflow_state.row_count(input_path)
            if input_path
            else 0,
            output_count=row_count_value
            if row_count_value is not None
            else (
                workflow_state.row_count(output_path)
                if output_path
                else 0
            ),
            message=message,
            completed_at=workflow_state.utc_now(),
        )


def initialize_run(
    args: argparse.Namespace,
    *,
    started_at: float,
    queries: list[str],
) -> RunLifecycleState:
    run_spec = RunSpec.from_namespace(args)
    args.run_spec = run_spec
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    args.session_queries = list(queries)
    args.session_iteration_id = (
        research_session.resume_iteration_id(out_dir)
        if getattr(args, "resume", False)
        else research_session.next_iteration_id(out_dir)
    )

    merge_base_path: Path | None = None
    if getattr(args, "merge_into", None):
        existing_candidates = next(
            (
                path
                for path in (
                    out_dir / "deduped_candidates.csv",
                    out_dir / "merged_candidates.csv",
                    out_dir / "api_candidates.csv",
                )
                if path.exists()
            ),
            None,
        )
        if existing_candidates is None:
            raise SystemExit(
                "--merge-into requires an existing Litminer output directory "
                "with deduped_candidates.csv, merged_candidates.csv, or "
                "api_candidates.csv"
            )
        merge_base_path = out_dir / "merge_base_candidates.csv"
        shutil.copyfile(existing_candidates, merge_base_path)
    args.merge_base_path = merge_base_path

    signature_payload = run_signature_payload(args, queries)
    signature = workflow_state.stable_fingerprint(signature_payload)
    prior_manifest = workflow_state.load_manifest(out_dir)
    existing_manifest = (
        prior_manifest if getattr(args, "resume", False) else {}
    )
    validate_resume_manifest(out_dir, args, existing_manifest, signature)
    if getattr(args, "merge_into", None):
        existing_manifest = {}
    manifest = workflow_state.new_manifest(
        args,
        existing=existing_manifest,
        signature=signature,
        signature_payload=signature_payload,
    )

    state_store = StateStore(
        args.state_store_path
        or default_state_store_path(workspace.workspace_root()),
        enabled=bool(args.state_enabled),
    )
    session_id = research_session.session_id(out_dir)
    run_id = str(manifest.get("run_id") or "")
    state_store.upsert_session(
        session_id,
        workspace_root=str(workspace.workspace_root()),
        output_dir=str(out_dir),
    )
    state_store.start_iteration(
        session_id=session_id,
        iteration_id=str(args.session_iteration_id),
        run_id=run_id,
        input_mode=run_spec.input.mode,
        spec=run_spec.to_dict(),
    )
    args.state_store_instance = state_store
    args.provider_runtime = ProviderRuntime(
        state_store,
        run_id=run_id,
        iteration_id=str(args.session_iteration_id),
    )
    args.pipeline_executor = PipelineExecutor(RunContext(
        run_spec=run_spec,
        output_dir=out_dir,
        run_id=run_id,
        iteration_id=str(args.session_iteration_id),
        session_id=session_id,
        state_store=state_store,
        started_monotonic=started_at,
        cancel_check=getattr(args, "cancel_check", None),
    ))

    run_spec_path = out_dir / "run_spec.json"
    write_text_atomic(
        run_spec_path,
        json.dumps(run_spec.to_dict(), indent=2, ensure_ascii=False) + "\n",
    )
    manifest["contract"] = {
        "run_spec_schema_version": run_spec.schema_version,
        "run_spec_path": str(run_spec_path),
    }
    manifest["state_store"] = {
        "enabled": state_store.enabled,
        "path": str(state_store.path) if state_store.enabled else "",
        "scope": "workspace_local_runtime_provider_and_evidence_state",
    }
    manifest["cache"] = {
        "enabled": bool(getattr(args, "cache_enabled", True)),
        "cache_dir": str(getattr(args, "cache_dir", "")),
        "ttl_days": getattr(args, "cache_ttl_days", None),
        "provider_failure_ttl_seconds": getattr(
            args,
            "provider_failure_cache_ttl_seconds",
            None,
        ),
        "scope": "workspace_local_metadata_and_short_lived_provider_failures",
    }
    if getattr(args, "resume_allow_mismatch", False):
        manifest["resume_mismatch_allowed"] = True
        manifest["resume_mismatch_reason"] = str(
            getattr(args, "resume_mismatch_reason", "") or ""
        ).strip()
    workflow_state.write_manifest(out_dir, manifest)
    return RunLifecycleState(
        args=args,
        output_dir=out_dir,
        run_spec=run_spec,
        manifest=manifest,
        state_store=state_store,
        merge_base_path=merge_base_path,
    )
