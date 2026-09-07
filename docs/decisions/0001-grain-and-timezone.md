# 0001. Grain and timezone

Status: accepted
Date: 2026-09-07

## Context

SMARD publishes one time series per generation type and per load measure, in
weekly files at hourly resolution, for the DE market area. Thirteen series are
ingested. Evidence from the first eight weeks of ingestion, 104 week-files:

- every file contains exactly 168 rows, which is 24 x 7
- zero nulls observed across all 104 files
- timestamps arrive as epoch milliseconds

## Grain

One row of the fact table is: **one hour, for one series, in one region.**

A series is either a generation type (solar, wind onshore, brown coal and so on)
or a load measure (total consumption). Both share the grain, which is why they
can live in one fact table.

Anything that would require two rows for the same hour, series and region is a
grain violation and must be rejected by the quality gate, not deduplicated
silently.

## Dimensions kept

- **series** (13 values): the generation type or load measure, carrying its
  SMARD filter code, a readable name, and a flag for whether it counts as
  renewable, conventional, or load. The renewable flag is what `renewable_share`
  depends on, so it belongs in the dimension, not in the metric SQL.
- **region** (1 value today, DE): kept as a dimension even though only one value
  exists, because SMARD also publishes control zones and neighbouring countries,
  and adding a dimension later is a migration while widening one is not.
- **date and hour**: a standard date dimension, plus hour of day.

## Dimensions deliberately dropped

- **Control zones** (50Hertz, Amprion, TenneT, TransnetBW). They would multiply
  the row count by five for a question this project does not ask. The region
  dimension is where they would go if that changes.
- **Neighbouring countries** (AT, LU and the combined market areas). Same reason.
- **Resolutions other than hourly.** Quarter-hourly quadruples the volume;
  daily and above are aggregations this model can compute itself. Storing a
  pre-aggregated copy alongside the detail is how two versions of one number
  start to disagree.

## Nulls

Zero nulls were observed in the ingestion window, so a null is **not normal** for
this source and is treated as a signal rather than as missing-by-design.

Nulls are still permitted at the raw and cleaned layers, because SMARD publishes
recent hours before every operator has reported and those cells arrive empty.
They are not permitted in the fact table. A null value at the fact layer means
one of two things and the pipeline must distinguish them:

- the hour has not been reported yet, which is expected near the leading edge and
  is why the current week is never ingested
- the hour is genuinely missing, which is a data quality failure

The quality gate records the null count per load. A non-zero null rate on a
settled week is a failure, not a warning.

## Timezone

SMARD returns epoch milliseconds, which is unambiguous. Germany observes daylight
saving, so in local time one hour is duplicated in late October and one hour does
not exist in late March.

**Decision: store UTC in the warehouse and convert to Europe/Berlin only at the
presentation edge.**

Reason: the duplicated October hour is two distinct hours with two distinct
values. If local time is the storage key, those two rows collide and one is lost,
or they are stored as a pair the model cannot tell apart. In UTC they are simply
two consecutive hours and nothing special happens. The cost is that a business
user reading the warehouse directly sees UTC, which is a documentation problem
rather than a correctness problem.

Consequences:

- The October transition week has **169** hours in local time and 168 in UTC.
  The row count check must expect 168 and must not treat 169 local hours as an
  anomaly when displayed.
- The March transition week has **167** local hours. The dashboard will show a
  gap at 02:00 on that day. This is correct and should be labelled, not filled.
- Any "daily" aggregate is a local-day aggregate and therefore contains 23, 24 or
  25 hours depending on the date. The daily grain is defined in Europe/Berlin,
  and this is written down here so that a 25 hour day is never treated as a bug.

## Related finding

The nuclear series returns 168 complete rows with zero nulls, and its most recent
available week is from January 2024, because Germany ceased nuclear generation in
April 2023. Row count and null checks pass on it. Only a **freshness** check
catches it. See process/incidents.md INC-001.

This is the reason freshness is a required check rather than an optional one:
a source can be complete, well formed and three years stale at the same time.
