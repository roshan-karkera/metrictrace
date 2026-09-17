"""
MetricTrace, the semantic layer.

Reads semantic/metrics.yaml and computes metrics. This is the only module
permitted to compute a metric. Everything else asks it.

Filters in the YAML are declarative rather than raw SQL, so a metric definition
cannot smuggle in its own filtering logic and drift from what the file says.

Run:
    python -m semantic.engine                       list metrics
    python -m semantic.engine renewable_share       compute it, daily
    python -m semantic.engine total_generation day
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field

import duckdb
import yaml

from config import DB_PATH          # overridable via METRICTRACE_DB, see config.py
METRICS_FILE = "semantic/metrics.yaml"

REQUIRED = ("name", "label", "definition", "grain", "unit", "numerator",
            "owner", "version", "last_changed")


@dataclass
class Metric:
    name: str
    label: str
    definition: str
    grain: str
    unit: str
    numerator: dict
    owner: str
    version: int
    last_changed: str
    denominator: dict | None = None
    denominator_note: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def is_ratio(self) -> bool:
        return self.denominator is not None


def load_metrics(path: str = METRICS_FILE) -> dict[str, Metric]:
    with open(path, encoding="utf-8-sig") as fh:
        raw = yaml.safe_load(fh)

    if not isinstance(raw, list):
        raise ValueError(
            f"{path} must be a list of metric definitions, got {type(raw).__name__}")

    out: dict[str, Metric] = {}
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ValueError(f"{path}: entry {i} is not a mapping")
        missing = [k for k in REQUIRED if k not in entry]
        if missing:
            raise ValueError(
                f"{path}: entry {i} ({entry.get('name', 'unnamed')}) "
                f"is missing required field(s): {', '.join(missing)}")

        d = dict(entry)
        name = d.pop("name")
        if name in out:
            raise ValueError(f"{path}: metric '{name}' is defined twice")

        out[name] = Metric(
            name=name,
            label=d.pop("label"),
            definition=d.pop("definition"),
            grain=d.pop("grain"),
            unit=d.pop("unit"),
            numerator=d.pop("numerator"),
            owner=d.pop("owner"),
            version=d.pop("version"),
            last_changed=str(d.pop("last_changed")),
            denominator=d.pop("denominator", None),
            denominator_note=d.pop("denominator_note", ""),
            extra=d,
        )
    return out


def _filter_sql(spec: dict) -> tuple[str, list]:
    """Build a WHERE fragment from a declarative filter. No SQL comes from YAML."""
    clauses, params = [], []
    if "categories" in spec:
        cats = spec["categories"]
        clauses.append(f"d.category IN ({', '.join('?' * len(cats))})")
        params += cats
    if "series" in spec:
        names = spec["series"]
        clauses.append(f"d.series_name IN ({', '.join('?' * len(names))})")
        params += names
    if not clauses:
        raise ValueError("filter must specify categories or series")
    return " AND ".join(clauses), params


def compute(con, metric: Metric, period: str = "day"):
    """Return rows of (period_start, value). period is 'hour', 'day' or 'month'."""
    if period not in {"hour", "day", "month"}:
        raise ValueError("period must be hour, day or month")
    bucket = "f.ts_utc" if period == "hour" else f"date_trunc('{period}', f.ts_utc)"

    # No ELSE 0. SUM over zero matching rows must return NULL, not 0, so that a
    # period with nothing contributing is undefined rather than a confident zero.
    # See INC-003: the first week of the warehouse held only the load series, and
    # total_generation reported 0 for seven days while every check passed.
    num_where, num_params = _filter_sql(metric.numerator)
    sql = f"""
        SELECT {bucket} AS period_start,
               SUM(CASE WHEN {num_where} THEN f.value END) AS numerator,
               count(DISTINCT CASE WHEN {num_where} THEN f.series_key END) AS num_series
    """
    params = list(num_params) + list(num_params)

    if metric.is_ratio:
        den_where, den_params = _filter_sql(metric.denominator)
        sql += f""",
               SUM(CASE WHEN {den_where} THEN f.value END) AS denominator,
               count(DISTINCT CASE WHEN {den_where} THEN f.series_key END) AS den_series"""
        params += den_params + den_params

    sql += """
        FROM fact_series_hourly f
        JOIN dim_series d ON d.series_key = f.series_key
        GROUP BY 1 ORDER BY 1
    """
    rows = con.execute(sql, params).fetchall()

    if metric.is_ratio:
        # A zero or absent denominator is not a zero share. It is an unanswerable
        # question. An absent numerator with a real denominator IS a true zero.
        out = []
        for period_start, num, _num_series, den, den_series in rows:
            if den_series == 0 or not den:
                out.append((period_start, None))
            else:
                out.append((period_start, (num or 0.0) / den))
        return out

    # Absolute figure. Nothing contributing means undefined, never zero.
    return [(r[0], r[1] if r[2] else None) for r in rows]


def describe(m: Metric) -> str:
    lines = [
        f"{m.label}  ({m.name})",
        f"  definition   {' '.join(m.definition.split())}",
        f"  grain        {m.grain}",
        f"  unit         {m.unit}",
        f"  owner        {m.owner}, version {m.version}, changed {m.last_changed}",
    ]
    if m.denominator_note:
        lines.append(f"  denominator  {' '.join(m.denominator_note.split())}")
    return "\n".join(lines)


if __name__ == "__main__":
    metrics = load_metrics()

    if len(sys.argv) == 1:
        for m in metrics.values():
            print(describe(m))
            print()
        sys.exit(0)

    name = sys.argv[1]
    if name not in metrics:
        sys.exit(f"Unknown metric '{name}'. Known: {', '.join(metrics)}")
    m = metrics[name]
    period = sys.argv[2] if len(sys.argv) > 2 else "day"

    con = duckdb.connect(DB_PATH, read_only=True)
    print(describe(m))
    print()
    for period_start, value in compute(con, m, period):
        if value is None:
            why = "no denominator" if m.is_ratio else "nothing contributing"
            print(f"{period_start:%Y-%m-%d}  {why}, undefined")
        elif m.unit == "ratio":
            print(f"{period_start:%Y-%m-%d}  {value:6.1%}")
        else:
            print(f"{period_start:%Y-%m-%d}  {value:14,.0f}")
    con.close()
