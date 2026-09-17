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

## INC-003. Total generation reported zero for seven days while every check passed

- **Date detected:** 2026-09-13
- **Detected by:** first end to end run of `agent/graph.py`. The agent reported
  the daily range as "0 to 1,475,914" and a minimum of zero for an absolute
  generation figure is not physically possible in the DE market area.
- **Series affected:** none individually. The defect is in the semantic layer.
- **Symptom:** `total_generation` returned exactly `0.0` for 2026-07-06 through
  2026-07-12, seven consecutive days, alongside plausible values for the
  remaining fifty six days.
- **Actual state:** the warehouse begins with one week containing only the
  `load` series. The twelve generation series start on 2026-07-13, when the
  filter set was widened from one series to thirteen. For that first week there
  were no generating rows at all.
- **Cause:** `semantic/engine.py` built its aggregate as
  `SUM(CASE WHEN <filter> THEN value ELSE 0 END)`. With no matching rows that
  sums a column of zeros and returns `0.0`, not `NULL`. A period with nothing
  contributing was therefore indistinguishable from a period in which the
  country generated no electricity.
- **Why no check caught it:** every existing check operates on a series against
  its own history. No series was missing rows, late, null heavy or out of
  schema. The absence was of whole series from an early period, which is a
  property of the fact table rather than of any one series, and nothing was
  looking at that.
- **Effect on published figures:** the seven zeros were included in aggregates.
  Mean daily generation was reported as 1,036,565 when the correct figure over
  the periods that have data is 1,166,135. An understatement of 12.5 percent,
  reported with no indication that anything was wrong.
- **Resolution:** the `ELSE 0` removed. An aggregate over zero contributing rows
  now returns `NULL` and the period is reported as undefined. Convention 7 in
  CLAUDE.md, which already said a zero denominator is not a zero, is extended to
  absolute figures: nothing contributing is not zero either.
- **Note on the ratio:** `renewable_share` was never wrong here. Its denominator
  was also zero for that week, which already produced `None` under the old code.
  The rule had been applied to ratios and not to sums.
- **Still open:** no check yet asserts that a period contains the expected number
  of series. INC-003 was caught by eye on the first run of a new component, which
  is not a control. A completeness check at fact table level is the follow up.

---

## INC-016. Two different findings answering to the same incident number

- **Opened:** 2026-09-17, by inspection after a gate run.
- **Status:** closed.
- **Detected by:** reading `select incident_id from incident` next to this file
  and finding `INC-003` in both, describing different things.
- **Cause:** `quality/gate.py` allocated identifiers as
  `INC-{count(*) + 2}`. That is not an identifier, it is a row count wearing
  one. It assumed the table is the only register of incidents and that nothing
  is ever closed or removed. This file is the second register, written by hand
  for findings no automated check could have raised, and it had already claimed
  `INC-001` and `INC-003`.
- **Effect:** the hand written INC-003, the undefined versus zero finding, and
  an auto opened freshness incident on `biomass` shared a name. Any reference to
  INC-003 in a commit message, runbook or agent answer became ambiguous.
- **Resolution:** identifiers are now allocated from the high water mark across
  both registers, by `next_incident_id` in `quality/gate.py`, and are never
  reused. The twelve auto opened incidents that had collided were shifted up by
  one so that INC-003 belongs to this file again.
- **Note:** the underlying error is one an incident process is supposed to
  prevent, which is why it is written up here rather than quietly patched. A
  process that cannot name its own findings unambiguously is not yet a process.

---

## INC-017. A trust verdict the code could describe but never reach

- **Opened:** 2026-09-17, while building `agent/eval`.
- **Status:** closed.
- **Detected by:** the `solar_incident` fixture, which publishes every series and
  leaves one incident open. The expected verdict was `partial`. The platform
  returned `ok` for both metrics.
- **Cause:** `agent/trust.py` looked up open incidents with
  `open_incidents(con, sorted(held))`, scoping the query to series the gate had
  held. When nothing is held that list is empty, so no incident is ever found,
  so the branch that degrades a verdict to `partial` because an incident is open
  could not execute. The reason string for that branch, "All inputs published,
  but an incident is still open against them", described a state the function
  was structurally incapable of reporting.
- **Effect:** a warehouse where every series passed the gate but a human had
  raised and not yet closed an incident was reported as fully trustworthy. This
  is the exact case the incident register exists for: a fault that the checks
  cannot see and a person has recorded.
- **Resolution:** incidents are now looked up for every contributing series
  rather than only the held ones.
- **Note:** the verdict logic had been read several times without this being
  noticed, because reading code confirms what it says and not what it can reach.
  It took a fixture that asserted an expected verdict to surface it, which is
  the argument for the evaluation set existing at all.
