# Runbook: freshness failure, stale source

Written on 2026-09-07, the day INC-002 opened, rather than at the end of the
project. Everything below was true of a real failure, not an imagined one.

## What the alert means

The newest hour landed for this series is older than
`FRESHNESS_MAX_AGE_DAYS` (currently 10, set in `quality/checks.py`).

It does **not** mean the load was incomplete. A stale series usually passes row
count, hour gaps and null rate, because the weeks it did return are perfectly
formed. Completeness and freshness are different properties. That is the whole
reason this check exists.

## Why 10 days

SMARD publishes weekly files, and ingestion deliberately skips the current,
still-being-written week. So immediately before a new week closes, the newest
hour on disk is legitimately about seven days old. Ten gives headroom for a late
publication without hiding a dead feed. If this threshold is changed, change it
here and in `quality/checks.py` in the same commit, and say why.

## Likely causes, most common first

1. **The series is closed.** The upstream stopped publishing it entirely and is
   never going to resume. This is INC-002: Germany ceased nuclear generation in
   April 2023, so filter 1224 has no data after that.
2. **The feed paused.** The publisher had an outage, or changed the filter code,
   and will resume. Distinguishable from (1) only by checking the source.
3. **Ingestion stopped running.** The pipeline did not execute at all, so every
   series would be stale together, not just one.
4. **Ingestion ran and failed silently for this series.** Check `ingest_log` for
   `status = 'failed'` rows.

## How to investigate

Start by asking whether this is one series or all of them:

```sql
select series, max(ts_utc) as last_hour
from cleaned_series group by series order by last_hour;
```

One stale series among fresh ones points at cause 1 or 2. Everything stale
together points at cause 3.

Then check whether ingestion even tried:

```sql
select filter_name, status, count(*), max(fetched_at)
from ingest_log group by filter_name, status order by filter_name;
```

Then check what the source actually offers, which is the only way to separate a
closed series from a paused one:

```
https://www.smard.de/app/chart_data/<filter>/DE/index_hour.json
```

If the last timestamp in that index is old, the source is stale, not the
pipeline. The pipeline is doing exactly what it was asked to do, which is fetch
the most recent complete weeks that exist.

## Resolution

**If the series is closed (cause 1):**
Do not delete it from `FILTERS` and do not raise the threshold to make the alert
stop. Both hide the fact rather than record it. Instead:

- keep ingesting it, so the history stays reproducible from raw
- mark the series as closed in the series dimension, with the date it ended
- exclude closed series from metrics through the dimension, never by removing
  them from ingestion
- leave the incident open, or close it with `status = 'accepted'` and a note that
  the staleness is expected and permanent

**If the feed paused (cause 2):** leave the incident open, re-run ingestion when
the source resumes, and let the gate close the loop by passing.

**If ingestion did not run (cause 3):** fix the schedule. This is an
orchestration failure, not a data failure, and every series will be affected.

**If ingestion failed for this series (cause 4):** read the `message` column in
`ingest_log` for the actual error before doing anything else.

## What must not be done

- **Do not widen the threshold to silence the alert.** The number is a decision
  with a reason. If the reason has changed, change the reason first.
- **Do not backfill from another source** to make the series look current. Two
  sources for one series is how two versions of one number begin.
- **Do not publish the series anyway.** A stale number that renders on a
  dashboard is worse than a gap, because a gap is visibly a gap.

## Who decides

The series owner, recorded in `semantic/metrics.yaml` for any metric that
depends on it. If no metric depends on the series, the platform owner decides.

## Closing the incident

Set `closed_at` and `status` in the `incident` table, and add the resolution to
`process/incidents.md`. An incident closed in the database but not written up is
an incident nobody can learn from later, and the agent reads the written record.
