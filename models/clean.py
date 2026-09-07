"""
MetricTrace, raw to cleaned.

Reads the immutable landed JSON and produces typed rows. Two rules govern
everything here:

  1. Never drop a row silently. A row that fails validation goes to the
     quarantine table with a reason, so "where did it go" is answerable.
  2. Rebuild from raw every time. Raw is immutable, so a full rebuild is
     idempotent by construction and there is no incremental state to get wrong.
     At this volume that is a second of work and it removes a whole class of bug.

Timestamps are stored in UTC, per docs/decisions/0001-grain-and-timezone.md.

Run:
    python -m models.clean
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import duckdb

RAW_DIR = Path("data/raw")
DB_PATH = Path("data/warehouse.db")

HOUR_MS = 3_600_000
WEEK_MS = 7 * 24 * HOUR_MS

# Which series count as what. This lives here only until the series dimension
# exists in models/dimensional.py, which is where it belongs.
RENEWABLE = {"wind_onshore", "wind_offshore", "solar", "hydro", "biomass",
             "other_renewable"}
LOAD = {"total_consumption"}

DDL = """
DROP TABLE IF EXISTS cleaned_series;
CREATE TABLE cleaned_series (
    ts_utc       TIMESTAMP,
    filter_code  VARCHAR,
    series       VARCHAR,
    region       VARCHAR,
    resolution   VARCHAR,
    value        DOUBLE,
    week_ts      BIGINT,
    source_file  VARCHAR,
    loaded_at    TIMESTAMP
);

DROP TABLE IF EXISTS quarantine;
CREATE TABLE quarantine (
    ts_raw       BIGINT,
    filter_code  VARCHAR,
    series       VARCHAR,
    region       VARCHAR,
    value_raw    VARCHAR,
    week_ts      BIGINT,
    source_file  VARCHAR,
    reason       VARCHAR,
    loaded_at    TIMESTAMP
);
"""


def series_name(filter_code: str) -> str:
    """Resolve a readable name from the ingest log rather than duplicating a map."""
    return NAME_BY_CODE.get(filter_code, f"unknown_{filter_code}")


def validate(ts_ms, value, week_ts: int) -> str | None:
    """Return a quarantine reason, or None if the row is good."""
    if not isinstance(ts_ms, int):
        return "timestamp_not_integer"
    if ts_ms % HOUR_MS != 0:
        return "timestamp_not_on_hour"
    if not (week_ts <= ts_ms < week_ts + WEEK_MS):
        return "timestamp_outside_declared_week"
    if value is None:
        return "null_value"
    if not isinstance(value, (int, float)):
        return "value_not_numeric"
    return None


def clean() -> None:
    con = duckdb.connect(str(DB_PATH))

    global NAME_BY_CODE
    NAME_BY_CODE = dict(
        con.execute(
            "select distinct filter_code, filter_name from ingest_log "
            "where status = 'ok'"
        ).fetchall()
    )

    con.execute(DDL)
    now = datetime.now(timezone.utc)

    good: list[tuple] = []
    bad: list[tuple] = []
    restated_seen: list[str] = []

    for path in sorted(RAW_DIR.rglob("*.json")):
        stem = path.stem

        # Restated weeks are landed beside the original with a suffix. Reconciling
        # them is a real decision that has not been made yet, so they are skipped
        # and reported rather than quietly preferred in either direction.
        if "__restated_" in stem:
            restated_seen.append(str(path))
            continue

        # data/raw/<filter>/<region>/<resolution>/<week_ts>.json
        resolution = path.parent.name
        region = path.parent.parent.name
        filter_code = path.parent.parent.parent.name
        week_ts = int(stem)
        name = series_name(filter_code)

        payload = json.loads(path.read_text(encoding="utf-8"))
        for row in payload.get("series", []):
            ts_ms, value = row[0], row[1]
            reason = validate(ts_ms, value, week_ts)
            if reason:
                bad.append((ts_ms if isinstance(ts_ms, int) else None, filter_code,
                            name, region, str(value), week_ts, str(path), reason, now))
            else:
                good.append((datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc),
                             filter_code, name, region, resolution, float(value),
                             week_ts, str(path), now))

    if good:
        con.executemany(
            "INSERT INTO cleaned_series VALUES (?,?,?,?,?,?,?,?,?)", good)
    if bad:
        con.executemany(
            "INSERT INTO quarantine VALUES (?,?,?,?,?,?,?,?,?)", bad)

    # The grain assertion. One row per hour, per series, per region.
    dupes = con.execute("""
        select ts_utc, series, region, count(*) as n
        from cleaned_series
        group by ts_utc, series, region
        having count(*) > 1
    """).fetchall()

    print(f"cleaned   {len(good):>6} rows")
    print(f"quarantine{len(bad):>6} rows")
    if bad:
        for reason, n in con.execute(
            "select reason, count(*) from quarantine group by reason order by 2 desc"
        ).fetchall():
            print(f"           {n:>5}  {reason}")
    if restated_seen:
        print(f"skipped   {len(restated_seen):>6} restated files, unreconciled")
    if dupes:
        print(f"GRAIN VIOLATION: {len(dupes)} duplicate (hour, series, region) keys")
    else:
        print("grain     ok, no duplicate (hour, series, region) keys")

    print()
    for r in con.execute("""
        select series, count(*) as n_hours,
               min(ts_utc) as first_hour, max(ts_utc) as last_hour
        from cleaned_series group by series order by max(ts_utc), series
    """).fetchall():
        print(f"{r[0]:20} {r[1]:>5} hours  {r[2]:%Y-%m-%d} .. {r[3]:%Y-%m-%d}")

    con.close()


if __name__ == "__main__":
    clean()
