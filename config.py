"""
Paths, in one place.

DB_PATH is overridable through the METRICTRACE_DB environment variable. That
exists for one reason: agent/eval needs to point the agent at a copy of the
warehouse whose gate decisions have been deliberately changed, so that refusal
behaviour can be measured without touching the real warehouse. Nothing else
should set it.

.env is loaded here rather than in agent/graph.py, because app/views/ask.py
checks os.environ for GROQ_API_KEY before agent.graph is ever imported. Almost
every module imports this one first, so this is the one place a load happens
early enough to matter.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")

RAW_DIR = ROOT / "data" / "raw"
METRICS_FILE = ROOT / "semantic" / "metrics.yaml"

DEFAULT_DB = ROOT / "data" / "warehouse.db"
DB_PATH = Path(os.environ.get("METRICTRACE_DB") or DEFAULT_DB)
