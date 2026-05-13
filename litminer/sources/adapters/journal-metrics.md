# Journal Metrics Adapter

## Purpose

Use this adapter when a user asks for journal impact factor, JCR-style metrics,
CiteScore, SJR, quartile filters, or journal ranking filters.

## Core Rule

Do not invent or recall metrics from memory. Journal metrics are time-bound and
source-dependent. Treat them as externally verified data.

## Preferred Input

Use a user-provided or project-provided metrics CSV with these columns:

```text
journal,aliases,issn,impact_factor,metric_year,metric_source,source_url,last_checked,confidence
```

Then run:

```bash
python engine/journal_metrics.py \
  --input work/verified_candidates.csv \
  --output work/metrics_annotated_candidates.csv \
  --metrics work/verified_journal_metrics.csv \
  --min-if 10 \
  --pass-output work/strict_candidates.csv \
  --backup-output work/backup_candidates.csv
```

## Boundaries

- If no verified metric row matches, mark the paper as unverified.
- If using CiteScore or SJR, label the metric by its actual name; do not call it
  impact factor.
- Publisher journal pages are acceptable only when they state metric name,
  metric value, and metric year.
- Journal reputation is not a substitute for a metric value.
