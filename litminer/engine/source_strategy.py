#!/usr/bin/env python3
"""Source-strategy hints for Agent-facing literature acquisition.

The strategy layer is advisory. It does not rewrite user intent, add hidden
queries, or make scientific inclusion decisions. Its job is to make source
coverage, gaps, and retrieval risks explicit in `query_plan.json` so an Agent
can choose the next retrieval step deliberately.
"""

from __future__ import annotations

import re
from typing import Any


PROVIDER_ROLES: dict[str, dict[str, str]] = {
    "openalex": {
        "role": "primary_broad_discovery",
        "strength": "large cross-domain metadata coverage",
        "boundary": "preliminary metadata; not bibliographic authority",
    },
    "semantic_scholar": {
        "role": "recall_booster_and_graph",
        "strength": "semantic recall and citation/reference-adjacent discovery",
        "boundary": "rate-limit prone; not bibliographic authority",
    },
    "arxiv": {
        "role": "preprint_discovery",
        "strength": "fast-moving preprint-heavy fields",
        "boundary": "preprints and arXiv metadata only",
    },
    "europe_pmc": {
        "role": "biomedical_fulltext_metadata",
        "strength": "biomedical/life-science metadata and full-text link hints",
        "boundary": "domain-specific; not a final article-fact verifier",
    },
}


DOMAIN_HINTS: dict[str, tuple[str, ...]] = {
    "biomedical": (
        "biomedical", "clinical", "cancer", "tumor", "therapy", "patient",
        "disease", "gene", "genomic", "protein", "enzyme", "pubmed",
        "pmid", "immunotherapy", "cell", "mouse", "mice",
    ),
    "preprint_heavy": (
        "machine learning", "deep learning", "neural", "llm", "large language",
        "computer vision", "reinforcement learning", "algorithm", "physics",
        "mathematics", "computer science", "arxiv",
    ),
    "chemistry_materials": (
        "catalyst", "photocatal", "electrocatal", "hydrogen", "h2",
        "degradation", "nanomaterial", "perovskite", "mof", "cof",
        "graphene", "chemistry", "materials",
    ),
    "environmental": (
        "pollutant", "wastewater", "degradation", "remediation", "water",
        "environmental", "contaminant", "dye",
    ),
}


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _normalize_sources(value: Any) -> list[str]:
    items: list[str] = []
    for item in _as_list(value):
        for part in re.split(r"[,;]", item):
            source = part.strip().lower().replace("-", "_")
            if source and source not in items:
                items.append(source)
    return items


def _terms(value: Any) -> list[str]:
    terms: list[str] = []
    for item in _as_list(value):
        if "=" in item:
            item = item.split("=", 1)[1]
        for part in re.split(r"[|,;]", item):
            part = part.strip()
            if part:
                terms.append(part)
    return terms


def infer_domain_tags(
    queries: list[str],
    required_concepts: list[str] | None = None,
    optional_concepts: list[str] | None = None,
    negative_concepts: list[str] | None = None,
) -> list[str]:
    """Infer coarse source-selection tags from query/concept text."""
    blob = " ".join([
        *queries,
        *_terms(required_concepts),
        *_terms(optional_concepts),
        *_terms(negative_concepts),
    ]).lower()
    tags: list[str] = []
    for tag, hints in DOMAIN_HINTS.items():
        if any(hint in blob for hint in hints):
            tags.append(tag)
    return tags


def recommended_sources(domain_tags: list[str], selected_sources: list[str]) -> list[str]:
    """Return source suggestions without changing the selected run sources."""
    recommended = ["openalex"]
    if "semantic_scholar" in selected_sources:
        recommended.append("semantic_scholar")
    if "preprint_heavy" in domain_tags:
        recommended.extend(["semantic_scholar", "arxiv"])
    if "biomedical" in domain_tags:
        recommended.extend(["europe_pmc", "semantic_scholar"])
    if "chemistry_materials" in domain_tags or "environmental" in domain_tags:
        recommended.append("semantic_scholar")

    unique: list[str] = []
    for source in recommended:
        if source not in unique:
            unique.append(source)
    return unique


def _query_terms(query: str) -> list[str]:
    return [part for part in re.split(r"\s+", query.strip()) if part]


def _risk_flags(
    *,
    queries: list[str],
    selected_sources: list[str],
    recommended: list[str],
    required_concepts: list[str],
    year_from: int | None,
    controls: dict[str, Any],
) -> list[str]:
    flags: list[str] = []
    if not queries:
        flags.append("no_query_provided")
    elif len(queries) == 1:
        flags.append("single_query_low_recall_risk")
    if any(len(_query_terms(query)) <= 2 for query in queries):
        flags.append("very_short_query_high_noise_risk")
    if not required_concepts:
        flags.append("no_required_concepts_triage_will_be_weak")
    missing = [source for source in recommended if source not in selected_sources]
    if missing:
        flags.append("recommended_sources_not_selected")
    if "semantic_scholar" in selected_sources:
        flags.append("semantic_scholar_rate_limit_risk")
    if year_from is not None and year_from >= 2025:
        flags.append("recent_year_range_metadata_lag_risk")
    max_results = controls.get("max_results_per_query")
    try:
        if max_results is not None and int(max_results) >= 150:
            flags.append("high_result_limit_timeout_risk")
    except (TypeError, ValueError):
        pass
    return flags


def _source_selection(
    *,
    selected_sources: list[str],
    recommended: list[str],
    controls: dict[str, Any],
) -> dict[str, Any]:
    missing = [source for source in recommended if source not in selected_sources]
    origin = str(controls.get("discovery_sources_origin") or "unknown")
    raw_configured = _normalize_sources(controls.get("configured_discovery_sources") or selected_sources)
    effective_configured = list(raw_configured)
    if origin != "input_csv":
        if controls.get("skip_openalex"):
            effective_configured = [source for source in effective_configured if source != "openalex"]
        include_flags = {
            "semantic_scholar": controls.get("include_semantic_scholar"),
            "arxiv": controls.get("include_arxiv"),
            "europe_pmc": controls.get("include_europe_pmc"),
        }
        for source, enabled in include_flags.items():
            if enabled and source not in effective_configured:
                effective_configured.append(source)
    not_enabled_reasons: dict[str, str] = {}
    for source in missing:
        if origin == "input_csv":
            reason = "API discovery was skipped because the workflow started from input_csv"
        elif source == "openalex" and controls.get("skip_openalex"):
            reason = "disabled by skip_openalex"
        elif origin == "explicit":
            reason = "not included in caller-selected discovery_sources or include flags"
        elif origin == "config":
            reason = "not enabled by runtime config channels"
        else:
            reason = "not selected for this run"
        not_enabled_reasons[source] = reason
    return {
        "selected_sources": selected_sources,
        "selection_origin": origin,
        "configured_sources": effective_configured,
        "raw_configured_sources": raw_configured,
        "effective_configured_sources": effective_configured,
        "recommended_sources": recommended,
        "recommended_not_selected": missing,
        "not_enabled_reasons": not_enabled_reasons,
        "automatic_expansion": False,
    }


def estimate_discovery_calls(
    queries: list[str],
    selected_sources: list[str],
    controls: dict[str, Any] | None = None,
) -> dict[str, Any]:
    controls = controls or {}
    semantic_query_limit = controls.get("semantic_query_limit")
    calls_by_source: dict[str, int] = {}
    for source in selected_sources:
        if source == "semantic_scholar" and semantic_query_limit is not None:
            try:
                query_count = min(len(queries), max(0, int(semantic_query_limit)))
            except (TypeError, ValueError):
                query_count = len(queries)
        else:
            query_count = len(queries)
        calls_by_source[source] = query_count
    return {
        "queries": len(queries),
        "provider_calls": sum(calls_by_source.values()),
        "calls_by_source": calls_by_source,
    }


def build_strategy(
    *,
    queries: list[str],
    selected_sources: list[str] | None = None,
    required_concepts: list[str] | None = None,
    optional_concepts: list[str] | None = None,
    negative_concepts: list[str] | None = None,
    year_from: int | None = None,
    mode: str = "",
    controls: dict[str, Any] | None = None,
) -> dict[str, Any]:
    controls = controls or {}
    normalized_sources = _normalize_sources(selected_sources)
    domain_tags = infer_domain_tags(
        queries,
        required_concepts=required_concepts,
        optional_concepts=optional_concepts,
        negative_concepts=negative_concepts,
    )
    recommended = recommended_sources(domain_tags, normalized_sources)
    missing = [source for source in recommended if source not in normalized_sources]
    fallback_order = [
        source
        for source in ["openalex", "europe_pmc", "arxiv", "semantic_scholar"]
        if source in normalized_sources
    ]

    return {
        "schema_version": 1,
        "mode": mode,
        "domain_tags": domain_tags,
        "selected_sources": normalized_sources,
        "recommended_sources": recommended,
        "missing_recommended_sources": missing,
        "source_selection": _source_selection(
            selected_sources=normalized_sources,
            recommended=recommended,
            controls=controls,
        ),
        "provider_roles": {
            source: PROVIDER_ROLES.get(source, {
                "role": "caller_selected_source",
                "strength": "caller selected",
                "boundary": "inspect provider trace before trusting",
            })
            for source in normalized_sources
        },
        "fallback_order": fallback_order,
        "request_estimate": estimate_discovery_calls(queries, normalized_sources, controls),
        "risk_flags": _risk_flags(
            queries=queries,
            selected_sources=normalized_sources,
            recommended=recommended,
            required_concepts=_as_list(required_concepts),
            year_from=year_from,
            controls=controls,
        ),
        "agent_actions": [
            "Keep selected_sources unchanged unless the Agent explicitly decides to broaden retrieval.",
            "Use missing_recommended_sources as retrieval-gap hints, not as automatic requirements.",
            "Use provider trace status before treating low result counts as scientific absence.",
        ],
    }
