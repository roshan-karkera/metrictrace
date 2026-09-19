"""
Ask: the same questions in a sentence.

The demo this page exists for is the refusal. Anybody can build an assistant
that answers. The thing worth showing is the one that checks the state of the
data first and declines to attribute a movement it cannot see properly, instead
of inventing a business reason for a gap in the load.

The trust step runs with or without a model key, because it is ordinary code
reading the gate and the incident log. Only the prose needs the model.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import os
import time

import streamlit as st

from app import data, theme
from agent.trust import OK, PARTIAL, REFUSE, check as trust_check

theme.install()
c = theme.C()

st.title("Ask")

metrics = data.metrics()
names = list(metrics)

# --------------------------------------------------------------------------
# the trust step, shown on its own because it is the part that matters
# --------------------------------------------------------------------------

st.subheader("The check that runs before any answer")
theme.note(
    "This is the ordering the whole project argues for. The agent resolves which "
    "metric you asked about, then reads the gate decisions and the open incidents "
    "for the series behind it, and only then decides whether it is allowed to "
    "answer. Nothing here calls a model.")

chosen = st.selectbox("Metric", names, format_func=lambda n: metrics[n].label,
                      index=names.index(st.session_state.get("metric", names[0]))
                      if st.session_state.get("metric") in names else 0)

report = trust_check(data.con(), metrics[chosen])
level = {OK: "good", PARTIAL: "serious", REFUSE: "critical"}[report.verdict]

st.markdown(theme.chip(report.verdict.upper(), level), unsafe_allow_html=True)
st.markdown(f"**{report.reason}**")

if report.numerator_held:
    st.markdown(f'<span class="mt-kv">numerator series held: '
                f'{", ".join(report.numerator_held)}</span>', unsafe_allow_html=True)
if report.denominator_held:
    st.markdown(f'<span class="mt-kv">denominator series held: '
                f'{", ".join(report.denominator_held)}</span>', unsafe_allow_html=True)
if report.incidents:
    # Collapsed. The verdict and its reason are the answer; the incident list is
    # the evidence behind it, and a dozen rows of evidence above the fold buries
    # the one sentence the reader came for.
    with st.expander(f"{len(report.incidents)} incident(s) the check read as evidence"):
        for inc in report.incidents:
            st.markdown(
                f'<span class="mt-kv">{inc["incident_id"]} &nbsp; {inc["series"]} / '
                f'{inc["check_name"]} &nbsp; runbook <code>{inc["runbook"]}</code>'
                f'</span>', unsafe_allow_html=True)
    if len(report.incidents) > 3:
        st.caption(
            "An incident opens by itself when a check fails and nothing closes it "
            "automatically, by design: one incident per series and check until "
            "somebody decides it is resolved. A long list here usually means a "
            "past outage was fixed in the data and never closed in the log.")

theme.hairline()

# --------------------------------------------------------------------------
# the full graph
# --------------------------------------------------------------------------

st.subheader("Ask in a sentence")

has_key = bool(os.environ.get("GROQ_API_KEY"))
if not has_key:
    st.info(
        "No `GROQ_API_KEY` in the environment, so the composing step has no model "
        "to call. The trust check above still runs, and so does everything else "
        "except the final prose.")

question = st.text_input(
    "Question",
    placeholder="why did the renewable share move between 5 and 6 September",
)

examples = [
    "what is the renewable share",
    "how much electricity was generated per day",
    "why did the renewable share drop",
]
cols = st.columns(len(examples))
for i, ex in enumerate(examples):
    if cols[i].button(ex, width="stretch"):
        question = ex

if question:
    with st.spinner("resolve, trust, then answer or refuse"):
        started = time.time()
        try:
            from agent.graph import build
            result = build().invoke({"question": question, "trace": []})
        except Exception as exc:
            st.error(f"The agent raised: {exc}")
            st.stop()
        elapsed = time.time() - started

    answer = result.get("answer", "(no answer)")
    trace = result.get("trace", [])
    refused = any("refuse" in str(step).lower() for step in trace)

    if refused:
        st.error("**Refused**")
    else:
        st.success("**Answered**")
    st.markdown(answer)
    st.caption(f"{elapsed:.2f}s")

    with st.expander("The path it took", expanded=True):
        for step in trace:
            st.markdown(f'<span class="mt-kv">{step}</span>', unsafe_allow_html=True)

theme.note(
    "Evaluated against a golden set of 20 cases over 5 fixture warehouses, "
    "including the ones whose correct answer is a refusal, tracked in MLflow. "
    "Run it with <code>python -m agent.eval.run</code>.")
