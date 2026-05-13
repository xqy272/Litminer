# Publisher Page Evidence SOP

This SOP covers publisher landing pages and publisher-visible HTML article
pages. It does not cover PDF parsing.

## Goal

Turn a DOI or publisher URL into auditable page-level evidence:

- resolved publisher URL
- access status
- abstract or visible article text
- task-specific values explicitly visible on the page
- PDF/SI URLs when exposed
- evidence pointers

Unpaywall OA links can help locate an accessible landing page or PDF, but they
are access hints only. Article-level claims still need publisher-visible text,
full-text evidence, or another explicitly inspected source.

## Steps

1. Open the DOI URL or publisher URL.
2. Record the resolved URL and publisher domain.
3. Determine whether the page exposes:
   - abstract only
   - full HTML article text
   - supplementary information link
   - PDF link
   - no useful article content
4. Extract only values that are explicitly visible.
5. For each extracted value, record a short evidence pointer.
6. Leave unsupported task-specific fields as `Unknown`.

## Section Targeting

When full HTML is visible, use semantic sections rather than layout selectors:

- abstract
- highlights or graphical abstract text
- introduction/background
- methods/experimental/procedure
- results/discussion
- tables
- figure captions
- supplementary information links

Publisher page layouts vary, but section headings are usually stable enough for
LLM parsing.

## Evidence Pointer Format

Examples:

- `Publisher abstract`
- `Methods section, paragraph 2`
- `Results section`
- `Table 1`
- `Figure 3 caption`
- `Supplementary information link`

## Boundaries

- Do not infer missing values from database metadata.
- Do not treat WebSearch snippets as final evidence.
- Do not parse PDFs in this workflow.
- Record `pdf_url` and `si_url` for downstream tools when visible.
- Do not bypass access controls.
