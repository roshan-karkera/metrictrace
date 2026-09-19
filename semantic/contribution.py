"""
MetricTrace, contribution analysis.

A number moved. This answers what moved it, by series, and it does so using the
same filters the semantic layer uses, so the decomposition and the headline
figure can never disagree.

Two cases, and they are genuinely different.

An absolute figure decomposes cleanly. The change in the total is the sum of
the changes in its parts, so each series contributes exactly its own delta and
the shares add to one hundred percent.

A ratio does not. r = N / D, and a change in r can come from the numerator, the
denominator, or both, and there is no decomposition that is simultaneously
additive, symmetric and free of an arbitrary choice. So this module makes the
choice explicitly rather than hiding it:

    dr  =  (N1 - N0) / D1        the numerator effect, evaluated at the new
                                 denominator
         +  N0 * (1/D1 - 1/D0)   the denominator effect, evaluated at the old
                                 numerator

Those two terms sum exactly to r1 - r0, which is checked in code, not assumed.
The convention is the standard first term at new base, and reversing the order
would attribute the interaction to the other side. Any reader is entitled to
know which was chosen, so it is printed with the output and recorded here.

Series level contributions are then attributed within each effect in proportion
to that series' own change, because a series can sit in the numerator, the
denominator, or both, and the attribution has to say which.

Run from the repo root:
    python -m semantic.contribution total_generation 2026-08-01 2026-09-01 --grain month
    python -m semantic.contribution renewable_share 2026-09-05 2026-09-06
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import duckdb

from config import DB_PATH, METRICS_FILE
from semantic.engine import load_metrics, Metric, _filter_sql

CONVENTION = ("numerator effect at the new denominator, "
              "denominator effect at the old numerator")


@dataclass
class SeriesMove:
    series: str
    category: str
    role: str            # numerator, denominator, or both
    value_a: float | None
    value_b: float | None
    delta: float
    effect: float        # this series' effect on the metric itself
    share: float         # share of the total movement, as a fraction


@dataclass
class Decomposition:
    metric: str
    is_ratio: bool
    grain: str
    period_a: str
    period_b: str
    value_a: float | None
    value_b: float | None
    change: float | None
    numerator_effect: float | None
    denominator_effect: float | None
    moves: list
    residual: float
    note: str


def _series_totals(con, spec: dict, grain: str, period: str) -> dict:
    """Total per series for one period, using the metric's own filter."""
    where, params = _filter_sql(spec)
    rows = con.execute(f"""
        SELECT d.series_name, d.category, SUM(f.value)
        FROM fact_series_hourly f
        JOIN dim_series d ON d.series_key = f.series_key
        WHERE date_trunc('{grain}', f.ts_utc) = TIMESTAMP '{period}'
          AND {where}
        GROUP BY 1, 2
    """, params).fetchall()
    return {r[0]: (r[1], r[2]) for r in rows}


def decompose(con, metric: Metric, period_a: str, period_b: str,
              grain: str = "day") -> Decomposition:
    num_a = _series_totals(con, metric.numerator, grain, period_a)
    num_b = _series_totals(con, metric.numerator, grain, period_b)

    n_a = sum(v for _, v in num_a.values()) if num_a else None
    n_b = sum(v for _, v in num_b.values()) if num_b else None

    if not metric.is_ratio:
        # Clean case. Each series contributes its own delta and they add up.
        names = sorted(set(num_a) | set(num_b))
        total_delta = (n_b or 0.0) - (n_a or 0.0)
        moves = []
        for s in names:
            ca, va = num_a.get(s, (None, None))
            cb, vb = num_b.get(s, (None, None))
            delta = (vb or 0.0) - (va or 0.0)
            moves.append(SeriesMove(
                s, cb or ca or "", "numerator", va, vb, delta, delta,
                (delta / total_delta) if total_delta else 0.0))
        moves.sort(key=lambda m: abs(m.effect), reverse=True)
        residual = total_delta - sum(m.effect for m in moves)
        return Decomposition(metric.name, False, grain, period_a, period_b,
                             n_a, n_b, total_delta, None, None, moves,
                             residual, "absolute figure, decomposition is exact")

    den_a = _series_totals(con, metric.denominator, grain, period_a)
    den_b = _series_totals(con, metric.denominator, grain, period_b)
    d_a = sum(v for _, v in den_a.values()) if den_a else None
    d_b = sum(v for _, v in den_b.values()) if den_b else None

    if not d_a or not d_b:
        # Convention 7. A zero or absent denominator is not a zero share, so
        # there is nothing to decompose and saying so beats inventing terms.
        return Decomposition(metric.name, True, grain, period_a, period_b,
                             None, None, None, None, None, [], 0.0,
                             "denominator missing or zero in at least one "
                             "period, the ratio is undefined there and no "
                             "decomposition exists")

    r_a, r_b = (n_a or 0.0) / d_a, (n_b or 0.0) / d_b
    change = r_b - r_a
    num_effect = ((n_b or 0.0) - (n_a or 0.0)) / d_b
    den_effect = (n_a or 0.0) * (1.0 / d_b - 1.0 / d_a)

    num_delta_total = sum(
        (num_b.get(s, (None, 0.0))[1] or 0.0) - (num_a.get(s, (None, 0.0))[1] or 0.0)
        for s in set(num_a) | set(num_b))
    den_delta_total = sum(
        (den_b.get(s, (None, 0.0))[1] or 0.0) - (den_a.get(s, (None, 0.0))[1] or 0.0)
        for s in set(den_a) | set(den_b))

    moves, names = [], sorted(set(num_a) | set(num_b) | set(den_a) | set(den_b))
    for s in names:
        in_num, in_den = s in num_a or s in num_b, s in den_a or s in den_b
        role = "both" if in_num and in_den else ("numerator" if in_num else "denominator")

        va = (num_a.get(s) or den_a.get(s) or (None, None))[1]
        vb = (num_b.get(s) or den_b.get(s) or (None, None))[1]
        cat = (num_b.get(s) or num_a.get(s) or den_b.get(s) or den_a.get(s))[0]

        eff = 0.0
        if in_num and num_delta_total:
            d = (num_b.get(s, (None, 0.0))[1] or 0.0) - (num_a.get(s, (None, 0.0))[1] or 0.0)
            eff += num_effect * (d / num_delta_total)
        if in_den and den_delta_total:
            d = (den_b.get(s, (None, 0.0))[1] or 0.0) - (den_a.get(s, (None, 0.0))[1] or 0.0)
            eff += den_effect * (d / den_delta_total)

        moves.append(SeriesMove(s, cat, role, va, vb, (vb or 0.0) - (va or 0.0),
                                eff, (eff / change) if change else 0.0))

    moves.sort(key=lambda m: abs(m.effect), reverse=True)
    residual = change - (num_effect + den_effect)
    return Decomposition(metric.name, True, grain, period_a, period_b,
                         r_a, r_b, change, num_effect, den_effect, moves,
                         residual, CONVENTION)


def render(d: Decomposition, unit: str) -> str:
    def fmt(v):
        if v is None:
            return "undefined"
        return f"{v:.1%}" if unit == "ratio" else f"{v:,.0f}"

    out = [f"{d.metric}  {d.period_a} to {d.period_b}  (grain {d.grain})", ""]
    out.append(f"  {d.period_a:12} {fmt(d.value_a)}")
    out.append(f"  {d.period_b:12} {fmt(d.value_b)}")
    out.append(f"  change       {fmt(d.change)}")
    if not d.moves:
        out.append("")
        out.append(f"  {d.note}")
        return "\n".join(out)

    if d.is_ratio:
        out.append("")
        out.append(f"  numerator effect    {fmt(d.numerator_effect)}")
        out.append(f"  denominator effect  {fmt(d.denominator_effect)}")
        out.append(f"  convention          {d.note}")
    out.append("")
    out.append(f"  {'series':22} {'role':12} {'effect':>14}  share")
    for m in d.moves:
        if abs(m.share) < 0.0005:
            continue
        out.append(f"  {m.series:22} {m.role:12} {fmt(m.effect):>14}  {m.share:6.1%}")
    out.append("")
    out.append(f"  residual after decomposition: {d.residual:.2e}  "
               f"(exact decomposition means this is zero to floating point)")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("metric")
    ap.add_argument("period_a")
    ap.add_argument("period_b")
    ap.add_argument("--grain", default="day", choices=["day", "month"])
    args = ap.parse_args()

    metrics = load_metrics(str(METRICS_FILE))
    if args.metric not in metrics:
        sys.exit(f"unknown metric. known: {', '.join(metrics)}")
    m = metrics[args.metric]

    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        d = decompose(con, m, args.period_a, args.period_b, args.grain)
        print(render(d, m.unit))
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
