#!/usr/bin/env python3
"""Analyze backtest output and generate allow/block rules for weak buckets."""

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
REPORT_DIR = BASE_DIR / "reports" / "backtest_analysis"


def _parse_args():
    parser = argparse.ArgumentParser(description="Analyze backtest pattern summary and generate filter rules.")
    parser.add_argument("report_dir", help="Backtest report folder containing pattern_summary.csv and summary.json.")
    parser.add_argument("--target-win-rate", type=float, default=45.0)
    parser.add_argument("--min-trades", type=int, default=50)
    parser.add_argument("--block-negative-points", action="store_true", default=True)
    return parser.parse_args()


def _read_csv(path: Path) -> list[dict]:
    with path.open(newline="") as csv_file:
        return list(csv.DictReader(csv_file))


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text())


def _bucket_key(row: dict) -> str:
    return f"{row['symbol']}|{row['regime']}|{row['time_bucket']}|{row['signal']}"


def _row_action(row: dict, target_win_rate: float, min_trades: int) -> tuple[str, str]:
    trades = int(float(row.get("trades") or 0))
    win_rate = float(row.get("win_rate") or 0)
    points = float(row.get("total_points") or 0)

    if trades < min_trades:
        return "ALLOW", "Small sample, not filtered yet."
    if points < 0 and win_rate < target_win_rate:
        return "BLOCK", f"Negative points {points} and win rate {win_rate}% below target {target_win_rate}%."
    if points < 0:
        return "REDUCE", f"Negative points {points}; reduce size until option backtest confirms."
    if win_rate < target_win_rate - 5:
        return "REDUCE", f"Win rate {win_rate}% is weak, but points are positive."
    return "ALLOW", "Bucket is acceptable."


def _simulate_filtered(rows: list[dict], rules: dict) -> dict:
    total_trades = 0
    wins = 0
    losses = 0
    points = 0.0
    blocked_trades = 0

    for row in rows:
        trades = int(float(row.get("trades") or 0))
        action = rules.get(_bucket_key(row), "ALLOW")
        if action == "BLOCK":
            blocked_trades += trades
            continue
        multiplier = 0.5 if action == "REDUCE" else 1.0
        total_trades += trades
        wins += int(float(row.get("wins") or 0))
        losses += int(float(row.get("losses") or 0))
        points += float(row.get("total_points") or 0) * multiplier

    return {
        "trades": total_trades,
        "wins": wins,
        "losses": losses,
        "blocked_trades": blocked_trades,
        "win_rate": round((wins / total_trades) * 100, 2) if total_trades else 0,
        "total_points": round(points, 2),
    }


def _write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main():
    args = _parse_args()
    source_dir = Path(args.report_dir)
    if not source_dir.is_absolute():
        source_dir = BASE_DIR / source_dir

    summary = _read_json(source_dir / "summary.json")
    rows = _read_csv(source_dir / "pattern_summary.csv")

    rule_rows = []
    rules = {}
    for row in rows:
        action, reason = _row_action(row, args.target_win_rate, args.min_trades)
        key = _bucket_key(row)
        rules[key] = action
        rule_rows.append({
            **row,
            "action": action,
            "reason": reason,
        })

    blocked = [row for row in rule_rows if row["action"] == "BLOCK"]
    reduced = [row for row in rule_rows if row["action"] == "REDUCE"]
    simulated = _simulate_filtered(rows, rules)
    baseline_trades = sum(item.get("total_trades", 0) for item in summary.get("summaries", []))
    baseline_wins = sum(item.get("wins", 0) for item in summary.get("summaries", []))
    baseline_points = round(sum(float(item.get("total_points", 0)) for item in summary.get("summaries", [])), 2)
    baseline_win_rate = round((baseline_wins / baseline_trades) * 100, 2) if baseline_trades else 0

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = REPORT_DIR / timestamp
    out_dir.mkdir(parents=True, exist_ok=True)

    rules_json = {
        "created_at": datetime.now().isoformat(),
        "source_report": str(source_dir),
        "target_win_rate": args.target_win_rate,
        "min_trades": args.min_trades,
        "rules": rules,
        "baseline": {
            "trades": baseline_trades,
            "win_rate": baseline_win_rate,
            "total_points": baseline_points,
        },
        "filtered_simulation": simulated,
    }
    rules_path = out_dir / "rules.json"
    rules_path.write_text(json.dumps(rules_json, indent=2))
    _write_csv(out_dir / "bucket_rules.csv", rule_rows, [
        "symbol", "regime", "time_bucket", "signal", "trades", "wins", "losses",
        "win_rate", "total_points", "action", "reason"
    ])

    print("Backtest analysis complete.")
    print(f"Baseline: trades={baseline_trades} win_rate={baseline_win_rate}% points={baseline_points}")
    print(
        f"Filtered simulation: trades={simulated['trades']} "
        f"win_rate={simulated['win_rate']}% points={simulated['total_points']} "
        f"blocked={simulated['blocked_trades']}"
    )
    print(f"BLOCK rules: {len(blocked)}")
    print(f"REDUCE rules: {len(reduced)}")
    print(f"Rules JSON: {rules_path}")
    print(f"Bucket rules CSV: {out_dir / 'bucket_rules.csv'}")
    print()
    print("Next test command:")
    print(f"python3 tools/backtest_from_csv.py --symbols NIFTY SENSEX --interval 5minute --rules-file {rules_path}")


if __name__ == "__main__":
    main()
