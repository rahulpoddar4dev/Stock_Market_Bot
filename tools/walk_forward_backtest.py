#!/usr/bin/env python3
"""Run rolling walk-forward backtests on collected historical CSV candles."""

import argparse
import csv
import json
import sys
from datetime import date, datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import config
from indicators import enrich_candles
from tools.backtest_from_csv import (
    DATA_DIR,
    _read_candles,
    _vix_by_datetime,
    run_symbol_backtest_on_candles,
)

REPORT_DIR = BASE_DIR / "reports" / "walk_forward"
WINDOW_FIELDS = [
    "symbol",
    "train_start",
    "train_end",
    "test_start",
    "test_end",
    "train_trades",
    "train_win_rate",
    "train_points",
    "train_profit_factor",
    "train_max_loss_streak",
    "test_trades",
    "test_win_rate",
    "test_points",
    "test_profit_factor",
    "test_max_loss_streak",
    "test_t1_hits",
    "test_t2_hits",
    "test_t3_hits",
]


def _parse_args():
    parser = argparse.ArgumentParser(description="Walk-forward backtest current strategy.")
    parser.add_argument("--symbols", nargs="+", default=config.ANALYZE_INDICES)
    parser.add_argument("--interval", default=config.CANDLE_INTERVAL)
    parser.add_argument("--train-months", type=int, default=3)
    parser.add_argument("--test-months", type=int, default=1)
    parser.add_argument("--atr-target", type=float, default=2.0)
    parser.add_argument("--atr-stop", type=float, default=1.2)
    parser.add_argument("--adaptive-exit-profile", action="store_true")
    parser.add_argument("--min-setup-score", type=float, default=None)
    parser.add_argument("--min-adx", type=float, default=None)
    parser.add_argument("--breakout-lookback", type=int, default=None)
    return parser.parse_args()


def _candle_date(candle: dict) -> date:
    return datetime.fromisoformat(str(candle["date"])).date()


def _trade_date(trade: dict) -> date:
    return datetime.fromisoformat(str(trade["entry_time"])).date()


def _month_start(value: date) -> date:
    return date(value.year, value.month, 1)


def _add_months(value: date, months: int) -> date:
    month_index = (value.month - 1) + months
    return date(value.year + month_index // 12, (month_index % 12) + 1, 1)


def _between(value: date, start: date, end: date) -> bool:
    return start <= value < end


def _slice_candles(candles: list[dict], start: date, end: date) -> list[dict]:
    return [candle for candle in candles if _between(_candle_date(candle), start, end)]


def _metrics(trades: list[dict]) -> dict:
    pnl_values = [float(trade.get("pnl_points") or 0) for trade in trades]
    wins = [pnl for pnl in pnl_values if pnl > 0]
    losses = [pnl for pnl in pnl_values if pnl < 0]
    loss_streak = 0
    max_loss_streak = 0
    for pnl in pnl_values:
        if pnl < 0:
            loss_streak += 1
            max_loss_streak = max(max_loss_streak, loss_streak)
        else:
            loss_streak = 0

    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    total = len(trades)
    return {
        "trades": total,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round((len(wins) / total) * 100, 2) if total else 0,
        "points": round(sum(pnl_values), 2),
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else (round(gross_win, 2) if gross_win else 0),
        "max_loss_streak": max_loss_streak,
        "t1_hits": sum(1 for trade in trades if int(trade.get("target_stage") or 0) >= 1),
        "t2_hits": sum(1 for trade in trades if int(trade.get("target_stage") or 0) >= 2),
        "t3_hits": sum(1 for trade in trades if str(trade.get("result")) == "SUCCESS" and int(trade.get("target_stage") or 0) >= 2),
    }


def _write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _window_rows_for_symbol(
    symbol: str,
    interval: str,
    candles: list[dict],
    train_months: int,
    test_months: int,
    atr_target: float,
    atr_stop: float,
    vix_map: dict[str, float],
    adaptive_exit_profile: bool,
) -> list[dict]:
    if not candles:
        return []

    first_month = _month_start(_candle_date(candles[0]))
    last_date = _candle_date(candles[-1])
    rows = []
    train_start = first_month

    while True:
        train_end = _add_months(train_start, train_months)
        test_start = train_end
        test_end = _add_months(test_start, test_months)
        if test_start > last_date:
            break

        window_candles = _slice_candles(candles, train_start, test_end)
        if len(window_candles) <= config.MIN_CANDLES_FOR_STRATEGY:
            train_start = _add_months(train_start, test_months)
            continue

        result = run_symbol_backtest_on_candles(
            symbol=symbol,
            interval=interval,
            candles=window_candles,
            atr_target=atr_target,
            atr_stop=atr_stop,
            vix_map=vix_map,
            rules=None,
            adaptive_exit_profile=adaptive_exit_profile,
        )
        train_trades = [trade for trade in result["trades"] if _between(_trade_date(trade), train_start, train_end)]
        test_trades = [trade for trade in result["trades"] if _between(_trade_date(trade), test_start, test_end)]
        train_metrics = _metrics(train_trades)
        test_metrics = _metrics(test_trades)
        rows.append({
            "symbol": symbol,
            "train_start": train_start.isoformat(),
            "train_end": train_end.isoformat(),
            "test_start": test_start.isoformat(),
            "test_end": min(test_end, _add_months(_month_start(last_date), 1)).isoformat(),
            "train_trades": train_metrics["trades"],
            "train_win_rate": train_metrics["win_rate"],
            "train_points": train_metrics["points"],
            "train_profit_factor": train_metrics["profit_factor"],
            "train_max_loss_streak": train_metrics["max_loss_streak"],
            "test_trades": test_metrics["trades"],
            "test_win_rate": test_metrics["win_rate"],
            "test_points": test_metrics["points"],
            "test_profit_factor": test_metrics["profit_factor"],
            "test_max_loss_streak": test_metrics["max_loss_streak"],
            "test_t1_hits": test_metrics["t1_hits"],
            "test_t2_hits": test_metrics["t2_hits"],
            "test_t3_hits": test_metrics["t3_hits"],
        })
        train_start = _add_months(train_start, test_months)

    return rows


def _aggregate(rows: list[dict]) -> dict:
    test_trades = sum(int(row["test_trades"]) for row in rows)
    weighted_win = sum(float(row["test_win_rate"]) * int(row["test_trades"]) for row in rows)
    points = round(sum(float(row["test_points"]) for row in rows), 2)
    return {
        "windows": len(rows),
        "test_trades": test_trades,
        "avg_test_trades_per_window": round(test_trades / len(rows), 2) if rows else 0,
        "weighted_test_win_rate": round(weighted_win / test_trades, 2) if test_trades else 0,
        "test_points": points,
        "test_t1_hits": sum(int(row["test_t1_hits"]) for row in rows),
        "test_t2_hits": sum(int(row["test_t2_hits"]) for row in rows),
        "test_t3_hits": sum(int(row["test_t3_hits"]) for row in rows),
    }


def main():
    args = _parse_args()
    if args.min_setup_score is not None:
        config.MIN_SETUP_SCORE = args.min_setup_score
    if args.min_adx is not None:
        config.MIN_ADX = args.min_adx
    if args.breakout_lookback is not None:
        config.BREAKOUT_LOOKBACK_CANDLES = args.breakout_lookback

    started_at = datetime.now()
    run_dir = REPORT_DIR / started_at.strftime("%Y%m%d_%H%M%S")
    vix_map = _vix_by_datetime(args.interval)
    all_rows = []
    by_symbol = {}

    for symbol in args.symbols:
        print(f"Loading {symbol} {args.interval} candles...", flush=True)
        candles = enrich_candles(_read_candles(DATA_DIR / f"{symbol}_{args.interval}.csv"))
        rows = _window_rows_for_symbol(
            symbol=symbol,
            interval=args.interval,
            candles=candles,
            train_months=args.train_months,
            test_months=args.test_months,
            atr_target=args.atr_target,
            atr_stop=args.atr_stop,
            vix_map=vix_map,
            adaptive_exit_profile=args.adaptive_exit_profile,
        )
        all_rows.extend(rows)
        by_symbol[symbol] = _aggregate(rows)
        print(
            f"{symbol}: windows={by_symbol[symbol]['windows']} "
            f"test_trades={by_symbol[symbol]['test_trades']} "
            f"win_rate={by_symbol[symbol]['weighted_test_win_rate']}% "
            f"points={by_symbol[symbol]['test_points']}",
            flush=True,
        )

    _write_csv(run_dir / "windows.csv", all_rows, WINDOW_FIELDS)
    summary = {
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now().isoformat(),
        "interval": args.interval,
        "train_months": args.train_months,
        "test_months": args.test_months,
        "adaptive_exit_profile": args.adaptive_exit_profile,
        "min_setup_score": config.MIN_SETUP_SCORE,
        "min_adx": config.MIN_ADX,
        "breakout_lookback": config.BREAKOUT_LOOKBACK_CANDLES,
        "by_symbol": by_symbol,
        "combined": _aggregate(all_rows),
        "windows_file": str(run_dir / "windows.csv"),
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    print("Walk-forward backtest complete.")
    print(f"Report folder: {run_dir}")


if __name__ == "__main__":
    main()
