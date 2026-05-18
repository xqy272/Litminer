"""Lightweight workflow manifest helpers for resumable Litminer runs."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from litminer.engine.common import read_csv_rows, write_text_atomic


MANIFEST_NAME = "run_manifest.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def manifest_path(out_dir: Path) -> Path:
    return out_dir / MANIFEST_NAME


def load_manifest(out_dir: Path) -> dict[str, Any]:
    path = manifest_path(out_dir)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def stable_fingerprint(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def file_sha256(path: Path | None) -> str:
    if path is None or not path.exists() or not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def csv_fieldnames(path: Path | None) -> list[str]:
    if path is None or not path.exists() or not path.is_file():
        return []
    if path.suffix.lower() != ".csv":
        return []
    try:
        fieldnames, _rows = read_csv_rows(path)
    except Exception:
        return []
    return fieldnames


def new_manifest(
    args: Any,
    existing: dict[str, Any] | None = None,
    signature: str = "",
    signature_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    prior = existing or {}
    run_id = prior.get("run_id") or datetime.now(timezone.utc).strftime("workflow_%Y%m%dT%H%M%SZ")
    mode = getattr(args, "mode", None) or "custom/default"
    started_at = prior.get("started_at") or prior.get("created_at") or utc_now()
    return {
        "schema_version": 1,
        "run_id": run_id,
        "created_at": prior.get("created_at") or started_at,
        "started_at": started_at,
        "updated_at": utc_now(),
        "mode": mode,
        "resume_enabled": bool(getattr(args, "resume", False)),
        "output_dir": str(getattr(args, "output_dir", "")),
        "query_count": len((signature_payload or {}).get("queries", []) or getattr(args, "query", None) or []),
        "year_from": getattr(args, "year_from", None),
        "year_to": getattr(args, "year_to", None),
        "run_signature": signature or prior.get("run_signature", ""),
        "run_signature_payload": signature_payload or prior.get("run_signature_payload", {}),
        "stages": prior.get("stages", []) if isinstance(prior.get("stages"), list) else [],
    }


def write_manifest(out_dir: Path, manifest: dict[str, Any]) -> None:
    manifest["updated_at"] = utc_now()
    write_text_atomic(manifest_path(out_dir), json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")


def row_count(path: Path | None) -> int:
    if path is None or not path.exists() or not path.is_file():
        return 0
    try:
        _fieldnames, rows = read_csv_rows(path)
    except Exception:
        return 0
    return len(rows)


def reusable_csv(path: Path) -> bool:
    if not path.exists() or not path.is_file():
        return False
    try:
        fieldnames, _rows = read_csv_rows(path)
    except Exception:
        return False
    return bool(fieldnames)


def _path_text(path: Path | None) -> str:
    return str(path.resolve(strict=False)) if path else ""


def record_stage(
    manifest: dict[str, Any],
    name: str,
    status: str,
    *,
    input_path: Path | None = None,
    output_path: Path | None = None,
    row_count_value: int | None = None,
    message: str = "",
) -> None:
    records = manifest.setdefault("stages", [])
    if not isinstance(records, list):
        records = []
        manifest["stages"] = records
    now = utc_now()
    records.append({
        "name": name,
        "status": status,
        "input": _path_text(input_path),
        "output": _path_text(output_path),
        "input_path": _path_text(input_path),
        "output_path": _path_text(output_path),
        "input_sha256": file_sha256(input_path),
        "output_sha256": file_sha256(output_path),
        "output_fields": csv_fieldnames(output_path),
        "row_count": row_count_value if row_count_value is not None else row_count(output_path),
        "message": message,
        "recorded_at": now,
    })


def latest_stage(manifest: dict[str, Any] | None, name: str) -> dict[str, Any] | None:
    if not manifest:
        return None
    records = manifest.get("stages", [])
    if not isinstance(records, list):
        return None
    for record in reversed(records):
        if isinstance(record, dict) and record.get("name") == name:
            return record
    return None


def reusable_stage(
    manifest: dict[str, Any] | None,
    name: str,
    output_path: Path,
    *,
    input_path: Path | None = None,
    completed_statuses: set[str] | None = None,
) -> bool:
    """Return true when an existing stage output matches the manifest record."""
    if not reusable_csv(output_path):
        return False
    record = latest_stage(manifest, name)
    if record is None:
        return False
    statuses = completed_statuses or {"completed", "skipped_existing", "skipped_single_input", "skipped_input_csv"}
    if str(record.get("status") or "") not in statuses:
        return False

    recorded_output = str(record.get("output") or "")
    current_output = _path_text(output_path)
    if recorded_output and recorded_output != current_output:
        return False

    recorded_output_sha = str(record.get("output_sha256") or "")
    if not recorded_output_sha or recorded_output_sha != file_sha256(output_path):
        return False

    if input_path is not None:
        recorded_input_sha = str(record.get("input_sha256") or "")
        if not recorded_input_sha or recorded_input_sha != file_sha256(input_path):
            return False

    recorded_fields = record.get("output_fields")
    if isinstance(recorded_fields, list) and recorded_fields:
        return recorded_fields == csv_fieldnames(output_path)
    return True
