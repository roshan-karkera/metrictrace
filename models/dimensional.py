"""
MetricTrace, cleaned to dimensional.

Builds the star schema at the grain declared in docs/decisions/0001.

The rule that matters here: a series the gate blocked is NOT republished, and
its existing rows are left exactly as they were. The fact table is rebuilt per
series, not wholesale, so a stale or broken source keeps its last known good
data instead of being wiped or refreshed with something worse.

Run:
    python -m quality.gate        (must run first, this reads its decision)
    python -m models.dimensional
"""

from __future__ import annotations

from datetime import datetime, timezone

import duckdb

DB_PATH = "data/warehouse.db"

# Category drives every metric that aggregates across series. It lives in the
# dimension, not in metric SQL, so a metric never has to hardcode a list of
# names. See docs/decisions/0002 for the pumped storage question.
CATEGORY = {
    "wind_onshore":       "renewable",
    "wind_offshore":      "renewable",
    "solar":              "renewable",
    "hydro":              "renewable",
    "biomass":            "renewable",
    "other_renewable":    "renewable",
    "brown_coal":         "conventional",
    "hard_coal":          "conventional",
    "natural_gas":        "conventional",
    "nuclear":            "conventional",
    "other_conventional": "conventional",
    "pumped_storage":     "storage",
    "total_consumption":  "load",
}

DDL = """
CREATE TABLE IF NOT EXISTS dim_series (
    series_key   INTEGER,
    filter_code  VARCHAR,
    series_name  VARCHAR,
    category     VARCHAR,
    status       VARCHAR,
    first_hour   TIMESTAMP,
    last_hour    TIMESTAMP,
    updated_at   TIMESTAMP
);

CREATE TABLE IF NOT EXISTS dim_region (
    region_key   INTEGER,
    region_code  VARCHAR,
    region_name  VARCHAR
);

CREATE TABLE IF NOT EXISTS dim_date (
    date_key     DATE,
    year         INTEGER,
    quarter      INTEGER,
    month        INTEGER,
    day          INTEGER,
    day_of_week  INTEGER,
    is_weekend   BOOLEAN
);

-- Grain: one row per hour, per series, per region. Enforced below.
CREATE TABLE IF NOT EXISTS fact_series_hourly (
    ts_utc       TIMESTAMP,
    date_key     DATE,
    hour_utc     INTEGER,
    series_key   INTEGER,
    region_key   INTEGER,
    value        DOUBLE,
    source_file  VARCHAR,
    loaded_at    TIMESTAMP
);
"""


def published_series(con) -> list[str]:
    """Series the most recent gate run allowed through."""
    run_id = con.execute(
        "select max(run_id) from gate_decision").fetchone()[0]
    if run_id is None:
        raise SystemExit(
            "No gate decision found. Run 'python -m quality.gate' first.\n"
            "The dimensional layer deliberately will not publish data that "
            "has not been through the gate.")
    rows = con.execute(
        "select series from gate_decision where run_id = ? and decision = 'publish' "
        "order by series", [run_id]).fetchall()
    return run_id, [r[0] for r in rows]


def build() -> None:
    con = duckdb.connect(DB_PATH)
    con.execute(DDL)
    now = datetime.now(timezone.utc)

    run_id, allowed = published_series(con)
    all_series = [r[0] for r in con.execute(
        "select distinct series from cleaned_series order by series").fetchall()]
    blocked = [s for s in all_series if s not in allowed]

    # ---- dimensions ---------------------------------------------------------
    # Rebuilt in full every run. They are tiny, and a series whose status has
    # changed from open to closed must be reflected even while its facts are held.
    con.execute("DELETE FROM dim_series")
    for i, s in enumerate(all_series, start=1):
        first, last = con.execute(
            "select min(ts_utc), max(ts_utc) from cleaned_series where series = ?",
            [s]).fetchone()
        con.execute(
            "INSERT INTO dim_series VALUES (?,?,?,?,?,?,?,?)",
            [i, con.execute("select any_value(filter_code) from cleaned_series where series = ?",
                            [s]).fetchone()[0],
             s, CATEGORY.get(s, "unknown"),
             "open" if s in allowed else "closed_or_blocked",
             first, last, now])

    con.execute("DELETE FROM dim_region")
    con.execute("INSERT INTO dim_region VALUES (1, 'DE', 'Germany, DE market area')")

    con.execute("DELETE FROM dim_date")
    con.execute("""
        INSERT INTO dim_date
        SELECT DISTINCT
            CAST(ts_utc AS DATE)              AS date_key,
            EXTRACT(year    FROM ts_utc)      AS year,
            EXTRACT(quarter FROM ts_utc)      AS quarter,
            EXTRACT(month   FROM ts_utc)      AS month,
            EXTRACT(day     FROM ts_utc)      AS day,
            EXTRACT(dow     FROM ts_utc)      AS day_of_week,
            EXTRACT(dow     FROM ts_utc) IN (0, 6) AS is_weekend
        FROM cleaned_series
    """)

    # ---- facts, per published series only -----------------------------------
    keys = dict(con.execute("select series_name, series_key from dim_series").fetchall())

    inserted = {}
    for s in allowed:
        con.execute("DELETE FROM fact_series_hourly WHERE series_key = ?", [keys[s]])
        n = con.execute("""
            INSERT INTO fact_series_hourly
            SELECT
                c.ts_utc,
                CAST(c.ts_utc AS DATE),
                EXTRACT(hour FROM c.ts_utc),
                ?, 1,
                c.value,
                c.source_file,
                ?
            FROM cleaned_series c
            WHERE c.series = ?
        """, [keys[s], now, s]).fetchone()
        inserted[s] = con.execute(
            "select count(*) from fact_series_hourly where series_key = ?",
            [keys[s]]).fetchone()[0]

    # ---- grain assertion ----------------------------------------------------
    dupes = con.execute("""
        select count(*) from (
            select ts_utc, series_key, region_key
            from fact_series_hourly
            group by ts_utc, series_key, region_key
            having count(*) > 1)
    """).fetchone()[0]

    # ---- report -------------------------------------------------------------
    total = con.execute("select count(*) from fact_series_hourly").fetchone()[0]
    print(f"gate run   {run_id}")
    print(f"published  {len(allowed)} series, {sum(inserted.values())} rows refreshed")
    for s in blocked:
        held = con.execute(
            "select count(*) from fact_series_hourly where series_key = ?",
            [keys[s]]).fetchone()[0]
        print(f"HELD       {s}: not republished, {held} existing rows left untouched")
    print(f"fact total {total} rows")
    print("grain      " + ("ok" if dupes == 0 else f"VIOLATION, {dupes} duplicate keys"))
    print()
    for r in con.execute("""
        select d.category, count(distinct d.series_name) as n_series, count(f.ts_utc) as n_rows
        from dim_series d
        left join fact_series_hourly f on f.series_key = d.series_key
        group by d.category order by d.category
    """).fetchall():
        print(f"{r[0]:14} {r[1]} series  {r[2]:>6} fact rows")

    con.close()


if __name__ == "__main__":
    build()
