#!/usr/bin/env python3
"""Search strategy parameters for better win rate without killing trade frequency."""

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import config
from tools.backtest_from_csv import _vix_by_datetime, run_symbol_backtest


REPORT_DIR = BASE_DIR / "reports" / "optimizations"
FIELDS = [
    "rank",
    "min_setup_score",
    "min_adx",
    "breakout_lookback",
    "rules_file",
    "trades",
    "wins",
    "losses",
    "breakevens",
    "win_rate",
    "total_points",
    "avg_trades_per_day",
    "total_days",
    "profitable_days",
    "daily_profit_ratio",
    "max_loss_streak",
    "score",
    "verdict",
]


def _parse_args():
    parser = argparse.ArgumentParser(description="Optimize backtest parameters.")
    parser.add_argument("--symbols", nargs="+", default=config.ANALYZE_INDICES)
    parser.add_argument("--interval", default=config.CANDLE_INTERVAL)
    parser.add_argument("--setup-scores", nargs="+", type=float, default=[8, 10, 12, 15, 18, 20, 25])
    parser.add_argument("--adx-values", nargs="+", type=float, default=[18, 20, 22, 25, 28])
    parser.add_argument("--breakout-lookbacks", nargs="+", type=int, default=[0])
    parser.add_argument("--adaptive-exit-profile", action="store_true")
    parser.add_argument("--rules-file", default="")
    parser.add_argument("--target-win-rate", type=float, default=50.0)
    parser.add_argument("--min-avg-trades-per-day", type=float, default=2.0)
    parser.add_argument("--top", type=int, default=15)
    return parser.parse_args()


def _trade_day(value: str) -> str:
    try:
        return datetime.fromisoformat(value).date().isoformat()
    except ValueError:
        return value[:10]


def _max_loss_streak(trades: list[dict]) -> int:
    streak = 0
    max_streak = 0
    for trade in sorted(trades, key=lambda row: row.get("entry_time", "")):
        if trade.get("result") == "FAIL":
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0
    return max_streak


def _daily_profit_ratio(trades: list[dict]) -> tuple[int, int, float]:
    daily_points = {}
    for trade in trades:
        day = _trade_day(trade.get("exit_time") or trade.get("entry_time", ""))
        if not day:
            continue
        daily_points[day] = daily_points.get(day, 0.0) + float(trade.get("pnl_points") or 0)

    total_days = len(daily_points)
    profitable_days = sum(1 for pnl in daily_points.values() if pnl > 0)
    ratio = round((profitable_days / total_days) * 100, 2) if total_days else 0
    return total_days, profitable_days, ratio


def _evaluate(trades: list[dict], target_win_rate: float, min_avg_trades_per_day: float) -> dict:
    total = len(trades)
    wins = sum(1 for trade in trades if trade.get("result") == "SUCCESS")
    losses = sum(1 for trade in trades if trade.get("result") == "FAIL")
    breakevens = sum(1 for trade in trades if trade.get("result") == "BREAKEVEN")
    win_rate = round((wins / total) * 100, 2) if total else 0
    points = round(sum(float(trade.get("pnl_points") or 0) for trade in trades), 2)
    days = {_trade_day(trade.get("entry_time", "")) for trade in trades if trade.get("entry_time")}
    avg_trades_per_day = round(total / len(days), 2) if days else 0
    total_days, profitable_days, daily_profit_ratio = _daily_profit_ratio(trades)
    max_loss_streak = _max_loss_streak(trades)

    frequency_penalty = max(0, min_avg_trades_per_day - avg_trades_per_day) * 10
    profit_bonus = min(points / 10000, 10)
    loss_streak_penalty = max(0, max_loss_streak - 8) * 0.8
    daily_bonus = min(daily_profit_ratio / 10, 8)
    score = round(win_rate + profit_bonus + daily_bonus - frequency_penalty - loss_streak_penalty, 2)

    if win_rate >= target_win_rate and avg_trades_per_day >= min_avg_trades_per_day and points > 0:
        verdict = "TARGET_MET"
    elif win_rate >= 45 and avg_trades_per_day >= min_avg_trades_per_day and points > 0:
        verdict = "GOOD_NEXT_STEP"
    elif points > 0 and avg_trades_per_day >= min_avg_trades_per_day:
        verdict = "PROFITABLE_BUT_LOW_WIN_RATE"
    else:
        verdict = "REJECT"

    return {
        "trades": total,
        "wins": wins,
        "losses": losses,
        "breakevens": breakevens,
        "win_rate": win_rate,
        "total_points": points,
        "avg_trades_per_day": avg_trades_per_day,
        "total_days": total_days,
        "profitable_days": profitable_days,
        "daily_profit_ratio": daily_profit_ratio,
        "max_loss_streak": max_loss_streak,
        "score": score,
        "verdict": verdict,
    }


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def main():
    args = _parse_args()
    original_score = config.MIN_SETUP_SCORE
    original_adx = config.MIN_ADX
    original_breakout = getattr(config, "BREAKOUT_LOOKBACK_CANDLES", 0)
    vix_map = _vix_by_datetime(args.interval)
    results = []

    for min_score in args.setup_scores:
        for min_adx in args.adx_values:
            for breakout_lookback in args.breakout_lookbacks:
                config.MIN_SETUP_SCORE = min_score
                config.MIN_ADX = min_adx
                config.BREAKOUT_LOOKBACK_CANDLES = breakout_lookback
                all_trades = []

                print(
                    f"Testing MIN_SETUP_SCORE={min_score}, MIN_ADX={min_adx}, "
                    f"BREAKOUT_LOOKBACK={breakout_lookback}",
                    flush=True,
                )
                for symbol in args.symbols:
                    result = run_symbol_backtest(
                        symbol=symbol,
                        interval=args.interval,
                        atr_target=2.0,
                        atr_stop=1.2,
                        vix_map=vix_map,
                        rules={"rules": {}} if not args.rules_file else json.loads(Path(args.rules_file).read_text()),
                        adaptive_exit_profile=args.adaptive_exit_profile,
                    )
                    all_trades.extend(result["trades"])

                metrics = _evaluate(all_trades, args.target_win_rate, args.min_avg_trades_per_day)
                results.append({
                    "min_setup_score": min_score,
                    "min_adx": min_adx,
                    "breakout_lookback": breakout_lookback,
                    "rules_file": args.rules_file or "NONE",
                    **metrics,
                })

    config.MIN_SETUP_SCORE = original_score
    config.MIN_ADX = original_adx
    config.BREAKOUT_LOOKBACK_CANDLES = original_breakout

    ranked = sorted(results, key=lambda row: (row["score"], row["win_rate"], row["total_points"]), reverse=True)
    for index, row in enumerate(ranked, start=1):
        row["rank"] = index

    out_dir = REPORT_DIR / datetime.now().strftime("%Y%m%d_%H%M%S")
    _write_csv(out_dir / "optimization_results.csv", ranked)
    (out_dir / "summary.json").write_text(json.dumps({
        "created_at": datetime.now().isoformat(),
        "symbols": args.symbols,
        "interval": args.interval,
        "target_win_rate": args.target_win_rate,
        "min_avg_trades_per_day": args.min_avg_trades_per_day,
        "rules_file": args.rules_file,
        "adaptive_exit_profile": args.adaptive_exit_profile,
        "best": ranked[: args.top],
    }, indent=2))

    print()
    print("Optimization complete.")
    print(f"Report folder: {out_dir}")
    print("Top results:")
    for row in ranked[: args.top]:
        print(
            f"#{row['rank']} score={row['score']} setup={row['min_setup_score']} "
            f"adx={row['min_adx']} breakout={row['breakout_lookback']} "
            f"trades={row['trades']} avg/day={row['avg_trades_per_day']} "
            f"win={row['win_rate']}% daily_profit={row['daily_profit_ratio']}% points={row['total_points']} "
            f"loss_streak={row['max_loss_streak']} {row['verdict']}"
        )


if __name__ == "__main__":
    main()
