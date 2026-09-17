"""
Paths, in one place.

DB_PATH is overridable through the METRICTRACE_DB environment variable. That
exists for one reason: agent/eval needs to point the agent at a copy of the
warehouse whose gate decisions have been deliberately changed, so that refusal
behaviour can be measured without touching the real warehouse. Nothing else
should set it.
"""

import os
from pathlib import Path

ROOT = Path(__file__).parent
RAW_DIR = ROOT / "data" / "raw"
METRICS_FILE = ROOT / "semantic" / "metrics.yaml"

DEFAULT_DB = ROOT / "data" / "warehouse.db"
DB_PATH = Path(os.environ.get("METRICTRACE_DB") or DEFAULT_DB)
