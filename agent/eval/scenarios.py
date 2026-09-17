"""
Warehouse fixtures for the agent evaluation.

The refusal rule is the most important behaviour in this project and it depends
entirely on warehouse state: which series the gate published, and which
incidents are open. Measuring it therefore means controlling that state.

Each scenario copies the real warehouse to a temporary file and rewrites only
the gate decisions and the incident table. Facts, dimensions and the semantic
layer are untouched, so the figures an answer contains are the real ones. What
changes is whether the platform is allowed to report them.

The scenarios set every series explicitly rather than inheriting whatever the
last real gate run decided. An evaluation whose expected results drift with the
freshness of the source data is not an evaluation.

Nothing here writes to data/warehouse.db.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import duckdb

from config import DEFAULT_DB

RUNBOOK = "process/runbooks/stale_source.md"


def _all_series(con) -> list[str]:
    return [r[0] for r in con.execute(
        "select series_name from dim_series order by 1").fetchall()]


def _set_gate(con, held: set[str]) -> None:
    """Rewrite the most recent gate run so every series has an explicit decision."""
    run_id, run_at = con.execute(
        "select run_id, run_at from gate_decision order by run_at desc limit 1"
    ).fetchone()
    con.execute("delete from gate_decision")
    for series in _all_series(con):
        decision = "block" if series in held else "publish"
        con.execute(
            "insert into gate_decision values (?,?,?,?,?)",
            [run_id, run_at, series, decision,
             "freshness" if decision == "block" else None])


def _set_incidents(con, open_for: dict[str, str]) -> None:
    """Close everything, then open one incident per series named here."""
    con.execute("update incident set status = 'closed'")
    n = 900
    for series, check_name in open_for.items():
        n += 1
        con.execute("delete from incident where incident_id = ?", [f"INC-{n:03d}"])
        con.execute(
            "insert into incident values (?,?,?,?,?,?,?,?)",
            [f"INC-{n:03d}",
             con.execute("select run_at from gate_decision limit 1").fetchone()[0],
             None, series, check_name,
             f"Fixture incident for the evaluation: {series} failed {check_name}.",
             RUNBOOK, "open"])


# --------------------------------------------------------------------------
# the scenarios themselves
# --------------------------------------------------------------------------

def _all_clear(con):
    _set_gate(con, held=set())
    _set_incidents(con, {})


def _nuclear_held(con):
    """The real INC-002 shape. Nuclear is conventional, so it sits in the
    denominator of renewable_share and in the numerator of total_generation.
    One held series therefore produces two different verdicts, which is the
    case worth measuring."""
    _set_gate(con, held={"nuclear"})
    _set_incidents(con, {"nuclear": "freshness"})


def _solar_incident(con):
    """Everything published, but an incident is still open. Tests that an open
    incident degrades a verdict on its own, without any series being held."""
    _set_gate(con, held=set())
    _set_incidents(con, {"solar": "row_count"})


def _all_held(con):
    _set_gate(con, held=set(_all_series(con)))
    _set_incidents(con, {})


def _no_gate(con):
    """Nothing has ever been certified. Not the same as everything failing, and
    the platform has to say so differently."""
    con.execute("delete from gate_decision")
    _set_incidents(con, {})


SCENARIOS = {
    "all_clear":      _all_clear,
    "nuclear_held":   _nuclear_held,
    "solar_incident": _solar_incident,
    "all_held":       _all_held,
    "no_gate":        _no_gate,
}


def build(name: str, into: Path) -> Path:
    """Copy the real warehouse to `into` and apply the named scenario to it."""
    if name not in SCENARIOS:
        raise KeyError(f"unknown scenario {name}, known: {', '.join(SCENARIOS)}")
    into.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(DEFAULT_DB, into)
    con = duckdb.connect(str(into))
    try:
        SCENARIOS[name](con)
    finally:
        con.close()
    return into


if __name__ == "__main__":
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    for name in SCENARIOS:
        db = build(name, tmp / f"{name}.db")
        con = duckdb.connect(str(db), read_only=True)
        gates = con.execute(
            "select decision, count(*) from gate_decision group by 1 order by 1").fetchall()
        opens = con.execute(
            "select count(*) from incident where status = 'open'").fetchone()[0]
        con.close()
        print(f"{name:16s} gate={dict(gates) or 'none'}  open incidents={opens}")
