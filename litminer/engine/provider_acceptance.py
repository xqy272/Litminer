"""Controlled live acceptance for every registered scholarly provider.

The acceptance surface deliberately issues one minimal parser-level request per
provider. It is intended for manual or scheduled native Windows and macOS
runs; normal pull-request tests mock the provider entry points.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from litminer.contracts.errors import ErrorEnvelope, classify_exception
from litminer.engine import api_discovery, status_policy
from litminer.runtime.provider_runtime import ProviderRuntime
from litminer.runtime.state_store import StateStore
from litminer.sources.api import crossref_verify, registry, unpaywall_lookup


CORE_PROVIDERS = ("openalex", "crossref")
FULL_PROVIDERS = tuple(registry.PROVIDER_SPECS)
RELEASE_REQUIRED_PROVIDERS = CORE_PROVIDERS
RELEASE_TRANSIENT_ERROR_CLASSES = frozenset({
    "network",
    "rate_limited",
    "timeout",
    "tls",
})
DISCOVERY_QUERIES = {
    "openalex": "machine learning",
    "semantic_scholar": "machine learning",
    "arxiv": "all:electron",
    "europe_pmc": "cancer",
}
ACCEPTANCE_DOI = "10.1038/nature12373"
SAMPLE_FIELDS = (
    "title",
    "doi",
    "publication_year",
    "journal",
    "article_type",
    "openalex_id",
    "s2_id",
    "arxiv_id",
    "pmid",
    "pmcid",
    "landing_page_url",
    "url",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _bounded(value: Any, limit: int = 240) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + "..."
    if isinstance(value, list):
        return [_bounded(item, limit) for item in value[:3]]
    if isinstance(value, dict):
        return {
            str(key): _bounded(item, limit)
            for key, item in list(value.items())[:16]
            if "email" not in str(key).lower() and "key" not in str(key).lower()
        }
    return _bounded(str(value), limit)


def _row_sample(row: dict[str, Any]) -> dict[str, Any]:
    sample = {
        field: _bounded(row.get(field))
        for field in SAMPLE_FIELDS
        if row.get(field) not in (None, "")
    }
    return sample or _bounded(row)


def _shape(value: Any) -> dict[str, Any]:
    if isinstance(value, list):
        first = value[0] if value and isinstance(value[0], dict) else None
        return {
            "type": "list",
            "count": len(value),
            "row_keys": sorted(str(key) for key in first)[:40] if first else [],
            "sample": _row_sample(first) if first else None,
        }
    if isinstance(value, dict):
        return {
            "type": "object",
            "keys": sorted(str(key) for key in value)[:40],
            "sample": _bounded(value),
        }
    return {"type": type(value).__name__, "sample": _bounded(value)}


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _probe_result_error(
    provider: str,
    status: str,
    value: Any,
    message: str,
) -> dict[str, Any] | None:
    """Project non-exception provider failures into the shared error contract."""
    if status not in {"failed", "skipped"}:
        return None

    raw_status = ""
    retry_after: float | None = None
    attempts: int | None = None
    request_count: int | None = None
    http_status: int | None = None
    if isinstance(value, dict):
        raw_status = str(
            value.get("status_class")
            or value.get("status")
            or ""
        ).strip().lower()
        retry_after = _optional_float(value.get("retry_after_seconds"))
        attempts = _optional_int(value.get("attempts"))
        request_count = _optional_int(value.get("request_count"))
        http_status = _optional_int(value.get("http_status"))
    if http_status is None:
        match = re.fullmatch(r"http_(\d{3})", raw_status)
        if match:
            http_status = int(match.group(1))

    status_class = status_policy.classify_status(raw_status)
    lowered = f"{raw_status} {message}".lower()
    next_actions: tuple[str, ...]
    if status == "skipped" or status_class == "auth":
        error_class = "auth"
        code = (
            "contact_email_required"
            if "missing_email" in raw_status
            else "provider_auth_error"
        )
        transient = False
        next_actions = ("check_provider_credentials_contact_email_or_access_policy",)
    elif status_class == "rate_limited":
        error_class = "rate_limited"
        code = "provider_rate_limited"
        transient = True
        next_actions = ("resume_after_retry_after", "reduce_request_volume")
    elif status_class == "network":
        if any(marker in lowered for marker in ("ssl", "certificate", "tls")):
            error_class = "tls"
            code = "tls_error"
            next_actions = ("check_tls_certificate_proxy_and_system_clock",)
        elif "timeout" in lowered or "timed out" in lowered:
            error_class = "timeout"
            code = "request_timeout"
            next_actions = ("resume_or_retry_with_lower_request_volume",)
        else:
            error_class = "network"
            code = "network_error"
            next_actions = ("check_network_proxy_dns_and_retry",)
        transient = True
    elif http_status is not None:
        error_class = "provider_response"
        code = "provider_http_error"
        transient = http_status >= 500
        next_actions = ("inspect_provider_contract_or_use_alternate_source",)
    else:
        error_class = "provider_response"
        code = "provider_response_invalid"
        transient = False
        next_actions = ("inspect_provider_contract_or_use_alternate_source",)

    return ErrorEnvelope(
        error_class=error_class,
        code=code,
        message=message,
        provider=provider,
        http_status=http_status,
        transient=transient,
        retry_after_seconds=retry_after,
        attempts=attempts,
        request_count=request_count,
        stage="provider_acceptance",
        next_actions=next_actions,
        details={"probe_status": raw_status} if raw_status else {},
    ).to_dict()


def _discovery_probe(provider: str, runtime: ProviderRuntime) -> tuple[str, Any, str]:
    query = DISCOVERY_QUERIES[provider]
    rows = runtime.execute(
        provider,
        "acceptance_search",
        query,
        lambda: api_discovery.run_provider(
            provider,
            query,
            None,
            None,
            1,
            os.environ.get("OPENALEX_API_KEY"),
            os.environ.get("OPENALEX_MAILTO") or os.environ.get("LITMINER_CONTACT_EMAIL"),
        ),
    )
    if not isinstance(rows, list):
        return "failed", rows, "provider parser did not return a list"
    if not rows:
        return "empty", rows, "provider request completed but returned no parser rows"
    first = rows[0]
    if not isinstance(first, dict) or not (
        first.get("title") or first.get("doi") or first.get("url")
    ):
        return "failed", rows, "provider parser row lacks a title, DOI, or URL"
    return "success", rows, "one bounded parser row was validated"


def _crossref_probe(runtime: ProviderRuntime) -> tuple[str, Any, str]:
    metadata = runtime.execute(
        "crossref",
        "acceptance_doi_lookup",
        ACCEPTANCE_DOI,
        lambda: crossref_verify.verify_doi(ACCEPTANCE_DOI, raise_transient=True),
    )
    if metadata is None:
        return "empty", metadata, "known DOI returned no Crossref metadata"
    if (
        not isinstance(metadata, dict)
        or not metadata.get("crossref_doi")
        or not metadata.get("crossref_title")
    ):
        return "failed", metadata, "Crossref parser result lacks DOI or title"
    return "success", metadata, "known DOI metadata shape was validated"


def _unpaywall_probe(runtime: ProviderRuntime) -> tuple[str, Any, str]:
    email = unpaywall_lookup.resolve_email(None)
    result = runtime.execute(
        "unpaywall",
        "acceptance_doi_lookup",
        ACCEPTANCE_DOI,
        lambda: unpaywall_lookup.lookup_doi(ACCEPTANCE_DOI, email=email),
    )
    if not isinstance(result, dict):
        return "failed", result, "Unpaywall parser did not return an object"
    status = str(result.get("status") or "")
    if status == "skipped_missing_email":
        return "skipped", result, (
            "UNPAYWALL_EMAIL or LITMINER_CONTACT_EMAIL is not configured"
        )
    if status != "ok":
        return "failed", result, f"Unpaywall returned status={status or 'unknown'}"
    data = result.get("data")
    if not isinstance(data, dict) or not data.get("doi"):
        return "failed", result, "Unpaywall parser result lacks DOI data"
    return "success", result, "known DOI OA metadata shape was validated"


def probe_provider(provider: str, runtime: ProviderRuntime) -> dict[str, Any]:
    started = time.monotonic()
    try:
        if provider in DISCOVERY_QUERIES:
            status, value, message = _discovery_probe(provider, runtime)
        elif provider == "crossref":
            status, value, message = _crossref_probe(runtime)
        elif provider == "unpaywall":
            status, value, message = _unpaywall_probe(runtime)
        else:
            raise ValueError(f"unsupported provider acceptance probe: {provider}")
        result = {
            "provider": provider,
            "status": status,
            "passed": status == "success",
            "duration_seconds": round(time.monotonic() - started, 6),
            "message": message,
            "response_shape": _shape(value),
        }
        error = _probe_result_error(provider, status, value, message)
        if error is not None:
            result["error"] = error
        return result
    except (SystemExit, Exception) as exc:
        envelope = classify_exception(
            exc,
            provider=provider,
            stage="provider_acceptance",
        )
        return {
            "provider": provider,
            "status": "failed",
            "passed": False,
            "duration_seconds": round(time.monotonic() - started, 6),
            "message": envelope.message,
            "error": envelope.to_dict(),
        }


def select_providers(profile: str, requested: list[str] | None) -> list[str]:
    if requested:
        selected: list[str] = []
        for value in requested:
            provider = registry.normalize_provider_name(value)
            if provider not in selected:
                selected.append(provider)
        return selected
    return list(CORE_PROVIDERS if profile == "core" else FULL_PROVIDERS)


def _release_transient_failure(result: dict[str, Any]) -> bool:
    if result.get("status") != "failed":
        return False
    error = result.get("error")
    if not isinstance(error, dict) or error.get("transient") is not True:
        return False
    error_class = str(error.get("class") or "")
    if error_class in RELEASE_TRANSIENT_ERROR_CLASSES:
        return True
    if error_class != "provider_response":
        return False
    raw_http_status = error.get("http_status")
    if not isinstance(raw_http_status, (int, str)):
        return False
    try:
        http_status = int(raw_http_status)
    except (TypeError, ValueError):
        return False
    return (
        str(error.get("code") or "") == "provider_http_error"
        and http_status >= 500
    )


def _apply_gate(
    results: list[dict[str, Any]],
    *,
    policy: str,
    allow_skipped: bool,
) -> dict[str, Any]:
    degraded: list[str] = []
    failed: list[str] = []
    for result in results:
        provider = str(result["provider"])
        probe_passed = bool(result.get("passed"))
        required = policy == "release" and provider in RELEASE_REQUIRED_PROVIDERS
        gate_outcome = "healthy" if probe_passed else "failed"
        accepted = probe_passed
        if not probe_passed and policy == "strict":
            if result.get("status") == "skipped" and allow_skipped:
                accepted = True
                gate_outcome = "accepted_skip"
                degraded.append(provider)
        elif not probe_passed and policy == "release" and not required:
            if _release_transient_failure(result):
                accepted = True
                gate_outcome = "degraded_transient"
                degraded.append(provider)
        result["probe_passed"] = probe_passed
        result["required_for_gate"] = required
        result["gate_accepted"] = accepted
        result["gate_outcome"] = gate_outcome
        if not accepted:
            failed.append(provider)
    gate_passed = all(bool(result["gate_accepted"]) for result in results)
    strict_passed = all(bool(result["probe_passed"]) for result in results)
    return {
        "passed": gate_passed,
        "strict_passed": strict_passed,
        "quality": (
            "healthy"
            if strict_passed
            else "degraded"
            if gate_passed
            else "failed"
        ),
        "degraded_providers": degraded,
        "failed_providers": failed,
    }


def run_acceptance(
    *,
    providers: list[str],
    output_dir: Path,
    allow_skipped: bool = False,
    policy: str = "strict",
) -> dict[str, Any]:
    if policy not in {"strict", "release"}:
        raise ValueError(f"unsupported provider acceptance policy: {policy}")
    if policy == "release":
        expected = set(FULL_PROVIDERS)
        selected = set(providers)
        missing = sorted(expected - selected)
        unexpected = sorted(selected - expected)
        duplicate_count = len(providers) - len(selected)
        if missing or unexpected or duplicate_count:
            details: list[str] = []
            if missing:
                details.append("missing=" + ",".join(missing))
            if unexpected:
                details.append("unexpected=" + ",".join(unexpected))
            if duplicate_count:
                details.append(f"duplicates={duplicate_count}")
            raise ValueError(
                "release provider acceptance requires the complete provider set"
                + (": " + "; ".join(details) if details else "")
            )
        if allow_skipped:
            raise ValueError("release provider acceptance never allows skipped providers")
    output_dir.mkdir(parents=True, exist_ok=True)
    state_path = output_dir / "provider_acceptance.sqlite3"
    run_id = "provider_acceptance_" + uuid.uuid4().hex[:12]
    store = StateStore(state_path)
    runtime = ProviderRuntime(store, run_id=run_id, iteration_id="acceptance")
    started = time.monotonic()
    results = [probe_provider(provider, runtime) for provider in providers]
    gate = _apply_gate(
        results,
        policy=policy,
        allow_skipped=allow_skipped,
    )
    report = {
        "schema_version": 1,
        "generated_at": utc_now(),
        "platform": platform.system(),
        "platform_detail": platform.platform(),
        "python_version": platform.python_version(),
        "run_id": run_id,
        "providers": providers,
        "policy": policy,
        "allow_skipped": allow_skipped,
        **gate,
        "required_providers": (
            list(RELEASE_REQUIRED_PROVIDERS) if policy == "release" else []
        ),
        "optional_providers": (
            [
                provider
                for provider in providers
                if provider not in RELEASE_REQUIRED_PROVIDERS
            ]
            if policy == "release"
            else []
        ),
        "duration_seconds": round(time.monotonic() - started, 6),
        "state_store": str(state_path),
        "request_summary": store.request_summary(run_id),
        "results": results,
    }
    report_path = output_dir / "provider_acceptance.json"
    report["report"] = str(report_path)
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile",
        choices=["core", "full", "release"],
        default="core",
    )
    parser.add_argument("--provider", action="append", default=None)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--allow-skipped", action="store_true")
    args = parser.parse_args()
    if args.profile == "release" and args.provider:
        parser.error("--profile release always validates the complete provider set")
    if args.profile == "release" and args.allow_skipped:
        parser.error("--profile release never allows skipped providers")
    try:
        providers = select_providers(args.profile, args.provider)
    except ValueError as exc:
        parser.error(str(exc))
    report = run_acceptance(
        providers=providers,
        output_dir=args.output_dir,
        allow_skipped=args.allow_skipped,
        policy="release" if args.profile == "release" else "strict",
    )
    print(json.dumps({
        "passed": report["passed"],
        "report": report["report"],
        "providers": providers,
        "policy": report["policy"],
        "quality": report["quality"],
        "degraded_providers": report["degraded_providers"],
        "failed_providers": report["failed_providers"],
    }, ensure_ascii=False))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
