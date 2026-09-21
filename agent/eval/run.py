"""
MetricTrace, agent evaluation.

Runs the golden set in agent/eval/cases.yaml against the fixtures in
agent/eval/scenarios.py and reports two things separately:

  1. Does the agent answer correctly when it is allowed to answer.
  2. Does it refuse correctly when it is not.

They are separate because they fail separately. An agent that answers
everything scores perfectly on the first and is worthless. An agent that
refuses everything scores perfectly on the second and is also worthless. The
number that matters most is neither of those: it is `unsafe_answer_rate`, the
share of cases where the platform reported a figure it had no right to report.

Results go to MLflow, local file store, so a change to a prompt or a threshold
can be compared against the run before it.

Run from the repo root:
    python -m agent.eval.run
    python -m agent.eval.run --case rs_refuse_denominator
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import re
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import yaml

from agent.eval import scenarios

CASES_FILE = Path(__file__).with_name("cases.yaml")
# Where MLflow keeps its tracking database and its artifacts.
#
# Beside the eval by default, which is right on Windows and on native Linux.
# It is overridable because of one environment where the default does not work:
# WSL writing to a Windows drive through /mnt/c. MLflow finishes an artifact
# with shutil.copystat, which calls utime, and utime on that mount fails with
# "Operation not permitted" unless the drive was mounted with metadata enabled.
# The artifact is already written at that point, so the failure is pure
# bookkeeping, but it still kills the run.
#
# Set METRICTRACE_MLFLOW_HOME to somewhere on the Linux filesystem and the
# problem goes away. Nothing here is source: both paths are gitignored scratch.
MLFLOW_HOME = Path(os.environ.get("METRICTRACE_MLFLOW_HOME")
                   or ROOT / "agent" / "eval")
MLFLOW_DB = MLFLOW_HOME / "mlflow.db"
MLFLOW_ARTIFACTS = MLFLOW_HOME / "mlartifacts"
EXPERIMENT = "metrictrace-agent"


def _short(path: Path) -> str:
    """Repo relative when it is inside the repo, absolute when it is not."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


# --------------------------------------------------------------------------
# grounding
# --------------------------------------------------------------------------

NUMBER = re.compile(r"-?\d[\d,]*(?:\.\d+)?%?")

# A language model does not type ASCII. It writes dates with a non breaking
# hyphen and separates a figure from its unit with a narrow no break space, and
# both are invisible in a terminal. The date pattern below is written with a
# plain hyphen, so without this the pattern quietly matched nothing and every
# date component was reported as an invented number. Normalise first, match
# second. Characters are written as escapes rather than literals to keep
# convention 16 true of this file.
TYPOGRAPHY = str.maketrans({
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-",
    "\u2014": "-", "\u2212": "-",                       # dashes and minus
    "\u00a0": " ", "\u2009": " ", "\u202f": " ",       # non breaking spaces
})

MONTHS = ("January|February|March|April|May|June|July|"
          "August|September|October|November|December")


def _norm(token: str) -> str:
    return token.replace(",", "").rstrip("%").rstrip("0").rstrip(".")


def allowed_numbers(stats: dict | None, period: str) -> set[str]:
    """Exactly the figures the composer was handed. Nothing else may appear."""
    if not stats:
        return set()
    out: set[str] = set()
    for key in ("latest", "mean", "min", "max"):
        v = stats.get(key)
        if v is None:
            continue
        out.add(_norm(f"{v:.1%}"))
        out.add(_norm(f"{v:,.0f}"))
        out.add(_norm(f"{v:.1f}"))
        out.add(_norm(f"{v:.2f}"))
    out.add(_norm(str(stats.get("periods", ""))))
    out.add(_norm(str(stats.get("undefined", ""))))
    out.discard("")
    return out


def grounding_violations(prose: str, stats: dict | None, period: str) -> list[str]:
    """Numeric tokens in the prose that were not among the computed figures.

    Labels are stripped first, because they are names rather than claims about
    a quantity: incident identifiers, dates, clock times and bare years.

    Dates have to be removed whole. Stripping only the year out of
    "2026-09-13 00:00" leaves "09", "13", "00" and "00" behind, and the check
    then reports four invented numbers in a sentence that invented nothing.
    That is how this check produced sixteen violations the moment the composer
    started writing real prose and naming the period it was describing, which
    it is handed in stats["first"] and stats["last"] and is supposed to say.

    A false positive here is worse than a miss. The whole point of the measure
    is that a violation means the agent made a number up, so a check that cries
    wolf over a timestamp makes the number unreadable.
    """
    text = prose.translate(TYPOGRAPHY)
    text = re.sub(r"INC-\d+", " ", text)
    # dates written out, "July 1 2026" and "1 July 2026"
    text = re.sub(rf"\b(?:{MONTHS})\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}}", " ", text)
    text = re.sub(rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{MONTHS}),?\s+\d{{4}}", " ", text)
    # ISO dates, with or without a time after them
    text = re.sub(r"\b\d{4}-\d{1,2}-\d{1,2}(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?", " ", text)
    # clock times on their own
    text = re.sub(r"\b\d{1,2}:\d{2}(?::\d{2})?\b", " ", text)
    # bare years
    text = re.sub(r"\b(?:19|20)\d{2}\b", " ", text)
    allowed = allowed_numbers(stats, period)
    return [t for t in NUMBER.findall(text) if _norm(t) not in allowed]


# --------------------------------------------------------------------------
# one case
# --------------------------------------------------------------------------

def run_case(case: dict, fixture: Path) -> dict:
    """Point the whole platform at a fixture, then reimport the agent so the
    module level DB_PATH is rebound. Cheaper and less fragile than threading a
    connection through every node signature."""
    os.environ["METRICTRACE_DB"] = str(fixture)
    for name in ("config", "semantic.engine", "agent.trust", "agent.graph"):
        if name in sys.modules:
            importlib.reload(sys.modules[name])
        else:
            importlib.import_module(name)
    graph = importlib.import_module("agent.graph")

    started = time.perf_counter()
    state = graph.build().invoke({"question": case["question"], "trace": []})
    latency = time.perf_counter() - started

    answer = state.get("answer", "")
    prose = answer.split("\nEvidence:")[0]
    report = state.get("trust")
    verdict = report.verdict if report is not None else None
    stats = state.get("stats")

    got_metric = state.get("metric") or None
    answered = stats is not None

    violations = grounding_violations(prose, stats, state.get("period", ""))

    return {
        "id": case["id"],
        "scenario": case["scenario"],
        "question": case["question"],
        "expect_metric": case["expect_metric"],
        "got_metric": got_metric,
        "metric_ok": got_metric == case["expect_metric"],
        "expect_period": case["expect_period"],
        "got_period": state.get("period"),
        "period_ok": state.get("period") == case["expect_period"],
        "expect_verdict": case["expect_verdict"],
        "got_verdict": verdict,
        "verdict_ok": verdict == case["expect_verdict"],
        "expect_answer": case["expect_answer"],
        "answered": answered,
        "answer_ok": answered == case["expect_answer"],
        "unsafe": answered and not case["expect_answer"],
        "violations": violations,
        "latency_s": round(latency, 3),
        "answer": answer,
        "trace": state.get("trace", []),
    }


# --------------------------------------------------------------------------
# the set
# --------------------------------------------------------------------------

def summarise(results: list[dict]) -> dict:
    n = len(results)
    with_metric = [r for r in results if r["expect_metric"] is not None]
    should_refuse = [r for r in results if not r["expect_answer"]]
    did_refuse = [r for r in results if not r["answered"]]

    lat = [r["latency_s"] for r in results]
    return {
        "cases": n,
        "metric_resolution_accuracy": round(
            sum(r["metric_ok"] for r in results) / n, 4),
        "period_resolution_accuracy": round(
            sum(r["period_ok"] for r in with_metric) / len(with_metric), 4),
        "verdict_accuracy": round(sum(r["verdict_ok"] for r in results) / n, 4),
        "answer_decision_accuracy": round(sum(r["answer_ok"] for r in results) / n, 4),
        "refusal_recall": round(
            sum(not r["answered"] for r in should_refuse) / len(should_refuse), 4),
        "refusal_precision": round(
            (sum(not r["expect_answer"] for r in did_refuse) / len(did_refuse))
            if did_refuse else 1.0, 4),
        "unsafe_answer_rate": round(sum(r["unsafe"] for r in results) / n, 4),
        "grounding_violations": sum(len(r["violations"]) for r in results),
        "mean_latency_s": round(statistics.mean(lat), 3),
        "p95_latency_s": round(sorted(lat)[max(0, int(0.95 * len(lat)) - 1)], 3),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", help="run one case by id")
    ap.add_argument("--no-mlflow", action="store_true")
    args = ap.parse_args()

    cases = yaml.safe_load(CASES_FILE.read_text(encoding="utf-8"))
    if args.case:
        cases = [c for c in cases if c["id"] == args.case]
        if not cases:
            sys.exit(f"no case with id {args.case}")

    composer = "model" if os.environ.get("GROQ_API_KEY") else "deterministic"
    tmp = Path(tempfile.mkdtemp(prefix="metrictrace-eval-"))
    built: dict[str, Path] = {}
    results = []

    for case in cases:
        name = case["scenario"]
        if name not in built:
            built[name] = scenarios.build(name, tmp / f"{name}.db")
        results.append(run_case(case, built[name]))

    os.environ.pop("METRICTRACE_DB", None)

    width = max(len(r["id"]) for r in results)
    print(f"\n{'case'.ljust(width)}  scen             verdict   answer  checks")
    print("-" * (width + 46))
    for r in results:
        flags = "".join([
            "M" if not r["metric_ok"] else ".",
            "P" if not r["period_ok"] else ".",
            "V" if not r["verdict_ok"] else ".",
            "A" if not r["answer_ok"] else ".",
            "G" if r["violations"] else ".",
        ])
        print(f"{r['id'].ljust(width)}  {r['scenario']:15s}  "
              f"{str(r['got_verdict'] or 'none'):8s}  "
              f"{'yes' if r['answered'] else 'no ':6s}  {flags}")

    print("\n  flags: M metric  P period  V verdict  A answer decision  G grounding")

    summary = summarise(results)
    print("\nSummary")
    for k, v in summary.items():
        print(f"  {k:28s} {v}")
    print(f"  {'composer':28s} {composer}")

    failed = [r for r in results if not (r["metric_ok"] and r["period_ok"]
                                         and r["verdict_ok"] and r["answer_ok"])
              or r["violations"]]
    if failed:
        print(f"\n{len(failed)} case(s) failed:")
        for r in failed:
            print(f"  {r['id']}: expected {r['expect_verdict']}/"
                  f"{'answer' if r['expect_answer'] else 'refuse'}, "
                  f"got {r['got_verdict']}/"
                  f"{'answer' if r['answered'] else 'refuse'}"
                  + (f", ungrounded numbers {r['violations']}" if r["violations"] else ""))

    out = ROOT / "agent" / "eval" / "last_run.json"
    out.write_text(json.dumps({"summary": summary, "composer": composer,
                               "results": results}, indent=2, default=str),
                   encoding="utf-8")
    print(f"\nwritten  {out.relative_to(ROOT)}")

    if not args.no_mlflow:
        import mlflow
        # SQLite rather than the file store: the file backend is in maintenance
        # mode upstream and refuses to start without an opt out flag.
        MLFLOW_ARTIFACTS.mkdir(parents=True, exist_ok=True)
        mlflow.set_tracking_uri(f"sqlite:///{MLFLOW_DB}")
        client = mlflow.tracking.MlflowClient()

        # An experiment's artifact location is fixed when it is created and
        # MLflow will not change it afterwards. A tracking database that has
        # moved between machines, or between a Windows checkout and the same
        # folder seen from WSL, therefore keeps pointing at a path that does
        # not exist here, and every run dies at log_artifact with a permission
        # error naming a directory from the other filesystem.
        #
        # Renaming the stale experiment out of the way is the recovery: the old
        # runs stay readable under their new name, and this one starts a fresh
        # experiment whose artifacts land where they belong.
        exp = client.get_experiment_by_name(EXPERIMENT)
        if exp is not None and exp.artifact_location != MLFLOW_ARTIFACTS.as_uri():
            stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
            client.rename_experiment(exp.experiment_id, f"{EXPERIMENT}-stale-{stamp}")
            print(f"note     the tracking database was created elsewhere "
                  f"(artifacts at {exp.artifact_location}). "
                  f"Renamed that experiment to {EXPERIMENT}-stale-{stamp} "
                  f"and started a new one.")
            exp = None
        if exp is None:
            client.create_experiment(
                EXPERIMENT, artifact_location=MLFLOW_ARTIFACTS.as_uri())
        mlflow.set_experiment(EXPERIMENT)
        with mlflow.start_run():
            mlflow.log_params({
                "composer": composer,
                "model": os.environ.get("METRICTRACE_MODEL", "none"),
                "cases": len(cases),
                "scenarios": ",".join(sorted(built)),
            })
            mlflow.log_metrics({k: v for k, v in summary.items()})
            mlflow.log_artifact(str(out))
            mlflow.log_artifact(str(CASES_FILE))
        print(f"logged   mlflow experiment {EXPERIMENT} in "
              f"{_short(MLFLOW_DB)}")
        print("         view with: mlflow ui --backend-store-uri "
              f"sqlite:///{MLFLOW_DB}")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
