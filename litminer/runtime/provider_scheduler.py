"""Polite provider-wide scheduling backed by persistent health state."""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from litminer.contracts.errors import ProviderCooldownError
from litminer.runtime.state_store import StateStore, utc_now
from litminer.sources.api import registry


def _parse_time(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _future(seconds: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=max(0.0, seconds))).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class ProviderPolicy:
    min_interval_seconds: float = 0.0
    default_rate_limit_cooldown_seconds: float = 60.0
    network_cooldown_seconds: float = 30.0
    auth_cooldown_seconds: float = 300.0


POLICIES: dict[str, ProviderPolicy] = {
    "openalex": ProviderPolicy(min_interval_seconds=0.1, default_rate_limit_cooldown_seconds=60.0),
    "semantic_scholar": ProviderPolicy(min_interval_seconds=0.25, default_rate_limit_cooldown_seconds=120.0),
    "arxiv": ProviderPolicy(min_interval_seconds=3.0, default_rate_limit_cooldown_seconds=60.0),
    "europe_pmc": ProviderPolicy(min_interval_seconds=0.1, default_rate_limit_cooldown_seconds=60.0),
    "crossref": ProviderPolicy(min_interval_seconds=0.1, default_rate_limit_cooldown_seconds=60.0),
    "unpaywall": ProviderPolicy(min_interval_seconds=0.1, default_rate_limit_cooldown_seconds=60.0),
}


class ProviderScheduler:
    _locks_guard = threading.Lock()
    _locks: dict[str, threading.Lock] = {}
    _last_started: dict[str, float] = {}

    def __init__(self, store: StateStore) -> None:
        self.store = store

    @classmethod
    def _lock(cls, provider: str) -> threading.Lock:
        with cls._locks_guard:
            return cls._locks.setdefault(provider, threading.Lock())

    def retry_after_seconds(self, provider: str) -> float:
        health = self.store.get_provider_health(provider)
        not_before = _parse_time(str(health.get("not_before") or ""))
        if not_before is None:
            return 0.0
        return max(0.0, (not_before - datetime.now(timezone.utc)).total_seconds())

    def acquire(self, provider: str) -> float:
        remaining = self.retry_after_seconds(provider)
        if remaining > 0:
            raise ProviderCooldownError(provider, remaining, reason="persisted provider health state")
        policy = POLICIES.get(provider, ProviderPolicy())
        with self._lock(provider):
            elapsed = time.monotonic() - self._last_started.get(provider, 0.0)
            wait = max(0.0, policy.min_interval_seconds - elapsed)
            if wait:
                time.sleep(wait)
            self._last_started[provider] = time.monotonic()
            return wait

    def record(self, provider: str, *, status_class: str, retry_after_seconds: float | None = None,
               latency_ms: float | None = None, metadata: dict[str, Any] | None = None) -> None:
        policy = POLICIES.get(provider, ProviderPolicy())
        health = self.store.get_provider_health(provider)
        streak = int(health.get("failure_streak") or 0)
        fields: dict[str, Any] = {
            "last_status_class": status_class,
            "recent_latency_ms": latency_ms,
            "metadata": metadata or health.get("metadata", {}),
        }
        if status_class in {"ok", "empty_or_missing"}:
            fields.update({
                "last_success_at": utc_now(),
                "failure_streak": 0,
                "not_before": "",
                "last_retry_after_seconds": None,
            })
        else:
            streak += 1
            fields.update({"last_failure_at": utc_now(), "failure_streak": streak})
            cooldown = 0.0
            if status_class == "rate_limited":
                cooldown = retry_after_seconds if retry_after_seconds is not None else policy.default_rate_limit_cooldown_seconds
            elif status_class in {"network", "tls", "timeout", "provider_response"}:
                cooldown = policy.network_cooldown_seconds
            elif status_class == "auth":
                cooldown = policy.auth_cooldown_seconds
                fields["credential_state"] = "invalid_or_denied"
            if status_class == "tls":
                fields["tls_state"] = "failed"
            if cooldown > 0:
                fields["not_before"] = _future(cooldown)
                fields["last_retry_after_seconds"] = float(cooldown)
        self.store.update_provider_health(provider, **fields)


def static_capability_rows(store: StateStore, providers: list[str] | None = None) -> list[dict[str, Any]]:
    names = providers or list(registry.PROVIDER_SPECS)
    rows: list[dict[str, Any]] = []
    key_env = {
        "openalex": "OPENALEX_API_KEY",
        "semantic_scholar": "SEMANTIC_SCHOLAR_API_KEY|S2_API_KEY",
    }
    contact_env = {
        "openalex": "OPENALEX_MAILTO|LITMINER_CONTACT_EMAIL",
        "crossref": "CROSSREF_MAILTO|LITMINER_CONTACT_EMAIL",
        "unpaywall": "UNPAYWALL_EMAIL|LITMINER_CONTACT_EMAIL",
    }
    for raw in names:
        provider = registry.normalize_provider_name(raw)
        spec = registry.PROVIDER_SPECS[provider]
        health = store.get_provider_health(provider)
        keys = key_env.get(provider, "").split("|") if key_env.get(provider) else []
        contacts = contact_env.get(provider, "").split("|") if contact_env.get(provider) else []
        key_configured = any(os.environ.get(name) for name in keys) if keys else None
        contact_configured = any(os.environ.get(name) for name in contacts) if contacts else None
        static_status = "ok"
        warnings: list[str] = []
        if spec.requires_key == "required" and not key_configured:
            static_status = "warning"
            warnings.append("required_api_key_missing")
        if spec.requires_contact in {"required", "email_required"} and not contact_configured:
            static_status = "warning"
            warnings.append("required_contact_email_missing")
        rows.append({
            **spec.capability_row(),
            "static_status": static_status,
            "key_configured": key_configured,
            "contact_configured": contact_configured,
            "warnings": warnings,
            "health": health,
            "retry_after_seconds": ProviderScheduler(store).retry_after_seconds(provider),
        })
    return rows
