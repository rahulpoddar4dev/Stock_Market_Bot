# performance_report.py - Summarize trade_history.csv performance

import csv
from pathlib import Path

from paper_fill_engine import (
    calculate_profit_based_win_rate,
    effective_net_pnl,
    effective_trade_charges,
)


HISTORY_FILE = Path("logs") / "trade_history.csv"


def build_performance_summary() -> dict | None:
    if not HISTORY_FILE.exists():
        return None

    exits = []
    with HISTORY_FILE.open() as csv_file:
        reader = csv.DictReader(csv_file)
        for row in reader:
            if row.get("event") == "EXIT":
                exits.append(row)

    total = len(exits)
    pnls = [effective_net_pnl(row) for row in exits]
    charges_list = [effective_trade_charges(row) for row in exits]
    total_charges = round(sum(charges_list), 2)
    gross_pnls = [
        float(row["gross_pnl"]) if row.get("gross_pnl") not in {None, ""} else round(pnls[i] + charges_list[i], 2)
        for i, row in enumerate(exits)
    ]
    success = sum(1 for pnl in pnls if pnl > 0)
    fail = sum(1 for pnl in pnls if pnl < 0)
    breakeven = sum(1 for pnl in pnls if pnl == 0)
    total_pnl = sum(pnls)
    gross_profit = sum(pnl for pnl in pnls if pnl > 0)
    gross_loss = abs(sum(pnl for pnl in pnls if pnl < 0))
    win_rate = calculate_profit_based_win_rate(pnls)
    trade_win_rate = round((success / total) * 100, 2) if total else 0.0

    return {
        "closed_trades": total,
        "success": success,
        "fail": fail,
        "breakeven": breakeven,
        "win_rate": win_rate,
        "trade_win_rate": trade_win_rate,
        "gross_pnl": round(sum(gross_pnls), 2),
        "total_charges": total_charges,
        "total_pnl": round(total_pnl, 2),
        "average_profit": round(gross_profit / success, 2) if success else 0,
        "average_loss": round(gross_loss / fail, 2) if fail else 0,
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss else (round(gross_profit, 2) if gross_profit else 0),
        "max_drawdown": _max_drawdown(pnls),
        "loss_streak": _max_loss_streak(pnls),
    }


def _max_drawdown(pnls: list[float]) -> float:
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)
    return round(abs(max_dd), 2)


def _max_loss_streak(pnls: list[float]) -> int:
    streak = 0
    max_streak = 0
    for pnl in pnls:
        if pnl < 0:
            streak += 1
            max_streak = max(max_streak, streak)
        elif pnl > 0:
            streak = 0
    return max_streak


def print_performance_summary() -> None:
    summary = build_performance_summary()
    if summary is None:
        print("No trade history found yet.")
        return

    print("TRADE PERFORMANCE REPORT")
    print("=" * 32)
    print(f"Closed trades: {summary['closed_trades']}")
    print(f"Profitable trades: {summary['success']}")
    print(f"Loss trades: {summary['fail']}")
    print(f"Breakeven trades: {summary['breakeven']}")
    print(f"Profit-based win rate: {summary['win_rate']}% (Trade count win %: {summary['trade_win_rate']}%)")
    print(f"Gross P&L: Rs {summary['gross_pnl']}")
    print(f"Brokerage & Taxes: Rs {summary['total_charges']}")
    print(f"Net P&L (After Charges): Rs {summary['total_pnl']}")
    print(f"Average profit: Rs {summary['average_profit']}")
    print(f"Average loss: Rs {summary['average_loss']}")
    print(f"Profit factor: {summary['profit_factor']}")
    print(f"Max drawdown: Rs {summary['max_drawdown']}")
    print(f"Max loss streak: {summary['loss_streak']}")


def main():
    print_performance_summary()


if __name__ == "__main__":
    main()
