"""Deterministic RIS serialization for canonical Litminer papers."""

from __future__ import annotations

from pathlib import Path

from litminer.engine.common import write_bytes_atomic
from litminer.exporters.common import split_authors


TYPE_MAP = {
    "article": "JOUR",
    "conference": "CPAPER",
    "preprint": "UNPB",
    "book": "BOOK",
    "book_chapter": "CHAP",
    "generic": "GEN",
}


def _line(tag: str, value: str) -> str:
    cleaned = " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split())
    return f"{tag}  - {cleaned}"


def serialize(rows: list[dict[str, str]]) -> str:
    records: list[str] = []
    for row in rows:
        lines = [_line("TY", TYPE_MAP.get(row.get("entry_type", "generic"), "GEN"))]
        if row.get("title"):
            lines.append(_line("TI", row["title"]))
        for author in split_authors(row.get("authors", "")):
            lines.append(_line("AU", author))
        if row.get("publication_year"):
            lines.append(_line("PY", row["publication_year"]))
        if row.get("journal"):
            lines.append(_line("JO", row["journal"]))
        if row.get("volume"):
            lines.append(_line("VL", row["volume"]))
        if row.get("issue"):
            lines.append(_line("IS", row["issue"]))
        if row.get("pages"):
            pages = row["pages"].split("-", 1)
            lines.append(_line("SP", pages[0]))
            if len(pages) == 2:
                lines.append(_line("EP", pages[1]))
        if row.get("publisher"):
            lines.append(_line("PB", row["publisher"]))
        if row.get("doi"):
            lines.append(_line("DO", row["doi"]))
        if row.get("url"):
            lines.append(_line("UR", row["url"]))
        if row.get("abstract"):
            lines.append(_line("AB", row["abstract"]))
        lines.append("ER  -")
        records.append("\r\n".join(lines))
    return "\r\n\r\n".join(records) + ("\r\n" if records else "")


def write(rows: list[dict[str, str]], path: Path) -> Path:
    write_bytes_atomic(path, serialize(rows).encode("utf-8"))
    return path
