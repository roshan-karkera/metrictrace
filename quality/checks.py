"""
MetricTrace, quality checks.

Each check is a pure function over the cleaned layer. It returns what it
expected, what it found, and a verdict. A check that only says "red" is not
worth running, because the first question anyone asks is "compared to what".

Thresholds live here, at the top, in one place, with the reason written down.
Every one of them is a decision, not a default.

Run:
    python -m quality.checks
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import duckdb

from config import DB_PATH          # overridable via METRICTRACE_DB, see config.py

# --- thresholds, and why -----------------------------------------------------

# SMARD publishes weekly files and ingestion deliberately skips the current,
# still-being-written week. So the newest hour on disk is at most about seven
# days old immediately before a new week closes. Ten days gives headroom for a
# late publication without hiding a genuinely dead source.
FRESHNESS_MAX_AGE_DAYS = 10

# A week at hourly resolution is exactly 168 hours in UTC, including the daylight
# saving weeks. See docs/decisions/0001-grain-and-timezone.md. This is exact,
# not a tolerance, because any other number means something is actually wrong.
HOURS_PER_WEEK = 168

# Zero nulls were observed across the whole ingestion window, so a null is a
# signal rather than normal. Any null on a settled week fails.
MAX_NULL_RATE = 0.0


@dataclass
class CheckResult:
    check: str
    series: str
    expected: str
    actual: str
    passed: bool
    detail: str = ""

    def line(self) -> str:
        mark = "pass" if self.passed else "FAIL"
        return f"{mark:4}  {self.check:14} {self.series:20} expected {self.expected:12} got {self.actual}"


# --- the checks --------------------------------------------------------------

def check_freshness(con, now: datetime | None = None) -> list[CheckResult]:
    """A source can be complete, well formed and years out of date. See INC-001."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=FRESHNESS_MAX_AGE_DAYS)
    out = []
    for series, last_hour in con.execute(
        "select series, max(ts_utc) from cleaned_series group by series order by series"
    ).fetchall():
        age = (now - last_hour.replace(tzinfo=timezone.utc)).days
        out.append(CheckResult(
            check="freshness",
            series=series,
            expected=f"<= {FRESHNESS_MAX_AGE_DAYS}d old",
            actual=f"{age}d old (last hour {last_hour:%Y-%m-%d %H:%M})",
            passed=last_hour.replace(tzinfo=timezone.utc) >= cutoff,
            detail="source may be closed or the feed may have stopped publishing",
        ))
    return out


def check_row_count(con) -> list[CheckResult]:
    """Every landed week must contain exactly 168 hours. Not roughly."""
    out = []
    for series, week_ts, n in con.execute(f"""
        select series, week_ts, count(*) as n
        from cleaned_series
        group by series, week_ts
        having count(*) <> {HOURS_PER_WEEK}
        order by series, week_ts
    """).fetchall():
        out.append(CheckResult(
            check="row_count", series=series,
            expected=str(HOURS_PER_WEEK), actual=str(n),
            passed=False,
            detail=f"week_ts {week_ts}",
        ))
    if not out:
        n_weeks = con.execute("select count(distinct (series, week_ts)) from cleaned_series").fetchone()[0]
        out.append(CheckResult("row_count", "all series", str(HOURS_PER_WEEK),
                               f"{HOURS_PER_WEEK} in every one of {n_weeks} weeks", True))
    return out


def check_hour_gaps(con) -> list[CheckResult]:
    """168 rows is not the same as 168 consecutive hours. Check the gaps too."""
    out = []
    rows = con.execute("""
        select series,
               count(*) as n,
               date_diff('hour', min(ts_utc), max(ts_utc)) + 1 as span
        from cleaned_series
        group by series order by series
    """).fetchall()
    for series, n, span in rows:
        out.append(CheckResult(
            check="hour_gaps", series=series,
            expected=f"{n} contiguous", actual=f"span {span}",
            passed=(n == span),
            detail="row count matches span, so no missing hours" if n == span
                   else f"{span - n} hours missing inside the range",
        ))
    return out


def check_null_rate(con) -> list[CheckResult]:
    """Nulls land in quarantine, so this reads the quarantine rather than the values."""
    out = []
    cleaned = dict(con.execute(
        "select series, count(*) from cleaned_series group by series").fetchall())
    quarantined = dict(con.execute(
        "select series, count(*) from quarantine where reason = 'null_value' group by series"
    ).fetchall())
    for series in sorted(cleaned):
        bad = quarantined.get(series, 0)
        total = cleaned[series] + bad
        rate = bad / total if total else 0.0
        out.append(CheckResult(
            check="null_rate", series=series,
            expected=f"<= {MAX_NULL_RATE:.0%}", actual=f"{rate:.2%} ({bad} rows)",
            passed=rate <= MAX_NULL_RATE,
        ))
    return out


def run_all(con, now: datetime | None = None) -> list[CheckResult]:
    return (check_freshness(con, now)
            + check_row_count(con)
            + check_hour_gaps(con)
            + check_null_rate(con))


if __name__ == "__main__":
    con = duckdb.connect(DB_PATH, read_only=True)
    results = run_all(con)
    for r in results:
        print(r.line())
    failed = [r for r in results if not r.passed]
    print()
    print(f"{len(results) - len(failed)} passed, {len(failed)} failed")
    for r in failed:
        print(f"  {r.check} / {r.series}: {r.detail}")
    con.close()
