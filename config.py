from pathlib import Path

ROOT = Path(__file__).parent
RAW_DIR = ROOT / "data" / "raw"
DB_PATH = ROOT / "data" / "warehouse.db"
METRICS_FILE = ROOT / "semantic" / "metrics.yaml"
