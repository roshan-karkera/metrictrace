"""
Incidents and change: what is broken, and what happens to a definition when
somebody wants to change it.

These two belong on one page because they are the same question at different
speeds. An incident is the data disagreeing with what the definition promised.
A change request is somebody proposing to move the promise.
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

theme.install()
c = theme.C()

st.title("Incidents and change")

tab_inc, tab_change, tab_tickets = st.tabs(
    ["Open incidents", "Change management", "Tickets"])

# --------------------------------------------------------------------------
with tab_inc:
    st.subheader("Open incidents")
    theme.note(
        "A failed check opens one of these by itself, with its runbook attached. "
        "It has to happen without anybody remembering to write it, because these "
        "records are what the agent reads as evidence when it is asked why a "
        "number moved. One incident per series and check: a recurring failure "
        "stays the same incident until somebody closes it.")

    inc = data.incidents("open")
    if inc.empty:
        st.success("Nothing open.")
    else:
        for _, r in inc.iterrows():
            with st.container(border=True):
                a, b = st.columns([3, 2])
                a.markdown(
                    f'**{r.incident_id}** &nbsp; {theme.chip(r.check_name, "critical")} '
                    f'&nbsp; <b>{r.series}</b>', unsafe_allow_html=True)
                b.markdown(f'<div style="text-align:right" class="mt-kv">'
                           f'opened {str(r.opened_at)[:16]}</div>',
                           unsafe_allow_html=True)
                st.markdown(f'<span class="mt-kv">{r.summary}</span>',
                            unsafe_allow_html=True)
                st.markdown(f'<span class="mt-kv">runbook <code>{r.runbook}</code>'
                            f'</span>', unsafe_allow_html=True)

    allinc = data.incidents(None)
    if not allinc.empty:
        with st.expander(f"All {len(allinc)} incidents ever opened"):
            st.dataframe(allinc, width="stretch", hide_index=True)

# --------------------------------------------------------------------------
with tab_change:
    st.subheader("Change management")
    theme.note(
        "<b>A metric definition may not change without a version bump.</b> The "
        "version and the date are printed beside the number on the metric page "
        "and cited by the agent, so a silent edit would make every one of those "
        "a lie. <code>process/change.py</code> refuses the approval in code "
        "rather than asking politely in a guideline.")

    try:
        from process.change import diff, impact, render_impact
        diffs = diff(data.con())
    except Exception as exc:
        diffs = None
        st.info(f"Change tracking is not initialised yet: {exc}. "
                "Run `python -m process.change baseline`.")

    if diffs is not None:
        moved = [d for d in diffs if d.status != "unchanged"]
        if not moved:
            st.success(f"All {len(diffs)} definitions match their approved baseline.")
        else:
            for d in moved:
                status = d.status
                bad = status == "changed_without_version_bump"
                st.markdown(
                    theme.chip(f"{d.metric}: {status.replace('_', ' ')}",
                               "critical" if bad else "warning"),
                    unsafe_allow_html=True)
                if d.fields:
                    st.markdown(f'<span class="mt-kv">fields that changed: '
                                f'{", ".join(d.fields)}</span>', unsafe_allow_html=True)
                if bad:
                    st.error(
                        f"`{d.metric}` changed meaning without a version bump, so "
                        f"approval is refused. Bump `version` in metrics.yaml and "
                        f"say what changed in the same commit.")

    st.markdown("**Impact of changing a definition**")
    metrics = data.metrics()
    names = list(metrics)
    if names:
        chosen = st.selectbox("Metric", names, format_func=lambda n: metrics[n].label,
                              key="impact_metric")
        try:
            up, down = impact(data.con(), chosen)
            i1, i2 = st.columns(2)
            i1.metric("Upstream nodes", len(up),
                      help="What this definition depends on. Change one of these "
                           "and the number moves without anybody touching the metric.")
            i2.metric("Downstream nodes", len(down),
                      help="What breaks if the definition changes. This list is the "
                           "reason lineage is built at all.")
            cols = st.columns(2)
            with cols[0]:
                st.markdown("**Upstream**")
                for row in up:
                    st.markdown(f'<span class="mt-kv">{" &rarr; ".join(str(x) for x in row[1:])}</span>',
                                unsafe_allow_html=True)
            with cols[1]:
                st.markdown("**Downstream**")
                for row in down:
                    st.markdown(f'<span class="mt-kv">{" &rarr; ".join(str(x) for x in row[1:])}</span>',
                                unsafe_allow_html=True)
            if not down:
                st.warning(
                    "No downstream nodes. Either the lineage table has not been "
                    "built, or this metric is terminal. A metric with nothing "
                    "downstream makes impact analysis report that a definition "
                    "change affects nothing, which is how this was found.")
        except Exception as exc:
            st.info(f"Impact analysis needs the lineage table: {exc}. "
                    "Run `python -m models.lineage`.")

# --------------------------------------------------------------------------
with tab_tickets:
    st.subheader("Tickets")
    theme.note(
        "Priority comes from the rule in <code>process/tickets.py</code>, not from "
        "who raised it. A metric the platform already refuses to publish cannot "
        "mislead anybody, so it is not a P1 however loudly it was reported.")

    tk = data.tickets()
    if tk.empty:
        st.info("No tickets raised. Raise one with `python -m process.tickets new ...`.")
    else:
        sev = {"P1": "critical", "P2": "serious", "P3": "warning", "P4": "good"}
        for _, r in tk.iterrows():
            with st.container(border=True):
                a, b = st.columns([3, 2])
                a.markdown(
                    f'**{r.ticket_id}** &nbsp; {theme.chip(r.priority, sev.get(r.priority, "warning"))}'
                    f' &nbsp; <b>{r.metric}</b>', unsafe_allow_html=True)
                b.markdown(f'<div style="text-align:right" class="mt-kv">'
                           f'{r.status} &nbsp;·&nbsp; {str(r.raised_at)[:16]}</div>',
                           unsafe_allow_html=True)
                st.markdown(f'<span class="mt-kv">rule applied: {r.rule}</span>',
                            unsafe_allow_html=True)
                if r.linked:
                    st.markdown(f'<span class="mt-kv">linked: {r.linked}</span>',
                                unsafe_allow_html=True)
