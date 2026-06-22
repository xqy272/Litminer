#!/usr/bin/env python3
"""Result profile: descriptive statistics about a retrieved collection.

Design constraints (see iteration_plan.md §2.1 and SKILL.md "Statistical
Output Boundary"):

- Statistics are stratified by Trust Tier (all_rows + crossref_verified).
  Mixing unverified rows with Crossref-verified rows in a single flat
  distribution would produce statistics that look clean but have uneven
  trust. The Agent can therefore say "of the 187 rows, 142 are
  Crossref-verified; among verified rows the top journal is Nature Energy
  (12)" rather than collapsing the trust distinction.
- ``completeness_caveats`` reports search process completeness only
  (provider failures, rate limits, circuit breaks, query caps). It must
  never claim result completeness (field coverage). "Semantic Scholar was
  circuit-broken after 2 failures" is a reportable fact; "there are 50
  more relevant papers Litminer missed" is a hallucination.
- When the collection is empty or the run failed broadly, the profile
  degrades to a ``failure_summary`` built from manifest/trace instead of
  emitting empty statistics. "0 results" is informative only if the
  Agent can also explain why.
- Missing columns degrade to ``None``. A statistic whose source column is
  absent returns None rather than 0 or skipping the whole profile. This
  keeps downstream readers from confusing "the field is absent" with
  "the value is zero".
- Only descriptive statistics about the retrieved collection are emitted.
  No field assertions ("X is the leading journal") and no query-comparison
  term hints ("frequent_terms_not_in_query"). See SKILL.md.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from litminer.engine import status_policy
from litminer.engine.common import read_csv_rows, write_text_atomic


PROFILE_NAME = "result_profile.json"

CROSSREF_TRUSTED_STATUSES = {"verified", "title_recovered"}
PROBLEM_PROVIDER_STATUSES = {
    "rate_limited", "network_error", "auth_error", "response_parse_error",
    "provider_error", "skipped_circuit_breaker", "skipped_cached_provider_failure",
    "partial_rate_limited", "partial_error", "error",
}


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    _fieldnames, rows = read_csv_rows(path)
    return rows


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _has_column(rows: list[dict[str, str]], column: str) -> bool:
    if not rows:
        return False
    return column in rows[0]


def _to_int(value: str) -> int | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _split_semicolon(value: str) -> list[str]:
    return [item.strip() for item in (value or "").split(";") if item.strip()]


def _year_distribution(rows: list[dict[str, str]]) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for row in rows:
        year = (row.get("publication_year") or row.get("crossref_year") or row.get("year") or "").strip()
        if year:
            counter[year] += 1
    return dict(sorted(counter.items(), key=lambda item: item[0]))


def _top_values(rows: list[dict[str, str]], column: str, limit: int = 15) -> list[tuple[str, int]] | None:
    if not _has_column(rows, column):
        return None
    counter: Counter[str] = Counter()
    for row in rows:
        value = (row.get(column) or "").strip()
        if value:
            counter[value] += 1
    return [(key, count) for key, count in counter.most_common(limit)]


def _top_authors(rows: list[dict[str, str]], limit: int = 15) -> list[tuple[str, int]] | None:
    if not _has_column(rows, "authors"):
        return None
    counter: Counter[str] = Counter()
    for row in rows:
        for author in _split_semicolon(row.get("authors") or ""):
            counter[author] += 1
    return [(key, count) for key, count in counter.most_common(limit)]


def _high_cited(rows: list[dict[str, str]], limit: int = 10) -> list[dict[str, Any]] | None:
    if not _has_column(rows, "cited_by_count"):
        return None
    candidates: list[tuple[int, dict[str, str]]] = []
    for row in rows:
        cited = _to_int(row.get("cited_by_count") or "")
        if cited is not None and cited > 0:
            candidates.append((cited, row))
    candidates.sort(key=lambda item: item[0], reverse=True)
    return [
        {
            "title": (row.get("crossref_title") or row.get("title") or "").strip(),
            "doi": (row.get("crossref_doi") or row.get("doi") or "").strip(),
            "cited_by_count": cited,
        }
        for cited, row in candidates[:limit]
    ]


def _article_type_distribution(rows: list[dict[str, str]]) -> dict[str, int] | None:
    column = None
    for candidate in ("article_type", "crossref_type"):
        if _has_column(rows, candidate):
            column = candidate
            break
    if column is None:
        return None
    counter: Counter[str] = Counter()
    for row in rows:
        value = (row.get(column) or "").strip() or "<blank>"
        counter[value] += 1
    return dict(sorted(counter.items(), key=lambda item: (-item[1], item[0])))


def _oa_rate(rows: list[dict[str, str]]) -> float | None:
    if not _has_column(rows, "is_oa"):
        return None
    total = len(rows)
    if total == 0:
        return None
    oa = sum(1 for row in rows if (row.get("is_oa") or "").strip().lower() in {"true", "1", "yes"})
    return round(oa / total, 4)


def _coverage(rows: list[dict[str, str]], column: str) -> float | None:
    if not _has_column(rows, column):
        return None
    total = len(rows)
    if total == 0:
        return None
    present = sum(1 for row in rows if (row.get(column) or "").strip())
    return round(present / total, 4)


def _triage_priority_distribution(rows: list[dict[str, str]]) -> dict[str, int] | None:
    if not _has_column(rows, "triage_priority"):
        return None
    counter: Counter[str] = Counter()
    for row in rows:
        value = (row.get("triage_priority") or "").strip() or "<blank>"
        counter[value] += 1
    return dict(sorted(counter.items(), key=lambda item: (-item[1], item[0])))


def _layer_stats(rows: list[dict[str, str]]) -> dict[str, Any]:
    """Compute descriptive statistics for one trust layer.

    Missing source columns degrade to None per the module docstring.
    """
    return {
        "total_rows": len(rows),
        "year_distribution": _year_distribution(rows),
        "top_journals": _top_values(rows, "journal", limit=15),
        "top_authors": _top_authors(rows, limit=15),
        "high_cited": _high_cited(rows, limit=10),
        "article_type_distribution": _article_type_distribution(rows),
        "oa_rate": _oa_rate(rows),
        "abstract_coverage": _coverage(rows, "abstract"),
        "doi_coverage": _coverage(rows, "doi"),
        "triage_priority_distribution": _triage_priority_distribution(rows),
    }


def _crossref_verified_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    if not _has_column(rows, "crossref_status"):
        return []
    return [
        row for row in rows
        if (row.get("crossref_status") or "").strip() in CROSSREF_TRUSTED_STATUSES
    ]


def _completeness_caveats(
    trace_rows: list[dict[str, str]],
    manifest: dict[str, Any],
) -> dict[str, Any]:
    """Build caveats describing search process completeness.

    Strictly process-side (provider failures, rate limits, circuit breaks,
    query caps). Never result completeness (field coverage).
    """
    provider_failures: dict[str, int] = {}
    circuit_broken: list[str] = []
    rate_limited_queries = 0
    for row in trace_rows:
        provider = (row.get("provider") or "").strip()
        status = (row.get("status") or "").strip()
        status_class = (row.get("status_class") or "").strip()
        if status == "skipped_circuit_breaker" and provider and provider not in circuit_broken:
            circuit_broken.append(provider)
        if status_class == "rate_limited":
            rate_limited_queries += 1
        if status in PROBLEM_PROVIDER_STATUSES and provider:
            provider_failures[provider] = provider_failures.get(provider, 0) + 1

    caveat_texts: list[str] = []
    if circuit_broken:
        caveat_texts.append(
            f"Provider(s) circuit-broken after repeated failures: {', '.join(circuit_broken)}. "
            "Coverage likely underestimates literature indexed by these providers."
        )
    if rate_limited_queries:
        caveat_texts.append(
            f"{rate_limited_queries} query/provider call(s) were rate-limited. "
            "Some results may be partial; consider retrying after cooldown."
        )
    if provider_failures:
        summary = ", ".join(f"{provider}={count}" for provider, count in sorted(provider_failures.items()))
        caveat_texts.append(f"Provider failure counts: {summary}.")

    return {
        "circuit_broken_providers": circuit_broken,
        "rate_limited_queries": rate_limited_queries,
        "provider_failure_counts": provider_failures,
        "caveat_text": " ".join(caveat_texts) if caveat_texts else "",
    }


def _failure_summary(
    trace_rows: list[dict[str, str]],
    manifest: dict[str, Any],
) -> dict[str, Any]:
    """Build a degraded summary for runs that produced 0 usable rows.

    Pulls provider failure info and next_action hints from trace + manifest
    so the Agent can explain "why 0 results" in 30 seconds.
    """
    provider_statuses: Counter[str] = Counter()
    next_actions: Counter[str] = Counter()
    for row in trace_rows:
        status = (row.get("status") or "").strip()
        if status:
            provider_statuses[status] += 1
        action = (row.get("next_action") or "").strip()
        if action:
            next_actions[action] += 1

    stages = manifest.get("stages", []) if isinstance(manifest, dict) else []
    stage_statuses: list[dict[str, str]] = []
    if isinstance(stages, list):
        for stage in stages:
            if isinstance(stage, dict):
                stage_statuses.append({
                    "name": str(stage.get("name") or ""),
                    "status": str(stage.get("status") or ""),
                    "status_class": status_policy.classify_status(str(stage.get("status") or "")),
                    "next_action": status_policy.next_action(str(stage.get("status") or "")),
                })

    return {
        "provider_status_counts": dict(provider_statuses),
        "provider_next_action_counts": dict(next_actions),
        "stage_statuses": stage_statuses,
        "run_status": str(manifest.get("run_status") or "") if isinstance(manifest, dict) else "",
        "stop_reason": str(manifest.get("stop_reason") or "") if isinstance(manifest, dict) else "",
    }


def _should_degrade_to_failure(rows: list[dict[str, str]], trace_rows: list[dict[str, str]]) -> bool:
    if rows:
        return False
    if not trace_rows:
        return True
    ok_count = sum(
        1 for row in trace_rows
        if (row.get("status") or "").strip() in {"ok", "empty_result"}
    )
    return ok_count == 0


def build_profile(
    triaged_path: Path,
    trace_path: Path,
    manifest_path: Path,
) -> dict[str, Any]:
    """Build a result profile dict from run artifacts.

    Reads ``triaged_candidates.csv``, ``api_discovery_trace.csv``, and
    ``run_manifest.json``. Returns a dict suitable for JSON serialization.
    """
    rows = _read_rows(triaged_path)
    trace_rows = _read_rows(trace_path)
    manifest = _read_json(manifest_path)

    if _should_degrade_to_failure(rows, trace_rows):
        return {
            "schema_version": 1,
            "degraded": True,
            "all_rows": None,
            "crossref_verified": None,
            "completeness_caveats": _completeness_caveats(trace_rows, manifest),
            "failure_summary": _failure_summary(trace_rows, manifest),
        }

    verified_rows = _crossref_verified_rows(rows)
    return {
        "schema_version": 1,
        "degraded": False,
        "all_rows": _layer_stats(rows),
        "crossref_verified": _layer_stats(verified_rows) if verified_rows else None,
        "completeness_caveats": _completeness_caveats(trace_rows, manifest),
        "failure_summary": None,
    }


def write_profile(
    triaged_path: Path,
    trace_path: Path,
    manifest_path: Path,
    output_path: Path | None = None,
) -> Path:
    output = output_path or triaged_path.parent / PROFILE_NAME
    profile = build_profile(triaged_path, trace_path, manifest_path)
    write_text_atomic(output, json.dumps(profile, indent=2, ensure_ascii=False) + "\n")
    return output


def to_summary_dict(profile: dict[str, Any]) -> dict[str, Any]:
    """Compact summary suitable for embedding in agent_summary.json."""
    if profile.get("degraded"):
        return {
            "degraded": True,
            "failure_summary": profile.get("failure_summary"),
            "completeness_caveats": profile.get("completeness_caveats"),
        }
    all_rows = profile.get("all_rows") or {}
    verified = profile.get("crossref_verified")
    return {
        "degraded": False,
        "all_rows_total": all_rows.get("total_rows", 0),
        "crossref_verified_total": (verified or {}).get("total_rows") if verified else 0,
        "all_rows_top_journals": (all_rows.get("top_journals") or [])[:5],
        "crossref_verified_top_journals": ((verified or {}).get("top_journals") or [])[:5] if verified else [],
        "high_cited_top_3": (all_rows.get("high_cited") or [])[:3],
        "oa_rate": all_rows.get("oa_rate"),
        "abstract_coverage": all_rows.get("abstract_coverage"),
        "triage_priority_distribution": all_rows.get("triage_priority_distribution"),
        "completeness_caveats": profile.get("completeness_caveats"),
    }


def to_markdown(profile: dict[str, Any]) -> str:
    """Human-readable Markdown block for embedding in processing_report.md."""
    lines: list[str] = ["## Result Profile", ""]

    if profile.get("degraded"):
        lines.append("Run produced 0 usable rows; profile degraded to failure summary.")
        lines.append("")
        failure = profile.get("failure_summary") or {}
        provider_counts = failure.get("provider_status_counts") or {}
        if provider_counts:
            lines.append("Provider status counts:")
            for status, count in sorted(provider_counts.items(), key=lambda item: (-item[1], item[0])):
                lines.append(f"- {status}: {count}")
            lines.append("")
        run_status = failure.get("run_status") or ""
        stop_reason = failure.get("stop_reason") or ""
        if run_status:
            lines.append(f"Run status: {run_status}")
        if stop_reason:
            lines.append(f"Stop reason: {stop_reason}")
        lines.append("")
        caveats = profile.get("completeness_caveats") or {}
        if caveats.get("caveat_text"):
            lines.append("Caveats:")
            lines.append(caveats["caveat_text"])
            lines.append("")
        return "\n".join(lines)

    all_rows = profile.get("all_rows") or {}
    verified = profile.get("crossref_verified")

    lines.append(f"Total rows: {all_rows.get('total_rows', 0)}")
    if verified is not None:
        lines.append(f"Crossref-verified rows: {(verified or {}).get('total_rows', 0)}")
    lines.append("")

    year_dist = all_rows.get("year_distribution") or {}
    if year_dist:
        lines.append("Year distribution (all rows):")
        for year, count in sorted(year_dist.items()):
            lines.append(f"- {year}: {count}")
        lines.append("")

    top_journals = all_rows.get("top_journals") or []
    if top_journals:
        lines.append("Top journals (all rows, top 10):")
        for journal, count in top_journals[:10]:
            lines.append(f"- {journal}: {count}")
        lines.append("")
        if verified:
            verified_journals = (verified.get("top_journals") or [])[:10]
            if verified_journals:
                lines.append("Top journals (Crossref-verified, top 10):")
                for journal, count in verified_journals:
                    lines.append(f"- {journal}: {count}")
                lines.append("")

    high_cited = all_rows.get("high_cited") or []
    if high_cited:
        lines.append("High-cited papers (top 10 by cited_by_count):")
        for entry in high_cited:
            title = entry.get("title") or ""
            doi = entry.get("doi") or ""
            cited = entry.get("cited_by_count", 0)
            lines.append(f"- {cited} citations — {title} ({doi})")
        lines.append("")

    priority_dist = all_rows.get("triage_priority_distribution") or {}
    if priority_dist:
        lines.append("Triage priority distribution (all rows):")
        for priority, count in sorted(priority_dist.items(), key=lambda item: (-item[1], item[0])):
            lines.append(f"- {priority}: {count}")
        lines.append("")

    oa_rate = all_rows.get("oa_rate")
    if oa_rate is not None:
        lines.append(f"OA rate: {oa_rate:.2%}")
    abstract_cov = all_rows.get("abstract_coverage")
    if abstract_cov is not None:
        lines.append(f"Abstract coverage: {abstract_cov:.2%}")
    doi_cov = all_rows.get("doi_coverage")
    if doi_cov is not None:
        lines.append(f"DOI coverage: {doi_cov:.2%}")
    if oa_rate is not None or abstract_cov is not None or doi_cov is not None:
        lines.append("")

    caveats = profile.get("completeness_caveats") or {}
    if caveats.get("caveat_text"):
        lines.append("Completeness caveats:")
        lines.append(caveats["caveat_text"])
        lines.append("")
        lines.append("Note: these are search-process completeness signals (failures, rate limits).")
        lines.append("Litminer cannot claim result completeness (field coverage).")
        lines.append("")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a result profile JSON for a Litminer run.")
    parser.add_argument("--output-dir", type=Path, required=True, help="Litminer run output directory")
    parser.add_argument("--output", type=Path, default=None, help="Output path (defaults to <output-dir>/result_profile.json)")
    args = parser.parse_args()
    triaged = args.output_dir / "triaged_candidates.csv"
    trace = args.output_dir / "api_discovery_trace.csv"
    manifest = args.output_dir / "run_manifest.json"
    path = write_profile(triaged, trace, manifest, output_path=args.output)
    print(f"Result profile: {path}")


if __name__ == "__main__":
    main()
