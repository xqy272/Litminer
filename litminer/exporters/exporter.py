"""Audited RIS/BibTeX export orchestration and CLI."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from litminer.engine.common import utc_now, write_text_atomic
from litminer.exporters import bibtex, ris
from litminer.exporters.common import file_sha256, load_rows, select_export_rows


MANIFEST_NAME = "export_manifest.json"
INVALID_PREFIX_CHARS = frozenset(r'<>:"/\|?*')


def _validate_output_prefix(value: str) -> str:
    output_prefix = str(value or "litminer_export").strip()
    invalid_character = any(
        character in INVALID_PREFIX_CHARS or ord(character) < 32
        for character in output_prefix
    )
    if (
        not output_prefix
        or output_prefix in {".", ".."}
        or output_prefix.endswith((".", " "))
        or invalid_character
        or Path(output_prefix).name != output_prefix
    ):
        raise ValueError(
            "output_prefix must be a plain file prefix without directory components "
            "or platform-reserved filename characters"
        )
    return output_prefix


def export_bibliography(
    input_csv: Path,
    output_dir: Path,
    *,
    formats: list[str] | tuple[str, ...],
    output_prefix: str = "litminer_export",
    include_unverified: bool = False,
    ascii_latex: bool = False,
) -> dict[str, Any]:
    output_prefix = _validate_output_prefix(output_prefix)
    normalized_formats = list(dict.fromkeys(str(item).strip().lower() for item in formats if str(item).strip()))
    unknown = sorted(set(normalized_formats) - {"ris", "bibtex"})
    if unknown:
        raise ValueError(f"unknown export format(s): {', '.join(unknown)}")
    if not normalized_formats:
        raise ValueError("at least one export format is required")
    rows = load_rows(input_csv)
    selected, excluded = select_export_rows(rows, include_unverified=include_unverified)
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs: dict[str, dict[str, Any]] = {}
    key_conflicts = 0
    if "ris" in normalized_formats:
        path = ris.write(selected, output_dir / f"{output_prefix}.ris")
        outputs["ris"] = {"path": str(path), "sha256": file_sha256(path)}
    if "bibtex" in normalized_formats:
        path, key_conflicts = bibtex.write(
            selected,
            output_dir / f"{output_prefix}.bib",
            ascii_latex=ascii_latex,
        )
        outputs["bibtex"] = {"path": str(path), "sha256": file_sha256(path)}

    unverified_exported = sum(
        1 for row in selected
        if str(row.get("trusted_bibliography") or "").strip().lower() != "true"
    )
    manifest = {
        "schema_version": 1,
        "generated_at": utc_now(),
        "input_csv": str(input_csv),
        "input_sha256": file_sha256(input_csv),
        "formats": normalized_formats,
        "input_rows": len(rows),
        "exported_rows": len(selected),
        "excluded_rows": sum(excluded.values()),
        "excluded_reasons": excluded,
        "include_unverified": bool(include_unverified),
        "unverified_exported": unverified_exported,
        "ascii_latex": bool(ascii_latex),
        "bibtex_key_conflicts": key_conflicts,
        "outputs": outputs,
        "boundary": (
            "Exports are bibliographic projections. Unverified rows are excluded by default "
            "and must not be presented as verified literature when explicitly included."
        ),
    }
    manifest_path = output_dir / MANIFEST_NAME
    write_text_atomic(manifest_path, json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    return {**manifest, "manifest": str(manifest_path)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Export canonical Litminer bibliography to RIS/BibTeX.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--format", action="append", choices=["ris", "bibtex"], required=True)
    parser.add_argument("--output-prefix", default="litminer_export")
    parser.add_argument("--include-unverified", action="store_true")
    parser.add_argument("--ascii-latex", action="store_true")
    args = parser.parse_args()
    result = export_bibliography(
        args.input,
        args.output_dir,
        formats=args.format,
        output_prefix=args.output_prefix,
        include_unverified=args.include_unverified,
        ascii_latex=args.ascii_latex,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
