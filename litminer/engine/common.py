"""Shared low-level helpers for Litminer engine and source modules."""

from __future__ import annotations

import csv
import os
import re
import tempfile
from pathlib import Path
from typing import Iterable, Literal


DOI_PREFIX_RE = re.compile(r"^(https?://(dx\.)?doi\.org/|doi:\s*)", re.I)


def cell_text(value: object) -> str:
    """Normalize CSV cell values, including DictReader overflow lists."""
    if value is None:
        return ""
    if isinstance(value, list):
        return " ".join(str(item) for item in value if item is not None)
    return str(value)


def normalize_doi(value: object) -> str:
    """Normalize DOI-like text to lowercase bare DOI form."""
    text = cell_text(value).strip().lower()
    text = DOI_PREFIX_RE.sub("", text)
    return text.strip().rstrip(".,;)[]")


def fieldnames_from_rows(
    rows: Iterable[dict[str, str]],
    fallback_fields: Iterable[str] | None = None,
) -> list[str]:
    fields = list(fallback_fields or [])
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    return fields


def read_csv_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    """Read a CSV into normalized string rows while preserving header order."""
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        rows = [
            {key: cell_text(value) for key, value in row.items() if key is not None}
            for row in reader
        ]
    return fieldnames, rows


def _atomic_replace(path: Path, writer) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        writer(tmp_path)
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def write_text_atomic(path: Path, text: str) -> None:
    """Write text atomically in the target directory."""

    def _write(tmp_path: Path) -> None:
        tmp_path.write_text(text, encoding="utf-8")

    _atomic_replace(path, _write)


def write_csv_atomic(
    rows: list[dict[str, str]],
    output: Path,
    fieldnames: Iterable[str] | None = None,
    fallback_fields: Iterable[str] | None = None,
    extrasaction: Literal["raise", "ignore"] = "ignore",
) -> None:
    """Write CSV atomically while preserving stable field order."""
    fields = list(fieldnames or fieldnames_from_rows(rows, fallback_fields))

    def _write(tmp_path: Path) -> None:
        with tmp_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction=extrasaction)
            writer.writeheader()
            writer.writerows(rows)

    _atomic_replace(output, _write)
