# time_quality.py - Trade quality by intraday time window

from __future__ import annotations

import csv
from datetime import datetime, time
from pathlib import Path

import config
from market_time import market_now_naive


HISTORY_FILE = Path("logs") / "trade_history.csv"


TIME_WINDOWS = [
    ("OPEN_NOISE", time(9, 15), time(9, 30), "SMALL_SIZE", 50),
    ("BEST_MOMENTUM", time(9, 30), time(11, 0), "NORMAL", 100),
    ("MIDDAY_CHOP", time(12, 0), time(13, 15), "AVOID_WEAK", 50),
    ("LATE_SESSION", time(14, 30), time(15, 0), "HIGH_SCORE_ONLY", 50),
]


def evaluate_time_quality(now: datetime | None = None, signal: dict | None = None) -> dict:
    now = now or market_now_naive()
    signal = signal or {}
    current = now.time()
    window = _window_for_time(current)
    score = float(signal.get("TRADE_QUALITY_SCORE") or signal.get("SETUP_SCORE") or 0)

    if not window:
        return {
            "time_window": "NORMAL_SESSION",
            "action": "NORMAL",
            "size_percent": 100,
            "reason": "No special time risk window active.",
        }

    name, _, _, action, size_percent = window
    if action == "HIGH_SCORE_ONLY" and score < float(getattr(config, "TIME_LATE_SESSION_MIN_QUALITY_SCORE", 80)):
        return {
            "time_window": name,
            "action": "LOG_ONLY",
            "size_percent": 0,
            "reason": f"Late session needs score >= {config.TIME_LATE_SESSION_MIN_QUALITY_SCORE}; current score {score}.",
        }

    if action == "AVOID_WEAK" and score < float(getattr(config, "TIME_MIDDAY_MIN_QUALITY_SCORE", 75)):
        return {
            "time_window": name,
            "action": "LOG_ONLY",
            "size_percent": 0,
            "reason": f"Midday chop needs score >= {config.TIME_MIDDAY_MIN_QUALITY_SCORE}; current score {score}.",
        }

    return {
        "time_window": name,
        "action": action,
        "size_percent": size_percent,
        "reason": f"{name} time window action: {action}.",
    }


def time_window_performance(rows: list[dict] | None = None) -> dict:
    rows = rows if rows is not None else _read_history()
    grouped: dict[str, list[float]] = {}
    for row in rows:
        if row.get("event") != "EXIT":
            continue
        pnl = _to_float(row.get("pnl"))
        window = row.get("time_window") or _window_name_from_text(row.get("opened_at") or row.get("event_time") or row.get("time"))
        grouped.setdefault(window, []).append(pnl)

    summary = {}
    for window, pnls in grouped.items():
        wins = sum(1 for pnl in pnls if pnl > 0)
        losses = sum(1 for pnl in pnls if pnl < 0)
        summary[window] = {
            "trades": len(pnls),
            "win_rate": round((wins / len(pnls)) * 100, 2) if pnls else 0,
            "pnl": round(sum(pnls), 2),
            "wins": wins,
            "losses": losses,
        }
    return summary


def _window_for_time(value: time):
    for row in TIME_WINDOWS:
        _, start, end, _, _ = row
        if start <= value < end:
            return row
    return None


def _window_name_from_text(value: str | None) -> str:
    if not value:
        return "UNKNOWN"
    try:
        if "T" in value:
            parsed = datetime.fromisoformat(value)
            current = parsed.time()
        else:
            current = time.fromisoformat(value[:8])
    except ValueError:
        return "UNKNOWN"
    window = _window_for_time(current)
    return window[0] if window else "NORMAL_SESSION"


def _read_history() -> list[dict]:
    if not HISTORY_FILE.exists():
        return []
    with HISTORY_FILE.open() as csv_file:
        return list(csv.DictReader(csv_file))


def _to_float(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
