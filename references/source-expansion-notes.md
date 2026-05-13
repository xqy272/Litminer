# Source Expansion Notes

These notes capture implementation lessons for Litminer's current scope:
research information acquisition first, basic automated processing second, LLM
tooling third.

## Lessons From Similar Tools

- Keep a unified Agent-facing search workflow even when sources multiply.
- Keep source connectors modular and registered through explicit capabilities.
- Prefer open/public sources by default; make restricted or paid connectors
  opt-in and non-blocking.
- Preserve provider failures as visible trace rows instead of hiding them.
- Do not mix source retrieval with final scientific judgement.
- Treat public full-text/PDF links as access hints unless the task-specific
  claim is verified on a trusted article surface.

## Current Source Policy

Default discovery stays lightweight with OpenAlex. Optional discovery sources
are enabled when the task calls for them:

- Semantic Scholar: semantic recall and citation/reference expansion.
- arXiv: preprint-heavy fields.
- Europe PMC: biomedical and life-science literature.

Crossref and Unpaywall are not default discovery sources. Crossref verifies
bibliographic metadata, and Unpaywall annotates OA/access hints for DOI-bearing
records.

## Criteria For Adding A New Source

Add a source only when it has:

- a clear evidence role
- a stable API or compliant page route
- a standard row mapping
- rate-limit/backoff behavior
- traceable provider status
- tests for response flattening
- documentation of what the source must not be used for

Potential future additions should be evaluated in this order: PubMed/NCBI
E-utilities, DataCite, OpenAIRE/CORE, DOAJ, Zenodo/HAL, and publisher-specific
metadata APIs where access terms are clear.
