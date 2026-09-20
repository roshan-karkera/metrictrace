# Orchestration

One DAG, `metrictrace_daily`, in `dags/metrictrace_daily.py`.

## Why this came last

Every stage in it already worked as a plain function with its own command line,
and every one of them was debugged that way. Wrapping working functions in a
scheduler took an hour. Debugging a broken transform inside a scheduler does
not, because the traceback you need is three layers down inside a worker
process.

So the order was deliberate, and the run order in `CLAUDE.md` still works with
Airflow uninstalled. This file imports the pipeline. The pipeline never imports
this file.

## What the scheduler added that a shell script would not

**The ordering became a declared fact.** `quality.gate` must run before
`models.dimensional`, because the dimensional build reads gate decisions to
decide which series may be promoted. That used to be enforced by the order of
lines in a README. It is now an edge, and a run cannot skip it.

**It forced the question "is this run healthy", which is not the same question
as "did that command exit zero".** The gate is where those two come apart:

`gate()` returns 1 whenever any series is blocked. From a terminal that is
useful, it tells the person who typed the command to go and look. From a
scheduler it is wrong, because it cannot tell an expected block from the source
feed disappearing. `nuclear` has been blocked since the first day and will stay
blocked: it is a closed series whose most recent week is January 2024. A DAG
that turned red for that would be red every night, and nobody reads a light that
is always on.

So the task reads the gate's own decision table instead of its return value, and
applies one threshold, `MAX_BLOCKED_FRACTION = 0.5`: a handful of blocked series
is reported and the run continues, more than half the warehouse blocked fails
the run. At that point the gate has stopped protecting anything and is reporting
that either the source or the gate itself is broken, which is convention 3.

Against every gate run recorded in this warehouse:

    20260907T115951Z   1/13 blocked    8%   pass, report the blocked series
    20260917T113342Z  13/13 blocked  100%   fail the run
    20260919T084304Z  13/13 blocked  100%   fail the run

The first is the day the pipeline was healthy and only `nuclear` was out. The
other two are this machine's warehouse going stale, where every series fails
freshness. The rule separates them, which is the whole point of having one.

**It found a bug that only a scheduler could find.** `ingest/smard.py` set its
own `RAW_DIR` and `DB_PATH` relative to the working directory, against
convention 11. That was invisible for as long as the only caller was a terminal
sitting in the repository root. An Airflow worker runs from somewhere else, and
the module would have quietly landed a second warehouse there. It now reads both
from `config.py` like everything else.

## Stage behaviour

| Task | Fails the run when |
|---|---|
| `ingest_smard` | SMARD is unreachable after 3 retries, 10 minutes apart. A timeout is somebody else's server, not a pipeline defect, so it is the only stage that retries. |
| `clean` | Validation raises. A row that merely fails validation goes to quarantine with a reason, which is not a failure. |
| `quality_gate` | No decisions recorded at all, or more than half the series blocked. See above. |
| `build_facts` | The build raises. Blocked series keep whatever facts they already had. |
| `build_lineage` | `verify()` reports a problem. A wrong lineage graph is worse than no graph, because `process/change.py` would approve changes against a wrong blast radius. |
| `completeness` | Any day is partial. Convention 12: a series absent from inside its own lifespan leaves aggregates understated and plausible, and unlike a blocked series there is no automatic containment. |
| `golden_set` | Any of the 20 cases fails, including the ones whose right answer is a refusal. |

`golden_set` has no upstream dependency on purpose. Its fixtures set gate state
explicitly, so a bad load today must not hide a regression in the agent's
refusal behaviour.

## Running it

**Not on native Windows.** Airflow needs POSIX process handling; use WSL2 or
Docker. The install may succeed on Windows and then fail at run time, which is
worse than failing at install.

The constraints URL carries both the Airflow version and your Python version,
and it is the usual first stumble: Airflow 2.x published no constraints file
for Python 3.13, so a 2.10.5 URL returns 404 on a 3.13 interpreter.

    pip install -r orchestration/requirements-airflow.txt \
      --constraint "https://raw.githubusercontent.com/apache/airflow/constraints-3.3.2/constraints-3.13.txt"

    export AIRFLOW_HOME=$(pwd)/.airflow
    export AIRFLOW__CORE__DAGS_FOLDER=$(pwd)/orchestration/dags
    export AIRFLOW__CORE__LOAD_EXAMPLES=False
    airflow db migrate
    airflow dags reserialize          # Airflow 3 serves the DAG list from the database

One run, in one process, with the traceback in front of you, which is the right
first run and needs no scheduler or webserver:

    airflow dags test metrictrace_daily

Then, if you want the scheduler and the UI:

    airflow standalone

The DAG imports `PythonOperator` from the standard provider on Airflow 3 and
falls back to the old path on Airflow 2, because the only thing it needs from
Airflow is an operator that calls a Python function, and that has not changed.

## What the first real run found

It was run under Airflow 3.3.2 against a copy of the warehouse whose `data/raw`
was missing, so ingestion landed nothing and the cleaned layer came out empty.
The DAG reported the pipeline **healthy**: "12 published, 1 blocked, 8 percent".

That was a bug in this file, not in the pipeline. `task_gate` asked for the
newest run in `gate_decision`, which is not the same question as "what did the
run I just started decide". A gate run that records no decisions at all leaves
the previous run as the newest, so the health check read yesterday's verdict and
passed. The one check written specifically to stop a green light over a broken
warehouse was the thing producing it.

The task now records the time before calling `gate()` and refuses anything older
than that. The same run then failed where it should, and said so:

    the gate recorded no decisions for this run. The newest rows in
    gate_decision predate it, so there is nothing to judge and nothing
    downstream may run. The usual cause is an empty cleaned layer, which
    means the failure is upstream of the gate.

`build_facts`, `build_lineage` and `completeness` were correctly left unrun,
which is the dependency chain doing its job. `golden_set` ran anyway, which is
the independence it was given on purpose.

## What has been verified, and what has not

Verified: the DAG serializes under Airflow 3.3.2 with no import errors, the
scheduler computes its next run, `airflow dags test` executes the tasks in the
declared order, the gate's staleness and blast radius rules both fire, and a
failed upstream task stops everything that depends on it.

Not verified: a run on a complete warehouse under a live scheduler and executor.
The verification above ran against a copy with no raw data, so ingestion and the
stages after the gate have not been exercised this way. Say that rather than
implying otherwise.
