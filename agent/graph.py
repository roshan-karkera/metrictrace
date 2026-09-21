"""
MetricTrace, the agent.

A five step graph over the rest of the platform. The order is the point:

    resolve -> trust -> (refuse | compute -> evidence -> compose)

The trust check runs BEFORE anything else and can end the run on its own. On
that path no model is called at all, which means the most important behaviour
in this project costs nothing and needs no API key to test.

The agent is also not allowed to compute a metric. It asks semantic/engine.py,
the same as the dashboard does, so a number on screen and a number in an answer
can never drift apart.

Run from the repo root:
    python -m agent.graph "what is the renewable share this week"
    python -m agent.graph "how much electricity was generated per day"
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Optional, TypedDict

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import duckdb
from langgraph.graph import StateGraph, START, END

from semantic.engine import load_metrics, compute
from agent.trust import check, REFUSE, PARTIAL, OK

from config import DB_PATH          # overridable via METRICTRACE_DB, see config.py; also loads .env
METRICS_FILE = ROOT / "semantic" / "metrics.yaml"

DEFAULT_MODEL = os.environ.get("METRICTRACE_MODEL", "openai/gpt-oss-120b")


class State(TypedDict, total=False):
    question: str
    metric: str
    period: str
    trust: object
    rows: list
    stats: dict
    sources: list
    answer: str
    trace: list


# --------------------------------------------------------------------------
# the one place a model is reached
# --------------------------------------------------------------------------

def complete(system: str, user: str) -> Optional[str]:
    """
    Single seam for the model. Returns None when no key is configured, and the
    caller falls back to a deterministic answer. Swap the body here to compare
    providers in agent/eval without touching the graph.
    """
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        return None
    try:
        from groq import Groq
        client = Groq(api_key=key)
        resp = client.chat.completions.create(
            model=DEFAULT_MODEL,
            temperature=0,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return resp.choices[0].message.content.strip()
    except Exception as exc:                        # noqa: BLE001
        return f"[model unavailable: {exc}]"


# --------------------------------------------------------------------------
# nodes
# --------------------------------------------------------------------------

def note(state: State, text: str) -> list:
    trace = list(state.get("trace", []))
    trace.append(text)
    return trace


def resolve_node(state: State) -> State:
    """
    Deterministic on purpose. If a model picked the metric, a refusal could be
    dodged by the model choosing a different one.
    """
    q = state["question"].lower()
    metrics = load_metrics(str(METRICS_FILE))

    best, best_score = None, 0
    for name, m in metrics.items():
        words = set((name.replace("_", " ") + " " + m.label).lower().split())
        score = sum(1 for w in words if len(w) > 3 and w in q)
        if score > best_score:
            best, best_score = name, score

    period = "day"
    if "month" in q:
        period = "month"
    elif "hour" in q:
        period = "hour"

    if best is None:
        return {
            "metric": "",
            "period": period,
            "answer": ("I do not have a metric that matches that question. Known metrics: "
                       + ", ".join(metrics) + "."),
            "trace": note(state, "resolve: no matching metric, stopping"),
        }

    return {
        "metric": best,
        "period": period,
        "trace": note(state, f"resolve: metric={best}, period={period}"),
    }


def trust_node(state: State) -> State:
    if not state.get("metric"):
        return {}
    metrics = load_metrics(str(METRICS_FILE))
    con = duckdb.connect(str(DB_PATH), read_only=True)
    report = check(con, metrics[state["metric"]])
    con.close()
    return {
        "trust": report,
        "trace": note(state, f"trust: {report.verdict} ({report.reason})"),
    }


def route(state: State) -> str:
    if not state.get("metric"):
        return "stop"
    report = state.get("trust")
    if report is None or report.verdict == REFUSE:
        return "stop"
    return "go"


def refuse_node(state: State) -> State:
    report = state.get("trust")
    if report is None:
        return {"trace": note(state, "refuse: no metric resolved")}

    lines = [
        f"I will not answer that with the current data.",
        "",
        f"Reason: {report.reason}",
    ]
    if report.denominator_held:
        lines.append("Held series in the denominator: " + ", ".join(report.denominator_held))
    if report.numerator_held:
        lines.append("Held series in the numerator: " + ", ".join(report.numerator_held))
    for inc in report.incidents:
        lines.append(f"Open incident {inc['incident_id']}: {inc['series']} failed "
                     f"{inc['check_name']}. Runbook: {inc['runbook']}")
    lines.append("")
    lines.append("Close the incident or publish the series and ask again.")

    return {
        "answer": "\n".join(lines),
        "trace": note(state, "refuse: answered without calling the model"),
    }


def compute_node(state: State) -> State:
    metrics = load_metrics(str(METRICS_FILE))
    m = metrics[state["metric"]]
    con = duckdb.connect(str(DB_PATH), read_only=True)
    rows = compute(con, m, state["period"])
    con.close()

    values = [v for _, v in rows if v is not None]
    stats = {
        "periods": len(rows),
        "undefined": sum(1 for _, v in rows if v is None),
        "first": str(rows[0][0])[:16] if rows else None,
        "last": str(rows[-1][0])[:16] if rows else None,
        "min": min(values) if values else None,
        "max": max(values) if values else None,
        "mean": (sum(values) / len(values)) if values else None,
        "latest": values[-1] if values else None,
        "unit": m.unit,
    }
    return {
        "rows": rows,
        "stats": stats,
        "trace": note(state, f"compute: {len(rows)} {state['period']}(s) via semantic layer, "
                             f"{stats['undefined']} undefined"),
    }


def evidence_node(state: State) -> State:
    """What feeds this number, read from the lineage graph rather than assumed."""
    sources = []
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        exists = con.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = 'lineage_edge'"
        ).fetchone()[0]
        if exists:
            sources = [r[0] for r in con.execute("""
                SELECT DISTINCT source_node FROM lineage_edge
                WHERE target_node = 'fact_series_hourly' ORDER BY 1
            """).fetchall()]
    finally:
        con.close()
    return {
        "sources": sources,
        "trace": note(state, f"evidence: {len(sources)} upstream node(s) from lineage"),
    }


SYSTEM = (
    "You answer questions about energy metrics for a data platform. "
    "You are given a metric definition and figures that were already computed for you. "
    "Never invent, estimate or recompute a number. Use only the figures given. "
    "If the trust verdict is PARTIAL you must say plainly that the figure is partial and why. "
    "Answer in at most four sentences, plain prose, no bullet points, no markdown."
)


def compose_node(state: State) -> State:
    metrics = load_metrics(str(METRICS_FILE))
    m = metrics[state["metric"]]
    s = state["stats"]
    report = state["trust"]

    def fmt(v):
        if v is None:
            return "undefined"
        return f"{v:.1%}" if m.unit == "ratio" else f"{v:,.0f}"

    facts = (
        f"Question: {state['question']}\n"
        f"Metric: {m.label} ({m.name})\n"
        f"Definition: {' '.join(m.definition.split())}\n"
        f"Unit: {m.unit}\n"
        f"Trust verdict: {report.verdict}. {report.reason}\n"
        f"Period: {state['period']}, {s['periods']} periods from {s['first']} to {s['last']}\n"
        f"Latest: {fmt(s['latest'])}, mean: {fmt(s['mean'])}, "
        f"min: {fmt(s['min'])}, max: {fmt(s['max'])}\n"
        f"Undefined periods: {s['undefined']}\n"
    )

    prose = complete(SYSTEM, facts)
    if prose is None:
        prose = (
            f"{m.label} over the last {s['periods']} {state['period']}(s): latest {fmt(s['latest'])}, "
            f"mean {fmt(s['mean'])}, ranging {fmt(s['min'])} to {fmt(s['max'])}."
        )
        if report.verdict == PARTIAL:
            prose += f" This figure is partial: {report.reason}"

    evidence = ["", "Evidence:", f"  definition: {' '.join(m.definition.split())}",
                f"  owner: {m.owner}, version {m.version}, last changed {m.last_changed}",
                f"  trust: {report.verdict}, gate run {report.last_gate_run}"]
    if s["undefined"]:
        why = ("zero or absent denominator" if m.is_ratio
               else "no contributing series in that period")
        evidence.append(f"  undefined periods: {s['undefined']} ({why}, not zero)")
    for inc in report.incidents:
        evidence.append(f"  open incident {inc['incident_id']}: {inc['series']} / {inc['check_name']}")
    if state.get("sources"):
        evidence.append("  upstream: " + ", ".join(state["sources"]))

    return {
        "answer": prose + "\n" + "\n".join(evidence),
        "trace": note(state, "compose: answer built from computed figures only"),
    }


# --------------------------------------------------------------------------
# graph
# --------------------------------------------------------------------------

def build():
    g = StateGraph(State)
    g.add_node("resolve", resolve_node)
    g.add_node("trust", trust_node)
    g.add_node("refuse", refuse_node)
    g.add_node("compute", compute_node)
    g.add_node("evidence", evidence_node)
    g.add_node("compose", compose_node)

    g.add_edge(START, "resolve")
    g.add_edge("resolve", "trust")
    g.add_conditional_edges("trust", route, {"stop": "refuse", "go": "compute"})
    g.add_edge("refuse", END)
    g.add_edge("compute", "evidence")
    g.add_edge("evidence", "compose")
    g.add_edge("compose", END)
    return g.compile()


if __name__ == "__main__":
    question = " ".join(sys.argv[1:]) or "what is the renewable share"
    started = time.time()
    result = build().invoke({"question": question, "trace": []})

    print(f"Q: {question}\n")
    print(result.get("answer", "(no answer)"))
    print("\nTrace:")
    for step in result.get("trace", []):
        print(f"  {step}")
    print(f"\n{time.time() - started:.2f}s")
