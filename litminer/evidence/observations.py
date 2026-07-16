"""Immutable source-observation ingestion for the local evidence ledger."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable

from litminer.engine.common import read_csv_rows, utc_now
from litminer.runtime.state_store import StateStore


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _doi(value: Any) -> str:
    text = _clean(value).lower()
    text = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", text)
    return text.removeprefix("doi:").strip()


def _paper_hint(row: dict[str, Any]) -> str:
    doi = _doi(row.get("crossref_doi") or row.get("doi"))
    if doi:
        return f"doi:{doi}"
    title = _clean(row.get("crossref_title") or row.get("title")).lower()
    year = _clean(row.get("crossref_year") or row.get("publication_year") or row.get("year"))
    return "title:" + hashlib.sha256(f"{title}|{year}".encode("utf-8")).hexdigest()


def observation_record(
    row: dict[str, Any],
    *,
    run_id: str,
    iteration_id: str,
    default_provider: str = "input_csv",
    provider_override: str = "",
    operation: str = "discovery",
) -> dict[str, Any]:
    raw = {str(key): "" if value is None else str(value) for key, value in row.items()}
    provider = _clean(provider_override) or _clean(
        row.get("discovery_provider") or row.get("discovery_source") or default_provider
    ) or default_provider
    query_id = _clean(row.get("discovery_query_id"))
    payload = json.dumps(raw, ensure_ascii=False, sort_keys=True)
    identity = "|".join((run_id, iteration_id, provider, query_id, payload))
    return {
        "observation_id": hashlib.sha256(identity.encode("utf-8")).hexdigest(),
        "run_id": run_id,
        "iteration_id": iteration_id,
        "provider": provider,
        "operation": operation,
        "query_id": query_id,
        "retrieved_at": _clean(row.get("retrieved_at")) or utc_now(),
        "paper_hint": _paper_hint(row),
        "raw_identifier": _doi(row.get("crossref_doi") or row.get("doi")),
        "raw_title": _clean(row.get("crossref_title") or row.get("title")),
        "raw_year": _clean(row.get("crossref_year") or row.get("publication_year") or row.get("year")),
        "raw": raw,
    }


def ingest_rows(
    rows: Iterable[dict[str, Any]],
    store: StateStore,
    *,
    run_id: str,
    iteration_id: str,
    default_provider: str = "input_csv",
    provider_override: str = "",
    operation: str = "discovery",
) -> int:
    count = 0
    for row in rows:
        store.record_observation(observation_record(
            row,
            run_id=run_id,
            iteration_id=iteration_id,
            default_provider=default_provider,
            provider_override=provider_override,
            operation=operation,
        ))
        count += 1
    return count


def ingest_csv_observations(
    path: Path,
    store: StateStore,
    *,
    run_id: str,
    iteration_id: str,
    default_provider: str = "input_csv",
    provider_override: str = "",
    operation: str = "discovery",
) -> int:
    if not path.exists():
        return 0
    _fields, rows = read_csv_rows(path)
    return ingest_rows(
        rows,
        store,
        run_id=run_id,
        iteration_id=iteration_id,
        default_provider=default_provider,
        provider_override=provider_override,
        operation=operation,
    )
