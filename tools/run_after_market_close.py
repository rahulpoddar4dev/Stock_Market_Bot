#!/usr/bin/env python3
"""One-command evening pipeline: collect data, then backtest and summarize patterns."""

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
LOG_DIR = BASE_DIR / "logs" / "evening_pipeline"


def _parse_args():
    parser = argparse.ArgumentParser(description="Run evening historical-data and backtest pipeline.")
    parser.add_argument("--years", type=int, default=10, help="Historical lookback years.")
    parser.add_argument("--interval", default="5minute", help="Candle interval.")
    parser.add_argument("--symbols", nargs="+", default=["NIFTY", "SENSEX"], help="Backtest symbols.")
    parser.add_argument("--skip-collect", action="store_true", help="Skip Fyers data collection.")
    parser.add_argument("--skip-backtest", action="store_true", help="Skip CSV backtest.")
    parser.add_argument("--drive-sync", action="store_true", help="Also sync log CSV files to Google Drive.")
    parser.add_argument("--move-csv-to-drive", action="store_true", help="Move synced CSV files off local disk after upload.")
    return parser.parse_args()


def _run_step(name: str, command: list[str]) -> dict:
    started = datetime.now()
    print()
    print(f"=== {name} ===")
    print(" ".join(command))
    process = subprocess.Popen(
        command,
        cwd=BASE_DIR,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
    )
    output_lines = []
    assert process.stdout is not None
    for line in process.stdout:
        print(line, end="")
        output_lines.append(line)
    return_code = process.wait()
    output = "".join(output_lines)
    return {
        "name": name,
        "command": command,
        "started_at": started.isoformat(),
        "finished_at": datetime.now().isoformat(),
        "return_code": return_code,
        "stdout": output,
        "stderr": "",
        "status": "OK" if return_code == 0 else "ERROR",
    }


def _write_log(run_log: dict) -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = LOG_DIR / f"evening_pipeline_{timestamp}.json"
    path.write_text(json.dumps(run_log, indent=2))
    (LOG_DIR / "latest.json").write_text(json.dumps(run_log, indent=2))
    return path


def main():
    args = _parse_args()
    started_at = datetime.now()
    steps = []

    if not args.skip_collect:
        steps.append(_run_step("Collect historical data", [
            sys.executable,
            "tools/collect_historical_data.py",
            "--years",
            str(args.years),
            "--interval",
            args.interval,
        ]))

    if not args.skip_backtest:
        steps.append(_run_step("Backtest collected data", [
            sys.executable,
            "tools/backtest_from_csv.py",
            "--symbols",
            *args.symbols,
            "--interval",
            args.interval,
        ]))

    if args.drive_sync:
        command = [
            sys.executable,
            "tools/sync_csv_to_drive.py",
            "--include-active-logs",
        ]
        if args.move_csv_to_drive:
            command.append("--move")
        steps.append(_run_step("Sync CSV files to Google Drive", command))

    run_log = {
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now().isoformat(),
        "years": args.years,
        "interval": args.interval,
        "symbols": args.symbols,
        "steps": steps,
        "status": "OK" if all(step["return_code"] == 0 for step in steps) else "ERROR",
    }
    log_path = _write_log(run_log)

    print()
    print(f"Evening pipeline status: {run_log['status']}")
    print(f"Pipeline log: {log_path}")
    if run_log["status"] != "OK":
        sys.exit(1)


if __name__ == "__main__":
    main()
