"""Replay scaffold for option-premium strategy testing.

This reads historical CSV data and produces an honest replay summary. It is a
foundation for testing score/entry/exit changes before trusting live mode.
Expected CSV columns: datetime, symbol, open, high, low, close, volume.
"""

import argparse
import csv
from pathlib import Path


def load_rows(path: Path) -> list[dict]:
    with path.open() as csv_file:
        return list(csv.DictReader(csv_file))


def replay(path: Path) -> dict:
    rows = load_rows(path)
    return {
        "file": str(path),
        "candles": len(rows),
        "note": "Replay loader ready. Strategy scoring hooks can be connected to collected option candles.",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_path")
    args = parser.parse_args()
    result = replay(Path(args.csv_path))
    for key, value in result.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
