"""
MetricTrace, column level lineage.

Lineage stored as a queryable table, not a picture. A diagram tells a human what
was true when somebody drew it. A table lets the change management process and
the agent both ask questions of it.

Two halves:

  DECLARED   Edges below are written by hand, next to the transforms they
             describe. Hand maintained lineage always drifts, so it is not
             trusted on its own.
  VERIFIED   Every declared column is checked against the live warehouse schema,
             and every column in a tracked table is checked for an inbound edge.
             The map cannot silently stop matching reality; it reports.

Metric lineage is not declared. It is derived from semantic/metrics.yaml, so a
new metric appears in lineage the moment it is defined.

Run:
    python -m models.lineage                              build, verify, report
    python -m models.lineage impact fact_series_hourly.value
    python -m models.lineage upstream metric:renewable_share
"""

from __future__ import annotations

import sys
from collections import deque
from datetime import datetime, timezone

import duckdb

sys.path.insert(0, ".")
from semantic.engine import load_metrics   # noqa: E402

from config import DB_PATH          # overridable via METRICTRACE_DB, see config.py

# Tables whose every column should be explainable. If a column here has no
# inbound edge, the map is incomplete and the report says so.
TRACKED = ["cleaned_series", "fact_series_hourly", "dim_series"]

# (source_node, source_column, target_node, target_column, transform)
DECLARED: list[tuple[str, str, str, str, str]] = [
    # raw JSON to cleaned, see models/clean.py
    ("smard_raw", "series[].0",   "cleaned_series", "ts_utc",      "epoch ms to UTC timestamp"),
    ("smard_raw", "series[].1",   "cleaned_series", "value",       "cast to double"),
    ("smard_raw", "path.filter",  "cleaned_series", "filter_code", "parsed from landed path"),
    ("smard_raw", "path.region",  "cleaned_series", "region",      "parsed from landed path"),
    ("smard_raw", "path.resolution", "cleaned_series", "resolution", "parsed from landed path"),
    ("smard_raw", "path.week_ts", "cleaned_series", "week_ts",     "parsed from landed path"),
    ("smard_raw", "path",         "cleaned_series", "source_file", "recorded verbatim"),
    ("ingest_log", "filter_name", "cleaned_series", "series",      "lookup by filter_code"),
    ("clean.py",  "now()",        "cleaned_series", "loaded_at",   "load timestamp"),

    # cleaned to dimension, see models/dimensional.py
    ("cleaned_series", "series",      "dim_series", "series_name", "distinct"),
    ("cleaned_series", "filter_code", "dim_series", "filter_code", "any_value per series"),
    ("cleaned_series", "ts_utc",      "dim_series", "first_hour",  "min per series"),
    ("cleaned_series", "ts_utc",      "dim_series", "last_hour",   "max per series"),
    ("dimensional.py", "CATEGORY",    "dim_series", "category",    "hardcoded map, see ADR 0002"),
    ("gate_decision",  "decision",    "dim_series", "status",      "open if published, else closed_or_blocked"),
    ("dimensional.py", "row_number",  "dim_series", "series_key",  "surrogate key"),
    ("dimensional.py", "now()",       "dim_series", "updated_at",  "build timestamp"),

    # cleaned to fact, gated
    ("cleaned_series", "ts_utc",      "fact_series_hourly", "ts_utc",     "direct"),
    ("cleaned_series", "ts_utc",      "fact_series_hourly", "date_key",   "cast to date"),
    ("cleaned_series", "ts_utc",      "fact_series_hourly", "hour_utc",   "extract hour"),
    ("cleaned_series", "value",       "fact_series_hourly", "value",      "direct, unit unverified, ADR 0002"),
    ("cleaned_series", "source_file", "fact_series_hourly", "source_file","direct"),
    ("dim_series",     "series_key",  "fact_series_hourly", "series_key", "lookup by series name"),
    ("dim_region",     "region_key",  "fact_series_hourly", "region_key", "constant 1, DE only today"),
    ("dimensional.py", "now()",       "fact_series_hourly", "loaded_at",  "build timestamp"),

    # the control path: this is why a blocked series never reaches the fact table
    ("cleaned_series",  "*",        "quality_log",   "passed",   "checks in quality/checks.py"),
    ("quality_log",     "passed",   "gate_decision", "decision", "any failed check blocks the series"),
    ("gate_decision",   "decision", "incident",      "status",   "a new failure opens an incident"),
    ("gate_decision",   "decision", "fact_series_hourly", "*",   "publish gate, blocked series are not written"),
]

DDL = """
DROP TABLE IF EXISTS lineage_edge;
CREATE TABLE lineage_edge (
    source_node   VARCHAR,
    source_column VARCHAR,
    target_node   VARCHAR,
    target_column VARCHAR,
    transform     VARCHAR,
    origin        VARCHAR,   -- declared or derived
    built_at      TIMESTAMP
);
"""


# Everything that reads a metric definition. Without these the map stops at the
# metric and a change to metrics.yaml appears to affect nothing, which is the
# opposite of true: these are the surfaces that change when a definition changes.
CONSUMERS: list[tuple[str, str]] = [
    ("app/dashboard.py", "renders the number, its definition, owner and version"),
    ("agent/graph.py",   "answers questions from it and cites the definition"),
    ("agent/trust.py",   "resolves the contributing series to decide the verdict"),
    ("agent/eval",       "golden set expectations are derived from the definition"),
]


def consumer_edges(con) -> list[tuple]:
    """Derived from CONSUMERS for every metric that exists."""
    edges = []
    for name in load_metrics():
        for node, how in CONSUMERS:
            edges.append((f"metric:{name}", name, node, name, how))
    return edges


def metric_edges(con) -> list[tuple]:
    """Derived, not declared. A new metric appears here as soon as it exists."""
    edges = []
    for m in load_metrics().values():
        node = f"metric:{m.name}"
        edges.append(("fact_series_hourly", "value", node, m.name,
                      "numerator and denominator aggregate"))
        for side, spec in (("numerator", m.numerator), ("denominator", m.denominator)):
            if not spec:
                continue
            if "categories" in spec:
                edges.append(("dim_series", "category", node, m.name,
                              f"{side} filter: {', '.join(spec['categories'])}"))
            if "series" in spec:
                edges.append(("dim_series", "series_name", node, m.name,
                              f"{side} filter: {', '.join(spec['series'])}"))
        edges.append(("semantic/metrics.yaml", m.name, node, m.name,
                      f"definition v{m.version}, owner {m.owner}"))
    # de-duplicate, a metric can reference the same column from both sides
    return sorted(set(edges))


def verify(con) -> list[str]:
    """The map is hand maintained, so check it against the real schema."""
    problems = []

    real: dict[str, set[str]] = {}
    for (tbl,) in con.execute(
            "select table_name from information_schema.tables "
            "where table_schema = 'main'").fetchall():
        real[tbl] = {c for (c,) in con.execute(
            "select column_name from information_schema.columns "
            "where table_name = ?", [tbl]).fetchall()}

    # 1. every declared column that names a real table must exist on it
    for src, scol, tgt, tcol, _ in DECLARED:
        for node, col in ((src, scol), (tgt, tcol)):
            if node in real and col not in ("*",) and not col.startswith(("path", "series[", "now(", "row_number", "CATEGORY")):
                if col not in real[node]:
                    problems.append(f"declared column does not exist: {node}.{col}")

    # 2. every column of a tracked table must have an inbound edge
    inbound = {(t, c) for _, _, t, c, _ in DECLARED}
    for tbl in TRACKED:
        if tbl not in real:
            problems.append(f"tracked table missing from warehouse: {tbl}")
            continue
        for col in sorted(real[tbl]):
            if (tbl, col) not in inbound and (tbl, "*") not in inbound:
                problems.append(f"column has no declared lineage: {tbl}.{col}")

    return problems


def build() -> None:
    con = duckdb.connect(DB_PATH)
    con.execute(DDL)
    now = datetime.now(timezone.utc)

    con.executemany(
        "INSERT INTO lineage_edge VALUES (?,?,?,?,?,?,?)",
        [(*e, "declared", now) for e in DECLARED])
    derived = metric_edges(con) + consumer_edges(con)
    con.executemany(
        "INSERT INTO lineage_edge VALUES (?,?,?,?,?,?,?)",
        [(*e, "derived", now) for e in derived])

    problems = verify(con)

    n = con.execute("select count(*) from lineage_edge").fetchone()[0]
    print(f"edges      {n} ({len(DECLARED)} declared, {len(derived)} derived from metrics.yaml)")
    nodes = con.execute("""
        select count(*) from (
            select source_node as n from lineage_edge
            union select target_node from lineage_edge)
    """).fetchone()[0]
    print(f"nodes      {nodes}")
    if problems:
        print(f"verify     {len(problems)} PROBLEMS")
        for p in problems:
            print(f"           {p}")
    else:
        print("verify     ok, every declared column exists and every tracked column is explained")
    con.close()


def _walk(con, start: str, direction: str) -> list[tuple[int, str, str]]:
    """BFS through the edge table. start is 'node.column' or 'node'."""
    if "." in start and not start.startswith("metric:"):
        node, col = start.split(".", 1)
    else:
        node, col = start, None

    seen, out = set(), []
    q = deque([(node, col, 0)])
    while q:
        n, c, depth = q.popleft()
        if (n, c) in seen:
            continue
        seen.add((n, c))

        if direction == "down":
            sql = ("select target_node, target_column, transform from lineage_edge "
                   "where source_node = ?")
            params = [n]
            if c:
                sql += " and (source_column = ? or source_column = '*')"
                params.append(c)
        else:
            sql = ("select source_node, source_column, transform from lineage_edge "
                   "where target_node = ?")
            params = [n]
            if c:
                sql += " and (target_column = ? or target_column = '*')"
                params.append(c)

        for nn, nc, tr in con.execute(sql, params).fetchall():
            out.append((depth + 1, f"{nn}.{nc}", tr))
            q.append((nn, nc, depth + 1))
    return out


if __name__ == "__main__":
    if len(sys.argv) == 1:
        build()
        sys.exit(0)

    cmd = sys.argv[1]
    if cmd not in {"impact", "upstream"} or len(sys.argv) < 3:
        sys.exit("usage: python -m models.lineage [impact|upstream] <node.column>")

    target = sys.argv[2]
    con = duckdb.connect(DB_PATH, read_only=True)
    direction = "down" if cmd == "impact" else "up"
    rows = _walk(con, target, direction)
    verb = "affects" if cmd == "impact" else "depends on"
    print(f"{target} {verb}:")
    if not rows:
        print("  nothing recorded")
    seen = set()
    for depth, name, transform in rows:
        if name in seen:
            continue
        seen.add(name)
        print(f"  {'  ' * (depth - 1)}-> {name}    [{transform}]")
    con.close()
