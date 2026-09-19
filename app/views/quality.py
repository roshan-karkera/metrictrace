"""
Data quality: what the checks found, what the gate did about it, and how much of
the warehouse that decision covers.

Every check result is on this page, passed and failed. Showing only failures
makes it impossible to answer the question that matters after an incident:
was that check even running.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import altair as alt
import pandas as pd
import streamlit as st

from app import data, theme
from quality.checks import FRESHNESS_MAX_AGE_DAYS, HOURS_PER_WEEK, MAX_NULL_RATE

theme.install()
c = theme.C()

st.title("Data quality")

state = data.platform_state()
checks = data.latest_checks()
gate = data.latest_gate()

# --------------------------------------------------------------------------
# the gate's decision, judged the way the scheduler judges it
# --------------------------------------------------------------------------

st.subheader("Last gate decision")

k1, k2, k3 = st.columns(3)
k1.metric("Published", state["total"] - state["blocked"])
k2.metric("Held", state["blocked"])
k3.metric("Blast radius", f"{state['fraction']:.0%}",
          help="Share of the warehouse the gate is holding.")

if state["fraction"] > 0.5:
    st.error(
        "Above the 50 percent limit the Airflow DAG applies. A handful of held "
        "series is normal operation and gets reported. More than half means the "
        "gate has stopped protecting anything and is reporting that either the "
        "source feed or the gate itself is broken, so the run fails instead.")
elif state["blocked"]:
    st.warning(
        "Below the 50 percent limit, so this is normal operation. The held series "
        "are reported and the run continues. A gate that turned the pipeline red "
        "for a permanently closed series would be red every night, and a light "
        "that is always on is not read.")
else:
    st.success("Nothing held. Every series was promoted to the fact table.")

theme.hairline()

# --------------------------------------------------------------------------
# every result, pass and fail
# --------------------------------------------------------------------------

st.subheader("Check results")
theme.note(
    f"Thresholds are decisions, so each one lives at the top of "
    f"<code>quality/checks.py</code> with its reason beside it. Freshness at most "
    f"{FRESHNESS_MAX_AGE_DAYS} days old &nbsp;·&nbsp; exactly {HOURS_PER_WEEK} hours in "
    f"a week, since a week is 168 hours and not approximately 168 &nbsp;·&nbsp; null "
    f"rate at most {MAX_NULL_RATE:.0%} on fields that should never be null.")

if checks.empty:
    st.info("No check results recorded. Run `python -m quality.gate`.")
else:
    grid = checks.copy()
    grid["result"] = grid["passed"].map({True: "passed", False: "failed"})

    # row_count reports once for the whole warehouse rather than per series, so
    # it does not belong in a series by check matrix. Putting it there leaves a
    # column that is one cell tall and twelve cells empty.
    overall = grid[grid["series"] == "all series"]
    grid = grid[grid["series"] != "all series"]

    if not overall.empty:
        theme.chips([(f"{r.check_name}: {r.actual}",
                      "good" if r.passed else "critical")
                     for _, r in overall.iterrows()])
        st.caption("Checks that report on the warehouse as a whole rather than per series.")

    heat = alt.Chart(grid).mark_rect(stroke=c["surface"], strokeWidth=2).encode(
        x=alt.X("check_name:N", title=None,
                axis=alt.Axis(labelAngle=0, orient="top", labelFontSize=11)),
        y=alt.Y("series:N", title=None,
                axis=alt.Axis(labelOverlap=False, labelLimit=220, labelFontSize=11)),
        # The exception gets the colour. A passing cell is a tint, because a
        # wall of saturated green makes the reader hunt for the one red square
        # instead of being handed it.
        color=alt.Color("result:N",
                        scale=alt.Scale(domain=["passed", "failed"],
                                        range=[theme.quiet(), theme.STATUS["critical"]]),
                        legend=alt.Legend(title=None)),
        tooltip=[alt.Tooltip("series:N", title="series"),
                 alt.Tooltip("check_name:N", title="check"),
                 alt.Tooltip("result:N", title="result"),
                 alt.Tooltip("expected:N", title="expected"),
                 alt.Tooltip("actual:N", title="actual"),
                 alt.Tooltip("detail:N", title="detail")],
    ).properties(height=26 * max(grid["series"].nunique(), 1))
    marks = alt.Chart(grid[~grid["passed"]]).mark_text(
        text="X", color=c["surface"], fontSize=11, fontWeight=600).encode(
        x=alt.X("check_name:N"), y=alt.Y("series:N"))
    st.altair_chart(heat + marks, width="stretch")

    failed = grid[~grid["passed"]]
    if not failed.empty:
        with st.expander(f"The {len(failed)} failing result(s)", expanded=True):
            for _, r in failed.iterrows():
                st.markdown(
                    f'{theme.chip(r.check_name, "critical")} **{r.series}** '
                    f'<span class="mt-kv">expected {r.expected}, got {r.actual}</span>',
                    unsafe_allow_html=True)

theme.hairline()

# --------------------------------------------------------------------------
# completeness, which asks a different question from the checks above
# --------------------------------------------------------------------------

st.subheader("Completeness")
theme.note(
    "Per series checks ask whether a series looks right. This one asks a fact "
    "table question instead: is any series missing from a day inside its own "
    "recorded lifespan. <b>Partial is the dangerous verdict</b>, because an "
    "aggregate over a partial day is understated and still looks plausible. "
    "There is no tolerance band here on purpose.")

comp = data.completeness()
if comp.empty:
    st.info("No completeness run logged. Run `python -m quality.completeness`.")
else:
    cal = comp.copy()
    cal["period_start"] = pd.to_datetime(cal["period_start"])
    order = ["complete", "partial", "empty"]
    palette = [theme.STATUS["good"], theme.STATUS["serious"], theme.STATUS["critical"]]

    strip = alt.Chart(cal).mark_rect(stroke=c["surface"], strokeWidth=2).encode(
        x=alt.X("period_start:T", title=None),
        color=alt.Color("verdict:N",
                        scale=alt.Scale(domain=order, range=palette),
                        legend=alt.Legend(title=None)),
        tooltip=[alt.Tooltip("period_start:T", title="day"),
                 alt.Tooltip("verdict:N", title="verdict"),
                 alt.Tooltip("expected:Q", title="series expected"),
                 alt.Tooltip("present:Q", title="series present"),
                 alt.Tooltip("missing:N", title="missing")],
    ).properties(height=46)
    st.altair_chart(strip, width="stretch")

    counts = cal["verdict"].value_counts()
    theme.chips([(f"{counts.get(v, 0)} {v}", s)
                 for v, s in zip(order, ["good", "serious", "critical"])])

theme.hairline()

# --------------------------------------------------------------------------
# what the fact table holds
# --------------------------------------------------------------------------

st.subheader("Series coverage")
cov = data.coverage()
if cov.empty:
    st.info("The fact table is empty.")
else:
    show = cov.copy()
    if not gate.empty:
        show = show.merge(gate[["series", "decision", "failed_checks"]],
                          on="series", how="left")
    st.dataframe(show, width="stretch", hide_index=True)
    st.caption(
        "A held series keeps its existing rows rather than being deleted, so a bad "
        "run never destroys good history. It is simply not refreshed. Closed series "
        "stay in the category list and leave the current numbers by having no "
        "published facts, which keeps the history intact.")
