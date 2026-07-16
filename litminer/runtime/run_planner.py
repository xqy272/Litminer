"""Pure run planning derived from a normalized :class:`RunSpec`."""

from __future__ import annotations

from typing import Any

from litminer.contracts.run_spec import RunSpec


STANDARD_STAGES = (
    "plan",
    "preflight",
    "discover_or_import",
    "normalize",
    "dedupe",
    "pretriage",
    "build_verification_queue",
    "verify_bibliography",
    "final_triage",
    "enrich_access",
    "build_publisher_queue",
    "finalize",
)


def build_run_plan(spec: RunSpec, normalized: Any | None = None) -> dict[str, Any]:
    """Return an execution forecast without provider calls or artifact writes."""

    stages = list(STANDARD_STAGES)
    extras = spec.extras
    if bool(extras.get("expand_citations") or getattr(normalized, "expand_citations", False)):
        insert_at = stages.index("final_triage") + 1
        stages[insert_at:insert_at] = ["citation_expand", "renormalize_expanded_observations"]
    if spec.output.export_formats:
        stages.append("export")

    providers = list(spec.retrieval.sources)
    if not spec.verification.skip_crossref:
        providers.append("crossref")
    if spec.verification.enrich_unpaywall:
        providers.append("unpaywall")
    providers = list(dict.fromkeys(providers))

    warnings: list[str] = []
    risks: list[dict[str, str]] = []
    if spec.verification.skip_crossref:
        warnings.append("Crossref verification is disabled; canonical bibliography may remain untrusted.")
        risks.append({"code": "verification_disabled", "severity": "high"})
    if not spec.controls.state_enabled:
        warnings.append("SQLite state is disabled; cross-process cooldown, recovery, and request-ledger continuity are unavailable.")
        risks.append({"code": "state_disabled", "severity": "medium"})
    if spec.output.include_unverified_export:
        warnings.append("Unverified bibliography was explicitly allowed for export and will be audited in export_manifest.json.")
        risks.append({"code": "unverified_export", "severity": "high"})
    if spec.controls.time_budget_seconds is not None or spec.controls.stop_after_stage:
        risks.append({"code": "partial_run_requested", "severity": "low"})
    if not spec.retrieval.sources and spec.input.mode != "import":
        warnings.append("No discovery provider is selected after normalization.")
        risks.append({"code": "no_discovery_provider", "severity": "high"})

    return {
        "ok": True,
        "input_mode": spec.input.mode,
        "run_spec": spec.to_dict(),
        "providers": providers,
        "stages": stages,
        "warnings": warnings,
        "risks": risks,
        "will_write_artifacts": False,
        "will_call_network": False,
    }
