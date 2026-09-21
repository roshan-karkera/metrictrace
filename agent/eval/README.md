# Agent evaluation

Twenty cases, five warehouse fixtures, two questions measured separately.

## Why two numbers and not one

An agent that answers everything scores perfectly on accuracy and is worthless.
An agent that refuses everything scores perfectly on safety and is also
worthless. Reporting a single blended score hides which of those you have built,
so accuracy and refusal are never combined here.

The number that matters most is `unsafe_answer_rate`: the share of cases where
the platform reported a figure it had no right to report. In this project that
is the only failure that would reach a person as a confident wrong answer.

## How the fixtures work

The refusal rule depends entirely on warehouse state, so measuring it means
controlling that state. `scenarios.py` copies `data/warehouse.db` to a temporary
file and rewrites only the gate decisions and the incident table. Facts,
dimensions and the semantic layer are untouched, so the figures in an answer are
the real ones. What changes is whether the platform is allowed to report them.

Each scenario sets every series explicitly rather than inheriting whatever the
last real gate run decided. An evaluation whose expected results drift with the
freshness of the source data is not an evaluation.

| Fixture | State | What it is for |
|---|---|---|
| `all_clear` | every series published, no open incident | the answer path |
| `nuclear_held` | one conventional series held | one held series, two different verdicts |
| `solar_incident` | everything published, one incident open | incident state alone must degrade a verdict |
| `all_held` | nothing published | refusal when no input survived |
| `no_gate` | no gate run on record | never certified, which is not the same failure |

`nuclear_held` is the case worth reading. Nuclear is conventional, so it sits in
the **denominator** of `renewable_share` and in the **numerator** of
`total_generation`. The same single held series must therefore produce a refusal
for one metric and a partial answer for the other. Dropping a conventional
source from a ratio inflates the renewable share while the number still looks
entirely plausible, and a plausible wrong number is worse than no number.

## Running it

    python -m agent.eval.run
    python -m agent.eval.run --case rs_refuse_denominator
    python -m agent.eval.run --no-mlflow

Results are written to `agent/eval/last_run.json` and logged to MLflow:

    mlflow ui --backend-store-uri sqlite:///agent/eval/mlflow.db

Exit code is non zero when any case fails, so this can gate a commit.

## What the numbers currently mean, and what they do not

The run of 2026-09-17 passes all twenty cases with an `unsafe_answer_rate` of
zero. Two honest qualifications belong next to that:

1. **The set found a real defect before it ever ran green.** `solar_incident`
   expected `partial` and got `ok`, because the trust check looked up incidents
   only for series the gate had already held. The branch that degrades a verdict
   on incident state alone could not execute. See INC-017. The expectations in
   `cases.yaml` were written from the platform's rules, not from observing its
   behaviour, which is why they were able to disagree with it.

2. **`grounding_violations` is not yet evidence about a language model.** With
   no `GROQ_API_KEY` set the composer is the deterministic fallback, which builds
   its sentence from the computed figures and therefore cannot invent one. The
   check is real and the harness records which composer ran, but this metric only
   becomes interesting with a model behind it.

The refusal path never calls a model at all, by design. The most important
behaviour in the project costs nothing to test and needs no API key.

## Adding a case

Add an entry to `cases.yaml` with the fixture it needs and the verdict the rules
require. Derive the expectation from `agent/trust.py` and `semantic/metrics.yaml`
rather than from running the agent and writing down what came back. A case that
only records current behaviour cannot fail, and a case that cannot fail is not a
test.

## Running it from WSL against a Windows checkout

MLflow finishes writing an artifact with `shutil.copystat`, which calls `utime`.
On a Windows drive seen through `/mnt/c` that call fails with "Operation not
permitted" unless the drive was mounted with metadata enabled, and the run dies
even though the artifact itself was written.

Point MLflow at the Linux filesystem instead:

    export METRICTRACE_MLFLOW_HOME=~/metrictrace-mlflow
    python -m agent.eval.run

Nothing is lost by moving it. The tracking database and the artifacts are
gitignored scratch, not source.

The alternative, which fixes the whole class of permission problem rather than
this one symptom, is to enable metadata on the mount. In `/etc/wsl.conf`:

    [automount]
    options = "metadata,umask=22,fmask=11"

then `wsl --shutdown` from PowerShell and reopen Ubuntu. That also silences
Airflow's warning about the permissions on `airflow.cfg`.
