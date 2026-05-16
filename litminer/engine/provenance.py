#!/usr/bin/env python3
"""Build field-level provenance summaries for Litminer candidate rows."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from litminer.engine.common import normalize_doi, read_csv_rows, write_text_atomic


PROVENANCE_NAME = "field_provenance.json"


def _row_key(row: dict[str, str], index: int) -> str:
    doi = normalize_doi(row.get("crossref_doi") or row.get("doi") or "")
    if doi:
        return f"doi:{doi}"
    title = " ".join((row.get("crossref_title") or row.get("title") or "").strip().lower().split())
    year = (row.get("crossref_year") or row.get("publication_year") or row.get("year") or "").strip()
    return f"row:{index}:title:{title}|year:{year}"


def _field_source(row: dict[str, str], field: str) -> dict[str, str]:
    if field == "title":
        if row.get("crossref_title"):
            return {"value": row["crossref_title"], "source": "crossref", "trust": "bibliographic_verified"}
        return {"value": row.get("title", ""), "source": row.get("discovery_source") or "candidate", "trust": "candidate"}
    if field == "doi":
        if row.get("crossref_doi"):
            return {"value": normalize_doi(row["crossref_doi"]), "source": "crossref", "trust": "bibliographic_verified"}
        return {"value": normalize_doi(row.get("doi", "")), "source": row.get("discovery_source") or "candidate", "trust": "candidate"}
    if field == "journal":
        if row.get("crossref_container"):
            return {"value": row["crossref_container"], "source": "crossref", "trust": "bibliographic_verified"}
        return {"value": row.get("journal", ""), "source": row.get("discovery_source") or "candidate", "trust": "candidate"}
    if field == "publication_year":
        if row.get("crossref_year"):
            return {"value": row["crossref_year"], "source": "crossref", "trust": "bibliographic_verified"}
        return {"value": row.get("publication_year") or row.get("year", ""), "source": row.get("discovery_source") or "candidate", "trust": "candidate"}
    if field == "abstract":
        return {"value": row.get("abstract", ""), "source": row.get("discovery_source") or "candidate", "trust": "candidate"}
    if field == "oa_link":
        value = row.get("best_oa_pdf_url") or row.get("best_oa_landing_url") or row.get("best_oa_url", "")
        return {"value": value, "source": "unpaywall" if value else "", "trust": "access_hint" if value else ""}
    if field == "journal_metric":
        value = row.get("journal_metric", "")
        return {
            "value": value,
            "source": row.get("journal_metric_source", ""),
            "trust": "verified_local_metric" if row.get("journal_metric_verified") == "true" else "not_verified",
        }
    if field == "publisher_access":
        return {
            "value": row.get("access_status", ""),
            "source": row.get("publisher_probe_method", ""),
            "trust": row.get("publisher_probe_confidence", ""),
        }
    return {"value": row.get(field, ""), "source": "", "trust": ""}


def build_provenance(rows: list[dict[str, str]]) -> dict[str, Any]:
    fields = [
        "title",
        "doi",
        "journal",
        "publication_year",
        "abstract",
        "oa_link",
        "journal_metric",
        "publisher_access",
    ]
    records = []
    for index, row in enumerate(rows):
        records.append({
            "record_key": _row_key(row, index),
            "title": row.get("crossref_title") or row.get("title", ""),
            "fields": {field: _field_source(row, field) for field in fields},
        })
    return {
        "schema_version": 1,
        "row_count": len(rows),
        "records": records,
        "notes": [
            "Provenance explains where fields came from; it does not prove article-level scientific claims.",
            "Unpaywall and publisher probe values are access hints unless confirmed by page/full-text inspection.",
        ],
    }


def build_from_csv(input_csv: Path) -> dict[str, Any]:
    _fieldnames, rows = read_csv_rows(input_csv)
    return build_provenance(rows)


def write_from_csv(input_csv: Path, output_path: Path) -> Path:
    provenance = build_from_csv(input_csv)
    write_text_atomic(output_path, json.dumps(provenance, indent=2, ensure_ascii=False) + "\n")
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate field-level provenance JSON for a Litminer CSV.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    path = write_from_csv(args.input, args.output)
    print(f"Field provenance: {path}")


if __name__ == "__main__":
    main()
