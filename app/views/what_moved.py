"""
What moved it: attribute a change between two periods to the series that caused it.

The filters come from the same semantic layer that produced the headline figure,
so the table on this page and the chart on the metric page cannot disagree. A
contribution view that can contradict the number it explains is worse than none.
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
from semantic.contribution import CONVENTION, decompose
from semantic.engine import compute

theme.install()
c = theme.C()

st.title("What moved it")

metrics = data.metrics()
names = list(metrics)
default = st.session_state.get("metric", names[0])

top = st.columns([3, 2])
with top[0]:
    name = st.selectbox("Metric", names, index=names.index(default) if default in names else 0,
                        format_func=lambda n: metrics[n].label)
with top[1]:
    period = st.radio("Period", ["day", "month"], horizontal=True,
                      index=0 if st.session_state.get("period") != "month" else 1)

m = metrics[name]
st.session_state["metric"] = name

rows = compute(data.con(), m, period)
df = pd.DataFrame(rows, columns=["period_start", "value"]).dropna(subset=["value"])
periods = list(df["period_start"])

if len(periods) < 2:
    st.info("Two defined periods are needed before a change can be attributed.")
    st.stop()

pick = st.columns(2)
with pick[0]:
    pa = st.selectbox("From", periods, index=len(periods) - 2,
                      format_func=lambda x: str(x)[:19])
with pick[1]:
    pb = st.selectbox("To", periods, index=len(periods) - 1,
                      format_func=lambda x: str(x)[:19])

if pa == pb:
    st.info("Pick two different periods.")
    st.stop()

d = decompose(data.con(), m, str(pa)[:19], str(pb)[:19], period)
if not d.moves:
    st.warning(d.note)
    st.stop()

f = lambda v: theme.fmt(v, m.unit)

k1, k2, k3 = st.columns(3)
k1.metric(str(pa)[:10], f(d.value_a))
k2.metric(str(pb)[:10], f(d.value_b))
k3.metric("Change", ("+" if d.change >= 0 else "") + f(d.change))

if d.is_ratio:
    st.markdown(
        f'<span class="mt-kv">numerator effect <b>{f(d.numerator_effect)}</b>'
        f' &nbsp;·&nbsp; denominator effect <b>{f(d.denominator_effect)}</b></span>',
        unsafe_allow_html=True)
    theme.note(
        "<b>A ratio has no neutral decomposition.</b> There is no split that is "
        "additive, symmetric and free of an arbitrary choice at the same time, so "
        f"this one is stated rather than hidden: {CONVENTION}. Reversing the order "
        "attributes the interaction to the other side.")

theme.hairline()

# --------------------------------------------------------------------------
# the effects. Polarity, so diverging: one pole pushed it up, the other down.
# --------------------------------------------------------------------------

table = pd.DataFrame([{
    "series": mv.series,
    "category": mv.category,
    "role": mv.role,
    "from": mv.value_a,
    "to": mv.value_b,
    "effect": mv.effect,
    "share": mv.share,
} for mv in d.moves])
table["direction"] = table["effect"].apply(lambda e: "pushed it up" if e >= 0 else "pulled it down")

st.subheader("Effect on the metric, by series")

base = alt.Chart(table).encode(
    y=alt.Y("series:N", sort=alt.EncodingSortField("effect", order="descending"),
            title=None,
            # Every series gets its own label. Letting Vega drop every other one
            # to save space turns the chart into a guessing game.
            axis=alt.Axis(labelOverlap=False, labelLimit=220, labelFontSize=11)),
    x=alt.X("effect:Q", title="effect on the metric",
            axis=alt.Axis(format=".1%" if m.unit == "ratio" else "~s")),
)
bars = base.mark_bar(height=13, cornerRadiusEnd=4).encode(
    color=alt.Color("direction:N",
                    scale=alt.Scale(domain=["pushed it up", "pulled it down"],
                                    range=[c["pos"], c["neg"]]),
                    legend=alt.Legend(title=None)),
    tooltip=[alt.Tooltip("series:N", title="series"),
             alt.Tooltip("category:N", title="category"),
             alt.Tooltip("role:N", title="role in the definition"),
             alt.Tooltip("from:Q", title=str(pa)[:10], format=",.0f"),
             alt.Tooltip("to:Q", title=str(pb)[:10], format=",.0f"),
             alt.Tooltip("effect:Q", title="effect",
                         format=".2%" if m.unit == "ratio" else ",.0f"),
             alt.Tooltip("share:Q", title="share of change", format=".1%")],
)
zero = alt.Chart(pd.DataFrame({"x": [0]})).mark_rule(
    color=c["axis"], strokeWidth=1).encode(x="x:Q")

st.altair_chart((bars + zero).properties(height=max(180, 32 * len(table))),
                width="stretch")

st.caption(
    f"Residual after decomposition {d.residual:.2e}. The parts are checked to add "
    "up to the whole in code rather than assumed. Shares can exceed 100 percent "
    "when movements cancel out, which is a property of the data and not an error.")

with st.expander("Table view", expanded=False):
    show = table.copy()
    show["effect"] = show["effect"].apply(f)
    show["share"] = show["share"].apply(lambda s: f"{s:.1%}")
    show["from"] = show["from"].apply(lambda v: f"{v:,.0f}" if v is not None else "undefined")
    show["to"] = show["to"].apply(lambda v: f"{v:,.0f}" if v is not None else "undefined")
    st.dataframe(show.drop(columns=["direction"]), width="stretch", hide_index=True)
