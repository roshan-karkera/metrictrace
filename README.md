# MetricTrace

**A small analytics platform run the way a real one has to be run.**

Pipelines that know when they cannot be trusted. Metrics that have exactly one
agreed definition. An incident process that decides what happens when either
breaks.

> A number moved. MetricTrace tells you what it is, cites the definition and the
> trust state behind it, and if the load was incomplete that week it refuses and
> points at the open incident instead of inventing a business reason.

Built on public German electricity data from [SMARD](https://www.smard.de),
thirteen hourly series for the DE market area, no API key.

---

## Why refusal is the feature

Most analytics tooling is built to always produce an answer. That is the wrong
default when the answer is going to be believed. MetricTrace is organised around
the opposite instinct: before it reports anything, it establishes whether it is
entitled to.

The trust check runs first, it never calls a language model, and it can end a
run on its own. Three verdicts:

| Verdict | Meaning |
|---|---|
| `ok` | every contributing series passed the most recent gate, no incident open |
| `partial` | the figure is computable but understated in a known direction, and is labelled so |
| `refuse` | the figure would be wrong in a way that still looks plausible |

The case that defines the design: **nuclear** is a conventional source, so it
sits in the *denominator* of `renewable_share` and in the *numerator* of
`total_generation`. If it is held back by the quality gate, the absolute figure
is merely understated and can be reported with a caveat, while the ratio is
silently inflated and must be refused. One held series, two different correct
behaviours.

---

## Two findings from real data

**INC-002. A series that was perfectly healthy and two years out of date.**
The `nuclear` series returns eight complete weeks: 168 rows each, zero nulls,
correct schema, indistinguishable from the other twelve in every column except
one. Its most recent week is January 2024, because Germany stopped generating
nuclear power in April 2023. Row count passed. Null rate passed. Gap check
passed. Only freshness caught it. Found in real data on day one, not staged.

**INC-003. Twelve and a half percent understated, with every check green.**
`total_generation` reported exactly zero for the first seven days of the
warehouse. The aggregate was written as `SUM(CASE WHEN ... THEN value ELSE 0
END)`, and with no matching rows that returns `0.0` rather than `NULL`, so a
period with nothing contributing was indistinguishable from a period in which
the country generated no electricity. Mean daily generation was reported 12.5
percent low. Every quality check passed, because each one looks at a series
against its own history and none was looking at whether a period contained the
series it should.

Both are written up in [`process/incidents.md`](process/incidents.md), including
the uncomfortable part: INC-003 was caught by eye, not by a control.

---

## The agent, and how it is measured

Five nodes: `resolve -> trust -> (refuse | compute -> evidence -> compose)`.
The agent is not permitted to compute a metric. It asks the semantic layer, the
same as the dashboard does, so a number on a screen and a number in an answer
cannot drift apart.

`agent/eval` runs twenty cases across five warehouse fixtures and reports
accuracy and refusal **separately**, because blending them hides which one you
built. The headline number is `unsafe_answer_rate`: cases where the platform
reported a figure it had no right to report.

Run of 2026-09-17, twenty of twenty:

| | |
|---|---|
| metric resolution accuracy | 1.00 |
| verdict accuracy | 1.00 |
| refusal recall | 1.00 |
| refusal precision | 1.00 |
| **unsafe answer rate** | **0.00** |
| grounding violations | 0 |
| mean latency | 0.066 s |

Two qualifications belong next to those numbers, and are in
[`agent/eval/README.md`](agent/eval/README.md) as well: the set found a real
defect before it ever ran green (INC-017, a trust verdict the code described but
could never reach), and `grounding_violations` is not yet evidence about a
language model, because the deterministic composer cannot invent a figure by
construction.

---

## Run

    pip install -r requirements.txt

    python -m ingest.smard --weeks 8    # lands raw JSON immutably, idempotent
    python -m models.clean              # typed, quarantined, grain asserted
    python -m quality.gate              # must run before dimensional
    python -m models.dimensional        # fact and dimension tables
    python -m models.lineage            # column level lineage, no arguments

    python -m semantic.engine renewable_share day
    python -m agent.trust                          # verdicts, no model needed
    python -m agent.graph "what is the renewable share of generation"
    python -m agent.eval.run                       # golden set, non zero exit on failure

    streamlit run app/dashboard.py

---

## Layout

    ingest/          extraction, lands raw immutably
    models/          raw to cleaned to dimensional, plus column level lineage
    semantic/        metric definitions, the single source of truth
    quality/         checks, thresholds with their reasons, and the gate
    agent/           the trust check, the graph, and agent/eval
    app/             dashboard, trust panel above the numbers
    process/         runbooks, incident log, ticket intake
    orchestration/   Airflow, deliberately last
    docs/decisions/  architecture decision records

[`CLAUDE.md`](CLAUDE.md) is the working handover document: current state,
decisions that must not be silently reversed, and what is next.

---

## Conventions that hold throughout

Raw is immutable. No row is ever dropped silently. Every check result is logged,
pass and fail, so nobody has to wonder whether a check was running. Blocking is
per series, never per run. Only the semantic layer computes a metric. Nothing
contributing returns undefined, never zero. Thresholds are decisions and live
next to the reason for them. Runbooks are written the first time a check fails,
not at the end.

---

## Status

Portfolio project, built independently. It has no real users and nothing depends
on it. Airflow is not wired up yet, deliberately: the pipeline works as plain
functions, and wrapping working functions in a scheduler later is an hour, while
debugging a broken transform inside one is not.
