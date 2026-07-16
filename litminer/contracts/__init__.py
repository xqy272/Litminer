"""Stable public contracts shared by Litminer interfaces and runtimes."""

from .errors import ErrorEnvelope, LitminerError, classify_exception
from .outcomes import RunOutcome
from .run_spec import RunSpec

__all__ = [
    "ErrorEnvelope",
    "LitminerError",
    "RunOutcome",
    "RunSpec",
    "classify_exception",
]
