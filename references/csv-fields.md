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
| `crossref_authors` | crossref | Verified | Structured Crossref authors serialized as `Family, Given; ...`. |
| `crossref_publisher` | crossref | Verified | Crossref publisher metadata. |
| `crossref_year` | crossref | Verified | Crossref publication year. |
| `crossref_type` | crossref | Verified | Article type metadata. |
| `crossref_volume` | crossref | Verified | Volume used by canonical export when trusted. |
| `crossref_issue` | crossref | Verified | Issue used by canonical export when trusted. |
| `crossref_pages` | crossref | Verified | Page range used by canonical export when trusted. |
| `crossref_abstract` | crossref | Verified | Crossref abstract when present; HTML tags are removed. |
| `crossref_status` | crossref | Debug | `verified`, `title_recovered`, `mismatch`, `lookup_failed`, `skipped_budget`, provider errors, etc. |
| `crossref_verified` | crossref | Debug | String boolean for trusted Crossref status. |
| `crossref_mismatches` | crossref | Debug | Real metadata field mismatches only; operational failures belong in status/error fields. |
| `crossref_error_code` | crossref | Debug | Machine-readable operational or lookup failure code. |

## Canonical Bibliography Fields

| Field | Stage | Trust | Notes |
|-------|-------|-------|-------|
| `paper_id` | canonicalize | Verified/Discovery | Stable DOI identity when available; otherwise a deterministic candidate hash. |
| `entry_type` | canonicalize | Verified/Discovery | Normalized `article`, `conference`, `preprint`, `book`, `book_chapter`, or `generic`. |
| `title` | canonicalize | Verified/Discovery | Selected by explicit source priority; see `field_provenance_json`. |
| `authors` | canonicalize | Verified/Discovery | Canonical semicolon-separated authors. |
| `publication_year` | canonicalize | Verified/Discovery | Canonical four-digit year when extractable. |
| `journal` | canonicalize | Verified/Discovery | Canonical journal/container. |
| `doi` | canonicalize | Verified/Discovery | Normalized DOI; Crossref wins only in a trusted verification state. |
| `url` | canonicalize | Queue | DOI resolver when DOI exists, otherwise best permitted page pointer. |
| `trusted_bibliography` | canonicalize | Debug | True only for trusted Crossref states. |
| `retraction_status` | canonicalize | Debug | Retraction/update status preserved independently of relevance. |
| `export_eligible` | canonicalize | Debug | Default RIS/BibTeX eligibility: trusted, titled, and not retracted. |
| `field_provenance_json` | canonicalize | Debug | Source field, trust class, and selection reason for every canonical value. |

Scientific fields such as `triage_priority` and `scientific_review_needed`
remain separate projections; canonical bibliographic selection never upgrades
scientific relevance.

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
| `attempts` | provider runtime | Debug | Highest attempt number observed for the logical provider call. |
| `request_count` | provider runtime | Debug | Actual HTTP attempt count for the logical call. |
| `provider_wait_seconds` | provider runtime | Debug | Provider-wide scheduler delay before the call. |
| `transient_error` | discovery | Debug | Whether retry may succeed later. |
| `cache_status` | discovery | Debug | Provider-failure cache status. |
| `next_action` | discovery | Debug | Agent-facing recovery hint. |
