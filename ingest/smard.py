"""
MetricTrace, ingestion from SMARD.

Lands raw SMARD JSON immutably and records every fetch in an ingest log.
No transformation happens here. Nothing in this file interprets the data.

Run:
    python -m ingest.smard --weeks 8
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import requests

# Paths come from config.py (convention 11). They used to be relative to the
# working directory, which was invisible for as long as the only caller was a
# terminal sitting in the repository root. Airflow runs workers from somewhere
# else, and this module would have quietly landed a second warehouse there.
from config import DB_PATH, RAW_DIR

BASE = "https://www.smard.de/app/chart_data"
REGION = "DE"
RESOLUTION = "hour"

# Fill these in from what you found in the network tab.
# Keep it to two or three. More sources is not more impressive.
FILTERS = {
    "410":  "total_consumption",
    "1223": "brown_coal",
    "1224": "nuclear",
    "1225": "wind_offshore",
    "1226": "hydro",
    "1227": "other_conventional",
    "1228": "other_renewable",
    "4066": "biomass",
    "4067": "wind_onshore",
    "4068": "solar",
    "4069": "hard_coal",
    "4070": "pumped_storage",
    "4071": "natural_gas",
}
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "MetricTrace/0.1 (portfolio project)"})


# --------------------------------------------------------------------------
# fetching
# --------------------------------------------------------------------------

def get_json(url: str, retries: int = 3, backoff: float = 2.0) -> dict:
    """GET with a small retry. Raises on final failure so the caller can log it."""
    last = None
    for attempt in range(retries):
        try:
            r = SESSION.get(url, timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception as exc:          # noqa: BLE001
            last = exc
            if attempt < retries - 1:
                time.sleep(backoff * (attempt + 1))
    raise RuntimeError(f"failed after {retries} attempts: {url}") from last


def available_weeks(filter_code: str) -> list[int]:
    """Week-start timestamps (epoch ms) SMARD has data for, oldest first."""
    url = f"{BASE}/{filter_code}/{REGION}/index_{RESOLUTION}.json"
    return get_json(url)["timestamps"]


def fetch_week(filter_code: str, week_ts: int) -> dict:
    """One week of hourly values. Note SMARD repeats filter and region in the filename."""
    url = (
        f"{BASE}/{filter_code}/{REGION}/"
        f"{filter_code}_{REGION}_{RESOLUTION}_{week_ts}.json"
    )
    return get_json(url)


# --------------------------------------------------------------------------
# landing, immutably
# --------------------------------------------------------------------------

def raw_path(filter_code: str, week_ts: int) -> Path:
    return RAW_DIR / filter_code / REGION / RESOLUTION / f"{week_ts}.json"


def land(filter_code: str, week_ts: int, payload: dict) -> tuple[Path, str, bool]:
    """
    Write the payload exactly as received. Returns (path, sha256, was_new).

    Idempotent: if the file exists with identical content we do not rewrite it.
    If it exists with DIFFERENT content, SMARD has restated the week. We keep
    the original and write the new one beside it, because silently overwriting
    history is how you lose the ability to explain a number later.
    """
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(body).hexdigest()

    path = raw_path(filter_code, week_ts)
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        existing = hashlib.sha256(path.read_bytes()).hexdigest()
        if existing == digest:
            return path, digest, False
        restated = path.with_name(f"{week_ts}__restated_{digest[:8]}.json")
        restated.write_bytes(body)
        return restated, digest, True

    path.write_bytes(body)
    return path, digest, True


# --------------------------------------------------------------------------
# ingest log
# --------------------------------------------------------------------------

DDL = """
CREATE TABLE IF NOT EXISTS ingest_log (
    fetched_at    TIMESTAMP,
    source        VARCHAR,
    filter_code   VARCHAR,
    filter_name   VARCHAR,
    region        VARCHAR,
    resolution    VARCHAR,
    week_ts       BIGINT,
    week_start    TIMESTAMP,
    row_count     INTEGER,
    null_count    INTEGER,
    content_sha   VARCHAR,
    was_new       BOOLEAN,
    status        VARCHAR,
    message       VARCHAR
);
"""


def log_fetch(con, **row) -> None:
    cols = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    con.execute(f"INSERT INTO ingest_log ({cols}) VALUES ({marks})", list(row.values()))


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def ingest(weeks: int) -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB_PATH))
    con.execute(DDL)

    for code, label in FILTERS.items():
        try:
            index = available_weeks(code)
        except Exception as exc:                                   # noqa: BLE001
            log_fetch(
                con, fetched_at=datetime.now(timezone.utc), source="smard",
                filter_code=code, filter_name=label, region=REGION,
                resolution=RESOLUTION, week_ts=None, week_start=None,
                row_count=None, null_count=None, content_sha=None,
                was_new=False, status="failed", message=f"index: {exc}",
            )
            print(f"[{label}] index failed: {exc}")
            continue

        # Skip the most recent week: it is still being written and would
        # give you a partial load on every run.
        targets = index[-(weeks + 1):-1]

        for ts in targets:
            week_start = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
            try:
                payload = fetch_week(code, ts)
                series = payload.get("series", [])
                nulls = sum(1 for r in series if r[1] is None)
                path, sha, was_new = land(code, ts, payload)

                log_fetch(
                    con, fetched_at=datetime.now(timezone.utc), source="smard",
                    filter_code=code, filter_name=label, region=REGION,
                    resolution=RESOLUTION, week_ts=ts, week_start=week_start,
                    row_count=len(series), null_count=nulls, content_sha=sha,
                    was_new=was_new, status="ok", message=str(path),
                )
                flag = "new" if was_new else "unchanged"
                print(f"[{label}] {week_start:%Y-%m-%d}  {len(series):>4} rows  "
                      f"{nulls:>3} null  {flag}")

            except Exception as exc:                               # noqa: BLE001
                log_fetch(
                    con, fetched_at=datetime.now(timezone.utc), source="smard",
                    filter_code=code, filter_name=label, region=REGION,
                    resolution=RESOLUTION, week_ts=ts, week_start=week_start,
                    row_count=None, null_count=None, content_sha=None,
                    was_new=False, status="failed", message=str(exc),
                )
                print(f"[{label}] {week_start:%Y-%m-%d} FAILED: {exc}")

    con.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--weeks", type=int, default=8,
                    help="how many complete weeks to land, most recent first")
    ingest(ap.parse_args().weeks)
