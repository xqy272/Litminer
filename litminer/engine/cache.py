#!/usr/bin/env python3
"""Small JSON cache helpers for resumable Agent retrieval.

The cache is intentionally simple: one JSON file per namespace under the active
workspace cache directory. It is used only for deterministic provider metadata
and short-lived provider failure state; it is not a database and it does not
replace run artifacts or provenance.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from litminer.engine.common import write_text_atomic
from litminer.engine import workflow_state


DEFAULT_CACHE_DIR = ".litminer/cache"
DEFAULT_TTL_DAYS = 30.0
DEFAULT_PROVIDER_FAILURE_TTL_SECONDS = 300.0


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_time(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def cache_key(*parts: object) -> str:
    payload = {"parts": ["" if part is None else str(part) for part in parts]}
    return workflow_state.stable_fingerprint(payload)


@dataclass
class CacheHit:
    key: str
    value: Any
    status: str
    record: dict[str, Any]


class JsonCache:
    """Tiny JSON-object cache with TTL-aware reads."""

    def __init__(
        self,
        root: Path | str | None,
        namespace: str,
        *,
        enabled: bool = True,
        ttl_seconds: float | None = None,
    ) -> None:
        self.enabled = bool(enabled and root)
        self.root = Path(root) if root else Path(DEFAULT_CACHE_DIR)
        self.namespace = namespace
        self.path = self.root / f"{namespace}.json"
        self.ttl_seconds = ttl_seconds
        self._data: dict[str, Any] | None = None
        self.hits = 0
        self.misses = 0
        self.stores = 0
        self.expired = 0

    def _load(self) -> dict[str, Any]:
        if self._data is not None:
            return self._data
        if not self.enabled or not self.path.exists():
            self._data = {}
            return self._data
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {}
        self._data = data if isinstance(data, dict) else {}
        return self._data

    def _write(self) -> None:
        if not self.enabled:
            return
        data = self._load()
        write_text_atomic(self.path, json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n")

    def _expired(self, record: dict[str, Any]) -> bool:
        expires_at = parse_time(str(record.get("expires_at") or ""))
        if expires_at is not None:
            return utc_now() >= expires_at
        if self.ttl_seconds is None:
            return False
        updated_at = parse_time(str(record.get("updated_at") or record.get("created_at") or ""))
        if updated_at is None:
            return True
        return utc_now() - updated_at > timedelta(seconds=max(0.0, self.ttl_seconds))

    def get(self, key: str) -> CacheHit | None:
        if not self.enabled:
            return None
        data = self._load()
        raw = data.get(key)
        if not isinstance(raw, dict):
            self.misses += 1
            return None
        if self._expired(raw):
            self.expired += 1
            data.pop(key, None)
            self._write()
            return None
        self.hits += 1
        return CacheHit(
            key=key,
            value=raw.get("value"),
            status=str(raw.get("status") or ""),
            record=raw,
        )

    def set(
        self,
        key: str,
        value: Any,
        *,
        status: str = "ok",
        ttl_seconds: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if not self.enabled:
            return
        now = iso_now()
        existing = self._load().get(key)
        created_at = existing.get("created_at") if isinstance(existing, dict) else now
        expires_at = ""
        effective_ttl = self.ttl_seconds if ttl_seconds is None else ttl_seconds
        if effective_ttl is not None:
            expires_at = (utc_now() + timedelta(seconds=max(0.0, effective_ttl))).strftime("%Y-%m-%dT%H:%M:%SZ")
        record = {
            "schema_version": 1,
            "namespace": self.namespace,
            "key": key,
            "status": status,
            "created_at": created_at,
            "updated_at": now,
            "expires_at": expires_at,
            "metadata": metadata or {},
            "value": value,
        }
        self._load()[key] = record
        self.stores += 1
        self._write()

    def stats(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "namespace": self.namespace,
            "path": str(self.path),
            "hits": self.hits,
            "misses": self.misses,
            "stores": self.stores,
            "expired": self.expired,
        }


def ttl_days_to_seconds(value: float | int | None) -> float | None:
    if value is None:
        return None
    return max(0.0, float(value)) * 86400.0
