"""Structured error contracts for CLI, MCP, stages, and providers."""

from __future__ import annotations

import ssl
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable


ERROR_CLASSES = {
    "validation",
    "workspace",
    "auth",
    "rate_limited",
    "network",
    "tls",
    "timeout",
    "provider_response",
    "budget_limited",
    "cancelled",
    "internal",
}


@dataclass(frozen=True)
class ErrorEnvelope:
    """Machine-readable error returned by every Agent-facing interface."""

    error_class: str
    code: str
    message: str
    provider: str = ""
    http_status: int | None = None
    transient: bool | None = None
    retry_after_seconds: float | None = None
    attempts: int | None = None
    request_count: int | None = None
    stage: str = ""
    next_actions: tuple[str, ...] = field(default_factory=tuple)
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.error_class not in ERROR_CLASSES:
            raise ValueError(f"unknown Litminer error class: {self.error_class}")

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["class"] = data.pop("error_class")
        data["next_actions"] = list(self.next_actions)
        return {key: value for key, value in data.items() if value not in (None, "", {}, [])}


class LitminerError(RuntimeError):
    """Exception carrying a stable :class:`ErrorEnvelope`."""

    def __init__(self, envelope: ErrorEnvelope) -> None:
        super().__init__(envelope.message)
        self.envelope = envelope


class LitminerValidationError(LitminerError):
    def __init__(self, message: str, *, code: str = "invalid_arguments", details: dict[str, Any] | None = None) -> None:
        super().__init__(ErrorEnvelope(
            error_class="validation",
            code=code,
            message=message,
            transient=False,
            next_actions=("correct_arguments_and_retry",),
            details=details or {},
        ))


class ProviderCooldownError(LitminerError):
    def __init__(self, provider: str, retry_after_seconds: float, *, reason: str = "provider cooldown") -> None:
        super().__init__(ErrorEnvelope(
            error_class="rate_limited",
            code="provider_cooldown_active",
            message=f"{provider} is unavailable until its persisted cooldown expires: {reason}",
            provider=provider,
            transient=True,
            retry_after_seconds=max(0.0, float(retry_after_seconds)),
            next_actions=("resume_after_retry_after", "continue_with_healthy_sources"),
        ))


def _next_actions(error_class: str) -> tuple[str, ...]:
    return {
        "validation": ("correct_arguments_and_retry",),
        "workspace": ("move_paths_under_workspace_or_update_workspace_root",),
        "auth": ("check_provider_credentials_contact_email_or_access_policy",),
        "rate_limited": ("resume_after_retry_after", "reduce_request_volume"),
        "network": ("check_network_proxy_dns_and_retry",),
        "tls": ("check_tls_certificate_proxy_and_system_clock",),
        "timeout": ("resume_or_retry_with_lower_request_volume",),
        "provider_response": ("inspect_provider_contract_or_use_alternate_source",),
        "budget_limited": ("resume_with_higher_budget_or_use_existing_artifacts",),
        "cancelled": ("resume_if_the_same_run_is_still_required",),
        "internal": ("inspect_debug_trace_and_report_bug",),
    }[error_class]


def classify_exception(
    exc: BaseException,
    *,
    provider: str = "",
    stage: str = "",
    code: str = "",
) -> ErrorEnvelope:
    """Convert arbitrary exceptions to the stable Litminer error vocabulary."""

    if isinstance(exc, LitminerError):
        return exc.envelope

    message = str(exc) or exc.__class__.__name__
    status = str(getattr(exc, "status", "") or "").strip().lower()
    http_status = getattr(exc, "http_status", None)
    if http_status in (None, ""):
        http_status = getattr(exc, "code", None)
    retry_after = getattr(exc, "retry_after_seconds", None)
    transient = getattr(exc, "transient", None)
    attempts = getattr(exc, "attempts", None)
    request_count = getattr(exc, "request_count", None)
    lowered = message.lower()

    if isinstance(exc, ValueError) and any(
        marker in lowered
        for marker in ("escapes litminer workspace", "workspace_root=", "outside the workspace")
    ):
        error_class = "workspace"
        default_code = "workspace_boundary_violation"
        transient = False
    elif isinstance(exc, (ValueError, TypeError, KeyError)) or isinstance(exc, SystemExit):
        error_class = "validation"
        default_code = "invalid_arguments"
        transient = False
    elif isinstance(exc, (FileNotFoundError, PermissionError)):
        error_class = "workspace"
        default_code = "workspace_io_error"
        transient = False
    elif isinstance(exc, (ssl.SSLError, ssl.CertificateError)) or any(
        marker in lowered for marker in ("ssl", "certificate", "tls")
    ):
        error_class = "tls"
        default_code = "tls_error"
        transient = True if transient is None else transient
    elif isinstance(exc, TimeoutError) or "timed out" in lowered or "timeout" in status:
        error_class = "timeout"
        default_code = "request_timeout"
        transient = True if transient is None else transient
    elif http_status in {401, 403} or status in {"auth_error", "http_401", "http_403"}:
        error_class = "auth"
        default_code = "provider_auth_error"
        transient = False
    elif http_status == 429 or "rate_limit" in status or "too many requests" in lowered:
        error_class = "rate_limited"
        default_code = "provider_rate_limited"
        transient = True
    elif status == "skipped_budget" or "budget" in status:
        error_class = "budget_limited"
        default_code = "budget_exhausted"
        transient = False
    elif any(marker in status or marker in lowered for marker in ("network", "dns", "connection", "name resolution")):
        error_class = "network"
        default_code = "network_error"
        transient = True if transient is None else transient
    elif status in {"response_parse_error"} or any(marker in lowered for marker in ("invalid json", "parse error")):
        error_class = "provider_response"
        default_code = "provider_response_invalid"
        transient = True if transient is None else transient
    elif (isinstance(http_status, int) and http_status >= 400) or status.startswith("http_"):
        error_class = "provider_response"
        default_code = "provider_http_error"
        transient = (bool(http_status and int(http_status) >= 500) if transient is None else transient)
    else:
        error_class = "internal"
        default_code = "internal_error"

    return ErrorEnvelope(
        error_class=error_class,
        code=code or default_code,
        message=message,
        provider=provider or str(getattr(exc, "provider", "") or ""),
        http_status=int(http_status) if http_status not in (None, "") else None,
        transient=transient,
        retry_after_seconds=float(retry_after) if retry_after not in (None, "") else None,
        attempts=int(attempts) if attempts not in (None, "") else None,
        request_count=int(request_count) if request_count not in (None, "") else None,
        stage=stage,
        next_actions=_next_actions(error_class),
    )


def error_result(error: ErrorEnvelope, *, debug_trace: str = "") -> dict[str, Any]:
    payload: dict[str, Any] = {"ok": False, "error": error.to_dict()}
    if debug_trace:
        payload["debug_trace"] = debug_trace
    return payload


def validation_error(message: str, *, fields: Iterable[str] = ()) -> LitminerValidationError:
    return LitminerValidationError(message, details={"fields": list(fields)} if fields else {})
