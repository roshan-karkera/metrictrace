"""
MetricTrace, ticket intake and triage.

Somebody says a number looks wrong. What happens next should not depend on who
said it or how annoyed they sounded, so the priority is decided by a rule
written down here and applied by code.

The rule, in order. The first matching line wins:

    P1  the metric is currently trusted and published, and the reporter saw a
        figure that disagrees. A number people believe may be wrong, and it is
        in circulation right now
    P2  the metric is currently PARTIAL. The platform already knows the figure
        is understated, so the report is probably correct and the cause is
        probably already recorded
    P3  the metric is currently REFUSED, or an incident is already open against
        a series it depends on. This is not a new investigation, it is a
        duplicate of something being worked on, and the reporter needs the
        incident number rather than a new ticket
    P4  the metric is not one this platform owns. Answer with what is owned

The reason P1 is reserved for a trusted metric is that trust is what makes a
wrong number dangerous. A figure the platform has already refused to stand
behind cannot mislead anybody, so it does not deserve the same urgency as one
sitting on a dashboard with a green trust panel above it.

Run from the repo root:
    python -m process.tickets new --metric renewable_share --period 2026-09-01 \\
        --expected "about 70 percent" --saw "41 percent" --where "dashboard" --by "A. Colleague"
    python -m process.tickets list
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import duckdb

from config import DB_PATH, METRICS_FILE
from semantic.engine import load_metrics
from agent.trust import check, OK, PARTIAL, REFUSE

TICKET_LOG = ROOT / "process" / "tickets.md"

DDL = """
CREATE TABLE IF NOT EXISTS ticket (
    ticket_id   VARCHAR,
    raised_at   TIMESTAMP,
    raised_by   VARCHAR,
    metric      VARCHAR,
    period      VARCHAR,
    seen_where  VARCHAR,
    expected    VARCHAR,
    observed    VARCHAR,
    priority    VARCHAR,
    rule        VARCHAR,
    linked      VARCHAR,
    status      VARCHAR
);
"""

REQUIRED = ["metric", "period", "where", "expected", "saw", "by"]


def next_id(con) -> str:
    con.execute(DDL)
    n = con.execute("select count(*) from ticket").fetchone()[0]
    return f"TIC-{n + 1:03d}"


def triage(con, metric: str) -> tuple[str, str, str]:
    """Returns (priority, the rule that fired, linked incidents)."""
    metrics = load_metrics(str(METRICS_FILE))
    if metric not in metrics:
        return ("P4",
                "metric is not owned by this platform. Known metrics: "
                + ", ".join(metrics), "")

    report = check(con, metrics[metric])
    linked = ", ".join(i["incident_id"] for i in report.incidents)

    if report.verdict == REFUSE or linked:
        return ("P3",
                f"platform already refuses or has an open incident. Verdict "
                f"{report.verdict}. Duplicate of work in progress, send the "
                f"incident number rather than opening an investigation", linked)
    if report.verdict == PARTIAL:
        return ("P2",
                "metric is PARTIAL, the platform already reports it as "
                "understated. The report is probably correct and the cause is "
                "probably already recorded", linked)
    return ("P1",
            "metric is trusted and published, so a figure that disagrees means "
            "a number people believe may be wrong and is in circulation now",
            linked)


def cmd_new(con, args) -> int:
    missing = [f for f in REQUIRED if not getattr(args, f.replace("where", "seen_where"), None)
               and not getattr(args, f, None)]
    if missing:
        print("A ticket without these gets sent back, by rule, not by mood: "
              + ", ".join(missing))
        return 1

    priority, rule, linked = triage(con, args.metric)
    tid = next_id(con)
    now = datetime.now(timezone.utc)

    con.execute("INSERT INTO ticket VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                [tid, now, args.by, args.metric, args.period, args.where,
                 args.expected, args.saw, priority, rule, linked, "open"])

    entry = [
        f"\n## {tid}  {args.metric}  {priority}",
        "",
        f"- **Raised:** {now:%Y-%m-%d %H:%M} UTC by {args.by}",
        f"- **Metric and period:** {args.metric}, {args.period}",
        f"- **Seen in:** {args.where}",
        f"- **Expected:** {args.expected}",
        f"- **Observed:** {args.saw}",
        f"- **Priority {priority}, by rule:** {rule}",
    ]
    if linked:
        entry.append(f"- **Linked incidents:** {linked}")
    entry.append("- **Status:** open")
    with TICKET_LOG.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(entry) + "\n")

    print(f"{tid}  {priority}")
    print(f"  rule: {rule}")
    if linked:
        print(f"  linked incidents: {linked}")
    print(f"  written to process/tickets.md")
    return 0


def cmd_list(con) -> int:
    con.execute(DDL)
    rows = con.execute("""
        SELECT ticket_id, priority, metric, period, raised_by, status, linked
        FROM ticket ORDER BY priority, raised_at
    """).fetchall()
    if not rows:
        print("no tickets")
        return 0
    for t, p, m, per, by, st, linked in rows:
        tail = f"  linked {linked}" if linked else ""
        print(f"{t}  {p}  {m:18} {per:12} {by:18} {st}{tail}")
    print(f"\n{len(rows)} ticket(s)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("new")
    for f in ("metric", "period", "where", "expected", "saw", "by"):
        n.add_argument(f"--{f}", required=True)
    sub.add_parser("list")
    args = ap.parse_args()

    con = duckdb.connect(str(DB_PATH))
    try:
        return cmd_new(con, args) if args.cmd == "new" else cmd_list(con)
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
