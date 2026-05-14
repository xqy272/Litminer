# Unpaywall Adapter

## Purpose

Use Unpaywall after DOI verification to collect structured open-access status
and access-link hints:

- `is_oa`
- `oa_status`
- best OA landing URL
- best OA PDF URL
- host type
- version
- license

## Do Not Use For

- Topical discovery.
- Bibliographic authority.
- Extracting article-level facts.
- PDF parsing or access-control bypassing.

## Command

```bash
python -m litminer.sources.api.unpaywall_lookup \
  --input .litminer/runs/litminer_run/verified_candidates.csv \
  --output .litminer/runs/litminer_run/oa_annotated_candidates.csv \
  --email "you@example.org"
```

The email can also come from `UNPAYWALL_EMAIL` or `LITMINER_CONTACT_EMAIL`.

## Boundary

Unpaywall links are planning hints. Use them to decide where an Agent should
inspect next, not as evidence for scientific claims.
