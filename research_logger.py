# research_logger.py - No-trade and ghost trade research logs

import csv
import json
from datetime import datetime
from pathlib import Path

import config
from log_utils import csv_date, csv_datetime, csv_time
from market_time import market_now_naive


LOG_DIR = Path("logs")
DATEWISE_DIR = LOG_DIR / "by_date"
NO_TRADE_FILE = LOG_DIR / "no_trade_decisions.csv"
GHOST_FILE = LOG_DIR / "ghost_trades.csv"


NO_TRADE_FIELDS = [
    "date", "time", "event_time", "symbol", "signal", "final_decision", "final_score",
    "nifty_reason", "sensex_reason", "option_reason", "market_context_reason",
    "learning_penalty", "learning_penalty_reason", "risk_block_reason", "status",
    "option_symbol", "display_symbol", "entry_price", "target", "stop_loss",
    "trade_quality_score", "option_chain_score", "reason",
]

GHOST_FIELDS = [
    "date", "time", "event_time", "status", "symbol", "option_symbol", "display_symbol",
    "signal", "blocked_reason", "final_score", "entry_price", "target", "stop_loss",
    "quantity", "next_5m_pnl", "next_15m_pnl", "next_30m_pnl", "target_hit",
    "sl_hit", "max_profit_after_signal", "max_loss_after_signal", "last_price",
    "last_checked_at", "closed_at",
]


def _append(path: Path, row: dict, base_fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    if path.exists():
        with path.open() as csv_file:
            rows = list(csv.DictReader(csv_file))
    fieldnames = list(base_fields)
    for existing in rows:
        for key in existing.keys():
            if key and key not in fieldnames:
                fieldnames.append(key)
    for key in row.keys():
        if key not in fieldnames:
            fieldnames.append(key)
    with path.open("w", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        for existing in rows:
            writer.writerow(existing)
        writer.writerow(row)


def _to_float(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _index_reason(market_data: dict, symbol: str) -> str:
    analyses = market_data.get("index_analyses_summary") or {}
    if isinstance(analyses, str):
        try:
            analyses = json.loads(analyses)
        except json.JSONDecodeError:
            analyses = {}
    item = analyses.get(symbol) if isinstance(analyses, dict) else {}
    if not item or not isinstance(item, dict):
        return ""
    return item.get("reason") or item.get("signal") or ""


def record_no_trade_decision(market_data: dict, signal: dict, option_trade: dict | None, reason: str = "") -> None:
    if not config.RESEARCH_LOGGING_ENABLED:
        return
    option_trade = option_trade or {}
    final_score = (
        option_trade.get("confidence_score")
        or option_trade.get("option_chain_score")
        or signal.get("TRADE_QUALITY_SCORE")
        or signal.get("SETUP_SCORE")
    )
    row = {
        "date": csv_date(),
        "time": csv_time(),
        "event_time": csv_datetime(),
        "symbol": market_data.get("symbol"),
        "signal": signal.get("SIGNAL"),
        "final_decision": option_trade.get("status") or "HOLD",
        "final_score": final_score,
        "nifty_reason": _index_reason(market_data, "NIFTY"),
        "sensex_reason": _index_reason(market_data, "SENSEX"),
        "option_reason": option_trade.get("option_chain_decision_reason") or option_trade.get("reason"),
        "market_context_reason": signal.get("MARKET_CONTEXT_REASON") or market_data.get("market_context_reason"),
        "learning_penalty": option_trade.get("learning_score_penalty") or signal.get("LEARNING_SCORE_PENALTY"),
        "learning_penalty_reason": option_trade.get("learning_penalty_reason") or signal.get("LEARNING_PENALTY_REASON"),
        "risk_block_reason": option_trade.get("risk_block_reason"),
        "status": option_trade.get("status"),
        "option_symbol": option_trade.get("option_symbol"),
        "display_symbol": option_trade.get("display_symbol"),
        "entry_price": option_trade.get("entry_price"),
        "target": option_trade.get("target"),
        "stop_loss": option_trade.get("stop_loss"),
        "trade_quality_score": signal.get("TRADE_QUALITY_SCORE") or option_trade.get("trade_quality_score"),
        "option_chain_score": option_trade.get("option_chain_score"),
        "reason": reason or signal.get("REASON") or option_trade.get("risk_block_reason"),
    }
    _append(NO_TRADE_FILE, row, NO_TRADE_FIELDS)
    _append(DATEWISE_DIR / row["date"] / "no_trade_decisions.csv", row, NO_TRADE_FIELDS)


def should_start_ghost(option_trade: dict | None) -> bool:
    if not config.GHOST_TRADE_TRACKING_ENABLED or not option_trade:
        return False
    status = str(option_trade.get("status") or "")
    quantity = int(_to_float(option_trade.get("quantity")))
    return bool(option_trade.get("option_symbol") and option_trade.get("entry_price")) and (
        quantity <= 0 or status.startswith("BLOCKED") or "LOG_ONLY" in status
    )


def record_ghost_trade(market_data: dict, signal: dict, option_trade: dict | None, reason: str = "") -> None:
    if not should_start_ghost(option_trade):
        return
    option_trade = option_trade or {}
    row = {
        "date": csv_date(),
        "time": csv_time(),
        "event_time": csv_datetime(),
        "status": "OPEN",
        "symbol": market_data.get("symbol") or option_trade.get("underlying"),
        "option_symbol": option_trade.get("option_symbol"),
        "display_symbol": option_trade.get("display_symbol"),
        "signal": signal.get("SIGNAL"),
        "blocked_reason": reason or option_trade.get("risk_block_reason") or option_trade.get("option_chain_decision_reason"),
        "final_score": option_trade.get("confidence_score") or option_trade.get("option_chain_score") or signal.get("TRADE_QUALITY_SCORE"),
        "entry_price": option_trade.get("entry_price"),
        "target": option_trade.get("target_2") or option_trade.get("target"),
        "stop_loss": option_trade.get("stop_loss"),
        "quantity": option_trade.get("quantity") or option_trade.get("lot_size") or 1,
        "target_hit": "",
        "sl_hit": "",
        "max_profit_after_signal": 0,
        "max_loss_after_signal": 0,
    }
    _append(GHOST_FILE, row, GHOST_FIELDS)
    _append(DATEWISE_DIR / row["date"] / "ghost_trades.csv", row, GHOST_FIELDS)


def _read_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open() as csv_file:
        return list(csv.DictReader(csv_file))


def _write_rows(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(fields)
    for row in rows:
        for key in row.keys():
            if key and key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _update_file_rows(path: Path, fyers) -> None:
    rows = _read_rows(path)
    changed = False
    now = market_now_naive()
    for row in rows:
        if row.get("status") == "CLOSED":
            continue
        event_time = row.get("event_time")
        try:
            opened = datetime.fromisoformat(event_time)
            if opened.tzinfo is not None:
                opened = opened.replace(tzinfo=None)
        except (TypeError, ValueError):
            continue
        minutes = (now - opened).total_seconds() / 60
        opt_sym = str(row.get("option_symbol") or "")
        quote_key = opt_sym if ":" in opt_sym else f"{row.get('exchange', 'NSE')}:{opt_sym}"
        try:
            quote = fyers.quote([quote_key]).get(quote_key, {})
            current_price = _to_float(quote.get("last_price"))
        except Exception:
            current_price = 0
        if current_price <= 0:
            continue

        entry = _to_float(row.get("entry_price"))
        quantity = int(_to_float(row.get("quantity"), 1)) or 1
        pnl = round((current_price - entry) * quantity, 2)
        row["last_price"] = current_price
        row["last_checked_at"] = csv_datetime()
        row["max_profit_after_signal"] = max(_to_float(row.get("max_profit_after_signal")), pnl)
        row["max_loss_after_signal"] = min(_to_float(row.get("max_loss_after_signal")), pnl)
        target = _to_float(row.get("target"))
        stop_loss = _to_float(row.get("stop_loss"))
        if target and current_price >= target:
            row["target_hit"] = "YES"
        if stop_loss and current_price <= stop_loss:
            row["sl_hit"] = "YES"
        for marker in config.GHOST_TRACK_MINUTES:
            if minutes >= int(marker) and not row.get(f"next_{marker}m_pnl"):
                row[f"next_{marker}m_pnl"] = pnl
        if minutes >= max(int(item) for item in config.GHOST_TRACK_MINUTES):
            row["status"] = "CLOSED"
            row["closed_at"] = csv_datetime()
        changed = True
    if changed:
        _write_rows(path, rows, GHOST_FIELDS)


def update_ghost_trades(fyers) -> None:
    if not config.GHOST_TRADE_TRACKING_ENABLED:
        return
    _update_file_rows(GHOST_FILE, fyers)
    daily = DATEWISE_DIR / csv_date() / "ghost_trades.csv"
    if daily.exists():
        _update_file_rows(daily, fyers)
