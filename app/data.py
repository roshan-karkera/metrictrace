"""
Every warehouse read the app makes, in one place.

Two rules this file exists to keep:

  1. The dashboard opens the warehouse read only and never writes to it. A
     reporting layer that can change the thing it reports on is not a reporting
     layer.
  2. Nothing here computes a metric. `semantic/engine.py` is the only thing
     allowed to do that (convention 6), so a number on a chart and the same
     number in an agent answer cannot come apart.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd
import streamlit as st

from config import DB_PATH

ROOT = Path(__file__).resolve().parents[1]
METRICS_FILE = ROOT / "semantic" / "metrics.yaml"

# How long a cached read stays good. Short, because the point of the trust panel
# is to show the state of the warehouse now, not a minute ago.
TTL = 30


@st.cache_resource
def con():
    return duckdb.connect(str(DB_PATH), read_only=True)


def _has(name: str) -> bool:
    q = "SELECT count(*) FROM information_schema.tables WHERE table_name = ?"
    return con().execute(q, [name]).fetchone()[0] > 0


def _df(sql: str, params: list | None = None) -> pd.DataFrame:
    return con().execute(sql, params or []).df()


# --------------------------------------------------------------------------
# gate and incidents
# --------------------------------------------------------------------------

@st.cache_data(ttl=TTL)
def latest_gate() -> pd.DataFrame:
    """The most recent run only. Older runs are history, not status."""
    if not _has("gate_decision"):
        return pd.DataFrame(columns=["series", "decision", "failed_checks", "run_at"])
    return _df("""
        SELECT series, decision, failed_checks, run_at
        FROM gate_decision
        WHERE run_id = (SELECT run_id FROM gate_decision ORDER BY run_at DESC LIMIT 1)
        ORDER BY decision, series
    """)


@st.cache_data(ttl=TTL)
def gate_history() -> pd.DataFrame:
    if not _has("gate_decision"):
        return pd.DataFrame(columns=["run_id", "run_at", "total", "blocked"])
    return _df("""
        SELECT run_id, max(run_at) AS run_at, count(*) AS total,
               sum(CASE WHEN decision = 'block' THEN 1 ELSE 0 END) AS blocked
        FROM gate_decision GROUP BY run_id ORDER BY run_id
    """)


@st.cache_data(ttl=TTL)
def latest_checks() -> pd.DataFrame:
    if not _has("quality_log"):
        return pd.DataFrame(columns=["check_name", "series", "passed", "expected",
                                     "actual", "detail"])
    return _df("""
        SELECT check_name, series, passed, expected, actual, detail
        FROM quality_log
        WHERE run_id = (SELECT run_id FROM quality_log ORDER BY run_at DESC LIMIT 1)
        ORDER BY check_name, series
    """)


@st.cache_data(ttl=TTL)
def incidents(status: str | None = "open") -> pd.DataFrame:
    cols = ["incident_id", "series", "check_name", "summary", "runbook",
            "opened_at", "closed_at", "status"]
    if not _has("incident"):
        return pd.DataFrame(columns=cols)
    where = "WHERE status = ?" if status else ""
    return _df(f"""
        SELECT incident_id, series, check_name, summary, runbook,
               opened_at, closed_at, status
        FROM incident {where} ORDER BY incident_id
    """, [status] if status else [])


@st.cache_data(ttl=TTL)
def tickets() -> pd.DataFrame:
    cols = ["ticket_id", "raised_at", "raised_by", "metric", "priority",
            "rule", "linked", "status"]
    if not _has("ticket"):
        return pd.DataFrame(columns=cols)
    return _df("""
        SELECT ticket_id, raised_at, raised_by, metric, priority, rule,
               linked, status
        FROM ticket ORDER BY ticket_id
    """)


# --------------------------------------------------------------------------
# what the warehouse actually holds
# --------------------------------------------------------------------------

@st.cache_data(ttl=TTL)
def coverage() -> pd.DataFrame:
    """
    Per series: how much is in the fact table and how old the newest row is.

    The join is a LEFT JOIN from dim_series on purpose. A series the gate has
    held since the first run has no facts at all, and an INNER JOIN would drop
    it from this table entirely. That is exactly backwards: the series with
    nothing published is the one the reader most needs to see.
    """
    if not _has("fact_series_hourly"):
        return pd.DataFrame()
    return _df("""
        SELECT d.series_name  AS series,
               d.category     AS category,
               count(f.value) AS hours,
               min(f.ts_utc)  AS first_hour,
               max(f.ts_utc)  AS last_hour,
               date_diff('day', max(f.ts_utc), now()::TIMESTAMP) AS days_old
        FROM dim_series d
        LEFT JOIN fact_series_hourly f ON d.series_key = f.series_key
        GROUP BY 1, 2 ORDER BY 6 DESC NULLS FIRST, 1
    """)


@st.cache_data(ttl=TTL)
def completeness() -> pd.DataFrame:
    cols = ["period_start", "expected", "present", "missing", "verdict"]
    if not _has("completeness_log"):
        return pd.DataFrame(columns=cols)
    return _df("""
        SELECT period_start, expected, present, missing, verdict
        FROM completeness_log
        WHERE run_at = (SELECT max(run_at) FROM completeness_log)
          AND grain = 'day'
        ORDER BY period_start
    """)


@st.cache_data(ttl=TTL)
def lineage() -> pd.DataFrame:
    cols = ["source_node", "source_column", "target_node", "target_column",
            "transform", "origin"]
    if not _has("lineage_edge"):
        return pd.DataFrame(columns=cols)
    return _df("""
        SELECT source_node, source_column, target_node, target_column,
               transform, origin
        FROM lineage_edge ORDER BY source_node, target_node
    """)


# --------------------------------------------------------------------------
# metric metadata
# --------------------------------------------------------------------------

@st.cache_data(ttl=TTL)
def metric_names() -> list[str]:
    from semantic.engine import load_metrics
    return list(load_metrics(str(METRICS_FILE)))


def metrics():
    """Not cached: the file is the source of truth and is edited by hand, so a
    change to a definition must show up on the next rerun rather than in 30
    seconds."""
    from semantic.engine import load_metrics
    return load_metrics(str(METRICS_FILE))


def series_behind(metric) -> list[str]:
    """
    Which series a metric's definition selects, resolved through dim_series with
    the same declarative filter the engine uses. Reading it from the definition
    rather than hardcoding it is what stops this drifting from the metric.
    """
    wanted: set[str] = set()
    for spec in (metric.numerator, metric.denominator):
        if not spec:
            continue
        if "categories" in spec:
            marks = ", ".join("?" * len(spec["categories"]))
            rows = con().execute(
                f"SELECT series_name FROM dim_series WHERE category IN ({marks})",
                spec["categories"]).fetchall()
            wanted |= {r[0] for r in rows}
        if "series" in spec:
            wanted |= set(spec["series"])
    return sorted(wanted)


# --------------------------------------------------------------------------
# the one derived judgement the app makes
# --------------------------------------------------------------------------

def platform_state() -> dict:
    """
    One verdict for the whole platform, computed the same way on every page.

    It deliberately mirrors the rule the Airflow DAG applies, so the banner on
    the overview and the red task in the scheduler can never tell two different
    stories.
    """
    gate = latest_gate()
    inc = incidents("open")
    comp = completeness()

    total = len(gate)
    blocked = int((gate["decision"] != "publish").sum()) if total else 0
    fraction = blocked / total if total else 0.0
    partial_days = int((comp["verdict"] == "partial").sum()) if len(comp) else 0

    if total == 0:
        level, headline = "critical", "The gate has never run against this warehouse."
    elif fraction > 0.5:
        level = "critical"
        headline = (f"{blocked} of {total} series are held. Above half the warehouse "
                    f"is the point at which the gate stops being a per series "
                    f"incident and starts being a source or gate failure.")
    elif partial_days:
        level = "serious"
        headline = (f"{partial_days} day(s) are missing a series from inside its own "
                    f"lifespan. Aggregates over those days are understated and look "
                    f"plausible.")
    elif blocked:
        level = "warning"
        headline = (f"{blocked} of {total} series held, which is normal operation. "
                    f"Numbers below exclude them.")
    else:
        level, headline = "good", "All series published, no open quality incidents."

    return {
        "level": level, "headline": headline, "total": total, "blocked": blocked,
        "fraction": fraction, "partial_days": partial_days,
        "open_incidents": len(inc),
        "held": sorted(gate.loc[gate["decision"] != "publish", "series"]) if total else [],
        "last_run": str(gate["run_at"].max())[:16] if total else "never",
    }
