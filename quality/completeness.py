"""
MetricTrace, fact level completeness.

Every check in quality/checks.py looks at one series against its own history.
That is structurally blind to a whole series being absent from a period: no
series is late, short, null heavy or out of schema, because the series simply
is not there. INC-003 was exactly that shape, and it was caught by eye rather
than by a control.

This check looks at the fact table instead of at a series. For each period it
asks a different question: of the series that were alive at that time, how many
actually contributed?

"Alive" matters. A series that had not started publishing yet is not missing,
it is absent for a good reason, and treating those two the same would make the
check fire on the first week of every warehouse and be switched off within a
fortnight. So a series counts as expected for a period only when that period
falls inside the span between its first and last recorded fact. Anything absent
inside its own span is a genuine hole.

Three verdicts per period:

    complete    every expected series contributed
    partial     some contributed, some did not. This is the dangerous one,
                because an aggregate over a partial period returns a confident
                number that is understated in an unknown direction
    empty       nothing contributed. Already safe: semantic/engine.py returns
                NULL rather than zero, see convention 7

Run from the repo root:
    python -m quality.completeness
    python -m quality.completeness --grain hour
    python -m quality.completeness --open-incidents
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import duckdb

from config import DB_PATH

# --- threshold, and why ------------------------------------------------------

# A period missing a single series out of thirteen still produces a number that
# looks entirely plausible, so there is no tolerance band here at all: one
# expected series absent from its own lifespan is a partial period. The reason
# to have no tolerance is that the failure mode is silent. A loud check that
# fires occasionally is cheaper than a quiet aggregate that is wrong by an
# unknown amount.
MAX_MISSING_SERIES = 0

GRAINS = {
    "hour":  "date_trunc('hour', ts_utc)",
    "day":   "date_trunc('day', ts_utc)",
    "week":  "date_trunc('week', ts_utc)",
    "month": "date_trunc('month', ts_utc)",
}

COMPLETE, PARTIAL, EMPTY = "complete", "partial", "empty"


@dataclass
class PeriodResult:
    period_start: str
    expected: int
    present: int
    missing: list
    verdict: str

    def line(self) -> str:
        mark = {COMPLETE: "ok  ", PARTIAL: "PART", EMPTY: "none"}[self.verdict]
        tail = ("  missing: " + ", ".join(self.missing)) if self.missing else ""
        return (f"{mark}  {self.period_start:19}  "
                f"{self.present:2}/{self.expected:2} series{tail}")


def series_names(con) -> dict:
    """series_key is a surrogate. Incidents and runbooks are written about
    series names, so resolve once and report in names throughout."""
    return {r[0]: r[1] for r in con.execute(
        "SELECT series_key, series_name FROM dim_series").fetchall()}


def series_spans(con) -> dict:
    """First and last recorded fact per series. A series is only expected to
    contribute inside this window."""
    rows = con.execute("""
        SELECT series_key, min(ts_utc), max(ts_utc)
        FROM fact_series_hourly
        GROUP BY series_key
    """).fetchall()
    return {r[0]: (r[1], r[2]) for r in rows}


def check(con, grain: str = "day") -> list[PeriodResult]:
    if grain not in GRAINS:
        raise KeyError(f"unknown grain {grain}, known: {', '.join(GRAINS)}")
    bucket = GRAINS[grain]

    spans = series_spans(con)
    if not spans:
        return []
    names = series_names(con)

    rows = con.execute(f"""
        SELECT {bucket} AS period_start,
               min(ts_utc) AS lo,
               max(ts_utc) AS hi,
               list(DISTINCT series_key) AS present
        FROM fact_series_hourly
        GROUP BY 1 ORDER BY 1
    """).fetchall()

    out = []
    for period_start, lo, hi, present in rows:
        present = set(present)
        expected = {s for s, (first, last) in spans.items()
                    if first <= hi and last >= lo}
        missing = sorted(names.get(k, str(k)) for k in (expected - present))

        if not present:
            verdict = EMPTY
        elif len(missing) > MAX_MISSING_SERIES:
            verdict = PARTIAL
        else:
            verdict = COMPLETE

        out.append(PeriodResult(str(period_start)[:19], len(expected),
                                len(present), missing, verdict))
    return out


def log_results(con, grain: str, results: list[PeriodResult]) -> None:
    """Every result is recorded, pass and fail, the same as every other check.
    Only logging failures makes it impossible to answer whether the check ran."""
    con.execute("""
        CREATE TABLE IF NOT EXISTS completeness_log (
            run_at        TIMESTAMP,
            grain         VARCHAR,
            period_start  TIMESTAMP,
            expected      INTEGER,
            present       INTEGER,
            missing       VARCHAR,
            verdict       VARCHAR
        )
    """)
    con.execute("DELETE FROM completeness_log WHERE grain = ?", [grain])
    for r in results:
        con.execute(
            "INSERT INTO completeness_log VALUES (now(), ?, ?, ?, ?, ?, ?)",
            [grain, r.period_start, r.expected, r.present,
             ", ".join(r.missing), r.verdict])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--grain", default="day", choices=list(GRAINS))
    ap.add_argument("--open-incidents", action="store_true",
                    help="open an incident for each series missing from its own lifespan")
    ap.add_argument("--quiet", action="store_true", help="show only partial and empty periods")
    args = ap.parse_args()

    con = duckdb.connect(str(DB_PATH))
    results = check(con, args.grain)
    if not results:
        print("no facts in the warehouse, nothing to check")
        return 0

    for r in results:
        if args.quiet and r.verdict == COMPLETE:
            continue
        print(r.line())

    counts = {v: sum(1 for r in results if r.verdict == v)
              for v in (COMPLETE, PARTIAL, EMPTY)}
    print(f"\n{len(results)} {args.grain}(s): {counts[COMPLETE]} complete, "
          f"{counts[PARTIAL]} partial, {counts[EMPTY]} empty")

    log_results(con, args.grain, results)
    print(f"written  completeness_log, grain {args.grain}")

    partial = [r for r in results if r.verdict == PARTIAL]
    if partial:
        culprits = sorted({s for r in partial for s in r.missing})
        print(f"\n{len(partial)} partial period(s). Series absent from their own "
              f"lifespan: {', '.join(culprits)}")
        print("An aggregate over these periods is understated and looks plausible.")

        if args.open_incidents:
            from quality.gate import open_incident_if_new, next_incident_id   # noqa: F401
            from datetime import datetime, timezone
            opened = []
            for s in culprits:
                n = sum(1 for r in partial if s in r.missing)
                inc = open_incident_if_new(
                    con, datetime.now(timezone.utc), s, "completeness",
                    f"{s} is absent from {n} {args.grain}(s) inside its own "
                    f"recorded lifespan. Aggregates over those periods are "
                    f"understated.")
                if inc:
                    opened.append(f"{inc} {s}")
            print("opened: " + (", ".join(opened) if opened else "none, already open"))

    con.close()
    return 1 if partial else 0


if __name__ == "__main__":
    raise SystemExit(main())
