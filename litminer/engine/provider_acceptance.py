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
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from litminer.contracts.errors import classify_exception
from litminer.engine import api_discovery
from litminer.runtime.provider_runtime import ProviderRuntime
from litminer.runtime.state_store import StateStore
from litminer.sources.api import crossref_verify, registry, unpaywall_lookup


CORE_PROVIDERS = ("openalex", "crossref")
FULL_PROVIDERS = tuple(registry.PROVIDER_SPECS)
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
        return {
            "provider": provider,
            "status": status,
            "passed": status == "success",
            "duration_seconds": round(time.monotonic() - started, 6),
            "message": message,
            "response_shape": _shape(value),
        }
    except BaseException as exc:
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


def run_acceptance(
    *,
    providers: list[str],
    output_dir: Path,
    allow_skipped: bool = False,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    state_path = output_dir / "provider_acceptance.sqlite3"
    run_id = "provider_acceptance_" + uuid.uuid4().hex[:12]
    store = StateStore(state_path)
    runtime = ProviderRuntime(store, run_id=run_id, iteration_id="acceptance")
    started = time.monotonic()
    results = [probe_provider(provider, runtime) for provider in providers]
    for result in results:
        if result["status"] == "skipped" and allow_skipped:
            result["passed"] = True
    passed = all(bool(result["passed"]) for result in results)
    report = {
        "schema_version": 1,
        "generated_at": utc_now(),
        "platform": platform.system(),
        "platform_detail": platform.platform(),
        "python_version": platform.python_version(),
        "run_id": run_id,
        "providers": providers,
        "allow_skipped": allow_skipped,
        "passed": passed,
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
    parser.add_argument("--profile", choices=["core", "full"], default="core")
    parser.add_argument("--provider", action="append", default=None)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--allow-skipped", action="store_true")
    args = parser.parse_args()
    try:
        providers = select_providers(args.profile, args.provider)
    except ValueError as exc:
        parser.error(str(exc))
    report = run_acceptance(
        providers=providers,
        output_dir=args.output_dir,
        allow_skipped=args.allow_skipped,
    )
    print(json.dumps({
        "passed": report["passed"],
        "report": report["report"],
        "providers": providers,
    }, ensure_ascii=False))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
