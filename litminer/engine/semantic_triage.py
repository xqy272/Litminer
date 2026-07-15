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
import math
import re
import sys
from collections import OrderedDict
from dataclasses import dataclass, field as dataclass_field
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
    "workflow_status",
    "bibliographic_status",
    "bibliographic_review_needed",
    "scientific_review_needed",
    "semantic_tags",
    "matched_required",
    "matched_required_evidence",
    "matched_optional",
    "matched_optional_evidence",
    "matched_negative",
    "matched_negative_evidence",
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
MAX_PATTERN_CACHE_SIZE = 512
_PATTERN_CACHE: OrderedDict[tuple[str, bool], re.Pattern[str]] = OrderedDict()
CROSSREF_TRUSTED_STATUSES = {"verified", "title_recovered"}
CROSSREF_PROVIDER_PENDING_STATUSES = {
    "rate_limited",
    "network_error",
    "auth_error",
    "response_parse_error",
    "provider_error",
}
CROSSREF_LOOKUP_FAILED_STATUSES = {
    "lookup_failed",
    "title_lookup_failed",
    "http_404",
}


@dataclass
class Concept:
    name: str
    patterns: list[str]
    scope: str = "title_abstract"
    weight: float = 1.0
    op: str = "any"
    children: list["Concept"] = dataclass_field(default_factory=list)
    window: int = 8


@dataclass
class TriageProfile:
    required: list[Concept]
    optional: list[Concept]
    negative: list[Concept]
    year_from: int | None = None
    year_to: int | None = None
    require_doi: bool = False
    exclude_article_types: set[str] | None = None
    allow_regex: bool = False


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


def _maybe_json_spec(value: str) -> Any:
    text = (value or "").strip()
    if not text or text[0] not in "{[":
        return value
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return value


def _concept_items(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _concept_name(obj: dict[str, Any], default: str = "concept") -> str:
    return slug_name(str(obj.get("name") or obj.get("label") or default))


def concept_from_obj(obj: Any, default_weight: float) -> Concept:
    obj = _maybe_json_spec(obj) if isinstance(obj, str) else obj
    if isinstance(obj, str):
        return parse_concept_spec(obj, default_weight=default_weight)
    if not isinstance(obj, dict):
        raise ValueError(f"Concept must be string or object, got {type(obj).__name__}")

    name = _concept_name(obj)
    weight = float(obj.get("weight", default_weight))
    scope = str(obj.get("scope") or "title_abstract")

    for op in ("all_of", "any_of"):
        if op in obj:
            children = [
                concept_from_obj(item, default_weight=weight)
                for item in _concept_items(obj.get(op))
            ]
            if not children:
                raise ValueError(f"Concept '{name}' has empty {op}")
            return Concept(
                name=name,
                patterns=[],
                scope=scope,
                weight=weight,
                op="all" if op == "all_of" else "any",
                children=children,
                window=int(obj.get("window", 8) or 8),
            )

    if "not" in obj:
        children = [
            concept_from_obj(item, default_weight=weight)
            for item in _concept_items(obj.get("not"))
        ]
        if not children:
            raise ValueError(f"Concept '{name}' has empty not")
        return Concept(name=name, patterns=[], scope=scope, weight=weight, op="not", children=children)

    for op in ("near", "not_near"):
        if op in obj:
            terms = obj.get(op)
            if isinstance(terms, str):
                patterns = split_items(terms.replace(",", "|")) or [terms]
            elif isinstance(terms, list):
                patterns = [str(item).strip() for item in terms if str(item).strip()]
            else:
                raise ValueError(f"Concept '{name}' {op} must be a string or list")
            if len(patterns) < 2:
                raise ValueError(f"Concept '{name}' {op} needs at least two terms")
            return Concept(
                name=name,
                patterns=patterns,
                scope=scope,
                weight=weight,
                op=op,
                window=max(1, int(obj.get("window", 8) or 8)),
            )

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
        scope=scope,
        weight=weight,
    )


def load_profile(path: Path | None = None,
                 required_specs: list[str] | None = None,
                 optional_specs: list[str] | None = None,
                 negative_specs: list[str] | None = None,
                  year_from: int | None = None,
                  year_to: int | None = None,
                  require_doi: bool = False,
                  exclude_article_types: list[str] | None = None,
                  allow_regex: bool = False) -> TriageProfile:
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

    required.extend(concept_from_obj(item, default_weight=3.0) for item in (required_specs or []))
    optional.extend(concept_from_obj(item, default_weight=1.0) for item in (optional_specs or []))
    negative.extend(concept_from_obj(item, default_weight=-2.0) for item in (negative_specs or []))

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


def scope_fields(scope: str) -> list[str]:
    scope = (scope or "title_abstract").lower()
    if scope == "title":
        return ["title", "crossref_title"]
    if scope == "abstract":
        return ["abstract", "summary"]
    if scope == "article_type":
        return ["article_type", "crossref_type"]
    if scope == "metadata":
        return [
            "title", "crossref_title", "abstract", "summary", "journal",
            "crossref_container", "article_type", "crossref_type", "keywords",
        ]
    return ["title", "crossref_title", "abstract", "summary", "keywords"]


def scoped_text(row: dict[str, str], scope: str) -> str:
    fields = scope_fields(scope)
    return normalize_text(" ".join(row.get(field, "") or "" for field in fields))


def compile_pattern(pattern: str, allow_regex: bool = False) -> re.Pattern[str]:
    pattern = normalize_text(pattern)
    if len(pattern) > MAX_PATTERN_LENGTH:
        raise ValueError(f"Pattern is too long ({len(pattern)} > {MAX_PATTERN_LENGTH})")
    cache_key = (pattern, allow_regex)
    if cache_key in _PATTERN_CACHE:
        _PATTERN_CACHE.move_to_end(cache_key)
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
    _PATTERN_CACHE.move_to_end(cache_key)
    while len(_PATTERN_CACHE) > MAX_PATTERN_CACHE_SIZE:
        _PATTERN_CACHE.popitem(last=False)
    return compiled


def _basic_pattern_match(text: str, pattern: str, allow_regex: bool = False) -> bool:
    compiled = compile_pattern(pattern, allow_regex=allow_regex)
    for match in compiled.finditer(text):
        prefix = text[max(0, match.start() - 80):match.start()]
        if NEGATION_BEFORE_RE.search(prefix):
            continue
        return True
    return False


def _word_positions(text: str, term: str, allow_regex: bool = False) -> list[int]:
    normalized_text = normalize_text(text).lower()
    if not normalized_text:
        return []
    tokens = re.findall(r"[a-z0-9]+", normalized_text)
    if not tokens:
        return []
    if term.startswith("re:"):
        if not allow_regex:
            raise ValueError("Regex concepts are disabled for this triage run")
        regex = re.compile(term[3:], re.I)
        return [
            index
            for index, token in enumerate(tokens)
            if regex.search(token)
        ]
    normalized_term = normalize_text(term).lower()
    term_tokens = re.findall(r"[a-z0-9]+", normalized_term)
    if not term_tokens:
        return []
    width = len(term_tokens)
    positions = []
    for index in range(0, len(tokens) - width + 1):
        if tokens[index:index + width] == term_tokens:
            positions.append(index)
    return positions


def _near_matches(text: str, patterns: list[str], window: int, allow_regex: bool = False) -> bool:
    position_groups = [_word_positions(text, pattern, allow_regex=allow_regex) for pattern in patterns]
    if any(not positions for positions in position_groups):
        return False
    chosen: list[int] = []

    def walk(group_index: int) -> bool:
        if group_index == len(position_groups):
            return max(chosen) - min(chosen) <= window
        for position in position_groups[group_index]:
            chosen.append(position)
            if max(chosen) - min(chosen) <= window and walk(group_index + 1):
                return True
            chosen.pop()
        return False

    return walk(0)


def concept_matches(row: dict[str, str], concept: Concept, allow_regex: bool = False) -> bool:
    text = scoped_text(row, concept.scope)
    if concept.op == "all":
        return bool(concept.children) and all(concept_matches(row, child, allow_regex) for child in concept.children)
    if concept.op == "any" and concept.children:
        return any(concept_matches(row, child, allow_regex) for child in concept.children)
    if concept.op == "not":
        return bool(concept.children) and not any(concept_matches(row, child, allow_regex) for child in concept.children)
    if not text:
        return False
    if concept.op == "near":
        return _near_matches(text, concept.patterns, concept.window, allow_regex=allow_regex)
    if concept.op == "not_near":
        return not _near_matches(text, concept.patterns, concept.window, allow_regex=allow_regex)
    for pattern in concept.patterns:
        try:
            if _basic_pattern_match(text, pattern, allow_regex=allow_regex):
                return True
        except re.error as exc:
            raise ValueError(f"Invalid pattern for concept '{concept.name}': {pattern}: {exc}") from exc
    return False


def _evidence_snippet(text: str, start: int, end: int, radius: int = 55) -> str:
    left = max(0, start - radius)
    right = min(len(text), end + radius)
    snippet = text[left:right].strip()
    return snippet.replace(";", ",")


def concept_evidence(row: dict[str, str], concept: Concept, allow_regex: bool = False) -> str:
    """Return transparent match provenance without interpreting scientific role."""
    if not concept_matches(row, concept, allow_regex):
        return ""
    if concept.children:
        child_evidence = [
            concept_evidence(row, child, allow_regex)
            for child in concept.children
            if concept_matches(row, child, allow_regex)
        ]
        details = " + ".join(item for item in child_evidence if item)
        return f"{concept.name}@{concept.scope}:{concept.op}({details})"

    if concept.op in {"near", "not_near"}:
        for field in scope_fields(concept.scope):
            text = normalize_text(row.get(field, "") or "")
            if text and (
                _near_matches(text, concept.patterns, concept.window, allow_regex)
                if concept.op == "near"
                else not _near_matches(text, concept.patterns, concept.window, allow_regex)
            ):
                return (
                    f"{concept.name}={'|'.join(concept.patterns)}@{field}:"
                    f"{_evidence_snippet(text, 0, min(len(text), 110))}"
                )
        return f"{concept.name}@{concept.scope}:{concept.op}"

    for field in scope_fields(concept.scope):
        text = normalize_text(row.get(field, "") or "")
        if not text:
            continue
        for pattern in concept.patterns:
            compiled = compile_pattern(pattern, allow_regex=allow_regex)
            for match in compiled.finditer(text):
                prefix = text[max(0, match.start() - 80):match.start()]
                if NEGATION_BEFORE_RE.search(prefix):
                    continue
                return (
                    f"{concept.name}={pattern}@{field}:"
                    f"{_evidence_snippet(text, match.start(), match.end())}"
                )
    return f"{concept.name}@{concept.scope}"


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


def bibliographic_status(row: dict[str, str]) -> str:
    """Return a workflow-safe bibliographic state independent of relevance."""
    status = (row.get("crossref_status") or "").strip().lower()
    verified = (row.get("crossref_verified") or "").strip().lower() in {"true", "1", "yes"}
    if status in CROSSREF_TRUSTED_STATUSES:
        return "verified"
    if status == "mismatch":
        return "mismatch"
    if status == "skipped_budget":
        return "pending_budget"
    if status in CROSSREF_PROVIDER_PENDING_STATUSES or status.startswith("http_5"):
        return "pending_provider"
    if status == "missing_doi":
        return "missing_identifier"
    if status in CROSSREF_LOOKUP_FAILED_STATUSES or status.startswith("http_4"):
        return "lookup_failed"
    if not status:
        return "verified" if verified else "not_checked"
    if verified:
        return "verified"
    return "unverified"


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

    crossref_status = (row.get("crossref_status") or "").strip().lower()
    mismatches = (row.get("crossref_mismatches") or "").strip()
    error_code = (row.get("crossref_error_code") or "").strip()
    if crossref_status == "mismatch":
        flags.append("crossref_mismatch")
        reasons.append(mismatches or "Crossref metadata mismatch")
    elif crossref_status == "skipped_budget":
        flags.append("crossref_pending_budget")
        reasons.append(error_code or "Crossref verification is pending because the row budget was exhausted")
    elif crossref_status in CROSSREF_PROVIDER_PENDING_STATUSES or crossref_status.startswith("http_5"):
        flags.append("crossref_pending_provider")
        reasons.append(error_code or f"Crossref status is {crossref_status}")
    elif crossref_status in CROSSREF_LOOKUP_FAILED_STATUSES or crossref_status.startswith("http_4"):
        flags.append(f"crossref_{crossref_status}")
        reasons.append(error_code or f"Crossref status is {crossref_status}")

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

    base_priority = _base_priority_for_concepts(
        profile, matched_required, matched_optional, matched_negative, missing_required,
    )
    return _apply_retraction_demotion(row, base_priority)


def _base_priority_for_concepts(profile: TriageProfile, matched_required: list[str],
                                matched_optional: list[str], matched_negative: list[str],
                                missing_required: list[str]) -> str:
    if profile.required and not missing_required:
        return "medium" if matched_negative else "high"
    if matched_required or matched_optional:
        return "low" if matched_negative and not matched_required else "medium"
    if matched_negative:
        return "low"
    return "needs_review"


_DEMOTION_ORDER = {"high": "medium", "medium": "needs_review", "needs_review": "low", "low": "low"}


def _apply_retraction_demotion(row: dict[str, str], priority: str) -> str:
    retraction = (row.get("retraction_status") or "").strip().lower()
    if retraction == "retracted":
        return _DEMOTION_ORDER.get(priority, "needs_review")
    return priority


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


def workflow_status(
    priority: str,
    metadata_status: str,
    bibliography: str,
    scientific_review: bool = False,
) -> str:
    if metadata_status == "blocked":
        return "blocked"
    if scientific_review or priority == "needs_review":
        return "scientific_review"
    if priority == "low":
        return "low_priority"
    if bibliography == "verified":
        return "ready_for_enrichment"
    if bibliography in {"pending_budget", "pending_provider", "not_checked", "unverified"}:
        return "pending_bibliographic_verification"
    if bibliography == "missing_identifier":
        return "identifier_recovery"
    if bibliography == "lookup_failed":
        return "bibliographic_review"
    return "metadata_review"


def triage_row(row: dict[str, str], profile: TriageProfile) -> dict[str, str]:
    matched_required = [c.name for c in profile.required if concept_matches(row, c, profile.allow_regex)]
    matched_optional = [c.name for c in profile.optional if concept_matches(row, c, profile.allow_regex)]
    matched_negative = [c.name for c in profile.negative if concept_matches(row, c, profile.allow_regex)]
    missing_required = [c.name for c in profile.required if c.name not in matched_required]
    required_evidence = [
        concept_evidence(row, concept, profile.allow_regex)
        for concept in profile.required
        if concept.name in matched_required
    ]
    optional_evidence = [
        concept_evidence(row, concept, profile.allow_regex)
        for concept in profile.optional
        if concept.name in matched_optional
    ]
    negative_evidence = [
        concept_evidence(row, concept, profile.allow_regex)
        for concept in profile.negative
        if concept.name in matched_negative
    ]

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

    cited = int(row.get("cited_by_count") or 0)
    citation_bonus = min(math.log2(cited + 1) * 0.3, 2.0) if cited > 0 else 0.0
    score += citation_bonus

    hard_flags, meta_status, meta_reasons = metadata_flags(row, profile)
    priority = priority_for(profile, matched_required, matched_optional,
                            matched_negative, missing_required, row)
    bibliography = bibliographic_status(row)
    scientific_review = priority == "needs_review" or bool(matched_negative)

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
    if citation_bonus > 0:
        reasons.append(f"citation signal: +{citation_bonus:.1f} ({cited} citations)")
    retraction = (row.get("retraction_status") or "").strip().lower()
    if retraction == "retracted":
        reasons.append("demoted due to Crossref retraction update")

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
        "workflow_status": workflow_status(
            priority,
            meta_status,
            bibliography,
            scientific_review=scientific_review,
        ),
        "bibliographic_status": bibliography,
        "bibliographic_review_needed": "false" if bibliography == "verified" else "true",
        "scientific_review_needed": "true" if scientific_review else "false",
        "semantic_tags": "; ".join(tags),
        "matched_required": "; ".join(matched_required),
        "matched_required_evidence": "; ".join(item for item in required_evidence if item),
        "matched_optional": "; ".join(matched_optional),
        "matched_optional_evidence": "; ".join(item for item in optional_evidence if item),
        "matched_negative": "; ".join(matched_negative),
        "matched_negative_evidence": "; ".join(item for item in negative_evidence if item),
        "missing_required": "; ".join(missing_required),
        "triage_reasons": "; ".join(reasons) if reasons else "no explicit match",
        "llm_review_needed": "true" if scientific_review else "false",
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
                allow_regex: bool = False,
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
    parser.add_argument("--enable-regex-concepts", dest="allow_regex", action="store_true", default=False,
                        help="Allow re: concepts. Disabled by default to avoid expensive caller-supplied regex.")
    parser.add_argument("--disable-regex-concepts", dest="allow_regex", action="store_false",
                        help="Reject re: concepts instead of compiling caller-supplied regex")
    parser.add_argument("--no-sort", action="store_true")
    args = parser.parse_args()

    try:
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
            allow_regex=args.allow_regex,
            sort_rows=not args.no_sort,
        )
    except ValueError as exc:
        print(f"Semantic triage validation error: {exc}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
