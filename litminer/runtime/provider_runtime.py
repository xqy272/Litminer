"""Provider call wrapper with request telemetry and persistent health."""

from __future__ import annotations

import os
import time
import urllib.parse
import uuid
from typing import Any, Callable, TypeVar

from litminer.contracts.errors import ProviderCooldownError, classify_exception
from litminer.engine import status_policy
from litminer.runtime.provider_scheduler import ProviderScheduler
from litminer.runtime.state_store import StateStore, query_fingerprint, utc_now


T = TypeVar("T")


class ProviderRuntime:
    def __init__(self, store: StateStore, *, run_id: str = "", iteration_id: str = "") -> None:
        self.store = store
        self.run_id = run_id
        self.iteration_id = iteration_id
        self.scheduler = ProviderScheduler(store)

    def _request_observer(self, provider: str, operation: str, query: str, wait_seconds: float,
                          records: list[dict[str, Any]]) -> Callable[[dict[str, Any]], None]:
        def observe(event: dict[str, Any]) -> None:
            record = {
                "request_id": str(event.get("request_id") or uuid.uuid4().hex),
                "run_id": self.run_id,
                "iteration_id": self.iteration_id,
                "provider": provider,
                "operation": operation,
                "query_hash": query_fingerprint(query),
                "attempt": int(event.get("attempt", 1)),
                "started_at": event.get("started_at", utc_now()),
                "ended_at": event.get("ended_at", utc_now()),
                "latency_ms": event.get("latency_ms"),
                "http_status": event.get("http_status"),
                "status_class": event.get("status_class", "unknown"),
                "retry_after_seconds": event.get("retry_after_seconds"),
                "response_bytes": event.get("response_bytes"),
                "error_code": event.get("error_code", ""),
                "wait_seconds": wait_seconds if not records else 0.0,
                "metadata": {"url_hash": event.get("url_hash", "")},
            }
            records.append(record)
            self.store.record_provider_request(record)
        return observe

    def execute(self, provider: str, operation: str, query: str, callback: Callable[[], T]) -> T:
        try:
            wait_seconds = self.scheduler.acquire(provider)
        except ProviderCooldownError as exc:
            self.store.record_provider_request({
                "request_id": uuid.uuid4().hex,
                "run_id": self.run_id,
                "iteration_id": self.iteration_id,
                "provider": provider,
                "operation": operation,
                "query_hash": query_fingerprint(query),
                "attempt": 0,
                "started_at": utc_now(),
                "ended_at": utc_now(),
                "status_class": "rate_limited",
                "retry_after_seconds": exc.envelope.retry_after_seconds,
                "error_code": exc.envelope.code,
                "wait_seconds": 0.0,
                "metadata": {"scheduler_skip": True},
            })
            raise
        records: list[dict[str, Any]] = []
        started = time.monotonic()
        try:
            from litminer.sources.api.http_client import observe_requests
            with observe_requests(self._request_observer(provider, operation, query, wait_seconds, records)):
                result = callback()
        except BaseException as exc:
            envelope = classify_exception(exc, provider=provider, stage=operation)
            latency_ms = (time.monotonic() - started) * 1000.0
            self.scheduler.record(
                provider,
                status_class=envelope.error_class,
                retry_after_seconds=envelope.retry_after_seconds,
                latency_ms=latency_ms,
            )
            if not records:
                synthetic = {
                    "request_id": uuid.uuid4().hex,
                    "run_id": self.run_id,
                    "iteration_id": self.iteration_id,
                    "provider": provider,
                    "operation": operation,
                    "query_hash": query_fingerprint(query),
                    "attempt": 1,
                    "started_at": utc_now(),
                    "ended_at": utc_now(),
                    "latency_ms": latency_ms,
                    "http_status": envelope.http_status,
                    "status_class": envelope.error_class,
                    "retry_after_seconds": envelope.retry_after_seconds,
                    "error_code": envelope.code,
                    "wait_seconds": wait_seconds,
                }
                self.store.record_provider_request(synthetic)
            raise

        latency_ms = (time.monotonic() - started) * 1000.0
        status = "ok"
        retry_after: float | None = None
        if isinstance(result, dict):
            explicit_status = str(result.get("status_class") or "")
            raw_status = str(result.get("status") or "")
            if explicit_status:
                status = explicit_status
            elif raw_status:
                status = status_policy.classify_status(raw_status)
            elif records:
                status = str(records[-1].get("status_class") or "ok")
            raw_retry = result.get("retry_after_seconds")
            try:
                retry_after = float(raw_retry) if raw_retry not in (None, "") else None
            except (TypeError, ValueError):
                retry_after = None
            result.setdefault("request_count", len(records))
            result.setdefault("attempts", max((int(item.get("attempt", 1)) for item in records), default=0))
            result.setdefault("provider_wait_seconds", round(wait_seconds, 6))
        elif records:
            status = str(records[-1].get("status_class") or "ok")
        if status == "error":
            status = "provider_response"
        if not records and status not in {"ok", "empty_or_missing"}:
            self.store.record_provider_request({
                "request_id": uuid.uuid4().hex,
                "run_id": self.run_id,
                "iteration_id": self.iteration_id,
                "provider": provider,
                "operation": operation,
                "query_hash": query_fingerprint(query),
                "attempt": 0,
                "started_at": utc_now(),
                "ended_at": utc_now(),
                "status_class": status,
                "retry_after_seconds": retry_after,
                "error_code": str(result.get("status") or status) if isinstance(result, dict) else status,
                "wait_seconds": wait_seconds,
                "metadata": {"pre_request_or_local_skip": True},
            })
        self.scheduler.record(
            provider,
            status_class=status,
            retry_after_seconds=retry_after,
            latency_ms=latency_ms,
        )
        return result

    def live_preflight(self, provider: str) -> dict[str, Any]:
        from litminer.sources.api.http_client import RetryPolicy, fetch_bytes

        contact = (
            os.environ.get('UNPAYWALL_EMAIL')
            or os.environ.get('LITMINER_CONTACT_EMAIL')
        )
        if provider == 'unpaywall' and not contact:
            return {
                'provider': provider,
                'ok': False,
                'error': {
                    'class': 'auth',
                    'code': 'contact_email_required',
                    'message': 'Unpaywall live preflight requires UNPAYWALL_EMAIL or LITMINER_CONTACT_EMAIL.',
                    'transient': False,
                    'next_actions': ['configure_contact_email_before_live_preflight'],
                },
            }
        urls = {
            'openalex': 'https://api.openalex.org/works?search=litminer&per-page=1',
            'semantic_scholar': 'https://api.semanticscholar.org/graph/v1/paper/search?query=litminer&limit=1',
            'arxiv': 'https://export.arxiv.org/api/query?search_query=all%3Alitminer&start=0&max_results=1',
            'europe_pmc': 'https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=litminer&pageSize=1&format=json',
            'crossref': 'https://api.crossref.org/works?rows=0',
            'unpaywall': (
                'https://api.unpaywall.org/v2/10.1038/nature12373?email='
                + urllib.parse.quote(contact)
            ),
        }
        if provider not in urls:
            raise ValueError(f'unsupported live preflight provider: {provider}')
        policy = RetryPolicy(max_retries=1, rate_limit_retries=1, max_wait_seconds=0)

        def call() -> dict[str, Any]:
            payload = fetch_bytes(urls[provider], retry=policy, timeout=10.0)
            return {
                'status': 'ok',
                'status_class': 'ok',
                'response_bytes': len(payload),
            }

        try:
            result = self.execute(provider, 'live_preflight', provider, call)
            return {'provider': provider, 'ok': True, **result}
        except BaseException as exc:
            envelope = classify_exception(exc, provider=provider, stage='live_preflight')
            return {'provider': provider, 'ok': False, 'error': envelope.to_dict()}
