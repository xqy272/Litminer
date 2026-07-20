"""Crash-only wrapper used by runtime resilience acceptance.

This helper keeps fault injection out of the production runner. It wraps the
normal stage recorder, lets the completed stage and SQLite transaction persist,
then terminates the process without Python cleanup.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from litminer.engine import run_lit_search


TARGET_STAGE = os.environ.get("LITMINER_CRASH_AFTER_STAGE", "dedupe")
EXIT_CODE = int(os.environ.get("LITMINER_CRASH_EXIT_CODE", "91"))
_original_record_manifest_stage = run_lit_search.record_manifest_stage


def _record_then_crash(*args, **kwargs) -> None:
    _original_record_manifest_stage(*args, **kwargs)
    name = str(args[2] if len(args) > 2 else kwargs.get("name") or "")
    status = str(args[3] if len(args) > 3 else kwargs.get("status") or "")
    if name == TARGET_STAGE and status == "completed":
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(EXIT_CODE)


run_lit_search.record_manifest_stage = _record_then_crash
run_lit_search.main()
