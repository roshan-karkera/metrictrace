"""
MetricTrace, the quality gate.

Runs the checks, records every result, and decides per series whether the data
may be promoted to the fact table.

Three rules:

  1. A blocked series is not published. The fact table keeps whatever it had.
     Half a week of data is worse than last week's complete data, because a
     partial number looks plausible and a missing one does not.
  2. Every result is written to quality_log, pass or fail. Only recording
     failures makes it impossible to say later whether a check was even running.
  3. A new failure opens an incident automatically. The incident is what the
     agent reads when it is asked why a number moved, so it has to exist without
     anyone remembering to write it.

Run:
    python -m quality.gate
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone

import duckdb

from quality.checks import run_all

DB_PATH = "data/warehouse.db"

DDL = """
CREATE TABLE IF NOT EXISTS quality_log (
    run_id     VARCHAR,
    run_at     TIMESTAMP,
    check_name VARCHAR,
    series     VARCHAR,
    expected   VARCHAR,
    actual     VARCHAR,
    passed     BOOLEAN,
    detail     VARCHAR
);

CREATE TABLE IF NOT EXISTS gate_decision (
    run_id        VARCHAR,
    run_at        TIMESTAMP,
    series        VARCHAR,
    decision      VARCHAR,
    failed_checks VARCHAR
);

CREATE TABLE IF NOT EXISTS incident (
    incident_id VARCHAR,
    opened_at   TIMESTAMP,
    closed_at   TIMESTAMP,
    series      VARCHAR,
    check_name  VARCHAR,
    summary     VARCHAR,
    runbook     VARCHAR,
    status      VARCHAR
);
"""

RUNBOOK_FOR = {
    "freshness":  "process/runbooks/stale_source.md",
    "row_count":  "process/runbooks/row_count_deviation.md",
    "hour_gaps":  "process/runbooks/row_count_deviation.md",
    "null_rate":  "process/runbooks/row_count_deviation.md",
}


def open_incident_if_new(con, run_at, series, check_name, summary) -> str | None:
    """One open incident per (series, check). A recurring failure is the same
    incident until somebody closes it, not a new one every morning."""
    existing = con.execute(
        "select incident_id from incident "
        "where series = ? and check_name = ? and status = 'open'",
        [series, check_name],
    ).fetchone()
    if existing:
        return None

    n = con.execute("select count(*) from incident").fetchone()[0]
    incident_id = f"INC-{n + 2:03d}"   # INC-001 is the manually written one
    con.execute(
        "INSERT INTO incident VALUES (?,?,?,?,?,?,?,?)",
        [incident_id, run_at, None, series, check_name, summary,
         RUNBOOK_FOR.get(check_name, ""), "open"],
    )
    return incident_id


def gate() -> int:
    con = duckdb.connect(DB_PATH)
    con.execute(DDL)

    run_at = datetime.now(timezone.utc)
    run_id = run_at.strftime("%Y%m%dT%H%M%SZ")

    results = run_all(con, now=run_at)

    con.executemany(
        "INSERT INTO quality_log VALUES (?,?,?,?,?,?,?,?)",
        [(run_id, run_at, r.check, r.series, r.expected, r.actual, r.passed, r.detail)
         for r in results],
    )

    # group failures by series
    failures: dict[str, list] = {}
    for r in results:
        if not r.passed and r.series != "all series":
            failures.setdefault(r.series, []).append(r)

    all_series = sorted({r.series for r in results if r.series != "all series"})

    published, blocked, opened = [], [], []
    for series in all_series:
        fails = failures.get(series, [])
        decision = "block" if fails else "publish"
        con.execute(
            "INSERT INTO gate_decision VALUES (?,?,?,?,?)",
            [run_id, run_at, series, decision,
             ",".join(f.check for f in fails)],
        )
        if fails:
            blocked.append(series)
            for f in fails:
                inc = open_incident_if_new(
                    con, run_at, series, f.check,
                    f"{f.check} failed: expected {f.expected}, got {f.actual}")
                if inc:
                    opened.append((inc, series, f.check))
        else:
            published.append(series)

    print(f"run {run_id}")
    print(f"  checks    {sum(r.passed for r in results)} passed, "
          f"{sum(not r.passed for r in results)} failed")
    print(f"  publish   {len(published)} series")
    print(f"  block     {len(blocked)} series" + (f": {', '.join(blocked)}" if blocked else ""))
    for inc, series, check in opened:
        print(f"  OPENED    {inc}  {series} / {check}  -> {RUNBOOK_FOR.get(check, '')}")
    if blocked and not opened:
        print("  (failures are already covered by open incidents, none reopened)")

    con.close()
    return 1 if blocked else 0


if __name__ == "__main__":
    sys.exit(gate())
