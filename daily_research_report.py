# daily_research_report.py - Truthful paper research summary from trade/no-trade/ghost logs

import csv
from pathlib import Path

from log_utils import csv_date


LOG_DIR = Path("logs")
DATEWISE_DIR = LOG_DIR / "by_date"


def _to_float(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open() as csv_file:
        return list(csv.DictReader(csv_file))


def _max_drawdown(pnls: list[float]) -> float:
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)
    return round(abs(max_dd), 2)


def build_daily_research_report(day: str | None = None) -> dict:
    day = day or csv_date()
    day_dir = DATEWISE_DIR / day
    trades = _read(day_dir / "trade_history.csv")
    no_trades = _read(day_dir / "no_trade_decisions.csv")
    ghosts = _read(day_dir / "ghost_trades.csv")
    exits = [row for row in trades if row.get("event") == "EXIT"]
    entries = [row for row in trades if row.get("event") == "ENTRY"]
    pnls = [_to_float(row.get("pnl")) for row in exits]
    wins = [pnl for pnl in pnls if pnl > 0]
    losses = [pnl for pnl in pnls if pnl < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    missed_profit = sum(max(_to_float(row.get("max_profit_after_signal")), 0) for row in ghosts)
    avoided_loss = abs(sum(min(_to_float(row.get("max_loss_after_signal")), 0) for row in ghosts))

    return {
        "day": day,
        "taken_trades": len(entries),
        "closed_trades": len(exits),
        "blocked_trades": len(no_trades),
        "ghost_trades": len(ghosts),
        "missed_profit": round(missed_profit, 2),
        "avoided_loss": round(avoided_loss, 2),
        "win_rate": round((len(wins) / len(exits)) * 100, 2) if exits else 0,
        "average_profit": round(gross_profit / len(wins), 2) if wins else 0,
        "average_loss": round(gross_loss / len(losses), 2) if losses else 0,
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss else (round(gross_profit, 2) if gross_profit else 0),
        "max_drawdown": _max_drawdown(pnls),
        "mfe": round(max([_to_float(row.get("max_profit_after_signal")) for row in ghosts] or [0]), 2),
        "mae": round(min([_to_float(row.get("max_loss_after_signal")) for row in ghosts] or [0]), 2),
        "slippage": round(sum(_to_float(row.get("paper_entry_slippage")) + _to_float(row.get("paper_exit_slippage")) for row in exits), 2),
        "charges": round(sum(_to_float(row.get("paper_total_charges")) for row in exits), 2),
    }


def print_daily_research_report(day: str | None = None) -> None:
    report = build_daily_research_report(day)
    print("\nDAILY RESEARCH REPORT")
    print("-" * 72)
    for key, value in report.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    print_daily_research_report()
