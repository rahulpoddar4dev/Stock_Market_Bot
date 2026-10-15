#!/usr/bin/env python3
"""Upload generated CSV files to the configured Google Drive folder."""

import argparse
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

from drive_sync import discover_csv_files, sync_csv_to_drive  # noqa: E402


def parse_args():
    parser = argparse.ArgumentParser(description="Sync trading-bot CSV files to Google Drive via rclone.")
    parser.add_argument("--move", action="store_true", help="Delete local CSV files after successful upload.")
    parser.add_argument(
        "--include-active-logs",
        action="store_true",
        help="Also upload/move active root log CSV files used by the dashboard.",
    )
    parser.add_argument("--max-files", type=int, default=0, help="Limit number of files for a test run.")
    parser.add_argument("--dry-run", action="store_true", help="Only show which CSV files would be synced.")
    return parser.parse_args()


def main():
    args = parse_args()

    if args.dry_run:
        files = discover_csv_files(include_active_logs=args.include_active_logs)
        if args.max_files:
            files = files[:args.max_files]
        for path in files:
            print(path.relative_to(BASE_DIR))
        print(f"CSV files found: {len(files)}")
        return

    result = sync_csv_to_drive(
        move_after_upload=args.move,
        include_active_logs=args.include_active_logs,
        max_files=args.max_files or None,
    )
    print(json.dumps(result, indent=2))
    if not result["ok"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
