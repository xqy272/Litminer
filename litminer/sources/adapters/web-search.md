# WebSearch Adapter

## Purpose

Use WebSearch as a supplemental discovery and gap-filling channel after API and
publisher-page routes have been considered. It can surface missing titles,
repository pages, publisher pages, or DOI clues through a different network
path.

WebSearch is not the source of truth.

## Do Not Use For

- Default discovery when OpenAlex, Semantic Scholar, arXiv, Europe PMC, or MCP
  wrappers work.
- Final DOI/title/year/journal verification.
- Journal metric verification.
- Final experimental or article-specific values.
- PDF parsing.

## Use Cases

1. API recall is low for a narrow named concept.
2. Crossref title lookup failed and a DOI clue may exist online.
3. A publisher page is hard to locate from existing metadata.
4. The environment cannot reach APIs and WebSearch is the only available lead
   generator.

## Procedure

Start from the specific gap:

```text
"exact paper title" DOI
site:publisher.example "concept phrase" "2026"
"concept phrase" "journal name" "DOI"
```

Record:

- query
- result title
- URL
- snippet
- visible DOI/year/journal clue
- why the result is useful

Then verify through Crossref and publisher pages before promotion.

## Boundary

WebSearch snippets can guide follow-up work, but they should not populate final
metadata, journal metrics, or requested article-level fields. Keep WebSearch
rows clearly tagged until verified.
