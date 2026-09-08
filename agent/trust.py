"""
MetricTrace, the trust check.

This runs BEFORE the agent is allowed to think about a question, and it never
calls a model. It answers one thing: given the current state of the warehouse,
may we say anything about this metric at all, and with what caveat.

Kept out of the graph on purpose. The refusal rule is the part of an agent most
likely to be wrong and least likely to be tested, so it lives in a plain module
that can be run and asserted on its own.

Run from the repo root:
    python -m agent.trust                    every metric
    python -m agent.trust renewable_share    one metric
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import duckdb

from semantic.engine import load_metrics, Metric

DB_PATH = ROOT / "data" / "warehouse.db"
METRICS_FILE = ROOT / "semantic" / "metrics.yaml"

OK = "ok"
PARTIAL = "partial"
REFUSE = "refuse"


@dataclass
class TrustReport:
    metric: str
    verdict: str                      # ok, partial or refuse
    reason: str                       # one sentence, written for a human
    numerator_held: list = field(default_factory=list)
    denominator_held: list = field(default_factory=list)
    incidents: list = field(default_factory=list)
    last_gate_run: str = ""

    @property
    def may_answer(self) -> bool:
        return self.verdict != REFUSE

    def render(self) -> str:
        lines = [f"{self.metric}: {self.verdict.upper()}", f"  {self.reason}"]
        if self.numerator_held:
            lines.append(f"  numerator series held: {', '.join(self.numerator_held)}")
        if self.denominator_held:
            lines.append(f"  denominator series held: {', '.join(self.denominator_held)}")
        for inc in self.incidents:
            lines.append(f"  {inc['incident_id']} {inc['series']} / {inc['check_name']}"
                         f" -> {inc['runbook']}")
        if self.last_gate_run:
            lines.append(f"  last gate run: {self.last_gate_run}")
        return "\n".join(lines)


# --------------------------------------------------------------------------
# reading the current state. Nothing here interprets, it only reports.
# --------------------------------------------------------------------------

def _table_exists(con, name: str) -> bool:
    q = "SELECT count(*) FROM information_schema.tables WHERE table_name = ?"
    return con.execute(q, [name]).fetchone()[0] > 0


def latest_gate(con) -> tuple[str, dict]:
    """(run_at, {series: decision}) for the most recent gate run only."""
    if not _table_exists(con, "gate_decision"):
        return "", {}
    rows = con.execute("""
        SELECT series, decision, run_at
        FROM gate_decision
        WHERE run_id = (SELECT run_id FROM gate_decision ORDER BY run_at DESC LIMIT 1)
    """).fetchall()
    if not rows:
        return "", {}
    return str(rows[0][2]), {r[0]: r[1] for r in rows}


def open_incidents(con, series: list) -> list:
    if not series or not _table_exists(con, "incident"):
        return []
    marks = ", ".join("?" * len(series))
    rows = con.execute(f"""
        SELECT incident_id, series, check_name, summary, runbook
        FROM incident
        WHERE status = 'open' AND series IN ({marks})
        ORDER BY opened_at
    """, series).fetchall()
    return [dict(zip(("incident_id", "series", "check_name", "summary", "runbook"), r))
            for r in rows]


def _series_for(con, spec: dict | None) -> list:
    """Resolve one declarative filter to series names, the same way the engine does."""
    if not spec:
        return []
    names = set(spec.get("series", []))
    cats = spec.get("categories")
    if cats:
        marks = ", ".join("?" * len(cats))
        rows = con.execute(
            f"SELECT series_name FROM dim_series WHERE category IN ({marks})", cats
        ).fetchall()
        names |= {r[0] for r in rows}
    return sorted(names)


# --------------------------------------------------------------------------
# the decision
# --------------------------------------------------------------------------

def check(con, metric: Metric) -> TrustReport:
    run_at, decisions = latest_gate(con)

    if not decisions:
        return TrustReport(
            metric=metric.name, verdict=REFUSE,
            reason="No gate run on record. Nothing in the warehouse has been certified, "
                   "so no number from it can be defended.")

    num = _series_for(con, metric.numerator)
    den = _series_for(con, metric.denominator) if metric.is_ratio else []
    inputs = sorted(set(num) | set(den))

    held = {s for s in inputs if decisions.get(s, "unknown") != "publish"}
    num_held = sorted(s for s in num if s in held)
    den_held = sorted(s for s in den if s in held)
    incidents = open_incidents(con, sorted(held))

    if held and held >= set(inputs):
        return TrustReport(metric.name, REFUSE,
                           "Every series behind this metric is held by the gate.",
                           num_held, den_held, incidents, run_at)

    # A ratio missing part of its denominator is not partial, it is wrong in a
    # direction. Removing a conventional source inflates the renewable share and
    # the number still looks entirely plausible. That is the case to refuse.
    if den_held:
        return TrustReport(
            metric.name, REFUSE,
            "Part of the denominator is held, so this ratio would be biased in a "
            "predictable direction while still looking plausible.",
            num_held, den_held, incidents, run_at)

    if num_held:
        return TrustReport(
            metric.name, PARTIAL,
            "Computed without one or more contributing series. The level is "
            "understated and must be reported as partial.",
            num_held, den_held, incidents, run_at)

    if incidents:
        return TrustReport(metric.name, PARTIAL,
                           "All inputs published, but an incident is still open against them.",
                           num_held, den_held, incidents, run_at)

    return TrustReport(metric.name, OK,
                       "All contributing series passed the most recent gate and no "
                       "incident is open against them.",
                       last_gate_run=run_at)


if __name__ == "__main__":
    metrics = load_metrics(str(METRICS_FILE))
    wanted = sys.argv[1:] or list(metrics)

    unknown = [w for w in wanted if w not in metrics]
    if unknown:
        sys.exit(f"Unknown metric(s): {', '.join(unknown)}. Known: {', '.join(metrics)}")

    con = duckdb.connect(str(DB_PATH), read_only=True)
    refused = 0
    for n in wanted:
        report = check(con, metrics[n])
        print(report.render())
        print()
        refused += report.verdict == REFUSE
    con.close()
    print(f"{len(wanted)} metric(s) checked, {refused} refused.")
