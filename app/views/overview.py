"""
Overview: can I believe anything on this platform right now.

This is the landing page on purpose. The whole argument of the project is that
the trust state comes before the numbers, and a panel you have to scroll past is
a panel nobody reads.
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
from quality.checks import FRESHNESS_MAX_AGE_DAYS

theme.install()
c = theme.C()
state = data.platform_state()

st.title("Overview")
st.caption("The state of the data, before any number that depends on it.")

# --------------------------------------------------------------------------
# the verdict, in words, with the colour as the secondary channel
# --------------------------------------------------------------------------

banner = {"good": st.success, "warning": st.warning,
          "serious": st.warning, "critical": st.error}[state["level"]]
banner(state["headline"])

k1, k2, k3, k4 = st.columns(4)
k1.metric("Series published", state["total"] - state["blocked"],
          help="Series the gate allowed into the fact table on its last run.")
k2.metric("Series held", state["blocked"],
          help="Held series keep their existing rows. They are not refreshed and "
               "not deleted, so a bad run never destroys good history.")
k3.metric("Open incidents", state["open_incidents"],
          help="One incident per series and check. A recurring failure stays the "
               "same incident until somebody closes it.")
k4.metric("Partial days", state["partial_days"],
          help="Days missing a series from inside its own lifespan. Aggregates "
               "over these are understated and still look plausible.")

if state["held"]:
    st.markdown("Held by the gate, and therefore absent from every number in this app: "
                + ", ".join(f"**{s}**" for s in state["held"]))

theme.hairline()

# --------------------------------------------------------------------------
# freshness, the check that found the finding this project is built around
# --------------------------------------------------------------------------

st.subheader("How old is each series")
theme.note(
    f"The freshness threshold is {FRESHNESS_MAX_AGE_DAYS} days, and it is the only "
    "check that caught <b>nuclear</b>. That series returned eight perfect weeks: "
    "168 rows each, zero nulls, no gaps, indistinguishable from the other twelve "
    "in every column except the date of its newest row. Germany stopped nuclear "
    "generation in April 2023."
)

cov = data.coverage()
if cov.empty:
    st.info("The fact table is empty. Run the pipeline in the order given in CLAUDE.md.")
else:
    cov = cov.copy()

    def verdict(row):
        # A series with no published facts is not "very old", it is absent.
        # Nothing has ever been promoted for it, so it has no age, and a bar
        # would invent one.
        if pd.isna(row["days_old"]) or row["hours"] == 0:
            return "nothing published"
        return "stale" if row["days_old"] > FRESHNESS_MAX_AGE_DAYS else "fresh"

    cov["state"] = cov.apply(verdict, axis=1)
    plotted = cov[cov["state"] != "nothing published"].copy()
    absent = cov.loc[cov["state"] == "nothing published", "series"].tolist()

    oldest = plotted["days_old"].max() if len(plotted) else None
    s1, s2, s3 = st.columns(3)
    s1.metric("Oldest published series", f"{oldest:.0f} days" if oldest is not None else "none")
    s2.metric("Freshness threshold", f"{FRESHNESS_MAX_AGE_DAYS} days")
    s3.metric("Nothing ever published", len(absent))

    if len(plotted):
        # One dot per series on a single age axis, stacked where they coincide.
        # Thirteen bars of identical length say nothing; a column of dots at one
        # value says "all of them, this old" at a glance, and the moment the
        # ages diverge the shape of the spread appears by itself.
        dots = alt.Chart(plotted).transform_window(
            rank="rank()", sort=[alt.SortField("series")], groupby=["days_old"]
        ).mark_circle(size=150, opacity=0.9, stroke=c["surface"], strokeWidth=2).encode(
            x=alt.X("days_old:Q",
                    title="days since the newest hour in the fact table",
                    scale=alt.Scale(domainMin=0,
                                    domainMax=max(FRESHNESS_MAX_AGE_DAYS + 2,
                                                  float(oldest) + 2))),
            y=alt.Y("rank:Q", title=None, axis=None,
                    scale=alt.Scale(domainMin=0, domainMax=8)),
            color=alt.Color("state:N",
                            scale=alt.Scale(domain=["fresh", "stale"],
                                            range=[theme.STATUS["good"],
                                                   theme.STATUS["critical"]]),
                            legend=alt.Legend(title=None)),
            tooltip=[alt.Tooltip("series:N", title="series"),
                     alt.Tooltip("category:N", title="category"),
                     alt.Tooltip("state:N", title="state"),
                     alt.Tooltip("days_old:Q", title="days old"),
                     alt.Tooltip("hours:Q", title="hours in fact table", format=","),
                     alt.Tooltip("last_hour:T", title="newest hour")],
        )
        rule = alt.Chart(pd.DataFrame({"x": [FRESHNESS_MAX_AGE_DAYS]})).mark_rule(
            color=c["muted"], strokeWidth=1).encode(x="x:Q")
        rule_label = alt.Chart(pd.DataFrame(
            {"x": [FRESHNESS_MAX_AGE_DAYS],
             "t": [f"threshold {FRESHNESS_MAX_AGE_DAYS}d"]}
        )).mark_text(align="left", dx=5, color=c["muted"], fontSize=10,
                     baseline="top").encode(x="x:Q", y=alt.value(4), text="t:N")

        st.altair_chart((dots + rule + rule_label).properties(height=190),
                        width="stretch")
        st.caption("One dot per series. Hover for the series name and its newest hour.")

    if absent:
        st.caption(
            "No dot for " + ", ".join(absent) + ": held since the first run, so "
            "nothing has ever been promoted and there is no age to plot. Named "
            "rather than dropped, because a series missing from a coverage chart "
            "is the easiest kind of gap to miss.")

    with st.expander("Every series, with its category and row count"):
        st.dataframe(
            cov[["series", "category", "state", "hours", "first_hour", "last_hour"]],
            width="stretch", hide_index=True)

theme.hairline()

# --------------------------------------------------------------------------
# did the pipeline run, and what did each stage say
# --------------------------------------------------------------------------

st.subheader("Last run of each stage")

rows = []
hist = data.gate_history()
checks = data.latest_checks()
comp = data.completeness()

if not hist.empty:
    last = hist.iloc[-1]
    frac = last["blocked"] / last["total"] if last["total"] else 0
    rows.append({
        "stage": "quality gate",
        "when": str(last["run_at"])[:16],
        "result": f"{int(last['total'] - last['blocked'])} published, {int(last['blocked'])} held",
        "verdict": "critical" if frac > 0.5 else ("warning" if last["blocked"] else "good"),
    })
if not checks.empty:
    failed = int((~checks["passed"]).sum())
    rows.append({
        "stage": "checks",
        "when": "with the gate",
        "result": f"{int(checks['passed'].sum())} passed, {failed} failed",
        "verdict": "warning" if failed else "good",
    })
if not cov.empty:
    rows.append({
        "stage": "fact table",
        "when": str(cov["last_hour"].max())[:16],
        "result": f"{int(cov['hours'].sum()):,} rows across {len(cov)} series",
        "verdict": "good",
    })
if not comp.empty:
    partial = int((comp["verdict"] == "partial").sum())
    rows.append({
        "stage": "completeness",
        "when": "last logged run",
        "result": f"{len(comp)} days, {partial} partial",
        "verdict": "serious" if partial else "good",
    })
lin = data.lineage()
if not lin.empty:
    nodes = len(set(lin["source_node"]) | set(lin["target_node"]))
    rows.append({"stage": "lineage", "when": "last build",
                 "result": f"{len(lin)} edges across {nodes} nodes", "verdict": "good"})

if not rows:
    st.info("Nothing has run yet against this warehouse.")
else:
    for r in rows:
        a, b, d = st.columns([2, 2, 5])
        a.markdown(theme.chip(r["stage"], r["verdict"]), unsafe_allow_html=True)
        b.markdown(f'<span class="mt-kv">{r["when"]}</span>', unsafe_allow_html=True)
        d.markdown(f'<span class="mt-kv">{r["result"]}</span>', unsafe_allow_html=True)

theme.note(
    "This is a portfolio project. It has no real users and nothing depends on it. "
    "Saying so plainly is stronger than implying otherwise."
)
