"""Shared CSV stage schema definitions."""

from __future__ import annotations


STAGE_REQUIRED: dict[str, list[str]] = {
    "candidate": ["title", "publication_year", "journal"],
    "triage": ["triage_priority", "triage_score", "triage_reasons"],
    "metadata": ["title", "doi", "journal", "publication_year"],
    "queue": ["title", "doi", "doi_url", "publisher_url", "fields_needed", "next_action"],
    "preliminary": ["title", "doi", "journal", "publication_year", "evidence_grade", "evidence_pointer"],
}
