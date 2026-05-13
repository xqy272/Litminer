#!/usr/bin/env python3
"""Merge heterogeneous candidate CSV files into one union-schema CSV."""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path


def merge_csv(inputs: list[Path], output: Path, allow_missing: bool = False) -> None:
    fieldnames: list[str] = []
    rows: list[dict[str, str]] = []

    for path in inputs:
        if not path.exists():
            if allow_missing:
                print(f"Skipping missing input: {path}", file=sys.stderr)
                continue
            raise SystemExit(f"Input CSV not found: {path}")

        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames:
                print(f"Skipping empty/headerless input: {path}", file=sys.stderr)
                continue

            for field in reader.fieldnames:
                if field not in fieldnames:
                    fieldnames.append(field)

            for row in reader:
                row = {k: ("" if v is None else v) for k, v in row.items() if k is not None}
                row["source_file"] = path.name
                if "source_file" not in fieldnames:
                    fieldnames.append("source_file")
                rows.append(row)

    if not fieldnames:
        raise SystemExit("No input rows or headers to merge")

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    print(f"Merged {len(rows)} rows from {len(inputs)} inputs -> {output}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description="Merge candidate CSV files.")
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-missing", action="store_true")
    args = parser.parse_args()
    merge_csv(args.inputs, args.output, allow_missing=args.allow_missing)


if __name__ == "__main__":
    main()
