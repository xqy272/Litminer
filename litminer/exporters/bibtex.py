"""Deterministic Unicode-first BibTeX serialization."""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from pathlib import Path

from litminer.engine.common import write_bytes_atomic
from litminer.exporters.common import split_authors


TYPE_MAP = {
    "article": "article",
    "conference": "inproceedings",
    "preprint": "misc",
    "book": "book",
    "book_chapter": "incollection",
    "generic": "misc",
}


def _ascii(value: str) -> str:
    return unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")


def _surname(author: str) -> str:
    text = author.strip()
    if "," in text:
        return text.split(",", 1)[0].strip()
    parts = text.split()
    return parts[-1] if parts else "Anon"


def _token(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "", _ascii(value))
    return cleaned or fallback


def _base_key(row: dict[str, str]) -> str:
    authors = split_authors(row.get("authors", ""))
    author = _token(_surname(authors[0]) if authors else "Anon", "Anon")
    year = _token(row.get("publication_year", ""), "ND")
    title_words = re.findall(r"[A-Za-z0-9]+", _ascii(row.get("title", "")))
    title = _token(next((word for word in title_words if len(word) > 2), "Work"), "Work")
    return f"{author}{year}{title}"


def citation_keys(rows: list[dict[str, str]]) -> tuple[dict[int, str], int]:
    ordered = sorted(rows, key=lambda row: (
        _base_key(row).lower(),
        row.get("paper_id", ""),
        row.get("doi", ""),
        row.get("title", ""),
    ))
    groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in ordered:
        groups[_base_key(row)].append(row)
    keys: dict[int, str] = {}
    conflicts = 0
    for base, group in sorted(groups.items()):
        if len(group) == 1:
            keys[id(group[0])] = base
            continue
        conflicts += len(group)
        for index, row in enumerate(group):
            suffix_index = index
            suffix = ""
            while True:
                suffix = chr(ord("a") + (suffix_index % 26)) + (str(suffix_index // 26) if suffix_index >= 26 else "")
                if suffix:
                    break
            keys[id(row)] = base + suffix
    return keys, conflicts


def escape(value: str, *, ascii_latex: bool = False) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ")
    text = re.sub(r"\s+", " ", text).strip()
    if ascii_latex:
        text = _ascii(text)
    replacements = {
        "\\": r"\textbackslash{}",
        "{": r"\{",
        "}": r"\}",
        "%": r"\%",
        "&": r"\&",
        "_": r"\_",
        "#": r"\#",
        "$": r"\$",
    }
    return "".join(replacements.get(char, char) for char in text)


def serialize(rows: list[dict[str, str]], *, ascii_latex: bool = False) -> tuple[str, int]:
    keys, conflicts = citation_keys(rows)
    blocks: list[str] = []
    for row in sorted(rows, key=lambda item: keys[id(item)].lower()):
        identity = id(row)
        entry_type = TYPE_MAP.get(row.get("entry_type", "generic"), "misc")
        fields: list[tuple[str, str]] = []
        authors = split_authors(row.get("authors", ""))
        mapping = [
            ("title", row.get("title", "")),
            ("author", " and ".join(authors)),
            ("year", row.get("publication_year", "")),
            ("journal", row.get("journal", "")),
            ("volume", row.get("volume", "")),
            ("number", row.get("issue", "")),
            ("pages", row.get("pages", "")),
            ("publisher", row.get("publisher", "")),
            ("doi", row.get("doi", "")),
            ("url", row.get("url", "")),
            ("abstract", row.get("abstract", "")),
        ]
        for name, value in mapping:
            if value:
                fields.append((name, escape(value, ascii_latex=ascii_latex)))
        lines = [f"@{entry_type}{{{keys[identity]},"]
        for index, (name, value) in enumerate(fields):
            comma = "," if index < len(fields) - 1 else ""
            lines.append(f"  {name} = {{{value}}}{comma}")
        lines.append("}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + ("\n" if blocks else ""), conflicts


def write(rows: list[dict[str, str]], path: Path, *, ascii_latex: bool = False) -> tuple[Path, int]:
    content, conflicts = serialize(rows, ascii_latex=ascii_latex)
    write_bytes_atomic(path, content.encode("utf-8"))
    return path, conflicts
