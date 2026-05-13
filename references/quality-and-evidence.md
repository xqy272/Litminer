# Quality and Evidence Rules

Litminer's goal is to give an Agent a professional research information
acquisition layer plus the basic processing tools needed to handle the retrieved
information. It should make facts easier to verify and candidates easier to
review, not pretend to make final scientific judgement by itself.

## Non-Negotiable Rules

- Do not fabricate DOI, journal metrics, article type, publication year, or
  publisher-page evidence.
- Keep unavailable values as `Unknown`, `Not verified`, or explicit empty queue
  fields.
- Keep task-specific semantic tags separate from metadata hard facts.
- Prefer tagging and sorting over deleting rows.
- WebSearch-only information remains tentative until verified through Crossref
  and publisher pages.

## Semantic Triage

Use `litminer.engine.semantic_triage` for script-assisted review. The Agent supplies
concepts based on the user's request:

```bash
python -m litminer.engine.semantic_triage \
  --input candidates.csv \
  --output triaged.csv \
  --required-concept "main=term1|term2" \
  --optional-concept "secondary=term3|term4" \
  --negative-concept "negative=term5|term6"
```

Rules:

- Required/optional/negative concepts are runtime inputs, not project defaults.
- Negative concepts create `matched_negative` and `semantic_tags`; they do not
  automatically remove rows.
- `triage_priority` is a review priority, not final inclusion.
- `hard_filter_flags` is reserved for metadata facts such as missing DOI, year
  outside range, Crossref mismatch, or journal metric failure.
- The Agent should inspect `triaged_candidates.csv` before final inclusion.

## Automated Processing

Basic automated processing is part of Litminer core because raw search results
are too noisy for efficient Agent reasoning.

Allowed processing:

- DOI normalization and metadata health checks
- deduplication
- Crossref verification and mismatch marking
- OA/access hint annotation
- source and provider distribution summaries
- triage priority summaries
- publisher-page queue generation
- compact reports such as `processing_report.md`

Not allowed:

- final scientific inclusion decisions
- invented missing values
- treating snippets or OA links as article-level evidence
- hiding failed or uncertain rows from the Agent

## Evidence Channels

| Channel | Evidence Level | Use |
|---------|----------------|-----|
| OpenAlex / Semantic Scholar | Bibliographic candidate evidence | Discovery and recall. |
| Crossref | Verified metadata evidence | DOI, title, journal, year, article type. |
| Unpaywall | OA/access-link evidence | OA status and structured landing/PDF links. |
| Publisher landing page / HTML | Article-page evidence | Access status, abstract, visible article sections, links to SI/PDF. |
| Journal metrics CSV | Verified metric evidence | IF/JCR-style filtering when table is trusted. |
| WebSearch | Supplemental clue | Must be verified before final use. |

PDF URLs and SI URLs may be recorded, but PDF reading is not part of Litminer
core.

## Failure Handling

| Problem | Litminer behavior |
|---------|-------------------|
| API unavailable | Log the limitation, use another API or WebSearch supplement if needed. |
| DOI missing | Try Crossref title lookup; otherwise mark `missing_doi`. |
| Crossref mismatch | Keep row, mark `crossref_mismatch`, require Agent review. |
| Unpaywall unavailable | Keep DOI-verified row; mark OA status unknown or skipped. |
| IF not verified | Mark `metric_unverified`; do not guess. |
| IF below threshold | Mark `metric_fail`; keep in backup output. |
| Publisher page unreachable | Mark access status and keep limitation visible. |
| Too few rows meet hard filters | Report actual counts and limiting filters. |

## Mode Guidance

Use a light path for small, metadata-heavy questions. Use the full workflow when
the user asks for many papers, strict filters, or a table requiring article-page
evidence.

Light path:

- API discovery
- Crossref spot verification
- Inline answer with source pointers

Full path:

- API discovery
- Dedupe
- Semantic triage
- Crossref verification
- Unpaywall OA/access-link annotation when configured
- Journal metric annotation if requested
- Publisher-page queue
- Feasibility, processing, and validation reports
