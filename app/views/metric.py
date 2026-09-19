"""
Metric: what this number is, who decided what it means, and whether the data
behind it is in a state where you may quote it.

The definition is not in a tooltip and not on another page. It sits next to the
figure, because every metric dispute worth having is a dispute about the
definition and not about the arithmetic.
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
from semantic.engine import compute

theme.install()
c = theme.C()

st.title("Metric")

metrics = data.metrics()
if not metrics:
    st.error("No metrics defined in semantic/metrics.yaml.")
    st.stop()

top = st.columns([3, 2, 2])
with top[0]:
    name = st.selectbox("Metric", list(metrics),
                        format_func=lambda n: metrics[n].label)
with top[1]:
    period = st.radio("Period", ["day", "month", "hour"], horizontal=True)

m = metrics[name]
st.session_state["metric"] = name      # what the other pages open with
st.session_state["period"] = period

# --------------------------------------------------------------------------
# the definition, beside the number rather than behind it
# --------------------------------------------------------------------------

st.markdown(
    f'<div class="mt-def"><b>{m.label}</b><br>'
    f'{" ".join(m.definition.split())}<br><br>'
    f'<span class="mt-kv">grain <b>{m.grain}</b> &nbsp;·&nbsp; unit <b>{m.unit}</b>'
    f' &nbsp;·&nbsp; owner <b>{m.owner}</b> &nbsp;·&nbsp; version <b>{m.version}</b>,'
    f' last changed {m.last_changed}</span></div>',
    unsafe_allow_html=True)

if m.denominator_note:
    theme.note("<b>Denominator.</b> " + " ".join(m.denominator_note.split()))

# --------------------------------------------------------------------------
# trust, specific to this metric rather than to the platform
# --------------------------------------------------------------------------

gate = data.latest_gate()
held = set(gate.loc[gate["decision"] != "publish", "series"]) if not gate.empty else set()
inputs = data.series_behind(m)
missing = [s for s in inputs if s in held]

st.markdown("**Series this definition selects**")
# Only the exception is coloured. Painting eleven healthy series green makes the
# one held series compete for attention with a wall of confirmation.
theme.chips([(s, "critical") for s in missing] +
            [(s, "quiet") for s in inputs if s not in missing])

if missing:
    one = len(missing) == 1
    st.warning(
        f"This figure is computed **without** {', '.join(missing)}, because the "
        f"gate is holding {'it' if one else 'them'}. It is not wrong, it is "
        f"partial, and the difference matters the moment somebody quotes it.")

rows = compute(data.con(), m, period)
df = pd.DataFrame(rows, columns=["period_start", "value"])

if df.empty:
    st.warning("The definition returned no periods. Nothing is published that it selects.")
    st.stop()

defined = df.dropna(subset=["value"])
undefined = len(df) - len(defined)

# --------------------------------------------------------------------------
# the headline figure. One number is a stat tile, not a chart.
# --------------------------------------------------------------------------

if len(defined) >= 2:
    latest, prev = defined.iloc[-1], defined.iloc[-2]
    change = latest["value"] - prev["value"]
    h1, h2, h3 = st.columns(3)
    h1.metric(f"Latest {period}", theme.fmt(latest["value"], m.unit),
              help=str(latest["period_start"])[:19])
    h2.metric(f"Previous {period}", theme.fmt(prev["value"], m.unit),
              help=str(prev["period_start"])[:19])
    # No delta pill here: the value of this tile already is the change, and
    # printing it twice invites the reader to look for a difference.
    h3.metric("Change", ("+" if change >= 0 else "") + theme.fmt(change, m.unit))
elif len(defined) == 1:
    st.metric(f"Latest {period}", theme.fmt(defined.iloc[0]["value"], m.unit))

theme.hairline()

# --------------------------------------------------------------------------
# the series. One measure, one axis, thin line, hairline grid.
# --------------------------------------------------------------------------

axis_fmt = "%" if m.unit == "ratio" else "~s"
hover = alt.selection_point(fields=["period_start"], nearest=True,
                            on="pointermove", empty=False)

base = alt.Chart(defined).encode(
    x=alt.X("period_start:T", title=None),
    y=alt.Y("value:Q", title=None,
            axis=alt.Axis(format=axis_fmt),
            scale=alt.Scale(zero=(m.unit != "ratio"))),
)
line = base.mark_line(color=c["series_1"], strokeWidth=2)
points = base.mark_point(size=70, color=c["series_1"], filled=True,
                         stroke=c["surface"], strokeWidth=2).encode(
    opacity=alt.condition(hover, alt.value(1), alt.value(0)),
    tooltip=[alt.Tooltip("period_start:T", title=period),
             alt.Tooltip("value:Q", title=m.label,
                         format=".1%" if m.unit == "ratio" else ",.0f")],
).add_params(hover)
crosshair = base.mark_rule(color=c["axis"], strokeWidth=1).encode(
    opacity=alt.condition(hover, alt.value(1), alt.value(0)))

# One series needs no legend box. The endpoint is labelled instead.
end = defined.tail(1)
endpoint = alt.Chart(end).mark_text(
    align="left", dx=8, dy=0, color=c["ink_2"], fontSize=11, fontWeight=600
).encode(x="period_start:T", y="value:Q",
         text=alt.Text("value:Q", format=".1%" if m.unit == "ratio" else ",.0f"))

st.altair_chart((crosshair + line + points + endpoint).properties(height=300),
                width="stretch")

if m.unit == "ratio":
    v = defined["value"]
    st.caption(f"{len(v)} {period}s. min {v.min():.1%}, mean {v.mean():.1%}, "
               f"max {v.max():.1%}.")
else:
    st.caption(
        "The axis carries no unit. ADR 0002: the unit SMARD returns has not been "
        "confirmed from their documentation, and an unlabelled axis is honest "
        "where a guessed label is not.")

if undefined:
    st.warning(
        f"{undefined} {period}(s) are **undefined rather than zero**. The "
        "denominator was zero, which makes the share an unanswerable question "
        "and not 0 percent. Writing `ELSE 0` here is how INC-003 happened.")
    with st.expander(f"The {undefined} undefined {period}(s)"):
        st.write([str(x)[:19] for x in df[df["value"].isna()]["period_start"]])

with st.expander("Table view"):
    out = df.copy()
    out["value"] = out["value"].apply(lambda v: theme.fmt(v, m.unit))
    st.dataframe(out, width="stretch", hide_index=True)

st.page_link("views/what_moved.py", label="Attribute a change in this metric",
             icon=":material/compare_arrows:")
