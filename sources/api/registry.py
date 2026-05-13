#!/usr/bin/env python3
"""Provider registry for Litminer scholarly information sources.

The registry keeps provider names, aliases, and capability notes in one place.
Wrappers still own provider-specific HTTP logic; orchestration code uses this
module to parse user/config source lists and to render capability reports.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    aliases: tuple[str, ...]
    role: str
    discovery: bool
    requires_key: str
    requires_contact: str
    supports_year_filter: str
    supports_doi_lookup: str
    returns_abstract: str
    rate_limit_policy: str

    def capability_row(self) -> dict[str, str]:
        return {
            "provider": self.name,
            "role": self.role,
            "requires_key": self.requires_key,
            "requires_contact": self.requires_contact,
            "supports_year_filter": self.supports_year_filter,
            "supports_doi_lookup": self.supports_doi_lookup,
            "returns_abstract": self.returns_abstract,
            "rate_limit_policy": self.rate_limit_policy,
        }


PROVIDER_SPECS: dict[str, ProviderSpec] = {
    "openalex": ProviderSpec(
        name="openalex",
        aliases=("oa", "openalex"),
        role="broad_discovery",
        discovery=True,
        requires_key="optional",
        requires_contact="recommended",
        supports_year_filter="yes",
        supports_doi_lookup="indirect",
        returns_abstract="often",
        rate_limit_policy="retry_with_backoff",
    ),
    "semantic_scholar": ProviderSpec(
        name="semantic_scholar",
        aliases=("s2", "semantic", "semantic_scholar", "semantic-scholar"),
        role="semantic_recall_and_graph",
        discovery=True,
        requires_key="optional",
        requires_contact="no",
        supports_year_filter="yes",
        supports_doi_lookup="external_ids",
        returns_abstract="often",
        rate_limit_policy="retry_with_backoff",
    ),
    "arxiv": ProviderSpec(
        name="arxiv",
        aliases=("arxiv", "arxiv_api", "arxiv-api"),
        role="preprint_discovery",
        discovery=True,
        requires_key="no",
        requires_contact="no",
        supports_year_filter="submitted_date",
        supports_doi_lookup="sometimes",
        returns_abstract="yes",
        rate_limit_policy="polite_sleep",
    ),
    "europe_pmc": ProviderSpec(
        name="europe_pmc",
        aliases=("epmc", "europepmc", "europe_pmc", "europe-pmc"),
        role="biomedical_fulltext_metadata_discovery",
        discovery=True,
        requires_key="no",
        requires_contact="no",
        supports_year_filter="query_filter",
        supports_doi_lookup="yes",
        returns_abstract="often",
        rate_limit_policy="retry_with_backoff",
    ),
    "crossref": ProviderSpec(
        name="crossref",
        aliases=("crossref", "cr"),
        role="metadata_verification",
        discovery=False,
        requires_key="no",
        requires_contact="recommended",
        supports_year_filter="not_discovery_default",
        supports_doi_lookup="yes",
        returns_abstract="rarely",
        rate_limit_policy="retry_with_backoff",
    ),
    "unpaywall": ProviderSpec(
        name="unpaywall",
        aliases=("unpaywall",),
        role="oa_location_lookup",
        discovery=False,
        requires_key="no",
        requires_contact="email_required",
        supports_year_filter="no",
        supports_doi_lookup="yes",
        returns_abstract="no",
        rate_limit_policy="polite_sleep",
    ),
}

ALIASES: dict[str, str] = {}
for spec in PROVIDER_SPECS.values():
    for alias in spec.aliases:
        ALIASES[alias.lower().replace("-", "_")] = spec.name


def normalize_provider_name(value: str) -> str:
    key = str(value).strip().lower().replace("-", "_")
    if not key:
        return ""
    if key not in ALIASES:
        raise ValueError(f"Unknown provider: {value}")
    return ALIASES[key]


def parse_provider_list(
    value: str | list[str] | None,
    default: list[str] | None = None,
    discovery_only: bool = True,
) -> list[str]:
    if value is None:
        raw = list(default or ["openalex"])
    elif isinstance(value, list):
        raw = []
        for item in value:
            raw.extend(str(item).replace(";", ",").split(","))
    else:
        raw = value.replace(";", ",").split(",")

    parsed: list[str] = []
    for item in raw:
        if not str(item).strip():
            continue
        try:
            provider = normalize_provider_name(str(item))
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        spec = PROVIDER_SPECS[provider]
        if discovery_only and not spec.discovery:
            raise SystemExit(f"Provider is not a discovery source: {item}")
        if provider not in parsed:
            parsed.append(provider)
    return parsed or list(default or ["openalex"])


def discovery_provider_names() -> list[str]:
    return [name for name, spec in PROVIDER_SPECS.items() if spec.discovery]


def provider_capability_rows(names: list[str] | None = None) -> list[dict[str, str]]:
    selected = names or list(PROVIDER_SPECS)
    rows = []
    for name in selected:
        provider = normalize_provider_name(name)
        rows.append(PROVIDER_SPECS[provider].capability_row())
    return rows
