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
    page_icon=":material/monitoring:",
    layout="wide",
    initial_sidebar_state="expanded",
)

from app import data, theme   # noqa: E402

theme.install()

# The logo sits above the page navigation, which Streamlit always renders at
# the top of the sidebar regardless of where st.logo is called from in the
# script. Two files, not one, because an externally referenced SVG is a static
# image: it cannot pick up the page's text colour, so light and dark mode each
# get their own wordmark instead of one that goes illegible in the other.
ASSETS = ROOT / "app" / "assets"
st.logo(str(ASSETS / f"logo_{theme.mode()}.svg"),
        size="large",          # the wordmark is the app name, so it should read as one
        icon_image=str(ASSETS / "icon.svg"))

# --------------------------------------------------------------------------
# one list drives both the navigation and the sidebar guide below, so the
# question a page answers can never drift from the page it names.
# --------------------------------------------------------------------------

PAGES = [
    ("views/overview.py",   "Overview",             ":material/dashboard:",
     "Can I believe anything here right now."),
    ("views/metric.py",     "Metric",                ":material/show_chart:",
     "What is this number, and who decided what it means."),
    ("views/what_moved.py", "What moved it",         ":material/compare_arrows:",
     "Why did it change, and how much of the change was what."),
    ("views/quality.py",    "Data quality",          ":material/verified:",
     "What did the checks find, and what is held."),
    ("views/incidents.py",  "Incidents and change",  ":material/report:",
     "What is broken, who owns it, what changes when it is fixed."),
    ("views/lineage.py",    "Lineage",               ":material/account_tree:",
     "If I change this definition, what breaks."),
    ("views/ask.py",        "Ask",                   ":material/chat:",
     "The same questions in a sentence, answered or refused."),
]

# --------------------------------------------------------------------------
# the trust summary, pinned to the sidebar so no page can be read without it
# --------------------------------------------------------------------------

state = data.platform_state()

with st.sidebar:
    st.caption("A number, its definition, and the state of the data behind it.")
    short = {"good": "healthy", "warning": f"{state['blocked']} series held",
             "serious": f"{state['partial_days']} partial day(s)",
             "critical": "not publishable"}[state["level"]]
    theme.chips([(short, state["level"])])
    st.caption(f"Last gate run {state['last_run']}")

    # Open by default. It is the map of the app, and a reader who does not
    # know the app yet is exactly the one who will not think to click it.
    with st.expander("What each page answers", icon=":material/help:", expanded=True):
        for _, title, icon, question in PAGES:
            st.markdown(f"{icon} **{title}**  \n:gray[{question}]")

    st.divider()

pages = [st.Page(path, title=title, icon=icon, default=(path == "views/overview.py"))
         for path, title, icon, _ in PAGES]

st.navigation(pages).run()
