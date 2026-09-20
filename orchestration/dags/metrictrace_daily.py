"""
MetricTrace, the daily pipeline as an Airflow DAG.

This was written last on purpose. Every stage below already worked as a plain
function with its own command line, and every one of them was debugged that way.
Wrapping working functions in a scheduler is an hour. Debugging a broken
transform inside a scheduler is not, because the traceback you need is three
layers down inside a worker.

What the scheduler is actually for here, beyond running things at six in the
morning:

  1. It makes the ordering a declared fact instead of a line in a README. The
     gate must run before the dimensional build, because the dimensional build
     reads gate decisions to decide what may be promoted. That was previously
     enforced by a comment in the run order. Here it is an edge, and a run that
     tries to skip it cannot.

  2. It is the first place that has to answer "is this run healthy" rather than
     "did this command exit zero". Those are different questions, and the gate
     is where they come apart. See MAX_BLOCKED_FRACTION below.

Install, and run it somewhere POSIX:
    pip install -r orchestration/requirements-airflow.txt

Airflow does not run on native Windows. The scheduler and executor rely on
POSIX process handling, so a Windows machine needs WSL2 or Docker. Installing
the package on Windows may succeed and then fail at run time, which is worse
than failing at install.

Point Airflow at this folder:
    export AIRFLOW__CORE__DAGS_FOLDER=<repo>/orchestration/dags

Nothing in the pipeline imports Airflow. This file imports the pipeline, never
the other way round, so the whole run order still works from a terminal with
Airflow uninstalled.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

from airflow import DAG
from airflow.exceptions import AirflowFailException

# PythonOperator moved into the standard provider in Airflow 3. Both spellings
# are accepted here rather than pinning the project to one major version,
# because the only thing this file needs from Airflow is an operator that calls
# a Python function, and that has not changed.
try:
    from airflow.providers.standard.operators.python import PythonOperator   # Airflow 3
except ImportError:                                                          # Airflow 2
    from airflow.operators.python import PythonOperator

# The DAGs folder is not the repository root and Airflow does not put it on the
# path. Every module resolves its own paths through config.py, which anchors on
# its own __file__, so the import is the only thing that needs fixing.
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# --------------------------------------------------------------------------
# thresholds, which are decisions (convention 8)
# --------------------------------------------------------------------------

# A blocked series is normal operation, not a failed run. nuclear has been
# blocked since the first day and will stay blocked: it is a closed series whose
# most recent week is January 2024. A DAG that turned red for that would be red
# every night, and nobody reads a light that is always on.
#
# What is not normal is the gate blocking most of the warehouse. At that point
# the gate has stopped protecting anything and is instead reporting that the
# source feed or the gate itself is broken. Convention 3 says a gate that blocks
# everything gets switched off within a week, so the run fails here, loudly,
# while a handful of blocked series is only reported.
MAX_BLOCKED_FRACTION = 0.5

# Ingestion is the only stage that depends on somebody else's server being up.
# A SMARD timeout is not a pipeline defect, so it gets retries; a transform that
# raises is a defect, so it does not.
INGEST_RETRIES = 3
DEFAULT_RETRIES = 0


# --------------------------------------------------------------------------
# stages
# --------------------------------------------------------------------------

def task_ingest(**_) -> None:
    from ingest.smard import ingest
    ingest(weeks=8)


def task_clean(**_) -> None:
    from models.clean import clean
    clean()


def task_gate(**_) -> None:
    """
    Run the gate, then ask the question the exit code cannot answer.

    gate() returns 1 whenever any series is blocked. From a terminal that is
    useful: it tells the person who typed the command to go and look. From a
    scheduler it is wrong, because it cannot tell a single expected block from
    the source feed disappearing. So the decision is taken from the gate's own
    table instead of from its return value.
    """
    from datetime import datetime, timezone

    import duckdb

    from config import DB_PATH
    from quality.gate import gate

    # Remember when this run began, so the check below can prove it is reading
    # the decisions this run produced and not an older set. Reading the newest
    # run in the table is not the same question, and the difference is not
    # theoretical: a gate run that records no decisions at all leaves the
    # previous run as the newest, and a health check that reads it declares the
    # pipeline healthy on the strength of yesterday's verdict. That is how this
    # task reported "12 published, 1 blocked, 8 percent" against an empty
    # warehouse, which is the exact failure it exists to prevent.
    started_at = datetime.now(timezone.utc)

    gate()   # writes quality_log and gate_decision, opens incidents

    con = duckdb.connect(str(DB_PATH))
    try:
        row = con.execute(
            "SELECT run_id, max(run_at) FROM gate_decision "
            "GROUP BY run_id ORDER BY 2 DESC LIMIT 1").fetchone()
        if row is None or row[1] is None or row[1] < started_at.replace(tzinfo=None):
            raise AirflowFailException(
                "the gate recorded no decisions for this run. The newest rows in "
                "gate_decision predate it, so there is nothing to judge and "
                "nothing downstream may run. The usual cause is an empty cleaned "
                "layer, which means the failure is upstream of the gate.")
        run_id = row[0]
        rows = con.execute(
            "SELECT decision, count(*) FROM gate_decision "
            "WHERE run_id = ? GROUP BY decision", [run_id]).fetchall()
        blocked_series = [r[0] for r in con.execute(
            "SELECT series FROM gate_decision "
            "WHERE run_id = ? AND decision = 'block' ORDER BY series",
            [run_id]).fetchall()]
    finally:
        con.close()

    counts = dict(rows)
    total = sum(counts.values())
    blocked = counts.get("block", 0)

    fraction = blocked / total
    print(f"gate run {run_id}: {total - blocked} published, {blocked} blocked "
          f"({fraction:.0%})")
    if blocked_series:
        print("  blocked: " + ", ".join(blocked_series))

    if fraction > MAX_BLOCKED_FRACTION:
        raise AirflowFailException(
            f"{blocked} of {total} series blocked ({fraction:.0%}), above the "
            f"{MAX_BLOCKED_FRACTION:.0%} limit. This is not a per series "
            f"incident. Either the source feed is broken or the gate is, and "
            f"the incidents it just opened are noise until that is settled. "
            f"Blocked: {', '.join(blocked_series)}")


def task_dimensional(**_) -> None:
    from models.dimensional import build
    build()


def task_lineage(**_) -> None:
    from models.lineage import build, verify

    import duckdb
    from config import DB_PATH

    build()
    con = duckdb.connect(str(DB_PATH))
    try:
        problems = verify(con)
    finally:
        con.close()
    if problems:
        raise AirflowFailException(
            "lineage verification failed, so impact analysis cannot be trusted "
            "and process/change.py would approve changes on a wrong blast "
            "radius:\n  " + "\n  ".join(problems))


def task_completeness(**_) -> None:
    """
    Partial is the dangerous verdict (convention 12): a series absent from
    inside its own recorded lifespan leaves every aggregate over that period
    understated and still plausible. There is no tolerance band, and unlike a
    blocked series this one has no automatic containment, so the run fails.
    """
    import duckdb

    from config import DB_PATH
    from quality.completeness import PARTIAL, check, log_results

    con = duckdb.connect(str(DB_PATH))
    try:
        results = check(con, "day")
        if not results:
            raise AirflowFailException(
                "no facts in the warehouse, so completeness checked nothing. "
                "The dimensional build ran but published no rows.")
        log_results(con, "day", results)
        partial = [r for r in results if r.verdict == PARTIAL]
        print(f"{len(results)} days checked, {len(partial)} partial")
    finally:
        con.close()

    if partial:
        culprits = sorted({s for r in partial for s in r.missing})
        raise AirflowFailException(
            f"{len(partial)} partial day(s). Absent from their own lifespan: "
            f"{', '.join(culprits)}. Aggregates over those days are understated "
            f"and look fine. Run `python -m quality.completeness "
            f"--open-incidents` to record it.")


def task_eval(**_) -> None:
    """
    The golden set runs as a subprocess, and that is deliberate.

    agent/eval points the agent at a fixture warehouse by setting
    METRICTRACE_DB in the process environment. Convention 11 says nothing else
    may set it. Running the eval in the same worker process that just wrote to
    the real warehouse makes that a promise rather than a guarantee, and one
    forgotten cleanup would send a later task to the fixture without saying so.
    A separate process cannot make that mistake.

    Its fixtures set gate state explicitly, so this stage does not depend on
    anything upstream succeeding. It is here to catch the agent answering when
    it should have refused.
    """
    result = subprocess.run(
        [sys.executable, "-m", "agent.eval.run"],
        cwd=str(ROOT), capture_output=True, text=True)
    print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    if result.returncode != 0:
        raise AirflowFailException(
            "the golden set failed. The agent's answers moved without the "
            "definitions moving, which is the case the evaluation exists for.")


# --------------------------------------------------------------------------
# the DAG
# --------------------------------------------------------------------------

with DAG(
    dag_id="metrictrace_daily",
    description="Ingest SMARD, clean, gate, build facts and lineage, then check",
    # Six in the morning UTC. The grain is a day and SMARD publishes whole weeks,
    # so there is nothing to gain from running it hourly.
    schedule="0 6 * * *",
    start_date=datetime(2026, 9, 1),
    # Ingestion always re-fetches the last eight weeks and is idempotent, proved
    # by running it twice. Catching up would re-fetch the same eight weeks once
    # per missed day and land nothing new.
    catchup=False,
    max_active_runs=1,
    default_args={
        "owner": "metrictrace",
        "retries": DEFAULT_RETRIES,
        "retry_delay": timedelta(minutes=5),
        "execution_timeout": timedelta(minutes=30),
    },
    tags=["metrictrace", "portfolio"],
) as dag:

    ingest = PythonOperator(
        task_id="ingest_smard", python_callable=task_ingest,
        retries=INGEST_RETRIES, retry_delay=timedelta(minutes=10),
        doc_md="Fetch eight weeks of thirteen series. Idempotent: a week "
               "already landed with the same hash is skipped, a restatement "
               "lands beside it and is reported, never preferred.")

    clean = PythonOperator(
        task_id="clean", python_callable=task_clean,
        doc_md="Validate and type the raw JSON. A row that fails validation "
               "goes to quarantine with a reason, never silently away.")

    gate = PythonOperator(
        task_id="quality_gate", python_callable=task_gate,
        doc_md="Run the checks, publish or block per series, open incidents. "
               "Fails the run only when the blast radius says the gate itself "
               "is the problem.")

    dimensional = PythonOperator(
        task_id="build_facts", python_callable=task_dimensional,
        doc_md="Promote published series into the fact table. Blocked series "
               "keep whatever facts they already had.")

    lineage = PythonOperator(
        task_id="build_lineage", python_callable=task_lineage,
        doc_md="Rebuild the column level lineage table and verify it. Change "
               "impact analysis reads this, so a wrong graph is worse than none.")

    completeness = PythonOperator(
        task_id="completeness", python_callable=task_completeness,
        doc_md="Fact level, not per series: which days are missing a series "
               "that should have been there.")

    evaluation = PythonOperator(
        task_id="golden_set", python_callable=task_eval,
        doc_md="Twenty cases over five fixtures, including the ones where the "
               "right answer is a refusal. Runs against fixtures, not the "
               "warehouse, so it is independent of everything above.")

    # The one ordering that is not obvious and is not optional: the gate writes
    # the decisions the dimensional build reads.
    ingest >> clean >> gate >> dimensional >> lineage >> completeness

    # Independent by design. It tests the agent against fixtures, so a bad load
    # today must not hide a regression in refusal behaviour.
    evaluation
