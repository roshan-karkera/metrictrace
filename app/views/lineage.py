"""
Lineage: where a column came from and what depends on it.

Stored as a queryable table rather than drawn as a picture, because a picture
cannot answer "what breaks if I change this" and a table can. The drawing on
this page is generated from that table, so the two cannot drift apart.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import streamlit as st

from app import data, theme

theme.install()
c = theme.C()

st.title("Lineage")

edges = data.lineage()
if edges.empty:
    st.info("No lineage table. Build it with `python -m models.lineage`.")
    st.stop()

nodes = sorted(set(edges["source_node"]) | set(edges["target_node"]))

k1, k2, k3 = st.columns(3)
k1.metric("Edges", len(edges))
k2.metric("Nodes", len(nodes))
k3.metric("Origins", edges["origin"].nunique())

theme.note(
    "Metrics used to be terminal nodes here, which meant impact analysis "
    "reported that changing a definition affected nothing. The consumer edges "
    "fixed that: a definition now reaches the dashboard, the agent, the trust "
    "check and the golden set, which is what makes the change management page "
    "able to say anything at all.")

# --------------------------------------------------------------------------
# the graph, generated from the table
# --------------------------------------------------------------------------

st.subheader("The graph")

focus = st.selectbox("Centre on", ["everything"] + nodes)

view = edges if focus == "everything" else edges[
    (edges["source_node"] == focus) | (edges["target_node"] == focus)]

def esc(s: str) -> str:
    return '"' + str(s).replace('"', '\\"') + '"'

lines = [
    "digraph lineage {",
    "  rankdir=LR;",
    f'  bgcolor="{c["surface"]}";',
    f'  node [shape=box style="rounded,filled" fontname="Helvetica" fontsize=10'
    f' color="{c["grid"]}" fillcolor="{c["plane"]}" fontcolor="{c["ink"]}"];',
    f'  edge [color="{c["muted"]}" penwidth=0.9 arrowsize=0.7 fontname="Helvetica" fontsize=8'
    f' fontcolor="{c["muted"]}"];',
]
touched = sorted(set(view["source_node"]) | set(view["target_node"]))
for n in touched:
    # Three kinds of node, and the reader should be able to tell them apart:
    # the one being centred on, a metric definition, and everything else.
    if n == focus:
        fill, fc, border = c["series_1"], c["surface"], c["series_1"]
    elif str(n).startswith("metric:"):
        fill, fc, border = c["surface"], c["series_1"], c["series_1"]
    else:
        fill, fc, border = c["plane"], c["ink"], c["axis"]
    lines.append(f'  {esc(n)} [fillcolor="{fill}" fontcolor="{fc}" color="{border}"];')
for _, e in view.iterrows():
    lines.append(f'  {esc(e.source_node)} -> {esc(e.target_node)};')
lines.append("}")

st.graphviz_chart("\n".join(lines), width="stretch")
st.caption(f"{len(view)} of {len(edges)} edges shown.")

theme.hairline()

# --------------------------------------------------------------------------
# the table it was drawn from
# --------------------------------------------------------------------------

st.subheader("Edges")
origins = sorted(edges["origin"].unique())
pick = st.multiselect("Origin", origins, default=origins,
                      help="How the edge was established: read from the code that "
                           "builds the column, or derived from a metric definition.")
st.dataframe(edges[edges["origin"].isin(pick)],
             width="stretch", hide_index=True)
