# live_safety_gate.py - Block live auto until paper/research metrics are stable

import csv
from pathlib import Path

import config


HISTORY_FILE = Path("logs") / "trade_history.csv"
GHOST_FILE = Path("logs") / "ghost_trades.csv"


def _to_float(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open() as csv_file:
        return list(csv.DictReader(csv_file))


def _max_drawdown_percent(pnls: list[float], starting_balance: float) -> float:
    if starting_balance <= 0:
        return 0.0
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)
    return round(abs(max_dd) / starting_balance * 100, 2)


def evaluate_live_safety_gate() -> tuple[bool, str, dict]:
    if not config.LIVE_SAFETY_GATE_ENABLED:
        return True, "Live safety gate disabled.", {}

    rows = _read_csv(HISTORY_FILE)
    exits = [row for row in rows if row.get("event") == "EXIT"]
    trading_days = sorted({row.get("date") for row in exits if row.get("date")})
    pnls = [_to_float(row.get("pnl")) for row in exits]
    gross_profit = sum(pnl for pnl in pnls if pnl > 0)
    gross_loss = abs(sum(pnl for pnl in pnls if pnl < 0))
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss else (round(gross_profit, 2) if gross_profit else 0)
    max_drawdown_percent = _max_drawdown_percent(pnls, float(config.PAPER_BALANCE_OVERRIDE or 100000))

    ghost_rows = _read_csv(GHOST_FILE)
    missed_profit = sum(max(_to_float(row.get("max_profit_after_signal")), 0) for row in ghost_rows)
    avoided_loss = abs(sum(min(_to_float(row.get("max_loss_after_signal")), 0) for row in ghost_rows))
    missed_report_positive = avoided_loss >= missed_profit or missed_profit == 0

    metrics = {
        "trading_days": len(trading_days),
        "profit_factor": profit_factor,
        "max_drawdown_percent": max_drawdown_percent,
        "missed_profit": round(missed_profit, 2),
        "avoided_loss": round(avoided_loss, 2),
        "missed_report_positive": missed_report_positive,
    }

    failures = []
    if len(trading_days) < int(config.LIVE_SAFETY_MIN_TRADING_DAYS):
        failures.append(f"needs {config.LIVE_SAFETY_MIN_TRADING_DAYS} paper days, has {len(trading_days)}")
    if profit_factor < float(config.LIVE_SAFETY_MIN_PROFIT_FACTOR):
        failures.append(f"profit factor {profit_factor} < {config.LIVE_SAFETY_MIN_PROFIT_FACTOR}")
    if max_drawdown_percent > float(config.LIVE_SAFETY_MAX_DRAWDOWN_PERCENT):
        failures.append(f"drawdown {max_drawdown_percent}% > {config.LIVE_SAFETY_MAX_DRAWDOWN_PERCENT}%")
    if not missed_report_positive:
        failures.append("ghost report says missed profit is higher than avoided loss")

    if failures:
        return False, "Live safety gate failed: " + "; ".join(failures), metrics

    return True, "Live safety gate passed.", metrics
