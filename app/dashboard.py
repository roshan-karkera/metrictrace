"""
MetricTrace, the dashboard.

The rule this app exists to demonstrate: a number is never shown without the
things that decide whether you may believe it. Its definition, the state of the
series feeding it, and anything currently known to be wrong with that data.

Why it is several pages rather than one. The platform answers four different
questions and they belong to different people on different days:

    Overview      can I believe anything here right now
    Metric        what is this number, and who decided what it means
    What moved it why did it change, and how much of the change was what
    Data quality  what did the checks find, and what is held
    Incidents     what is broken, who owns it, what changes when it is fixed
    Lineage       if I change this definition, what breaks
    Ask           the same questions in a sentence, answered or refused

Putting all of that on one scrolling page made the trust panel decoration, which
is exactly the failure the project is about. The panel only works if you cannot
get to a number without passing it, so it is the landing page and it is the
sidebar on every other page.

The page files live in app/views rather than app/pages on purpose. A folder
called `pages` next to the entrypoint triggers Streamlit's automatic multipage
discovery, which builds a second navigation out of the file names, runs each
script standalone and ignores everything st.navigation declares. Two navigations
in one sidebar is precisely the mess this rewrite was meant to remove.

Run from the repo root:
    streamlit run app/dashboard.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))   # pages run in this process, so this covers them all

import streamlit as st

st.set_page_config(
    page_title="MetricTrace",
    page_icon="//",
    layout="wide",
    initial_sidebar_state="expanded",
)

from app import data, theme   # noqa: E402

theme.install()

# --------------------------------------------------------------------------
# the trust summary, pinned to the sidebar so no page can be read without it
# --------------------------------------------------------------------------

state = data.platform_state()

with st.sidebar:
    st.markdown("### MetricTrace")
    st.caption("A number, its definition, and the state of the data behind it.")
    short = {"good": "healthy", "warning": f"{state['blocked']} series held",
             "serious": f"{state['partial_days']} partial day(s)",
             "critical": "not publishable"}[state["level"]]
    theme.chips([(short, state["level"])])
    st.caption(f"Last gate run {state['last_run']}")
    st.divider()

pages = [
    st.Page("views/overview.py",     title="Overview",     icon=":material/dashboard:",     default=True),
    st.Page("views/metric.py",       title="Metric",       icon=":material/show_chart:"),
    st.Page("views/what_moved.py",   title="What moved it", icon=":material/compare_arrows:"),
    st.Page("views/quality.py",      title="Data quality", icon=":material/verified:"),
    st.Page("views/incidents.py",    title="Incidents and change", icon=":material/report:"),
    st.Page("views/lineage.py",      title="Lineage",      icon=":material/account_tree:"),
    st.Page("views/ask.py",          title="Ask",          icon=":material/chat:"),
]

st.navigation(pages).run()
