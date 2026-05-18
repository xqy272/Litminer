#!/usr/bin/env python3
"""Common status classification for Agent recovery decisions.

This module is intentionally small and string-based because statuses are written
to CSV/JSON artifacts. Keep provider, stage, and cache recovery semantics here
so Agents see one vocabulary across reports, traces, and summaries.
"""

from __future__ import annotations


OK_STATUSES = {"ok", "verified", "title_recovered", "pass", "completed", "skipped_existing"}
EMPTY_OR_MISSING_STATUSES = {
    "empty_result",
    "not_found",
    "missing_doi",
    "title_lookup_failed",
    "lookup_failed",
}
BUDGET_STATUSES = {"skipped_budget", "partial_budget"}


def classify_status(status: str) -> str:
    normalized = (status or "").strip().lower()
    if normalized in OK_STATUSES:
        return "ok"
    if normalized in EMPTY_OR_MISSING_STATUSES:
        return "empty_or_missing"
    if normalized in BUDGET_STATUSES:
        return "budget_limited"
    if normalized in {"missing_email", "skipped_missing_email"} or "missing_email" in normalized:
        return "auth"
    if normalized.startswith("skipped"):
        return "skipped"
    if (
        "rate_limited" in normalized
        or "rate_limit" in normalized
        or "too_many_requests" in normalized
        or normalized in {"http_429", "429"}
    ):
        return "rate_limited"
    if normalized in {"http_401", "http_403"}:
        return "auth"
    if any(
        marker in normalized
        for marker in (
            "network",
            "certificate",
            "ssl",
            "dns",
            "timeout",
            "connection",
            "name_resolution",
            "name resolution",
            "http_408",
        )
    ):
        return "network"
    if "auth" in normalized or "forbidden" in normalized or "unauthorized" in normalized:
        return "auth"
    if normalized.startswith("partial"):
        return "partial"
    if normalized in {"mismatch", "error", "provider_error", "response_parse_error"} or normalized.startswith("http_"):
        return "error"
    if not normalized or normalized == "<blank>":
        return "unknown"
    return "needs_review"


def next_action(status: str) -> str:
    status_class = classify_status(status)
    if status_class == "ok":
        return "use_as_verified_for_this_stage"
    if status_class == "empty_or_missing":
        return "treat_as_missing_for_this_source_only_or_try_title/source_expansion"
    if status_class == "budget_limited":
        return "resume_with_higher_row_budget_or_continue_from_existing_artifacts"
    if status_class == "skipped":
        return "inspect_skip_reason_before_rerun"
    if status_class == "rate_limited":
        return "resume_later_or_reduce_request_volume"
    if status_class == "network":
        return "check_agent_network_permission_proxy_or_certificate"
    if status_class == "auth":
        return "check_api_key_contact_email_or_provider_access_policy"
    if status_class == "partial":
        return "keep_partial_rows_and_resume_or_retry_failed_stage"
    if status_class == "error":
        return "inspect_error_and_verify_with_alternate_source"
    return "manual_review"


def provider_next_action(status: str, retry_after_seconds: str = "") -> str:
    """Return a provider-trace-oriented next action for API discovery rows."""
    status_class = classify_status(status)
    if status_class == "ok":
        return "use_returned_candidates"
    if status_class == "empty_or_missing":
        return "treat_as_no_candidates_for_this_query_source_only"
    if status_class == "auth":
        return "check_provider_key_contact_email_or_access_policy_before_retrying"
    if status_class == "network":
        return "check_agent_network_permission_proxy_or_certificate_before_retrying"
    if status_class == "rate_limited":
        if retry_after_seconds:
            return "retry_provider_after_retry_after_or_resume_later"
        return "retry_provider_later_or_reduce_query_volume"
    if status_class == "partial":
        return "keep_partial_rows_and_resume_or_retry_provider_later"
    if status == "skipped_circuit_breaker":
        return "continue_with_other_sources_or_lower_provider_failure_threshold"
    if status == "skipped_rate_limit_cooldown":
        return "retry_this_provider_after_cooldown_or_continue_with_other_sources"
    if status == "skipped_cached_provider_failure":
        return "wait_for_failure_cache_ttl_or_disable_cache_after_environment_fix"
    if status_class == "skipped":
        return "inspect_skip_reason_before_rerun"
    if status_class == "budget_limited":
        return "resume_with_higher_row_budget_or_continue_from_existing_artifacts"
    return "inspect_error_and_continue_with_other_sources_when_possible"


def is_provider_failure(status: str) -> bool:
    return classify_status(status) in {"auth", "error", "network", "partial", "rate_limited"}


def is_cacheable_provider_failure(status: str, *, has_rows: bool = False, transient: bool | None = None) -> bool:
    """Return whether a failed provider call should suppress an immediate retry.

    Cache only transient failures that would likely repeat during the same run.
    Auth and generic errors are deliberately not cached because a user can fix a
    key/config/code issue immediately and should not be blocked by stale state.
    """
    if has_rows:
        return False
    status_class = classify_status(status)
    if status_class == "rate_limited":
        return True
    if status_class == "network":
        return transient is not False
    if status_class == "error":
        return transient is True
    return False
