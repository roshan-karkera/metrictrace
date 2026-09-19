"""
MetricTrace, the dashboard.

The rule this page exists to demonstrate: a number is never shown without the
things that decide whether you may believe it. Beside every metric you get its
definition from semantic/metrics.yaml, the gate decision for the series feeding
it, the freshness of those series, and any incident still open against them.

A normal BI page shows a chart and hopes. This one tells you what it is not
sure about, and refuses to quietly compute around a source that is held.

Run from the repo root:
    streamlit run app/dashboard.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))          # so `semantic.engine` imports when Streamlit runs

import duckdb
import pandas as pd
import streamlit as st

from semantic.engine import load_metrics, compute
from semantic.contribution import decompose, CONVENTION

from config import DB_PATH          # overridable via METRICTRACE_DB, see config.py
METRICS_FILE = ROOT / "semantic" / "metrics.yaml"


# --------------------------------------------------------------------------
# data access. Read only, always. The dashboard never writes to the warehouse.
# --------------------------------------------------------------------------

@st.cache_resource
def get_con():
    return duckdb.connect(str(DB_PATH), read_only=True)


def table_exists(con, name: str) -> bool:
    q = "SELECT count(*) FROM information_schema.tables WHERE table_name = ?"
    return con.execute(q, [name]).fetchone()[0] > 0


def latest_gate(con) -> pd.DataFrame:
    """The most recent gate run only. Older runs are history, not status."""
    if not table_exists(con, "gate_decision"):
        return pd.DataFrame()
    return con.execute("""
        SELECT series, decision, failed_checks, run_at
        FROM gate_decision
        WHERE run_id = (SELECT run_id FROM gate_decision ORDER BY run_at DESC LIMIT 1)
        ORDER BY decision, series
    """).df()


def open_incidents(con) -> pd.DataFrame:
    if not table_exists(con, "incident"):
        return pd.DataFrame()
    return con.execute("""
        SELECT incident_id, series, check_name, summary, runbook, opened_at
        FROM incident
        WHERE status = 'open'
        ORDER BY opened_at
    """).df()


def series_coverage(con) -> pd.DataFrame:
    """What the fact table actually contains, per series, right now."""
    return con.execute("""
        SELECT d.series_name        AS series,
               d.category           AS category,
               count(*)             AS hours,
               min(f.ts_utc)        AS first_hour,
               max(f.ts_utc)        AS last_hour
        FROM fact_series_hourly f
        JOIN dim_series d ON d.series_key = f.series_key
        GROUP BY 1, 2
        ORDER BY 2, 1
    """).df()


def series_behind(con, metric) -> list[str]:
    """
    Which series this metric's definition selects. Read from dim_series using the
    same declarative filter the engine uses, so this cannot drift from the metric.
    """
    wanted = set()
    for spec in (metric.numerator, metric.denominator):
        if not spec:
            continue
        if "categories" in spec:
            rows = con.execute(
                "SELECT series_name FROM dim_series WHERE category IN "
                f"({', '.join('?' * len(spec['categories']))})",
                spec["categories"],
            ).fetchall()
            wanted |= {r[0] for r in rows}
        if "series" in spec:
            wanted |= set(spec["series"])
    return sorted(wanted)


# --------------------------------------------------------------------------
# page
# --------------------------------------------------------------------------

st.set_page_config(page_title="MetricTrace", layout="wide")

con = get_con()
metrics = load_metrics(str(METRICS_FILE))
gate = latest_gate(con)
incidents = open_incidents(con)

held = set(gate.loc[gate["decision"] != "publish", "series"]) if not gate.empty else set()
published = set(gate.loc[gate["decision"] == "publish", "series"]) if not gate.empty else set()

st.title("MetricTrace")
st.caption(
    "Every number on this page carries its definition, the state of the data behind it, "
    "and anything currently known to be wrong with that data."
)

# ---- trust panel, deliberately above the numbers -------------------------

c1, c2, c3, c4 = st.columns(4)
c1.metric("Series published", len(published))
c2.metric("Series held", len(held))
c3.metric("Open incidents", 0 if incidents.empty else len(incidents))
c4.metric("Last gate run", "none" if gate.empty else str(gate["run_at"].max())[:16])

if held:
    st.warning(
        "Held by the quality gate and therefore absent from every number below: "
        + ", ".join(sorted(held))
    )

if not incidents.empty:
    with st.expander(f"Open incidents ({len(incidents)})", expanded=True):
        for _, inc in incidents.iterrows():
            st.markdown(
                f"**{inc.incident_id}**  {inc.series} / {inc.check_name}  \n"
                f"{inc.summary}  \n"
                f"Runbook: `{inc.runbook}`  opened {str(inc.opened_at)[:16]}"
            )

st.divider()

# ---- metric ---------------------------------------------------------------

left, right = st.columns([1, 3])

with left:
    name = st.selectbox("Metric", list(metrics), format_func=lambda n: metrics[n].label)
    period = st.radio("Period", ["day", "month", "hour"], horizontal=True)

m = metrics[name]
inputs = series_behind(con, m)
missing = [s for s in inputs if s in held]

with right:
    st.subheader(m.label)

    # The definition travels with the number. This is the point of the project.
    st.markdown(
        f"**Definition**  {' '.join(m.definition.split())}  \n"
        f"**Grain** {m.grain}  |  **Unit** {m.unit}  |  "
        f"**Owner** {m.owner}  |  **Version** {m.version}, last changed {m.last_changed}"
    )
    if m.denominator_note:
        st.info("Denominator: " + " ".join(m.denominator_note.split()))

    if missing:
        st.error(
            "This metric is computed WITHOUT the following series, because the gate "
            "is holding them: " + ", ".join(missing) + ". Treat the values as partial."
        )

rows = compute(con, m, period)
df = pd.DataFrame(rows, columns=["period_start", "value"]).set_index("period_start")

undefined = df["value"].isna().sum()

if m.unit == "ratio":
    st.line_chart(df, y="value", height=320)
    shown = df["value"].dropna()
    if not shown.empty:
        st.caption(
            f"{len(shown)} {period}s. min {shown.min():.1%}, "
            f"mean {shown.mean():.1%}, max {shown.max():.1%}."
        )
else:
    # ADR 0002: the unit of this series is not confirmed, so nothing here is
    # labelled with one. An unlabelled axis is honest; a wrong label is not.
    st.line_chart(df, y="value", height=320)
    st.caption("Unit unconfirmed, see docs/decisions/0002. Axis deliberately unlabelled.")

if undefined:
    st.warning(
        f"{undefined} {period}(s) are undefined rather than zero: the denominator was "
        "zero, which makes the share an unanswerable question, not 0%."
    )
    with st.expander("Which periods are undefined"):
        st.write(df[df["value"].isna()].index.tolist())

st.divider()

# ---- what moved it --------------------------------------------------------
#
# Same filters as the headline number, because they come from the same
# semantic layer. A contribution table that can disagree with the chart above
# it is worse than no contribution table.

st.subheader("What moved it")

periods = [i for i, v in zip(df.index, df["value"]) if v is not None]
if len(periods) < 2:
    st.caption("Two defined periods are needed before a change can be attributed.")
else:
    ca, cb = st.columns(2)
    with ca:
        pa = st.selectbox("From", periods, index=max(0, len(periods) - 2),
                          format_func=lambda x: str(x)[:19], key="pa")
    with cb:
        pb = st.selectbox("To", periods, index=len(periods) - 1,
                          format_func=lambda x: str(x)[:19], key="pb")

    if pa == pb:
        st.caption("Pick two different periods.")
    else:
        d = decompose(con, m, str(pa)[:19], str(pb)[:19], period)

        if not d.moves:
            st.warning(d.note)
        else:
            fmt = (lambda v: "undefined" if v is None else
                   (f"{v:.1%}" if m.unit == "ratio" else f"{v:,.0f}"))

            k1, k2, k3 = st.columns(3)
            k1.metric(str(pa)[:10], fmt(d.value_a))
            k2.metric(str(pb)[:10], fmt(d.value_b))
            k3.metric("Change", fmt(d.change))

            if d.is_ratio:
                st.markdown(
                    f"**Numerator effect** {fmt(d.numerator_effect)}  |  "
                    f"**Denominator effect** {fmt(d.denominator_effect)}"
                )
                st.info(
                    "A ratio has no decomposition that is additive, symmetric "
                    "and free of an arbitrary choice, so this one is stated "
                    f"rather than hidden: {CONVENTION}. Reversing the order "
                    "would attribute the interaction to the other side."
                )

            table = pd.DataFrame([{
                "series": mv.series,
                "category": mv.category,
                "role": mv.role,
                str(pa)[:10]: mv.value_a,
                str(pb)[:10]: mv.value_b,
                "effect on metric": mv.effect,
                "share of change": mv.share,
            } for mv in d.moves])
            st.dataframe(table, use_container_width=True, hide_index=True)

            st.bar_chart(table.set_index("series")["effect on metric"], height=260)
            st.caption(
                f"Residual after decomposition {d.residual:.2e}. The parts add "
                "up to the whole, checked in code rather than assumed. Shares "
                "can exceed 100 percent when movements cancel out, which is a "
                "property of the data and not an error."
            )

st.divider()

# ---- what is actually in the warehouse ------------------------------------

st.subheader("Series coverage")
cov = series_coverage(con)
if not gate.empty:
    cov = cov.merge(gate[["series", "decision", "failed_checks"]], on="series", how="left")
st.dataframe(cov, use_container_width=True, hide_index=True)
st.caption(
    "Held series keep their existing rows rather than being deleted, so a bad run "
    "never destroys good history. They are simply not refreshed."
)
