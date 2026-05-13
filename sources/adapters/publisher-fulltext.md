# Publisher Page Adapter

## Purpose

Use DOI resolution and publisher landing pages to determine article access,
collect publisher-visible metadata/abstract/article-page text, and record links
to supporting resources such as PDF or supplementary information.

This adapter is for page-level evidence collection. PDF parsing is outside
Litminer core.

## Do Not Use For

- Broad discovery across many papers; use OpenAlex or Semantic Scholar first.
- Journal metric verification unless a verified metric source is recorded.
- Guessing experimental conditions from snippets or inaccessible content.
- Solving CAPTCHAs, bypassing paywalls, or aggressive scraping.

## Procedure

1. Start from the normalized DOI URL:

```text
https://doi.org/<doi>
```

2. Resolve the DOI to the publisher page.
3. Record access status:
   - full HTML visible
   - abstract or landing page only
   - blocked/paywalled
   - server/network error
4. Capture publisher-visible text when available.
5. Record direct `pdf_url` and `si_url` if the page exposes them.
6. Fill only fields supported by visible evidence. Leave unavailable values as
   `Unknown`.

## Evidence Pointers

Use compact pointers that an Agent can audit later:

- `Publisher abstract`
- `Publisher highlights`
- `Methods section`
- `Results section`
- `Table 2`
- `Figure 4 caption`
- `Supplementary information link`

Avoid long quotes. Keep the pointer specific enough to revisit the source.

## Reliability Boundary

Publisher-visible HTML or abstracts can support article-page evidence. Detailed
experimental values should not be inferred from bibliographic database metadata
or WebSearch snippets. If the page only exposes an abstract, mark detailed
fields as `Unknown` unless the abstract explicitly reports them.

## Failure Handling

- If DOI resolution fails, retry later and keep the row in the queue.
- If the publisher page is blocked, record the status and limitation.
- If PDF/SI links exist, record the URLs for downstream tooling.
- If PDF/SI links do not exist, keep `pdf_url` / `si_url` empty and continue.
