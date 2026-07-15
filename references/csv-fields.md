# Litminer CSV Field Dictionary

This dictionary lists common CSV fields, the stage that creates or stabilizes
them, and how an Agent should treat them.

## Trust Levels

| Level | Meaning |
|-------|---------|
| Discovery | Provider metadata; useful for recall, not final evidence. |
| Verified | Crossref or deterministic validation supports the field. |
| Triage | Litminer ranking/tagging signal; requires human/Agent review. |
| Queue | Pointer for later page inspection, not an extracted claim. |
| Debug | Operational status and recovery metadata. |

## Candidate And Discovery Fields

| Field | Stage | Trust | Notes |
|-------|-------|-------|-------|
| `title` | discovery/import | Discovery | Candidate title from source or input. |
| `doi` | discovery/import | Discovery | Verify through Crossref before citing as fact. |
| `publication_year` | discovery/import | Discovery | May lag for very recent papers. |
| `journal` | discovery/import | Discovery | May differ from Crossref container. |
| `abstract` | discovery/import | Discovery | Used for triage; not final article evidence. |
| `landing_page_url` | discovery/import | Queue | Optional page target when DOI is missing or unavailable. |
| `discovery_source` | discovery/import | Debug | Source of candidate row. |
| `merged_discovery_sources` | dedupe | Debug | Sources combined during dedupe. |
| `provider_query` | discovery | Debug | Effective provider-specific query after transparent compilation. |
| `provider_query_mode` | discovery | Debug | For arXiv: `plain_and_compiled`, `advanced_raw`, or `empty`. |
| `europe_pmc_url` | Europe PMC discovery | Debug | Europe PMC/PubMed-adjacent record page; keep as provenance, not the primary article link when DOI or publisher URL exists. |

## Crossref Fields

| Field | Stage | Trust | Notes |
|-------|-------|-------|-------|
| `crossref_doi` | crossref | Verified | Prefer over discovery DOI when present. |
| `crossref_title` | crossref | Verified | Bibliographic title from Crossref. |
| `crossref_container` | crossref | Verified | Journal/container metadata. |
| `crossref_year` | crossref | Verified | Crossref publication year. |
| `crossref_type` | crossref | Verified | Article type metadata. |
| `crossref_status` | crossref | Debug | `verified`, `title_recovered`, `mismatch`, `lookup_failed`, `skipped_budget`, provider errors, etc. |
| `crossref_verified` | crossref | Debug | String boolean for trusted Crossref status. |
| `crossref_mismatches` | crossref | Debug | Real metadata field mismatches only; operational failures belong in status/error fields. |
| `crossref_error_code` | crossref | Debug | Machine-readable operational or lookup failure code. |

## Verification Queue Fields

| Field | Stage | Trust | Notes |
|-------|-------|-------|-------|
| `verification_queue_rank` | verification queue | Debug | Final deterministic position before Crossref budget is spent. |
| `verification_lane` | verification queue | Debug | Relevance tier + DOI/title lookup lane; metadata-blocked rows are demoted. |
| `verification_reason` | verification queue | Debug | Human-readable explanation of lane selection. |

## Triage Fields

| Field | Stage | Trust | Notes |
|-------|-------|-------|-------|
| `triage_priority` | triage | Triage | `high`, `medium`, `needs_review`, or `low`. |
| `triage_score` | triage | Triage | Ranking score, not scientific proof. |
| `triage_reasons` | triage | Triage | Explain why the row was ranked. |
| `matched_required` | triage | Triage | Required concept matches. |
| `matched_required_evidence` | triage | Triage | Field and text snippet supporting each required match. |
| `matched_optional` | triage | Triage | Optional concept matches. |
| `matched_optional_evidence` | triage | Triage | Field and text snippet supporting each optional match. |
| `matched_negative` | triage | Triage | Negative tags; not automatic deletion. |
| `matched_negative_evidence` | triage | Triage | Field and text snippet supporting each negative match. |
| `candidate_status` | triage | Triage | Review state for downstream queueing. |
| `metadata_status` | triage | Triage | Metadata-blocking flags. |
| `bibliographic_status` | triage | Debug | `verified`, `pending_budget`, `pending_provider`, `not_checked`, `mismatch`, `lookup_failed`, etc. |
| `bibliographic_review_needed` | triage | Debug | String boolean; true until bibliography is verified. |
| `scientific_review_needed` | triage | Triage | Scientific semantic ambiguity or negative-concept review need. |
| `workflow_status` | triage | Debug | Next workflow lane such as enrichment, bibliographic verification, identifier recovery, or scientific review. |
| `llm_review_needed` | triage | Triage | Compatibility field aligned with scientific review only. |

## OA And Queue Fields

| Field | Stage | Trust | Notes |
|-------|-------|-------|-------|
| `unpaywall_status` | unpaywall | Debug | OA lookup status. |
| `is_oa` | unpaywall | Discovery | OA hint; verify page claims separately. |
| `best_oa_url` | unpaywall | Queue | Access hint. |
| `best_oa_pdf_url` | unpaywall | Queue | Link hint, not parsed content. |
| `doi_url` | queue | Queue | DOI landing page target. |
| `publisher_url` | queue | Queue | Publisher-visible article page target. |
| `fields_needed` | queue | Queue | What the Agent should inspect. |
| `next_action` | queue/probe | Debug | Operational guidance for next step. |

Queue rule: when Crossref ran, the default full workflow only promotes
bibliographically verified rows into `publisher_queue.csv`. When Crossref was
intentionally disabled, unverified DOI pointers may remain and must not be
described as verified papers.

## Provider Trace Fields

| Field | Stage | Trust | Notes |
|-------|-------|-------|-------|
| `provider` | discovery | Debug | API provider name. |
| `status` | discovery | Debug | Provider-specific status. |
| `status_class` | discovery | Debug | Normalized status class. |
| `http_status` | discovery | Debug | HTTP status when available. |
| `retry_after_seconds` | discovery | Debug | Wait hint for rate limits. |
| `transient_error` | discovery | Debug | Whether retry may succeed later. |
| `cache_status` | discovery | Debug | Provider-failure cache status. |
| `next_action` | discovery | Debug | Agent-facing recovery hint. |
