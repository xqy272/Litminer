"""Shared canonical-row loading and export eligibility rules."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from litminer.engine.common import read_csv_rows


TRUTHY = {"1", "true", "yes", "y"}


def truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in TRUTHY


def load_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"canonical bibliography input not found: {path}")
    _fields, rows = read_csv_rows(path)
    return rows


def split_authors(value: str) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    if ";" in text:
        raw = text.split(";")
    elif "|" in text:
        raw = text.split("|")
    elif " and " in text.lower():
        raw = re.split(r"\s+and\s+", text, flags=re.IGNORECASE)
    else:
        raw = [text]
    return [re.sub(r"\s+", " ", item).strip() for item in raw if item.strip()]


def select_export_rows(rows: list[dict[str, str]], *, include_unverified: bool = False) -> tuple[list[dict[str, str]], dict[str, int]]:
    selected: list[dict[str, str]] = []
    reasons = {
        "missing_title": 0,
        "unverified": 0,
        "retracted": 0,
    }
    for row in rows:
        if not (row.get("title") or "").strip():
            reasons["missing_title"] += 1
            continue
        if (row.get("retraction_status") or "").strip().lower() == "retracted":
            reasons["retracted"] += 1
            continue
        trusted_marker = str(row.get("trusted_bibliography") or "").strip()
        trusted = (
            truthy(trusted_marker)
            if trusted_marker
            else truthy(row.get("export_eligible"))
        )
        if not trusted and not include_unverified:
            reasons["unverified"] += 1
            continue
        selected.append(row)
    return selected, reasons


def file_sha256(path: Path) -> str:
    if not path.exists():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
