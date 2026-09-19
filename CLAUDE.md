# MetricTrace, working context

Read this before changing anything. It is the handover document, kept current.

## What this is

A small analytics platform run the way a real one has to be run: pipelines that
know when they cannot be trusted, metrics that have exactly one agreed
definition, and a change and incident process that decides what happens when
either breaks.

The demo sentence: **a number moved, the platform tells you why, cites the query
behind every claim, and if the load was incomplete that week it opens an incident
instead of inventing a business reason.**

This is a portfolio project. It has no real users and nothing depends on it.
Say so plainly rather than implying otherwise.

## Three equal pillars, and why they interlock

Not a stack with an AI headline and a documentation footnote. Each pillar is load
bearing for another:

- **Lineage** (data engineering) is what makes change impact analysis possible in
  the process pillar.
- **Metric definitions** (BI) are what the agent retrieves to ground an answer.
- **Incidents** (process) are what the agent reads as evidence. A human note
  about a known outage is the difference between a correct answer and a
  confident lie.

Remove any pillar and the other two stop working.

## Source

German electricity generation and load from SMARD, public, no API key.

    index:  https://www.smard.de/app/chart_data/{filter}/DE/index_hour.json
    series: https://www.smard.de/app/chart_data/{f}/DE/{f}_DE_hour_{week_ts}.json

Thirteen series, hourly, DE market area. Filter codes are in `ingest/smard.py`.

## Current state, verified

| Layer | Module | Status |
|---|---|---|
| Ingestion | `ingest/smard.py` | Working. 13 series x 8 weeks. Idempotent, verified by running twice. |
| Cleaned | `models/clean.py` | Working. 17,640 rows, 0 quarantined, grain asserted. |
| Checks | `quality/checks.py` | Working. 4 checks, 40 results, 39 pass. |
| Gate | `quality/gate.py` | Working. 12 series published, nuclear blocked, INC-002 auto opened. |
| Dimensional | `models/dimensional.py` | Working. 16,296 fact rows, grain ok, nuclear HELD. |
| Semantic | `semantic/metrics.yaml`, `engine.py` | Working. Ratio and absolute paths both run. INC-003 fixed here on 2026-09-13. |
| Lineage | `models/lineage.py` | Working. 36 edges, 14 nodes, verify clean. Build with `python -m models.lineage` and no arguments. |
| Dashboard | `app/dashboard.py` | Built. Trust panel above the numbers, definitions read live from `metrics.yaml`. |
| Agent | `agent/` | Working. Five node graph, all three paths exercised. |
| Completeness | `quality/completeness.py` | Working. Fact level, not per series. 63/63 days complete on 2026-09-19; proved it fails by deleting one series from three days. |
| Change mgmt | `process/change.py` | Working. Refuses approval when meaning changes without a version bump, proved on 2026-09-19. Impact analysis reads the lineage table. |
| Tickets | `process/tickets.py` | Working. Priority set by a written rule, deduplicates against open incidents. TIC-001 correctly P3, TIC-002 P4. |
| BPMN | `process/bpmn/` | Working. Incident and change processes, generated so diagram and code cannot drift. Opens in Camunda Modeler. |
| Evaluation | `agent/eval/` | Working. 20 cases over 5 warehouse fixtures. 20/20 on 2026-09-17, unsafe answer rate 0. Logged to MLflow. |
| Orchestration | `orchestration/dags/` | **Empty on purpose. Airflow comes last.** |

Run order:

    python -m ingest.smard --weeks 8
    python -m models.clean
    python -m quality.gate            # must run before dimensional
    python -m models.dimensional
    python -m models.lineage          # no arguments builds the lineage table
    python -m semantic.engine renewable_share day
    python -m agent.graph "how much electricity was generated per day"
    python -m quality.completeness       # fact level, catches a whole series missing
    python -m agent.eval.run             # 20 case golden set, non zero exit on failure

    python -m process.change status      # blocks a definition change without a version bump
    python -m process.change impact renewable_share
    python -m process.tickets new --metric ... --period ... --where ... --expected ... --saw ... --by ...
    python process/bpmn/generate.py      # regenerate both BPMN models

## The finding that defines the project

**INC-002.** The `nuclear` series returns eight perfect weeks: 168 rows each,
zero nulls, status ok, indistinguishable from the other twelve in every column
except one. Its most recent week is January 2024, because Germany ceased nuclear
generation in April 2023.

Row count passed. Null rate passed. Gap check passed. Only freshness caught it.

This was found in real data on day one, not staged. It is why freshness is a
required check, why nuclear stays in the filter set deliberately, and why the
agent must check trust before attributing anything.

## Decisions already made, do not silently reverse

- **ADR 0001**: grain is one hour, one series, one region. Storage is UTC,
  conversion happens at the presentation edge. A week is exactly 168 hours.
- **ADR 0002**: `renewable_share = renewable / (renewable + conventional)`.
  Pumped storage is its own category and is excluded, because it returns
  electricity generated earlier and would be double counted. Consumption mix is
  a different metric and would get a different name.
- Closed series stay in the category list. They are excluded from current
  numbers by having no published facts, not by being deleted, so history stays
  intact.

## Conventions that must hold

1. **Raw is immutable.** Landed JSON is never edited. Restatements land beside
   the original with a hash suffix and are currently skipped and reported, not
   silently preferred. Reconciling them needs ADR 0003.
2. **Never drop a row silently.** Failed validation goes to `quarantine` with a
   reason and its source file.
3. **A blocked series is not republished** and its existing facts are left
   untouched. Blocking is per series, never per run: a gate that blocks
   everything gets switched off within a week.
4. **Every check result is logged, pass and fail.** Only logging failures makes
   it impossible to answer "was that check even running".
5. **One open incident per (series, check).** A recurring failure is the same
   incident until someone closes it.
6. **Only `semantic/engine.py` computes a metric.** Nothing else. Filters in
   `metrics.yaml` are declarative, never raw SQL, so a definition cannot acquire
   its own WHERE clause and drift.
7. **Nothing contributing returns undefined, not zero.** A zero denominator is
   not a zero share, and an aggregate over zero matching rows is not a zero
   level. Never write `SUM(CASE WHEN ... THEN value ELSE 0 END)`: the `ELSE 0`
   turns an absence into a confident number. See INC-003.
8. **Thresholds are decisions.** Every one lives at the top of
   `quality/checks.py` with its reason written next to it. Changing one means
   changing the reason first, in the same commit.
9. **Runbooks are written the first time a check fails**, not at the end.
10. **Incident identifiers come from `next_incident_id`** and are never reused.
    The `incident` table and `process/incidents.md` share one identifier space.
    See INC-016.
11. **Every module reads `DB_PATH` from `config.py`**, which honours the
    `METRICTRACE_DB` environment variable. Only `agent/eval` sets it, so that
    refusal behaviour can be measured against a fixture without touching the
    real warehouse.
12. **A period is complete, partial or empty, and partial is the dangerous
    one.** A series absent from inside its own recorded lifespan makes every
    aggregate over that period understated while still looking plausible.
    `quality/completeness.py` has no tolerance band for this on purpose.
13. **A metric definition may not change without a version bump.** `version`
    and `last_changed` are printed next to the number by the dashboard and
    cited by the agent. `process/change.py` refuses approval otherwise, and it
    refuses in code rather than in a guideline.
14. **Ticket priority comes from the rule in `process/tickets.py`**, not from
    who reported it. A metric the platform already refuses cannot mislead
    anybody, so it is not a P1.
15. **No em dashes anywhere in this repository.**

## Open items

- **Unit unverified.** The fact column is `value`, not `value_mwh`, because the
  unit SMARD returns has not been confirmed from their documentation. Nothing may
  print a unit until it is. Ratios are unaffected since the unit cancels.
  Blocking for `total_generation`, not for `renewable_share`.
- **Restatement policy.** Needs ADR 0003 the first time SMARD restates a week.
- **Airflow.** Deliberately last. The pipeline works as plain functions; wrapping
  working functions in a scheduler later is an hour. Debugging a broken transform
  inside a scheduler is not.

## Next, in order

1. **Contribution analysis in the dashboard.** Pick a metric and two periods, see
   what drove the change. The lineage table and the semantic layer both exist now,
   so this is assembly rather than design.
2. **Airflow.** Still last, deliberately.

3. **Re-ingest before trusting any current number.** The warehouse on this
   machine holds data to 2026-09-06. Every series now fails freshness, so the
   gate blocks all thirteen and the dashboard will refuse everything. Run
   `python -m ingest.smard --weeks 8` and then the rest of the run order. The
   evaluation is unaffected: its fixtures set gate state explicitly and do not
   depend on the freshness of the source.

## Tone

Write code and documentation the way the existing files do: state the decision,
state the reason next to it, and prefer a plain sentence over a clever one. If a
threshold or a category assignment is a judgement call, say so in the file rather
than leaving the next reader to guess.
