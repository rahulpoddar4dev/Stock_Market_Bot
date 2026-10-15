# analysis_snapshot.py - Persist the latest analysis context for dashboard display

import json
from pathlib import Path


SNAPSHOT_FILE = Path("logs/latest_analysis.json")


def save_analysis_snapshot(snapshot: dict) -> None:
    SNAPSHOT_FILE.parent.mkdir(exist_ok=True)
    SNAPSHOT_FILE.write_text(json.dumps(snapshot, indent=2, default=str))


def load_analysis_snapshot() -> dict:
    if not SNAPSHOT_FILE.exists():
        return {}
    try:
        data = json.loads(SNAPSHOT_FILE.read_text())
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}
