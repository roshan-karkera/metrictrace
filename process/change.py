"""
MetricTrace, change management for metric definitions.

A metric definition is the one thing in this platform that everything else
agrees to. Change it quietly and every dashboard, agent answer and evaluation
case silently means something different from what it meant yesterday, while
continuing to look exactly the same. That is the failure this module exists to
prevent.

The rule is simple and it is enforced rather than requested:

    a changed definition may not be approved until the impact has been listed
    and the version has been bumped

Version bumping is not bureaucracy here. `version` and `last_changed` are what
the dashboard prints next to the number and what the agent cites in its
evidence. A definition that changes without them tells every downstream reader
that nothing happened.

Run from the repo root:
    python -m process.change status
    python -m process.change impact renewable_share
    python -m process.change approve renewable_share --by "Roshan Karkera" --reason "..."
    python -m process.change baseline        # record today's file as approved, once
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import duckdb

from config import DB_PATH, METRICS_FILE
from semantic.engine import load_metrics
from models.lineage import _walk

CHANGE_LOG = ROOT / "process" / "change_log.md"

DDL = """
CREATE TABLE IF NOT EXISTS metric_definition (
    metric        VARCHAR,
    version       INTEGER,
    body_hash     VARCHAR,
    body          VARCHAR,
    approved_by   VARCHAR,
    approved_at   TIMESTAMP,
    rationale     VARCHAR,
    impact        VARCHAR
);
"""

# Fields that change what the number means. A change to any of these is a
# definition change and needs the full process. Everything else, for example a
# typo in the label, is cosmetic.
MEANING_FIELDS = ["numerator", "denominator", "grain", "unit", "definition",
                  "denominator_note"]


@dataclass
class Diff:
    metric: str
    status: str            # new, unchanged, changed, changed_without_version_bump
    fields: list
    old_version: int | None
    new_version: int


def _body(m) -> dict:
    """Canonical form of the fields that decide what the number means."""
    raw = {
        "numerator": m.numerator,
        "denominator": m.denominator,
        "grain": m.grain,
        "unit": m.unit,
        "definition": " ".join((m.definition or "").split()),
        "denominator_note": " ".join((m.denominator_note or "").split()),
    }
    return raw


def _hash(body: dict) -> str:
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()[:16]


def approved(con) -> dict:
    """Latest approved definition per metric."""
    con.execute(DDL)
    rows = con.execute("""
        SELECT metric, version, body_hash, body, approved_by, approved_at
        FROM metric_definition d
        WHERE approved_at = (SELECT max(approved_at) FROM metric_definition
                             WHERE metric = d.metric)
    """).fetchall()
    return {r[0]: {"version": r[1], "hash": r[2], "body": json.loads(r[3]),
                   "by": r[4], "at": r[5]} for r in rows}


def diff(con) -> list[Diff]:
    live = load_metrics(str(METRICS_FILE))
    base = approved(con)
    out = []
    for name, m in live.items():
        body = _body(m)
        h = _hash(body)
        prev = base.get(name)
        if prev is None:
            out.append(Diff(name, "new", sorted(body), None, m.version))
            continue
        if h == prev["hash"]:
            out.append(Diff(name, "unchanged", [], prev["version"], m.version))
            continue
        fields = [f for f in MEANING_FIELDS
                  if body.get(f) != prev["body"].get(f)]
        status = ("changed" if m.version > prev["version"]
                  else "changed_without_version_bump")
        out.append(Diff(name, status, fields, prev["version"], m.version))
    for name in base:
        if name not in live:
            out.append(Diff(name, "removed", [], base[name]["version"], 0))
    return out


def impact(con, metric: str) -> tuple[list, list]:
    """What feeds this metric, and what reads it. The second list is the one
    that matters for a change: those are the surfaces that will mean something
    different tomorrow."""
    node = f"metric:{metric}"
    upstream, downstream, seen_u, seen_d = [], [], set(), set()
    for _, name, transform in _walk(con, node, "up"):
        if name not in seen_u:
            seen_u.add(name); upstream.append((name, transform))
    for _, name, transform in _walk(con, node, "down"):
        if name not in seen_d:
            seen_d.add(name); downstream.append((name, transform))
    return upstream, downstream


def render_impact(metric: str, upstream, downstream) -> str:
    lines = [f"Impact analysis for {metric}", ""]
    lines.append(f"  reads from ({len(upstream)}):")
    for name, tr in upstream or [("nothing recorded", "")]:
        lines.append(f"    {name}" + (f"   [{tr}]" if tr else ""))
    lines.append(f"  read by ({len(downstream)}):")
    for name, tr in downstream or [("nothing recorded", "")]:
        lines.append(f"    {name}" + (f"   [{tr}]" if tr else ""))
    return "\n".join(lines)


def cmd_status(con) -> int:
    rows = diff(con)
    if not rows:
        print("no metrics found")
        return 0
    blocked = 0
    for d in rows:
        if d.status == "unchanged":
            print(f"  ok        {d.metric:18} v{d.new_version}")
        elif d.status == "new":
            print(f"  NEW       {d.metric:18} v{d.new_version}, never approved")
        elif d.status == "removed":
            print(f"  REMOVED   {d.metric:18} was v{d.old_version}, no longer in metrics.yaml")
        elif d.status == "changed":
            print(f"  CHANGED   {d.metric:18} v{d.old_version} -> v{d.new_version}, "
                  f"fields: {', '.join(d.fields)}")
        else:
            blocked += 1
            print(f"  BLOCKED   {d.metric:18} definition changed but version is still "
                  f"v{d.new_version}. Fields: {', '.join(d.fields)}")
    if blocked:
        print(f"\n{blocked} change(s) cannot be approved. Bump version and "
              f"last_changed in semantic/metrics.yaml first.")
        print("The dashboard and the agent both print the version next to the "
              "number. Changing meaning without changing it tells every reader "
              "that nothing happened.")
    return 1 if blocked else 0


def cmd_approve(con, metric: str, by: str, reason: str) -> int:
    rows = {d.metric: d for d in diff(con)}
    d = rows.get(metric)
    if d is None:
        print(f"unknown metric {metric}")
        return 1
    if d.status == "unchanged":
        print(f"{metric} is unchanged against the approved definition, nothing to approve")
        return 0
    if d.status == "changed_without_version_bump":
        print(f"REFUSED. {metric} changed {', '.join(d.fields)} but version is still "
              f"v{d.new_version}. Bump version and last_changed first.")
        return 1

    up, down = impact(con, metric)
    text = render_impact(metric, up, down)
    print(text)

    live = load_metrics(str(METRICS_FILE))[metric]
    body = _body(live)
    now = datetime.now(timezone.utc)
    con.execute(DDL)
    con.execute("INSERT INTO metric_definition VALUES (?,?,?,?,?,?,?,?)",
                [metric, live.version, _hash(body), json.dumps(body, default=str),
                 by, now, reason, text])

    entry = [
        f"\n## {now:%Y-%m-%d}  {metric}  "
        f"v{d.old_version if d.old_version is not None else 'new'} to v{live.version}",
        "",
        f"- **Approved by:** {by}",
        f"- **Reason:** {reason}",
        f"- **Fields changed:** {', '.join(d.fields) if d.fields else 'first approval'}",
        "- **Impact analysis, from the lineage table:**",
        "",
        "```",
        text,
        "```",
    ]
    with CHANGE_LOG.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(entry) + "\n")

    print(f"\napproved. {metric} v{live.version} recorded, "
          f"entry appended to process/change_log.md")
    if down:
        print("notify the owners of: " + ", ".join(n for n, _ in down))
    return 0


def cmd_baseline(con) -> int:
    """Record the current file as approved without a change. Used once, when the
    process is introduced to a platform that already has metrics."""
    live = load_metrics(str(METRICS_FILE))
    base = approved(con)
    now = datetime.now(timezone.utc)
    con.execute(DDL)
    n = 0
    for name, m in live.items():
        if name in base:
            continue
        body = _body(m)
        con.execute("INSERT INTO metric_definition VALUES (?,?,?,?,?,?,?,?)",
                    [name, m.version, _hash(body), json.dumps(body, default=str),
                     m.owner, now, "baseline recorded when change management was "
                     "introduced, not an approval of a change", ""])
        n += 1
    print(f"baseline recorded for {n} metric(s)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("baseline")
    pi = sub.add_parser("impact"); pi.add_argument("metric")
    pa = sub.add_parser("approve")
    pa.add_argument("metric")
    pa.add_argument("--by", required=True)
    pa.add_argument("--reason", required=True)
    args = ap.parse_args()

    con = duckdb.connect(str(DB_PATH))
    try:
        if args.cmd == "status":
            return cmd_status(con)
        if args.cmd == "baseline":
            return cmd_baseline(con)
        if args.cmd == "impact":
            up, down = impact(con, args.metric)
            print(render_impact(args.metric, up, down))
            return 0
        if args.cmd == "approve":
            return cmd_approve(con, args.metric, args.by, args.reason)
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
