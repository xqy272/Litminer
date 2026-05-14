#!/usr/bin/env python3
"""Generic semantic triage for literature candidate CSV files.

The script is intentionally domain-neutral. An Agent or caller supplies the
task concepts at runtime, usually after interpreting the user's request. The
script annotates, ranks, and tags rows; it does not delete rows or claim final
inclusion.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from litminer.engine.common import normalize_doi, read_csv_rows, write_csv_atomic


DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$", re.I)
HTML_TAG_RE = re.compile(r"<[^>]+>")
SUBSCRIPT_DIGITS = str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789")
NEGATION_BEFORE_RE = re.compile(
    r"(?:\bwithout\b|\bno\b|\bnot\b|\babsence of\b|\black of\b|"
    r"\blacking\b|\bdoes not\b|\bdid not\b|\bfree of\b)"
    r"(?:\W+\w+){0,6}\W*$",
    re.I,
)

OUTPUT_COLUMNS = [
    "triage_priority",
    "triage_score",
    "candidate_status",
    "semantic_tags",
    "matched_required",
    "matched_optional",
    "matched_negative",
    "missing_required",
    "triage_reasons",
    "llm_review_needed",
    "hard_filter_flags",
    "metadata_status",
    "metadata_reasons",
]

PRIORITY_ORDER = {
    "high": 0,
    "medium": 1,
    "needs_review": 2,
    "low": 3,
}
MAX_PATTERN_LENGTH = 300
_PATTERN_CACHE: dict[tuple[str, bool], re.Pattern[str]] = {}


@dataclass
class Concept:
    name: str
    patterns: list[str]
    scope: str = "title_abstract"
    weight: float = 1.0


@dataclass
class TriageProfile:
    required: list[Concept]
    optional: list[Concept]
    negative: list[Concept]
    year_from: int | None = None
    year_to: int | None = None
    require_doi: bool = False
    exclude_article_types: set[str] | None = None
    allow_regex: bool = True


def normalize_text(value: str) -> str:
    value = HTML_TAG_RE.sub(" ", value or "")
    value = value.translate(SUBSCRIPT_DIGITS)
    value = value.replace("\u03bc", "u")
    return re.sub(r"\s+", " ", value).strip()


def slug_name(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().lower())
    return value.strip("_")[:50] or "concept"


def split_items(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"[|;]", value or "") if item.strip()]


def parse_concept_spec(spec: str, default_scope: str = "title_abstract",
                       default_weight: float = 1.0) -> Concept:
    """Parse `name=pattern1|pattern2` or a plain pattern string."""
    raw = (spec or "").strip()
    if not raw:
        raise ValueError("Empty concept specification")

    separators = [pos for pos in (raw.find("="), raw.find(":")) if pos > 0]
    if separators:
        pos = min(separators)
        name = raw[:pos].strip()
        pattern_text = raw[pos + 1:].strip()
    else:
        pattern_text = raw
        name = slug_name(split_items(pattern_text)[0] if split_items(pattern_text) else raw)

    patterns = split_items(pattern_text)
    if not patterns:
        patterns = [pattern_text]
    return Concept(name=slug_name(name), patterns=patterns,
                   scope=default_scope, weight=default_weight)


def concept_from_obj(obj: Any, default_weight: float) -> Concept:
    if isinstance(obj, str):
        return parse_concept_spec(obj, default_weight=default_weight)
    if not isinstance(obj, dict):
        raise ValueError(f"Concept must be string or object, got {type(obj).__name__}")

    name = slug_name(str(obj.get("name") or obj.get("label") or "concept"))
    raw_patterns = obj.get("patterns", obj.get("terms", obj.get("term", [])))
    if isinstance(raw_patterns, str):
        patterns = split_items(raw_patterns) or [raw_patterns]
    elif isinstance(raw_patterns, list):
        patterns = [str(item).strip() for item in raw_patterns if str(item).strip()]
    else:
        patterns = []
    if not patterns:
        raise ValueError(f"Concept '{name}' has no patterns/terms")

    return Concept(
        name=name,
        patterns=patterns,
        scope=str(obj.get("scope") or "title_abstract"),
        weight=float(obj.get("weight", default_weight)),
    )


def load_profile(path: Path | None = None,
                 required_specs: list[str] | None = None,
                 optional_specs: list[str] | None = None,
                 negative_specs: list[str] | None = None,
                  year_from: int | None = None,
                  year_to: int | None = None,
                  require_doi: bool = False,
                  exclude_article_types: list[str] | None = None,
                  allow_regex: bool = True) -> TriageProfile:
    data: dict[str, Any] = {}
    if path is not None:
        text = path.read_text(encoding="utf-8-sig")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SystemExit(
                f"Triage profile must be JSON. Failed to parse {path}: {exc}"
            ) from exc

    hard_filters = data.get("hard_filters", {}) if isinstance(data.get("hard_filters"), dict) else {}

    required = [
        concept_from_obj(item, 3.0)
        for item in data.get("required", data.get("required_concepts", []))
    ]
    optional = [
        concept_from_obj(item, 1.0)
        for item in data.get("optional", data.get("optional_concepts", []))
    ]
    negative = [
        concept_from_obj(item, -2.0)
        for item in data.get("negative", data.get("negative_concepts", []))
    ]

    required.extend(parse_concept_spec(item, default_weight=3.0) for item in (required_specs or []))
    optional.extend(parse_concept_spec(item, default_weight=1.0) for item in (optional_specs or []))
    negative.extend(parse_concept_spec(item, default_weight=-2.0) for item in (negative_specs or []))

    profile_year_from = year_from if year_from is not None else hard_filters.get("year_from")
    profile_year_to = year_to if year_to is not None else hard_filters.get("year_to")
    profile_require_doi = bool(require_doi or hard_filters.get("require_doi", False))

    article_types = set()
    for value in data.get("exclude_article_types", hard_filters.get("exclude_article_types", [])):
        article_types.add(str(value).strip().lower().replace("_", "-"))
    for value in exclude_article_types or []:
        for item in split_items(value.replace(",", ";")):
            article_types.add(item.lower().replace("_", "-"))

    def optional_int(value: Any) -> int | None:
        if value in (None, ""):
            return None
        return int(str(value))

    return TriageProfile(
        required=required,
        optional=optional,
        negative=negative,
        year_from=optional_int(profile_year_from),
        year_to=optional_int(profile_year_to),
        require_doi=profile_require_doi,
        exclude_article_types=article_types,
        allow_regex=allow_regex,
    )


def scoped_text(row: dict[str, str], scope: str) -> str:
    scope = (scope or "title_abstract").lower()
    if scope == "title":
        fields = ["title", "crossref_title"]
    elif scope == "abstract":
        fields = ["abstract", "summary"]
    elif scope == "article_type":
        fields = ["article_type", "crossref_type"]
    elif scope == "metadata":
        fields = [
            "title", "crossref_title", "abstract", "summary", "journal",
            "crossref_container", "article_type", "crossref_type", "keywords",
        ]
    else:
        fields = ["title", "crossref_title", "abstract", "summary", "keywords"]
    return normalize_text(" ".join(row.get(field, "") or "" for field in fields))


def compile_pattern(pattern: str, allow_regex: bool = True) -> re.Pattern[str]:
    pattern = normalize_text(pattern)
    if len(pattern) > MAX_PATTERN_LENGTH:
        raise ValueError(f"Pattern is too long ({len(pattern)} > {MAX_PATTERN_LENGTH})")
    cache_key = (pattern, allow_regex)
    if cache_key in _PATTERN_CACHE:
        return _PATTERN_CACHE[cache_key]
    if pattern.startswith("re:"):
        if not allow_regex:
            raise ValueError("Regex concepts are disabled for this triage run")
        compiled = re.compile(pattern[3:], re.I)
    else:
        escaped = re.escape(pattern)
        escaped = re.sub(r"\\\s+", r"\\s+", escaped)
        if re.match(r"^[A-Za-z0-9_\s-]+$", pattern):
            escaped = rf"\b{escaped}\b"
        compiled = re.compile(escaped, re.I)
    _PATTERN_CACHE[cache_key] = compiled
    return compiled


def concept_matches(row: dict[str, str], concept: Concept, allow_regex: bool = True) -> bool:
    text = scoped_text(row, concept.scope)
    if not text:
        return False
    for pattern in concept.patterns:
        try:
            compiled = compile_pattern(pattern, allow_regex=allow_regex)
            for match in compiled.finditer(text):
                prefix = text[max(0, match.start() - 80):match.start()]
                if NEGATION_BEFORE_RE.search(prefix):
                    continue
                return True
        except re.error as exc:
            raise ValueError(f"Invalid pattern for concept '{concept.name}': {pattern}: {exc}") from exc
    return False


def row_year(row: dict[str, str]) -> int | None:
    for field in ("crossref_year", "publication_year", "year"):
        value = (row.get(field) or "").strip()
        match = re.search(r"\b(19|20)\d{2}\b", value)
        if match:
            return int(match.group(0))
    return None


def article_type(row: dict[str, str]) -> str:
    value = row.get("crossref_type") or row.get("article_type") or ""
    return value.strip().lower().replace("_", "-").replace(" ", "-")


def metadata_flags(row: dict[str, str], profile: TriageProfile) -> tuple[list[str], str, list[str]]:
    flags: list[str] = []
    reasons: list[str] = []

    title = (row.get("crossref_title") or row.get("title") or "").strip()
    if not title:
        flags.append("missing_title")
        reasons.append("title is missing")

    doi = normalize_doi(row.get("crossref_doi") or row.get("doi") or "")
    if not doi:
        flags.append("missing_doi")
        reasons.append("DOI is missing")
    elif not DOI_RE.match(doi):
        flags.append("invalid_doi_format")
        reasons.append("DOI format does not match standard DOI pattern")

    year = row_year(row)
    if year is None:
        flags.append("missing_year")
        reasons.append("publication year is missing")
    else:
        if profile.year_from is not None and year < profile.year_from:
            flags.append(f"year_before_{profile.year_from}")
            reasons.append(f"publication year {year} is before {profile.year_from}")
        if profile.year_to is not None and year > profile.year_to:
            flags.append(f"year_after_{profile.year_to}")
            reasons.append(f"publication year {year} is after {profile.year_to}")

    art_type = article_type(row)
    if profile.exclude_article_types and art_type in profile.exclude_article_types:
        flags.append(f"article_type_{art_type}")
        reasons.append(f"article type '{art_type}' is in caller-supplied excluded types")

    mismatches = (row.get("crossref_mismatches") or "").strip()
    if mismatches:
        flags.append("crossref_mismatch")
        reasons.append(mismatches)

    crossref_status = (row.get("crossref_status") or "").strip().lower()
    if crossref_status in {"lookup_failed", "title_lookup_failed"}:
        flags.append(f"crossref_{crossref_status}")
        reasons.append(f"Crossref status is {crossref_status}")
    elif crossref_status == "mismatch":
        flags.append("crossref_mismatch")
        reasons.append("Crossref metadata mismatch")

    metric_status = (row.get("metric_filter_status") or "").strip().lower()
    if metric_status in {"fail", "unverified"}:
        flags.append(f"metric_{metric_status}")
        reasons.append(row.get("metric_filter_reason") or f"journal metric status is {metric_status}")

    blocking = [
        flag for flag in flags
        if flag.startswith("year_before_")
        or flag.startswith("year_after_")
        or flag.startswith("article_type_")
        or (profile.require_doi and flag in {"missing_doi", "invalid_doi_format"})
        or flag == "crossref_mismatch"
        or flag in {"crossref_lookup_failed", "crossref_title_lookup_failed"}
        or flag == "metric_fail"
    ]
    if blocking:
        status = "blocked"
    elif flags:
        status = "check"
    else:
        status = "ok"
    return flags, status, reasons


def priority_for(profile: TriageProfile, matched_required: list[str],
                 matched_optional: list[str], matched_negative: list[str],
                 missing_required: list[str], row: dict[str, str]) -> str:
    has_concepts = bool(profile.required or profile.optional or profile.negative)
    has_text = bool(scoped_text(row, "title_abstract"))
    if not has_concepts or not has_text:
        return "needs_review"

    if profile.required and not missing_required:
        return "medium" if matched_negative else "high"
    if matched_required or matched_optional:
        return "low" if matched_negative and not matched_required else "medium"
    if matched_negative:
        return "low"
    return "needs_review"


def candidate_status(priority: str, metadata_status: str) -> str:
    if metadata_status == "blocked":
        return "metadata_blocked"
    if priority in {"high", "medium"} and metadata_status == "ok":
        return "ready_for_verification"
    if priority in {"high", "medium"}:
        return "metadata_check"
    if priority == "needs_review":
        return "llm_review"
    return "low_priority"


def triage_row(row: dict[str, str], profile: TriageProfile) -> dict[str, str]:
    matched_required = [c.name for c in profile.required if concept_matches(row, c, profile.allow_regex)]
    matched_optional = [c.name for c in profile.optional if concept_matches(row, c, profile.allow_regex)]
    matched_negative = [c.name for c in profile.negative if concept_matches(row, c, profile.allow_regex)]
    missing_required = [c.name for c in profile.required if c.name not in matched_required]

    score = 0.0
    score += sum(c.weight for c in profile.required if c.name in matched_required)
    score += sum(c.weight for c in profile.optional if c.name in matched_optional)
    score += sum(c.weight for c in profile.negative if c.name in matched_negative)
    score -= 1.0 * len(missing_required)

    doi = normalize_doi(row.get("crossref_doi") or row.get("doi") or "")
    if doi:
        score += 0.5
    if row.get("abstract"):
        score += 0.5

    hard_flags, meta_status, meta_reasons = metadata_flags(row, profile)
    priority = priority_for(profile, matched_required, matched_optional,
                            matched_negative, missing_required, row)

    reasons = []
    if matched_required:
        reasons.append("matched required: " + ", ".join(matched_required))
    if missing_required:
        reasons.append("missing required: " + ", ".join(missing_required))
    if matched_optional:
        reasons.append("matched optional: " + ", ".join(matched_optional))
    if matched_negative:
        reasons.append("matched caller negative tags: " + ", ".join(matched_negative))
    if not (profile.required or profile.optional or profile.negative):
        reasons.append("no semantic profile supplied; left for LLM review")
    if hard_flags:
        reasons.append("metadata flags: " + ", ".join(hard_flags))

    tags: list[str] = []
    tags.extend(f"required:{name}" for name in matched_required)
    tags.extend(f"optional:{name}" for name in matched_optional)
    tags.extend(f"negative:{name}" for name in matched_negative)
    tags.extend(f"metadata:{flag}" for flag in hard_flags)

    out = dict(row)
    out.update({
        "triage_priority": priority,
        "triage_score": f"{score:.1f}",
        "candidate_status": candidate_status(priority, meta_status),
        "semantic_tags": "; ".join(tags),
        "matched_required": "; ".join(matched_required),
        "matched_optional": "; ".join(matched_optional),
        "matched_negative": "; ".join(matched_negative),
        "missing_required": "; ".join(missing_required),
        "triage_reasons": "; ".join(reasons) if reasons else "no explicit match",
        "llm_review_needed": "true" if priority == "needs_review" or matched_negative or hard_flags else "false",
        "hard_filter_flags": "; ".join(hard_flags),
        "metadata_status": meta_status,
        "metadata_reasons": "; ".join(meta_reasons),
    })
    return out


def priority_sort_key(row: dict[str, str]) -> tuple[int, float, str]:
    priority = row.get("triage_priority", "needs_review")
    try:
        score = float(row.get("triage_score", "0"))
    except ValueError:
        score = 0.0
    title = row.get("crossref_title") or row.get("title") or ""
    return (PRIORITY_ORDER.get(priority, 99), -score, title.lower())


def triage_csv(input_path: Path, output_path: Path,
               profile_path: Path | None = None,
               required_concepts: list[str] | None = None,
               optional_concepts: list[str] | None = None,
               negative_concepts: list[str] | None = None,
               year_from: int | None = None,
               year_to: int | None = None,
                require_doi: bool = False,
                exclude_article_types: list[str] | None = None,
                allow_regex: bool = True,
                sort_rows: bool = True) -> dict[str, int]:
    profile = load_profile(
        profile_path,
        required_specs=required_concepts,
        optional_specs=optional_concepts,
        negative_specs=negative_concepts,
        year_from=year_from,
        year_to=year_to,
        require_doi=require_doi,
        exclude_article_types=exclude_article_types,
        allow_regex=allow_regex,
    )

    fieldnames, rows = read_csv_rows(input_path)
    if not fieldnames:
        raise SystemExit("Input CSV has no header")

    for col in OUTPUT_COLUMNS:
        if col not in fieldnames:
            fieldnames.append(col)

    output_rows = [triage_row(row, profile) for row in rows]
    if sort_rows:
        output_rows.sort(key=priority_sort_key)

    counts = {
        "rows": len(output_rows),
        "high": 0,
        "medium": 0,
        "needs_review": 0,
        "low": 0,
        "metadata_blocked": 0,
    }
    for row in output_rows:
        priority = row.get("triage_priority", "needs_review")
        if priority in counts:
            counts[priority] += 1
        if row.get("metadata_status") == "blocked":
            counts["metadata_blocked"] += 1

    write_csv_atomic(output_rows, output_path, fieldnames=fieldnames)

    print(
        "Semantic triage: "
        f"{counts['rows']} rows -> high={counts['high']}, "
        f"medium={counts['medium']}, needs_review={counts['needs_review']}, "
        f"low={counts['low']} -> {output_path}",
        file=sys.stderr,
    )
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Annotate and rank literature candidates with caller-supplied semantic concepts."
    )
    parser.add_argument("--input", type=Path, required=True, help="Candidate CSV")
    parser.add_argument("--output", type=Path, required=True, help="Triaged output CSV")
    parser.add_argument("--profile", type=Path, default=None,
                        help="JSON triage profile with required/optional/negative concepts")
    parser.add_argument("--required-concept", action="append", default=[],
                        help="Concept required by the user, e.g. name=term1|term2")
    parser.add_argument("--optional-concept", action="append", default=[],
                        help="Useful but non-mandatory concept")
    parser.add_argument("--negative-concept", action="append", default=[],
                        help="Caller-supplied negative tag. Rows are tagged, not deleted.")
    parser.add_argument("--year-from", type=int, default=None)
    parser.add_argument("--year-to", type=int, default=None)
    parser.add_argument("--require-doi", action="store_true",
                        help="Mark missing/invalid DOI as metadata-blocking")
    parser.add_argument("--exclude-article-type", action="append", default=[],
                        help="Metadata article type to mark as blocked, e.g. review")
    parser.add_argument("--disable-regex-concepts", action="store_true",
                        help="Treat re: concepts as invalid instead of compiling caller-supplied regex")
    parser.add_argument("--no-sort", action="store_true")
    args = parser.parse_args()

    triage_csv(
        args.input,
        args.output,
        profile_path=args.profile,
        required_concepts=args.required_concept,
        optional_concepts=args.optional_concept,
        negative_concepts=args.negative_concept,
        year_from=args.year_from,
        year_to=args.year_to,
        require_doi=args.require_doi,
        exclude_article_types=args.exclude_article_type,
        allow_regex=not args.disable_regex_concepts,
        sort_rows=not args.no_sort,
    )


if __name__ == "__main__":
    main()
