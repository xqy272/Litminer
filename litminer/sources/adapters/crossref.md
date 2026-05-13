# Crossref Adapter

## Purpose

Use Crossref for DOI verification, bibliographic metadata, publisher links,
article type, journal/container name, ISSN, and publication date consistency.
Crossref is the bibliographic authority in Litminer.

## Do Not Use For

- Primary topical discovery.
- Journal impact factor verification.
- Article-level experimental or methodological details.
- Final semantic inclusion decisions.

## Commands

Verify one DOI:

```bash
python sources/api/crossref_verify.py --doi "10.1234/example"
```

Verify a candidate CSV:

```bash
python sources/api/crossref_verify.py \
  --input work/candidates.csv \
  --output work/verified_candidates.csv \
  --title-lookup
```

Search by title when a DOI is missing:

```bash
python sources/api/crossref_verify.py \
  --title-search "Machine learning accelerates enzyme stability screening"
```

Be conservative with title recovery. If title, year, or journal context do not
match closely, keep the DOI unresolved and mark the row for review.

## Output To Inspect

- `crossref_doi`
- `crossref_title`
- `crossref_container`
- `crossref_year`
- `crossref_type`
- `crossref_mismatches`
- `crossref_match_status`

Rows with mismatches should stay visible for Agent review.
