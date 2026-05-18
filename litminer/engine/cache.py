#!/usr/bin/env python3
"""Small JSON cache helpers for resumable Agent retrieval.

The cache is intentionally simple: one JSON file per namespace under the active
workspace cache directory. It is used only for deterministic provider metadata
and short-lived provider failure state; it is not a database and it does not
replace run artifacts or provenance.
"""

from __future__ import annotations

import importlib
import json
import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from litminer.engine.common import write_text_atomic
from litminer.engine import workflow_state


DEFAULT_CACHE_DIR = ".litminer/cache"
DEFAULT_TTL_DAYS = 30.0
DEFAULT_PROVIDER_FAILURE_TTL_SECONDS = 300.0

_msvcrt: Any = importlib.import_module("msvcrt") if os.name == "nt" else None
_fcntl: Any = importlib.import_module("fcntl") if os.name != "nt" else None


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


@contextmanager
def _exclusive_file_lock(lock_path: Path):
    """Take an advisory lock for cache file read-modify-write cycles."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        if os.name == "nt":
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            assert _msvcrt is not None
            _msvcrt.locking(handle.fileno(), _msvcrt.LK_LOCK, 1)
        else:
            assert _fcntl is not None
            _fcntl.flock(handle.fileno(), _fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                assert _msvcrt is not None
                _msvcrt.locking(handle.fileno(), _msvcrt.LK_UNLCK, 1)
            else:
                assert _fcntl is not None
                _fcntl.flock(handle.fileno(), _fcntl.LOCK_UN)


class JsonCache:
    """Tiny JSON-object cache with TTL-aware, lock-protected reads/writes."""

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
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self._data: dict[str, Any] | None = None
        self.hits = 0
        self.misses = 0
        self.stores = 0
        self.expired = 0

    def _read_unlocked(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {}
        return data if isinstance(data, dict) else {}

    def _load(self) -> dict[str, Any]:
        if not self.enabled:
            self._data = {}
            return self._data
        with _exclusive_file_lock(self.lock_path):
            self._data = self._read_unlocked()
        return self._data

    def _write_unlocked(self, data: dict[str, Any]) -> None:
        write_text_atomic(self.path, json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
        self._data = data

    def _write(self) -> None:
        if not self.enabled:
            return
        with _exclusive_file_lock(self.lock_path):
            self._write_unlocked(self._data if self._data is not None else self._read_unlocked())

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
        with _exclusive_file_lock(self.lock_path):
            data = self._read_unlocked()
            raw = data.get(key)
            if not isinstance(raw, dict):
                self._data = data
                self.misses += 1
                return None
            if self._expired(raw):
                self.expired += 1
                data.pop(key, None)
                self._write_unlocked(data)
                return None
            self._data = data
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
        with _exclusive_file_lock(self.lock_path):
            data = self._read_unlocked()
            existing = data.get(key)
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
            data[key] = record
            self._write_unlocked(data)
            self.stores += 1

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
