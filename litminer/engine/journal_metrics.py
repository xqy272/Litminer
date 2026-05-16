#!/usr/bin/env python3
"""Annotate and filter candidate CSV rows by verified journal metrics.

The default metric file provides only the expected CSV header. For real metric
filtering, pass a project-specific verified metrics CSV with the same columns.
The script never guesses impact factors.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from litminer.engine.common import write_csv_atomic


PROJECT_DEFAULT_METRICS = Path(__file__).resolve().parents[2] / "references" / "journal_metrics_seed.csv"
PACKAGE_DEFAULT_METRICS = Path(__file__).resolve().parents[1] / "references" / "journal_metrics_seed.csv"
DEFAULT_METRICS = PROJECT_DEFAULT_METRICS if PROJECT_DEFAULT_METRICS.exists() else PACKAGE_DEFAULT_METRICS
REQUIRED_COLUMNS = [
    "journal",
    "aliases",
    "issn",
    "impact_factor",
    "metric_year",
    "metric_source",
    "source_url",
    "last_checked",
    "confidence",
]


@dataclass
class Metric:
    journal: str
    aliases: list[str]
    issns: list[str]
    impact_factor: str
    metric_year: str
    metric_source: str
    source_url: str
    last_checked: str
    confidence: str

    @property
    def impact_float(self) -> float | None:
        try:
            return float(self.impact_factor)
        except (TypeError, ValueError):
            return None


def normalize_journal(value: str) -> str:
    value = (value or "").lower().strip()
    value = value.replace("&amp;", "&").replace("&", " and ")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    value = re.sub(r"\b(the|journal|of)\b", " ", value)
    return " ".join(value.split())


def normalize_issn(value: str) -> str:
    return re.sub(r"[^0-9xX]", "", value or "").upper()


def split_list(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"[;|]", value or "") if item.strip()]


def load_metrics(path: Path = DEFAULT_METRICS) -> list[Metric]:
    if not path.exists():
        raise SystemExit(f"Journal metrics file not found: {path}")

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise SystemExit(f"Journal metrics file has no header: {path}")
        metrics = []
        for row in reader:
            journal = (row.get("journal") or "").strip()
            if not journal:
                continue
            metrics.append(Metric(
                journal=journal,
                aliases=split_list(row.get("aliases", "")),
                issns=[normalize_issn(v) for v in split_list(row.get("issn", ""))],
                impact_factor=(row.get("impact_factor") or "").strip(),
                metric_year=(row.get("metric_year") or "").strip(),
                metric_source=(row.get("metric_source") or "").strip(),
                source_url=(row.get("source_url") or "").strip(),
                last_checked=(row.get("last_checked") or "").strip(),
                confidence=(row.get("confidence") or "").strip(),
            ))
    return metrics


def validate_metrics(path: Path = DEFAULT_METRICS, *, require_numeric_if: bool = False) -> dict[str, object]:
    """Validate a journal metrics CSV before an Agent relies on it."""
    errors: list[str] = []
    warnings: list[str] = []
    if not path.exists():
        return {
            "status": "error",
            "row_count": 0,
            "errors": [f"journal metrics file not found: {path}"],
            "warnings": [],
        }

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        missing = [column for column in REQUIRED_COLUMNS if column not in fieldnames]
        if missing:
            errors.append(f"missing required columns: {', '.join(missing)}")
        rows = list(reader)

    by_name: dict[str, str] = {}
    by_issn: dict[str, str] = {}
    for index, row in enumerate(rows, start=2):
        journal = (row.get("journal") or "").strip()
        if not journal:
            warnings.append(f"row {index}: journal is blank")
            continue
        for column in ("metric_year", "metric_source", "source_url", "last_checked", "confidence"):
            if not (row.get(column) or "").strip():
                warnings.append(f"row {index} {journal}: {column} is blank")
        impact_factor = (row.get("impact_factor") or "").strip()
        if require_numeric_if or impact_factor:
            try:
                float(impact_factor)
            except ValueError:
                errors.append(f"row {index} {journal}: impact_factor is not numeric: {impact_factor!r}")
        for name in [journal, *split_list(row.get("aliases", ""))]:
            key = normalize_journal(name)
            if not key:
                continue
            owner = by_name.get(key)
            if owner and owner != journal:
                errors.append(f"normalized journal name {key!r} maps to both {owner!r} and {journal!r}")
            by_name[key] = journal
        for issn in [normalize_issn(value) for value in split_list(row.get("issn", ""))]:
            if not issn:
                continue
            owner = by_issn.get(issn)
            if owner and owner != journal:
                errors.append(f"ISSN {issn!r} maps to both {owner!r} and {journal!r}")
            by_issn[issn] = journal

    status = "error" if errors else "warning" if warnings else "ok"
    return {
        "status": status,
        "row_count": len(rows),
        "errors": errors,
        "warnings": warnings,
    }


def build_indexes(metrics: list[Metric]) -> tuple[dict[str, Metric], dict[str, Metric]]:
    by_name: dict[str, Metric] = {}
    by_issn: dict[str, Metric] = {}
    for metric in metrics:
        for name in [metric.journal, *metric.aliases]:
            key = normalize_journal(name)
            if key:
                by_name[key] = metric
        for issn in metric.issns:
            if issn:
                by_issn[issn] = metric
    return by_name, by_issn


def row_journal_names(row: dict[str, str]) -> list[str]:
    fields = [
        "crossref_container",
        "journal",
        "container",
        "source",
        "source_query_name",
    ]
    names = []
    for field in fields:
        value = (row.get(field) or "").strip()
        if value and value not in names:
            names.append(value)
    return names


def row_issns(row: dict[str, str]) -> list[str]:
    values = []
    for field in ("crossref_issn", "issn", "journal_issn"):
        values.extend(split_list(row.get(field, "")))
    return [normalize_issn(v) for v in values if normalize_issn(v)]


def match_metric(
    row: dict[str, str],
    metrics: list[Metric],
    indexes: tuple[dict[str, Metric], dict[str, Metric]] | None = None,
) -> Metric | None:
    by_name, by_issn = indexes or build_indexes(metrics)

    for issn in row_issns(row):
        if issn in by_issn:
            return by_issn[issn]

    for name in row_journal_names(row):
        key = normalize_journal(name)
        if key in by_name:
            return by_name[key]

    # Do not use substring matching here. It is too easy to turn
    # "Chemical Engineering Journal Advances" into "Chemical Engineering Journal".
    # All acceptable variants must be explicit aliases or ISSN matches.
    return None


def annotate_row(row: dict[str, str], metric: Metric | None,
                 min_if: float | None = None) -> dict[str, str]:
    row = dict(row)
    if metric is None:
        row.update({
            "journal_metric": "Not verified",
            "journal_metric_year": "",
            "journal_metric_source": "Not available",
            "journal_metric_url": "",
            "journal_metric_confidence": "",
            "journal_metric_verified": "false",
            "metric_filter_status": "unverified" if min_if is not None else "",
            "metric_filter_reason": "No matching verified metric record",
        })
        return row

    value = metric.impact_float
    status = ""
    reason = ""
    if min_if is not None:
        if value is None:
            status = "unverified"
            reason = "Metric value is not numeric"
        elif value > min_if:
            status = "pass"
            reason = f"Impact factor {value:g} > {min_if:g}"
        else:
            status = "fail"
            reason = f"Impact factor {value:g} <= {min_if:g}"

    row.update({
        "journal_metric": metric.impact_factor,
        "journal_metric_year": metric.metric_year,
        "journal_metric_source": metric.metric_source,
        "journal_metric_url": metric.source_url,
        "journal_metric_confidence": metric.confidence,
        "journal_metric_verified": "true",
        "metric_filter_status": status,
        "metric_filter_reason": reason,
    })
    return row


def filter_csv(input_path: Path, output_path: Path,
               metrics_path: Path = DEFAULT_METRICS,
               min_if: float | None = None,
               pass_output: Path | None = None,
               backup_output: Path | None = None) -> dict[str, int]:
    validation = validate_metrics(metrics_path, require_numeric_if=min_if is not None)
    if validation["status"] == "error":
        errors = validation.get("errors", [])
        detail = "; ".join(str(error) for error in errors) if isinstance(errors, list) else "validation failed"
        raise SystemExit(f"Journal metrics validation failed: {detail}")
    metrics = load_metrics(metrics_path)
    metric_indexes = build_indexes(metrics)

    with input_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise SystemExit("Input CSV has no header")
        fieldnames = list(reader.fieldnames)
        rows = list(reader)

    metric_cols = [
        "journal_metric",
        "journal_metric_year",
        "journal_metric_source",
        "journal_metric_url",
        "journal_metric_confidence",
        "journal_metric_verified",
        "metric_filter_status",
        "metric_filter_reason",
    ]
    for col in metric_cols:
        if col not in fieldnames:
            fieldnames.append(col)

    annotated = []
    counts = {"rows": len(rows), "pass": 0, "fail": 0, "unverified": 0, "annotated": 0}
    for row in rows:
        metric = match_metric(row, metrics, indexes=metric_indexes)
        out = annotate_row(row, metric, min_if=min_if)
        annotated.append(out)
        if metric is not None:
            counts["annotated"] += 1
        status = out.get("metric_filter_status")
        if status in ("pass", "fail", "unverified"):
            counts[status] += 1

    write_csv_atomic(annotated, output_path, fieldnames=fieldnames)

    if pass_output is not None:
        pass_rows = [row for row in annotated if row.get("metric_filter_status") == "pass"]
        write_csv_atomic(pass_rows, pass_output, fieldnames=fieldnames)

    if backup_output is not None:
        backup_rows = [row for row in annotated if row.get("metric_filter_status") != "pass"]
        write_csv_atomic(backup_rows, backup_output, fieldnames=fieldnames)

    print(
        "Journal metrics: "
        f"{counts['annotated']}/{counts['rows']} annotated, "
        f"pass={counts['pass']}, fail={counts['fail']}, unverified={counts['unverified']} "
        f"-> {output_path}",
        file=sys.stderr,
    )
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="Annotate/filter rows by verified journal metrics.")
    parser.add_argument("--input", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--metrics", type=Path, default=DEFAULT_METRICS)
    parser.add_argument("--min-if", type=float, default=None,
                        help="Minimum impact factor threshold; pass requires IF > threshold")
    parser.add_argument("--pass-output", type=Path, default=None)
    parser.add_argument("--backup-output", type=Path, default=None)
    parser.add_argument("--validate", action="store_true",
                        help="Validate the metrics CSV and exit")
    parser.add_argument("--require-numeric-if", action="store_true",
                        help="Require every non-header metric row to have a numeric impact_factor")
    parser.add_argument("--json", action="store_true",
                        help="Print validation result as JSON when --validate is used")
    args = parser.parse_args()

    if args.validate:
        result = validate_metrics(args.metrics, require_numeric_if=args.require_numeric_if)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"Journal metrics validation: {result['status']} ({result['row_count']} rows)")
            errors = result.get("errors", [])
            warnings = result.get("warnings", [])
            for error in errors if isinstance(errors, list) else []:
                print(f"ERROR: {error}", file=sys.stderr)
            for warning in warnings if isinstance(warnings, list) else []:
                print(f"WARNING: {warning}", file=sys.stderr)
        if result["status"] == "error":
            raise SystemExit(1)
        return

    if not args.input or not args.output:
        parser.error("--input and --output are required unless --validate is used")

    filter_csv(
        args.input,
        args.output,
        metrics_path=args.metrics,
        min_if=args.min_if,
        pass_output=args.pass_output,
        backup_output=args.backup_output,
    )


if __name__ == "__main__":
    main()
