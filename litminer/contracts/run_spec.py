"""Typed Litminer run specification with explicit input-mode semantics."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping

from litminer.contracts.errors import LitminerValidationError


RUNTIME_OPTION_ALIASES: dict[str, tuple[str, ...]] = {
    "config": ("config",),
    "resume_allow_mismatch": ("resume_allow_mismatch",),
    "resume_mismatch_reason": ("resume_mismatch_reason",),
    "include_arxiv": ("include_arxiv",),
    "include_europe_pmc": ("include_europe_pmc",),
    "include_semantic_scholar": ("include_semantic_scholar",),
    "skip_openalex": ("skip_openalex",),
    "exclude_article_types": ("exclude_article_types", "exclude_article_type"),
    "queue_priorities": ("queue_priorities",),
    "include_metadata_blocked": ("include_metadata_blocked",),
    "fields_needed": ("fields_needed",),
    "page_required_fields": ("page_required_fields", "page_required_field"),
    "openalex_work_types": ("openalex_work_types",),
    "semantic_query_limit": ("semantic_query_limit",),
    "semantic_max_results": ("semantic_max_results",),
    "provider_failure_threshold": ("provider_failure_threshold",),
    "provider_rate_limit_cooldown_seconds": ("provider_rate_limit_cooldown_seconds",),
    "cache_dir": ("cache_dir",),
    "cache_ttl_days": ("cache_ttl_days",),
    "provider_failure_cache_ttl_seconds": ("provider_failure_cache_ttl_seconds",),
    "cache_enabled": ("cache_enabled",),
    "crossref_checkpoint_interval": ("crossref_checkpoint_interval",),
    "unpaywall_checkpoint_interval": ("unpaywall_checkpoint_interval",),
    "skip_unpaywall": ("skip_unpaywall",),
    "unpaywall_sleep": ("unpaywall_sleep",),
    "metrics_csv": ("metrics_csv", "metrics"),
    "min_if": ("min_if",),
    "skip_journal_metrics": ("skip_journal_metrics",),
    "target_count": ("target_count",),
    "queue_strict_only": ("queue_strict_only",),
    "allow_missing_doi": ("allow_missing_doi",),
    "screenshot_root": ("screenshot_root",),
    "probe_publishers": ("probe_publishers",),
    "probe_limit": ("probe_limit",),
    "probe_sleep": ("probe_sleep",),
    "expand_citations": ("expand_citations",),
    "expand_seeds": ("expand_seeds",),
    "expand_top_n": ("expand_top_n",),
    "expand_max_per_seed": ("expand_max_per_seed",),
    "expand_direction": ("expand_direction",),
}


def _value(mapping: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return default


def _tuple(value: Any) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def _path(value: Any) -> str:
    if value in (None, ""):
        return ""
    return str(Path(value))


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    raise TypeError(f"value is not part of the serializable RunSpec contract: {type(value).__name__}")


@dataclass(frozen=True)
class InputSpec:
    mode: str
    queries: tuple[str, ...] = ()
    query_file: str = ""
    input_csv: str = ""
    merge_into: str = ""
    year_from: int | None = None
    year_to: int | None = None


@dataclass(frozen=True)
class RetrievalSpec:
    mode: str = "custom/default"
    sources: tuple[str, ...] = ()
    strict_discovery: bool = False
    max_results_per_query: int | None = None
    parallel_providers: bool = False
    provider_workers: int | None = None


@dataclass(frozen=True)
class VerificationSpec:
    crossref_row_budget: int | None = None
    unpaywall_row_budget: int | None = None
    publisher_probe_row_budget: int | None = None
    skip_crossref: bool = False
    enrich_unpaywall: bool = True


@dataclass(frozen=True)
class ConceptSpec:
    required: tuple[str, ...] = ()
    optional: tuple[str, ...] = ()
    negative: tuple[str, ...] = ()
    allow_regex: bool = False


@dataclass(frozen=True)
class OutputSpec:
    directory: str = ""
    export_formats: tuple[str, ...] = ()
    include_unverified_export: bool = False
    ascii_latex: bool = False


@dataclass(frozen=True)
class ControlSpec:
    resume: bool = False
    time_budget_seconds: float | None = None
    stop_after_stage: str = ""
    state_store: str = ""
    state_enabled: bool = True


@dataclass(frozen=True)
class RunSpec:
    input: InputSpec
    retrieval: RetrievalSpec
    verification: VerificationSpec
    concepts: ConceptSpec
    output: OutputSpec
    controls: ControlSpec
    extras: dict[str, Any] = field(default_factory=dict)
    schema_version: int = 1

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, Any], *, validate: bool = True) -> "RunSpec":
        queries = _tuple(_value(mapping, "queries", "query", default=()))
        query_file = _path(_value(mapping, "query_file"))
        input_csv = _path(_value(mapping, "input_csv"))
        merge_into = _path(_value(mapping, "merge_into"))

        has_discovery = bool(queries or query_file)
        has_import = bool(input_csv)
        if merge_into:
            input_mode = "iterate"
        elif has_import:
            input_mode = "import"
        else:
            input_mode = "discover"

        raw_sources = _value(mapping, "sources", "discovery_sources", default=())
        if isinstance(raw_sources, str):
            sources = tuple(item.strip() for item in raw_sources.replace(";", ",").split(",") if item.strip())
        else:
            sources = _tuple(raw_sources)

        export_formats = _value(mapping, "export_formats", "export", default=())
        if isinstance(export_formats, str):
            export_tuple = tuple(item.strip().lower() for item in export_formats.replace(";", ",").split(",") if item.strip())
        else:
            export_tuple = tuple(str(item).strip().lower() for item in export_formats or () if str(item).strip())

        spec = cls(
            input=InputSpec(
                mode=input_mode,
                queries=queries,
                query_file=query_file,
                input_csv=input_csv,
                merge_into=merge_into,
                year_from=_value(mapping, "year_from"),
                year_to=_value(mapping, "year_to"),
            ),
            retrieval=RetrievalSpec(
                mode=str(_value(mapping, "mode", default="custom/default") or "custom/default"),
                sources=sources,
                strict_discovery=bool(_value(mapping, "strict_discovery", default=False)),
                max_results_per_query=_value(mapping, "max_results_per_query"),
                parallel_providers=bool(_value(mapping, "parallel_providers", default=False)),
                provider_workers=_value(mapping, "provider_workers"),
            ),
            verification=VerificationSpec(
                crossref_row_budget=_value(mapping, "max_crossref_rows"),
                unpaywall_row_budget=_value(mapping, "max_unpaywall_rows"),
                publisher_probe_row_budget=_value(mapping, "max_publisher_probe_rows"),
                skip_crossref=bool(_value(mapping, "skip_crossref", default=False)),
                enrich_unpaywall=bool(_value(mapping, "enrich_unpaywall", default=True)),
            ),
            concepts=ConceptSpec(
                required=_tuple(_value(mapping, "required_concepts", "required_concept", default=())),
                optional=_tuple(_value(mapping, "optional_concepts", "optional_concept", default=())),
                negative=_tuple(_value(mapping, "negative_concepts", "negative_concept", default=())),
                allow_regex=bool(_value(mapping, "enable_regex_concepts", "allow_regex_concepts", default=False)),
            ),
            output=OutputSpec(
                directory=_path(_value(mapping, "output_dir")),
                export_formats=export_tuple,
                include_unverified_export=bool(_value(mapping, "include_unverified_export", default=False)),
                ascii_latex=bool(_value(mapping, "ascii_latex", default=False)),
            ),
            controls=ControlSpec(
                resume=bool(_value(mapping, "resume", default=False)),
                time_budget_seconds=_value(mapping, "time_budget_seconds"),
                stop_after_stage=str(_value(mapping, "stop_after_stage", default="") or ""),
                state_store=_path(_value(mapping, "state_store", "state_store_path")),
                state_enabled=bool(_value(mapping, "state_enabled", default=True)),
            ),
            extras=dict(mapping),
        )
        if validate:
            spec.validate()
        return spec

    @classmethod
    def from_namespace(cls, namespace: Any, *, validate: bool = True) -> "RunSpec":
        return cls.from_mapping(vars(namespace), validate=validate)

    def validate(self) -> None:
        has_discovery = bool(self.input.queries or self.input.query_file)
        has_import = bool(self.input.input_csv)
        if has_discovery and has_import:
            raise LitminerValidationError(
                "input_csv cannot be combined with queries or query_file; choose one input family",
                details={"input_mode": self.input.mode},
            )
        if not has_discovery and not has_import:
            raise LitminerValidationError(
                "provide queries/query_file or input_csv",
                details={"input_mode": self.input.mode},
            )
        if self.input.mode == "iterate" and not self.input.merge_into:
            raise LitminerValidationError("iterate mode requires merge_into")
        if self.controls.resume and self.input.merge_into:
            raise LitminerValidationError("resume and merge_into are separate workflows")
        if self.input.year_from is not None and self.input.year_to is not None:
            if int(self.input.year_from) > int(self.input.year_to):
                raise LitminerValidationError("year_from cannot be greater than year_to")
        unknown_exports = sorted(set(self.output.export_formats) - {"ris", "bibtex"})
        if unknown_exports:
            raise LitminerValidationError(
                f"unknown export format(s): {', '.join(unknown_exports)}",
                details={"allowed": ["ris", "bibtex"]},
            )

    def to_dict(self) -> dict[str, Any]:
        data = {
            'schema_version': self.schema_version,
            'input': asdict(self.input),
            'retrieval': asdict(self.retrieval),
            'verification': asdict(self.verification),
            'concepts': asdict(self.concepts),
            'output': asdict(self.output),
            'controls': asdict(self.controls),
        }
        data["input"]["queries"] = list(self.input.queries)
        data["retrieval"]["sources"] = list(self.retrieval.sources)
        data["concepts"]["required"] = list(self.concepts.required)
        data["concepts"]["optional"] = list(self.concepts.optional)
        data["concepts"]["negative"] = list(self.concepts.negative)
        data["output"]["export_formats"] = list(self.output.export_formats)
        runtime_options: dict[str, Any] = {}
        for public_name, aliases in RUNTIME_OPTION_ALIASES.items():
            value = _value(self.extras, *aliases)
            if value is None:
                continue
            try:
                runtime_options[public_name] = _json_safe(value)
            except TypeError:
                continue
        data["runtime_options"] = runtime_options
        return data
