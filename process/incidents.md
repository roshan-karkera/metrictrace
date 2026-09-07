# Incident log

The agent reads this file. A human note about a known outage is the difference
between a correct answer and a confident lie.

## INC-001. Nuclear series silently stale by more than two years

- **Date detected:** 2026-09-07
- **Detected by:** manual review of `ingest_log` after widening the filter set
  from one series to thirteen
- **Series affected:** `nuclear` (SMARD filter 1224)
- **Symptom:** the load looked healthy. Eight weeks ingested, 168 rows each,
  zero nulls, status `ok` on every row. Identical in every respect to the twelve
  other series except one column.
- **Actual state:** its most recent available week was 2024-01-22, while every
  other series returned weeks up to 2026-08-31.
- **Cause:** not a defect. Germany ceased nuclear generation in April 2023, so
  the series is closed. The ingestion asks for "the most recent complete weeks
  this filter has", and for a closed series those are historical weeks. The code
  did exactly what it was told.
- **Why no check caught it:** row count passed, null rate passed, schema was
  unchanged. Completeness and freshness are different properties, and only the
  first was being tested.
- **Resolution:** freshness promoted to a required check in the quality gate.
  A load whose maximum week start is more than N days behind the ingestion date
  is blocked and logged, regardless of how complete it looks.
- **Decision on the series itself:** kept in the filter set deliberately, as a
  permanent real example of a source that is complete and stale at once. It is
  excluded from `renewable_share` by the series dimension rather than by being
  removed from ingestion.
- **Runbook:** process/runbooks/stale_source.md
