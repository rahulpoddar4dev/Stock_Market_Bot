# dashboard.py - Local browser dashboard for the trading bot

import csv
import json
import time
import threading
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, urlparse

import config
from log_capture import init_log_capture
init_log_capture()
from account_status import load_account_status, save_account_status
from analysis_snapshot import load_analysis_snapshot
from fyers_login import (
    create_fyers_client,
    get_fyers_login_url,
    exchange_auth_code_for_token,
    save_access_token,
    try_saved_login,
    logout,
    _extract_auth_code,
)
from market_time import today_date
from order_manager import get_balance
from paper_account import build_paper_account
from paper_fill_engine import (
    calculate_profit_based_win_rate,
    effective_net_pnl,
    effective_trade_charges,
)
from pre_open_analyzer import load_pre_open_context
from runtime_settings import apply_runtime_settings, save_trading_mode
from symbol_formatter import format_option_display


BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "logs"
OPEN_TRADE_FILE = BASE_DIR / "open_trade.json"
BROKER_ACCOUNT_REFRESH_SECONDS = 30
_BROKER_ACCOUNT_CACHE = {"at": 0.0, "status": None}


def read_csv(path: Path, limit: int = 100) -> list[dict]:
    if not path.exists() or path.stat().st_size == 0:
        return []

    with path.open() as csv_file:
        rows = list(csv.DictReader(csv_file))

    return rows[-limit:]


def read_json(path: Path):
    if not path.exists():
        return None

    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return None


def list_trade_days() -> list[str]:
    days = set()
    by_date_dir = LOG_DIR / "by_date"
    if by_date_dir.exists():
        for path in by_date_dir.iterdir():
            if path.is_dir():
                days.add(path.name)
    try:
        from database import db_get_distinct_dates
        for d in db_get_distinct_dates():
            if d:
                days.add(str(d))
    except Exception:
        pass
    return sorted(list(days), reverse=True)


def calculate_performance(rows: list[dict]) -> dict:
    exits = [row for row in rows if row.get("event") == "EXIT"]
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
    square_off = sum(1 for row in exits if row.get("result") == "SQUARE_OFF")
    total_pnl = sum(pnls)
    gross_profit = sum(pnl for pnl in pnls if pnl > 0)
    gross_loss = abs(sum(pnl for pnl in pnls if pnl < 0))
    win_rate = calculate_profit_based_win_rate(pnls)
    trade_win_rate = round((success / total) * 100, 2) if total else 0
    avg_profit = round(gross_profit / success, 2) if success else 0
    avg_loss = round(gross_loss / fail, 2) if fail else 0
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss else (round(gross_profit, 2) if gross_profit else 0)
    max_drawdown = _max_drawdown(pnls)
    loss_streak = _max_loss_streak(pnls)
    best_entry_mode = _best_worst_bucket(exits, "entry_mode", best=True)
    worst_entry_mode = _best_worst_bucket(exits, "entry_mode", best=False)
    best_time_window = _best_worst_bucket(exits, "time_window", best=True)
    worst_time_window = _best_worst_bucket(exits, "time_window", best=False)
    best_strike_type = _best_worst_bucket(exits, "option_type", best=True)
    worst_strike_type = _best_worst_bucket(exits, "option_type", best=False)
    best_regime = _best_worst_bucket(exits, "market_regime", best=True)
    worst_regime = _best_worst_bucket(exits, "market_regime", best=False)
    loss_reasons = _bucket_counts(exits, "loss_reason")

    return {
        "closed_trades": total,
        "success": success,
        "fail": fail,
        "breakeven": breakeven,
        "square_off": square_off,
        "win_rate": win_rate,
        "trade_win_rate": trade_win_rate,
        "gross_pnl": round(sum(gross_pnls), 2),
        "total_charges": total_charges,
        "total_pnl": round(total_pnl, 2),
        "average_profit": avg_profit,
        "average_loss": avg_loss,
        "profit_factor": profit_factor,
        "max_drawdown": max_drawdown,
        "loss_streak": loss_streak,
        "best_entry_mode": best_entry_mode,
        "worst_entry_mode": worst_entry_mode,
        "best_time_window": best_time_window,
        "worst_time_window": worst_time_window,
        "best_strike_type": best_strike_type,
        "worst_strike_type": worst_strike_type,
        "best_market_regime": best_regime,
        "worst_market_regime": worst_regime,
        "loss_reasons": loss_reasons,
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


def _best_worst_bucket(rows: list[dict], field: str, best: bool) -> dict:
    buckets = {}
    for row in rows:
        key = row.get(field) or "UNKNOWN"
        pnl = effective_net_pnl(row)
        buckets.setdefault(key, {"name": key, "trades": 0, "pnl": 0.0})
        buckets[key]["trades"] += 1
        buckets[key]["pnl"] += pnl
    if not buckets:
        return {"name": "N/A", "trades": 0, "pnl": 0}
    selected = sorted(buckets.values(), key=lambda item: item["pnl"], reverse=best)[0]
    selected["pnl"] = round(selected["pnl"], 2)
    return selected


def _bucket_counts(rows: list[dict], field: str) -> dict:
    counts = {}
    for row in rows:
        value = row.get(field)
        if not value:
            continue
        counts[value] = counts.get(value, 0) + 1
    return counts


def is_live_mode(mode: str) -> bool:
    return mode in {"LIVE_AUTO", "SEMI_AUTO"}


def row_matches_mode(row: dict, mode: str) -> bool:
    row_mode = (row.get("mode") or row.get("trading_mode") or "").upper()
    if mode == "PAPER":
        return row_mode in {"", "PAPER"}
    return row_mode == mode or (is_live_mode(mode) and row_mode in {"LIVE_AUTO", "SEMI_AUTO"})


def filter_rows_for_mode(rows: list[dict], mode: str) -> list[dict]:
    return [row for row in rows if row_matches_mode(row, mode)]


def active_account_summary(account_status: dict, paper_account: dict, today_performance: dict, mode: str, open_trades: list[dict] | None = None) -> dict:
    broker_balance = float(account_status.get("broker_balance") or account_status.get("available_balance") or 0)
    paper_balance = float(paper_account.get("available_balance") or 0)
    today_pnl = float(today_performance.get("total_pnl") or 0)
    today_charges = float(today_performance.get("total_charges") or 0)
    today_gross_pnl = float(today_performance.get("gross_pnl") or 0)
    status_mode = str(account_status.get("mode") or "").upper()
    open_unrealized_pnl = round(sum(float(t.get("unrealized_pnl") or 0.0) for t in (open_trades or [])), 2)
    live_today_pnl = round(today_pnl + open_unrealized_pnl, 2)

    if mode == "PAPER":
        final_amount = paper_balance
        current_fund = round(final_amount - today_pnl, 2)
        return {
            "mode": "PAPER",
            "fund_source": "PAPER",
            "current_fund": current_fund,
            "today_gross_pnl": round(today_gross_pnl, 2),
            "today_charges": round(today_charges, 2),
            "today_pnl": round(today_pnl, 2),
            "open_unrealized_pnl": open_unrealized_pnl,
            "live_today_pnl": live_today_pnl,
            "final_amount": round(final_amount, 2),
            "broker_balance": broker_balance,
            "paper_balance": round(paper_balance, 2),
            "fund_stale": False,
            "fund_updated_at": account_status.get("updated_at"),
        }

    fund_stale = status_mode not in {"LIVE_AUTO", "SEMI_AUTO", mode}
    return {
        "mode": mode,
        "fund_source": "BROKER",
        "current_fund": round(broker_balance, 2),
        "today_gross_pnl": round(today_gross_pnl, 2),
        "today_charges": round(today_charges, 2),
        "today_pnl": round(today_pnl, 2),
        "open_unrealized_pnl": open_unrealized_pnl,
        "live_today_pnl": live_today_pnl,
        "final_amount": round(broker_balance + today_pnl, 2),
        "broker_balance": round(broker_balance, 2),
        "paper_balance": round(paper_balance, 2),
        "fund_stale": fund_stale,
        "fund_updated_at": account_status.get("updated_at"),
    }


def refresh_broker_account_status() -> dict | None:
    try:
        fyers = try_saved_login(silent=True)
        if not fyers:
            return None
        balance = get_balance(fyers)
    except Exception:
        return None

    try:
        profile = fyers.profile()
    except Exception:
        profile = None
    return save_account_status(balance, profile, active_balance=balance, mode=config.TRADING_MODE)


def load_active_account_status(mode: str) -> dict:
    account_status = load_account_status()
    if not is_live_mode(mode):
        return account_status

    now = time.time()
    cached_status = _BROKER_ACCOUNT_CACHE.get("status")
    cached_at = float(_BROKER_ACCOUNT_CACHE.get("at") or 0)
    if cached_status and now - cached_at < BROKER_ACCOUNT_REFRESH_SECONDS:
        return cached_status

    refreshed_status = refresh_broker_account_status()
    if refreshed_status:
        _BROKER_ACCOUNT_CACHE["at"] = now
        _BROKER_ACCOUNT_CACHE["status"] = refreshed_status
        return refreshed_status

    return account_status


_FYERS_STATUS_CACHE = {"status": None, "at": 0.0}


def fyers_session_status(force_refresh: bool = False) -> dict:
    global _FYERS_STATUS_CACHE
    now = time.time()
    if not force_refresh and _FYERS_STATUS_CACHE["status"] is not None and (now - _FYERS_STATUS_CACHE["at"]) < 5.0:
        return _FYERS_STATUS_CACHE["status"]

    try:
        fyers_client = try_saved_login(silent=True)
        if not fyers_client:
            res = {"active": False, "message": "Fyers login needed"}
            _FYERS_STATUS_CACHE = {"status": res, "at": now}
            return res
        profile = fyers_client.profile()
        res = {
            "active": True,
            "message": "Fyers session active",
            "user_name": profile.get("user_name"),
            "user_id": profile.get("user_id"),
        }
    except Exception as error:
        res = {"active": False, "message": str(error)}

    _FYERS_STATUS_CACHE = {"status": res, "at": now}
    return res


def clear_fyers_session_cache():
    global _FYERS_STATUS_CACHE
    _FYERS_STATUS_CACHE = {"status": None, "at": 0.0}




def _as_number(value, default=0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def enrich_rows(rows: list[dict], paper_rows: list[dict] | None = None) -> list[dict]:
    paper_rows = paper_rows or []
    paper_by_time = {
        (row.get("date"), row.get("time"), row.get("option_symbol")): row
        for row in paper_rows
        if row.get("option_symbol")
    }
    enriched = []

    for row in rows:
        item = dict(row)
        paper_match = paper_by_time.get((item.get("date"), item.get("time"), item.get("option_symbol")), {})

        for field in [
            "display_symbol",
            "underlying",
            "order_action",
            "news_sentiment",
            "news_risk_level",
            "news_reason",
            "news_headlines",
            "news_blocked_original_signal",
            "news_mode",
            "news_conflict",
            "decision_engine",
            "chart_signal",
            "index_alignment",
            "index_reason",
            "market_regime",
            "market_regime_risk",
            "market_regime_action",
            "market_regime_reason",
            "market_regime_size_percent",
            "time_window",
            "time_quality_action",
            "time_quality_reason",
            "time_quality_size_percent",
            "entry_mode",
            "entry_freshness",
            "entry_reason",
            "trade_quality_score",
            "trade_quality_decision",
            "trade_quality_reason",
            "trade_quality_decision_reason",
            "confidence_score",
            "option_chain_score",
            "option_chain_decision_reason",
            "learning_score_penalty",
            "learning_penalty_reason",
            "entry_logic",
            "entry_trigger_price",
            "current_price",
            "potential_left_percent",
            "risk_reward",
            "hard_reject_reason",
            "chain_support",
            "chain_resistance",
            "chain_context_reason",
            "quality_entry_freshness",
            "quality_trend_strength",
            "quality_retest_breakout",
            "quality_option_premium",
            "quality_market_context",
            "quality_candle_pattern",
            "quality_size_percent",
            "option_type",
            "selection_mode",
            "expiry",
            "strike",
            "lot_size",
            "lots",
            "remaining_quantity",
            "exit_quantity",
            "partial_realized_pnl",
            "estimated_required_amount",
            "estimated_risk_amount",
            "gross_pnl",
            "paper_total_charges",
            "paper_fill_status",
            "paper_entry_reference_price",
            "paper_entry_slippage",
            "paper_entry_charges",
            "paper_exit_reference_price",
            "paper_exit_slippage",
            "paper_exit_charges",
            "paper_missed_fill_reason",
            "entry_order_type",
            "entry_order_price",
            "order_status",
            "order_status_message",
            "filled_quantity",
            "pending_quantity",
            "average_price",
            "planned_entry_price",
            "best_bid",
            "best_ask",
            "bid_qty",
            "ask_qty",
            "bid_ask_spread",
            "bid_ask_spread_percent",
            "top_depth_sufficient",
            "market_depth_block_reason",
            "target_1",
            "target_2",
            "target_3",
            "target_stage",
            "stop_loss",
            "target_1_percent",
            "target_2_percent",
            "target_3_percent",
            "target_percent",
            "stop_loss_percent",
            "exit_profile",
            "exit_profile_reason",
            "target_stage_reason",
            "trailing_stop_reason",
            "exit_decision_reason",
        ]:
            if not item.get(field) and paper_match.get(field):
                item[field] = paper_match[field]

        if not item.get("display_symbol"):
            item["display_symbol"] = format_option_display(
                item.get("option_symbol"),
                item.get("expiry"),
                item.get("strike"),
                item.get("option_type"),
                underlying=item.get("underlying") or item.get("symbol") or "NIFTY",
            )

        if not item.get("order_action") and item.get("option_type"):
            item["order_action"] = f"BUY {item['option_type']}"

        quantity = int(_as_number(item.get("quantity")))
        lot_size = int(_as_number(item.get("lot_size"))) if item.get("lot_size") else 65
        if not item.get("lots") and quantity and lot_size:
            item["lots"] = str(quantity // lot_size)

        entry_price = _as_number(item.get("entry_price"))
        stop_loss = _as_number(item.get("stop_loss"))
        if not item.get("estimated_required_amount") and entry_price and quantity:
            item["estimated_required_amount"] = round(entry_price * quantity, 2)

        if not item.get("estimated_risk_amount") and entry_price and stop_loss and quantity:
            item["estimated_risk_amount"] = round(max(entry_price - stop_loss, 0) * quantity, 2)

        if item.get("event") == "EXIT" or item.get("status") == "CLOSED":
            charges_val = effective_trade_charges(item)
            if charges_val > 0 and not item.get("paper_total_charges"):
                item["paper_total_charges"] = charges_val
            item["pnl"] = effective_net_pnl(item)

        enriched.append(item)

    return enriched


def combine_trade_rows(rows: list[dict]) -> list[dict]:
    combined = []
    entries_by_opened_at = {}

    for row in rows:
        if row.get("event") != "ENTRY":
            continue

        key = row.get("opened_at") or row.get("event_time")
        if not key:
            key = f"{row.get('date')}|{row.get('time')}|{row.get('option_symbol')}"

        trade = dict(row)
        trade["entry_time"] = row.get("time")
        trade["exit_time"] = ""
        trade["result"] = row.get("result") or "OPEN"
        trade["pnl"] = row.get("pnl") or ""
        trade["trade_status"] = "OPEN"
        entries_by_opened_at[key] = trade
        combined.append(trade)

    for row in rows:
        if row.get("event") not in {"PARTIAL_EXIT", "EXIT"}:
            continue

        key = row.get("opened_at")
        trade = entries_by_opened_at.get(key)

        if not trade:
            trade = dict(row)
            trade["entry_time"] = row.get("opened_at", "")[-8:] if row.get("opened_at") else (row.get("entry_time") or row.get("time") or "")
            combined.append(trade)

        for field in [
            "display_symbol",
            "underlying",
            "exchange",
            "order_action",
            "news_sentiment",
            "news_risk_level",
            "news_reason",
            "news_headlines",
            "news_blocked_original_signal",
            "news_mode",
            "news_conflict",
            "decision_engine",
            "chart_signal",
            "index_alignment",
            "index_reason",
            "market_regime",
            "market_regime_risk",
            "market_regime_action",
            "market_regime_reason",
            "market_regime_size_percent",
            "time_window",
            "time_quality_action",
            "time_quality_reason",
            "time_quality_size_percent",
            "entry_mode",
            "entry_freshness",
            "entry_reason",
            "trade_quality_score",
            "trade_quality_decision",
            "trade_quality_reason",
            "trade_quality_decision_reason",
            "confidence_score",
            "option_chain_score",
            "option_chain_decision_reason",
            "learning_score_penalty",
            "learning_penalty_reason",
            "entry_logic",
            "entry_trigger_price",
            "current_price",
            "potential_left_percent",
            "risk_reward",
            "hard_reject_reason",
            "chain_support",
            "chain_resistance",
            "chain_context_reason",
            "quality_entry_freshness",
            "quality_trend_strength",
            "quality_retest_breakout",
            "quality_option_premium",
            "quality_market_context",
            "quality_candle_pattern",
            "quality_size_percent",
            "option_type",
            "selection_mode",
            "strike",
            "expiry",
            "lot_size",
            "lots",
            "remaining_quantity",
            "exit_quantity",
            "partial_realized_pnl",
            "estimated_required_amount",
            "estimated_risk_amount",
            "gross_pnl",
            "paper_total_charges",
            "paper_fill_status",
            "paper_entry_reference_price",
            "paper_entry_slippage",
            "paper_entry_charges",
            "paper_exit_reference_price",
            "paper_exit_slippage",
            "paper_exit_charges",
            "paper_missed_fill_reason",
            "entry_order_type",
            "entry_order_price",
            "order_status",
            "order_status_message",
            "filled_quantity",
            "pending_quantity",
            "average_price",
            "planned_entry_price",
            "best_bid",
            "best_ask",
            "bid_qty",
            "ask_qty",
            "bid_ask_spread",
            "bid_ask_spread_percent",
            "top_depth_sufficient",
            "market_depth_block_reason",
            "target_1",
            "target_2",
            "target_3",
            "target_stage",
            "stop_loss",
            "target_1_percent",
            "target_2_percent",
            "target_3_percent",
            "target_percent",
            "stop_loss_percent",
            "exit_profile",
            "exit_profile_reason",
            "target_stage_reason",
            "trailing_stop_reason",
            "exit_decision_reason",
        ]:
            if row.get(field):
                trade[field] = row[field]

        trade["partial_realized_pnl"] = row.get("partial_realized_pnl") or trade.get("partial_realized_pnl")
        if row.get("event") == "PARTIAL_EXIT":
            trade["pnl"] = row.get("partial_realized_pnl") or row.get("pnl") or trade.get("pnl")
            trade["result"] = row.get("result")
            trade["status"] = row.get("status") or trade.get("status")
            trade["trade_status"] = "OPEN"
            trade["reason"] = row.get("reason")
            continue

        closed_time = (str(row.get("closed_at", "")).split("T")[-1].split(" ")[-1][:8]) if row.get("closed_at") else ""
        trade["exit_time"] = closed_time or row.get("exit_time") or (row.get("time") if row.get("event") == "EXIT" else "")
        trade["exit_price"] = row.get("exit_price")
        charges_val = effective_trade_charges(row)
        if charges_val > 0:
            trade["paper_total_charges"] = charges_val
        trade["pnl"] = effective_net_pnl(row)
        trade["result"] = "SUCCESS" if float(trade["pnl"] or 0) > 0 else ("FAIL" if float(trade["pnl"] or 0) < 0 else "BREAKEVEN")
        trade["status"] = row.get("status")
        trade["trade_status"] = "CLOSED"
        trade["closed_at"] = row.get("closed_at")
        trade["reason"] = row.get("reason")

    return combined


def merge_open_trade_updates(combined_rows: list[dict], open_trades: list[dict]) -> list[dict]:
    if not open_trades:
        return combined_rows

    by_opened_at = {
        row.get("opened_at"): row
        for row in combined_rows
        if row.get("opened_at")
    }
    fields = [
        "status",
        "stop_loss",
        "target_1",
        "target_2",
        "target_3",
        "target",
        "target_stage",
        "target_stage_reason",
        "target_stage_updated_at",
        "trailing_stop_reason",
        "trailing_stop_updated_at",
        "exit_decision_reason",
        "estimated_required_amount",
        "estimated_risk_amount",
        "gross_pnl",
        "paper_total_charges",
        "paper_fill_status",
        "paper_entry_reference_price",
        "paper_entry_slippage",
        "paper_entry_charges",
        "paper_exit_reference_price",
        "paper_exit_slippage",
        "paper_exit_charges",
        "paper_missed_fill_reason",
        "remaining_quantity",
        "partial_realized_pnl",
        "order_status",
        "best_bid",
        "best_ask",
        "bid_qty",
        "ask_qty",
        "bid_ask_spread",
        "bid_ask_spread_percent",
        "top_depth_sufficient",
        "market_depth_block_reason",
        "market_regime",
        "market_regime_risk",
        "market_regime_action",
        "market_regime_reason",
        "market_regime_size_percent",
        "time_window",
        "time_quality_action",
        "time_quality_reason",
        "time_quality_size_percent",
        "loss_reason",
        "loss_reason_tags",
        "loss_reason_detail",
        "entry_mode",
        "entry_freshness",
        "entry_reason",
        "trade_quality_score",
        "trade_quality_decision",
        "trade_quality_reason",
        "trade_quality_decision_reason",
        "confidence_score",
        "option_chain_score",
        "option_chain_decision_reason",
        "learning_score_penalty",
        "learning_penalty_reason",
        "entry_logic",
        "entry_trigger_price",
        "current_price",
        "potential_left_percent",
        "risk_reward",
        "hard_reject_reason",
        "chain_support",
        "chain_resistance",
        "chain_context_reason",
        "quality_entry_freshness",
        "quality_trend_strength",
        "quality_retest_breakout",
        "quality_option_premium",
        "quality_market_context",
        "quality_candle_pattern",
        "quality_size_percent",
    ]

    for trade in open_trades:
        key = trade.get("opened_at")
        row = by_opened_at.get(key)
        if not row:
            row = dict(trade)
            row["entry_time"] = str(trade.get("opened_at", ""))[-8:] if trade.get("opened_at") else ""
            row["exit_time"] = ""
            row["result"] = trade.get("status", "OPEN")
            row["trade_status"] = "OPEN"
            row["pnl"] = trade.get("live_pnl") if trade.get("live_pnl") is not None else trade.get("unrealized_pnl", 0.0)
            row["gross_pnl"] = trade.get("unrealized_pnl", 0.0)
            row["current_price"] = trade.get("current_price")
            row["profit_percent"] = trade.get("profit_percent")
            combined_rows.append(row)
            continue

        for field in fields:
            if field in trade:
                row[field] = trade[field]
        row["result"] = trade.get("status", row.get("result", "OPEN"))
        row["trade_status"] = "OPEN"
        row["pnl"] = trade.get("live_pnl") if trade.get("live_pnl") is not None else trade.get("unrealized_pnl", 0.0)
        row["gross_pnl"] = trade.get("unrealized_pnl", 0.0)
        row["current_price"] = trade.get("current_price")
        row["profit_percent"] = trade.get("profit_percent")

    return combined_rows


def read_day_data(day: str, mode: str | None = None) -> dict:
    day_dir = LOG_DIR / "by_date" / day
    trade_history = read_csv(day_dir / "trade_history.csv", limit=300)
    paper_trades = read_csv(day_dir / "paper_option_trades.csv", limit=300)
    no_trade_decisions = read_csv(day_dir / "no_trade_decisions.csv", limit=300)
    ghost_trades = read_csv(day_dir / "ghost_trades.csv", limit=300)

    try:
        from database import db_get_trades_for_date, db_get_decisions_for_date
        db_trades = db_get_trades_for_date(day, mode)
        if not trade_history and db_trades:
            formatted_trades = []
            for t in db_trades:
                row = dict(t)
                row["date"] = t.get("date")
                row["time"] = t.get("time")
                row["symbol"] = t.get("symbol")
                row["underlying"] = t.get("underlying") or t.get("symbol")
                row["option_symbol"] = t.get("option_symbol")
                row["display_symbol"] = t.get("display_symbol")
                row["status"] = t.get("status")
                row["event"] = "EXIT" if t.get("status") == "CLOSED" else "ENTRY"
                row["trade_status"] = "CLOSED" if t.get("status") == "CLOSED" else "OPEN"
                pnl_val = float(t.get("pnl") or 0)
                row["result"] = "SUCCESS" if pnl_val > 0 else ("FAIL" if pnl_val < 0 else "BREAKEVEN")
                row["reason"] = t.get("exit_reason") or t.get("entry_reason")
                row["entry_reason"] = t.get("entry_reason")
                row["entry_price"] = t.get("entry_price")
                row["exit_price"] = t.get("exit_price")
                row["pnl"] = t.get("pnl")
                row["gross_pnl"] = t.get("gross_pnl")
                row["paper_total_charges"] = t.get("charges")
                row["quantity"] = t.get("quantity")
                row["lots"] = t.get("lots")
                row["lot_size"] = t.get("lot_size")
                row["stop_loss"] = t.get("stop_loss")
                row["target_1"] = t.get("target_1")
                row["target_2"] = t.get("target_2")
                row["target_3"] = t.get("target_3")
                row["entry_time"] = t.get("time")
                row["exit_time"] = str(t.get("closed_at", ""))[-8:] if t.get("closed_at") else ""
                row["mode"] = t.get("mode")
                formatted_trades.append(row)
            trade_history = formatted_trades
            if not paper_trades and mode in {"PAPER", None}:
                paper_trades = formatted_trades

        if not no_trade_decisions:
            db_decisions = db_get_decisions_for_date(day)
            if db_decisions:
                formatted_decisions = []
                for d in db_decisions:
                    d_row = dict(d)
                    d_row["reason"] = d.get("blocked_reason")
                    d_row["final_score"] = d.get("setup_score")
                    formatted_decisions.append(d_row)
                no_trade_decisions = formatted_decisions
    except Exception:
        pass

    paper_trades = enrich_rows(paper_trades)
    trade_history = enrich_rows(trade_history, paper_trades)
    if mode:
        trade_history = filter_rows_for_mode(trade_history, mode)
        paper_trades = filter_rows_for_mode(paper_trades, mode)
    return {
        "day": day,
        "performance": calculate_performance(trade_history),
        "trade_history": trade_history,
        "combined_trades": combine_trade_rows(trade_history),
        "paper_trades": paper_trades,
        "no_trade_decisions": no_trade_decisions,
        "ghost_trades": ghost_trades,
    }


def build_dashboard_data() -> dict:
    apply_runtime_settings(config)
    active_mode = config.TRADING_MODE
    today = today_date().isoformat()
    trade_history = read_csv(LOG_DIR / "trade_history.csv", limit=200)
    paper_trades = read_csv(LOG_DIR / "paper_option_trades.csv", limit=200)
    no_trade_decisions = read_csv(LOG_DIR / "no_trade_decisions.csv", limit=200)
    ghost_trades = read_csv(LOG_DIR / "ghost_trades.csv", limit=200)
    paper_trades = enrich_rows(paper_trades)
    trade_history = enrich_rows(trade_history, paper_trades)
    mode_trade_history = filter_rows_for_mode(trade_history, active_mode)
    mode_paper_trades = filter_rows_for_mode(paper_trades, active_mode)
    open_state = read_json(OPEN_TRADE_FILE)
    if isinstance(open_state, list):
        open_trades = [trade for trade in open_state if trade.get("status") in {"OPEN", "LIVE_OPEN", "LIVE_PENDING"}]
    elif isinstance(open_state, dict) and open_state.get("status") in {"OPEN", "LIVE_OPEN", "LIVE_PENDING"}:
        open_trades = [open_state]
    else:
        open_trades = []
    open_trades = filter_rows_for_mode(open_trades, active_mode)
    for ot in open_trades:
        entry_p = float(ot.get("entry_price") or 0)
        curr_p = float(ot.get("current_price") or ot.get("last_monitor_price") or entry_p)
        rem_q = int(ot.get("remaining_quantity") or ot.get("quantity") or 0)
        unrealized = round((curr_p - entry_p) * rem_q, 2) if entry_p and rem_q else 0.0
        booked = float(ot.get("partial_realized_pnl") or 0.0)
        ot["current_price"] = curr_p
        ot["unrealized_pnl"] = ot.get("unrealized_pnl") if ot.get("unrealized_pnl") is not None else unrealized
        ot["live_pnl"] = ot.get("live_pnl") if ot.get("live_pnl") is not None else round(unrealized + booked, 2)
        ot["profit_percent"] = ot.get("profit_percent") if ot.get("profit_percent") is not None else (round(((curr_p - entry_p) / entry_p) * 100, 2) if entry_p else 0.0)

    account_status = load_active_account_status(active_mode)
    paper_account = build_paper_account()
    performance = calculate_performance(mode_trade_history)

    today_data = read_day_data(today, active_mode)
    today_data["combined_trades"] = merge_open_trade_updates(today_data["combined_trades"], open_trades)
    active_account = active_account_summary(account_status, paper_account, today_data["performance"], active_mode, open_trades=open_trades)

    if not mode_trade_history and today_data.get("trade_history"):
        mode_trade_history = today_data["trade_history"]
        performance = today_data["performance"]
    if not mode_paper_trades and today_data.get("paper_trades"):
        mode_paper_trades = today_data["paper_trades"]
    if not no_trade_decisions and today_data.get("no_trade_decisions"):
        no_trade_decisions = today_data["no_trade_decisions"]

    return {
        "config": {
            "paper_trade": config.PAPER_TRADE,
            "trading_mode": config.TRADING_MODE,
            "enable_live_auto": config.ENABLE_LIVE_AUTO,
            "max_trade_amount": config.MAX_TRADE_AMOUNT,
            "max_daily_loss": config.MAX_DAILY_LOSS,
            "analysis_interval_seconds": config.ANALYSIS_INTERVAL_SECONDS,
            "open_trade_monitor_interval_seconds": config.OPEN_TRADE_MONITOR_INTERVAL_SECONDS,
            "paper_analysis_interval_seconds": config.PAPER_ANALYSIS_INTERVAL_SECONDS,
            "paper_open_trade_monitor_interval_seconds": config.PAPER_OPEN_TRADE_MONITOR_INTERVAL_SECONDS,
            "auto_analysis_interval_seconds": config.AUTO_ANALYSIS_INTERVAL_SECONDS,
            "auto_open_trade_monitor_interval_seconds": config.AUTO_OPEN_TRADE_MONITOR_INTERVAL_SECONDS,
            "no_new_trade_after": config.NO_NEW_TRADE_AFTER,
            "square_off_time": config.SQUARE_OFF_TIME,
        },
        "fyers_session": fyers_session_status(),
        "open_trade": open_trades[0] if open_trades else None,
        "open_trades": open_trades,
        "pre_open": load_pre_open_context(),
        "analysis": load_analysis_snapshot(),
        "account_status": account_status,
        "paper_account": paper_account,
        "active_account": active_account,
        "today": today_data,
        "performance": performance,
        "trade_history": mode_trade_history,
        "paper_trades": mode_paper_trades,
        "no_trade_decisions": no_trade_decisions,
        "ghost_trades": ghost_trades,
        "trade_days": list_trade_days(),
        "db_status": {
            "engine": "MongoDB Atlas" if (lambda: (__import__("database").is_mongo()))() else "SQLite Fallback",
            "connected": bool((lambda: (__import__("database").is_mongo()))()),
            "error": str((lambda: (__import__("database").get_mongo_error()))() or ""),
        },
    }


def process_auth_code(code_or_url: str) -> dict:
    auth_code = _extract_auth_code(code_or_url)
    if not auth_code:
        raise ValueError("No valid auth_code found.")

    print(f"[Fyers Login] Auth code captured: {auth_code[:15]}...")
    access_token = exchange_auth_code_for_token(auth_code)
    fyers_client = create_fyers_client(access_token)
    print(f"[Fyers Login] Session generated successfully! access_token saved.")
    profile = {}
    try:
        profile = fyers_client.profile()
        print(f"[Fyers Login] Welcome: {profile.get('user_name')} ({profile.get('user_id')})")
        save_account_status(
            load_account_status().get("available_balance", 0),
            profile,
            active_balance=load_account_status().get("available_balance", 0),
            mode=config.TRADING_MODE,
        )
    except Exception:
        pass

    return {
        "ok": True,
        "access_token": access_token,
        "user_name": profile.get("user_name"),
        "user_id": profile.get("user_id"),
    }


def process_request_token(token_or_url: str) -> dict:
    return process_auth_code(token_or_url)


class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)
        auth_code = (
            params.get("auth_code", [""])[0]
            or params.get("code", [""])[0]
            or params.get("request_token", [""])[0]
        )

        if auth_code:
            try:
                process_auth_code(auth_code)
                if path in {"/callback", "/fyers/callback"}:
                    self.send_fyers_login_page("Fyers login successful.", success=True)
                    return
                self.redirect("/")
                return
            except Exception as login_error:
                if path in {"/callback", "/fyers/callback"}:
                    self.send_fyers_login_page(f"Fyers login failed: {login_error}", success=False)
                    return

        if path == "/api/auth-status":
            session = fyers_session_status()
            db_conn = False
            try:
                from database import is_mongo
                db_conn = is_mongo()
            except Exception:
                pass
            self.send_json({
                "active": bool(session.get("active")),
                "user_name": session.get("user_name"),
                "user_id": session.get("user_id"),
                "db_status": {
                    "engine": "MongoDB Atlas" if db_conn else "SQLite Fallback",
                    "connected": db_conn,
                }
            })
            return

        if path == "/" or path == "/index.html":
            session = fyers_session_status()
            if not session.get("active"):
                self.redirect("/login")
                return
            self.send_html()
            return

        if path == "/login":
            session = fyers_session_status()
            if session.get("active"):
                self.redirect("/")
                return
            self.send_login_html()
            return

        if path == "/logs":
            session = fyers_session_status()
            if not session.get("active"):
                self.redirect("/login")
                return
            self.send_logs_html()
            return

        if path == "/api/logs":
            session = fyers_session_status()
            if not session.get("active"):
                self.send_json({"authenticated": False, "redirect": "/login"}, status=401)
                return
            from log_capture import read_live_logs
            date_str, size_str, content = read_live_logs()
            self.send_json({"date": date_str, "size_kb": size_str, "content": content})
            return

        if path == "/api/logs/raw":
            session = fyers_session_status()
            if not session.get("active"):
                self.redirect("/login")
                return
            from log_capture import read_live_logs
            _, _, content = read_live_logs()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(content.encode("utf-8", errors="replace"))
            return

        if path == "/api/logs/download":
            session = fyers_session_status()
            if not session.get("active"):
                self.redirect("/login")
                return
            from log_capture import read_live_logs
            date_str, _, content = read_live_logs()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="bot_logs_{date_str}.txt"')
            self.end_headers()
            self.wfile.write(content.encode("utf-8", errors="replace"))
            return

        if path == "/api/dashboard":
            session = fyers_session_status()
            if not session.get("active"):
                self.send_json({"authenticated": False, "redirect": "/login"}, status=401)
                return
            self.send_json(build_dashboard_data())
            return

        if path in {"/fyers/login", "/fyers/oauth"}:
            self.redirect(get_fyers_login_url())
            return

        if path in {"/fyers/logout", "/logout"}:
            logout()
            _BROKER_ACCOUNT_CACHE["status"] = None
            _BROKER_ACCOUNT_CACHE["at"] = 0.0
            clear_fyers_session_cache()
            self.redirect("/login?logged_out=true")
            return

        if path in {"/callback", "/fyers/callback"}:
            self.handle_fyers_callback(parsed)
            return

        if path == "/api/day":
            session = fyers_session_status()
            if not session.get("active"):
                self.send_json({"authenticated": False, "redirect": "/login"}, status=401)
                return
            params = parse_qs(parsed.query)
            day = params.get("date", [""])[0]
            if not day:
                self.send_json({
                    "day": "",
                    "performance": calculate_performance([]),
                    "trade_history": [],
                    "combined_trades": [],
                    "paper_trades": [],
                    "no_trade_decisions": [],
                    "ghost_trades": [],
                })
                return
            self.send_json(read_day_data(day))
            return

        self.send_json({"error": "Not found"})

    def handle_fyers_callback(self, parsed):
        params = parse_qs(parsed.query)
        auth_code = (
            params.get("auth_code", [""])[0]
            or params.get("code", [""])[0]
            or params.get("request_token", [""])[0]
        )
        status = params.get("s", [""])[0] or params.get("status", [""])[0]
        error = params.get("message", [""])[0] or params.get("error", [""])[0]

        if not auth_code or status not in {"", "ok", "200", "success"}:
            err_msg = error or "Fyers login did not return an auth code."
            self.redirect(f"/login?error={quote_plus(err_msg)}")
            return

        try:
            process_auth_code(auth_code)
            clear_fyers_session_cache()
            self.redirect("/")
            return
        except Exception as login_error:
            self.redirect(f"/login?error={quote_plus(str(login_error))}")
            return

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path in {"/api/fyers/submit-token", "/api/submit-token"}:
            length = int(self.headers.get("Content-Length", "0") or 0)
            body = self.rfile.read(length).decode("utf-8") if length else "{}"
            try:
                payload = json.loads(body)
                raw_value = str(
                    payload.get("auth_code")
                    or payload.get("code")
                    or payload.get("token")
                    or payload.get("url")
                    or payload.get("token_or_url")
                    or ""
                )
                res = process_auth_code(raw_value)
                clear_fyers_session_cache()
                self.send_json(res)
            except Exception as error:
                self.send_json({"ok": False, "error": str(error)}, status=400)
            return

        if parsed.path in {"/api/fyers/logout", "/api/logout"}:
            try:
                logout()
                _BROKER_ACCOUNT_CACHE["status"] = None
                _BROKER_ACCOUNT_CACHE["at"] = 0.0
                clear_fyers_session_cache()
                self.send_json({"ok": True, "message": "Logged out successfully", "login_url": "/login?logged_out=true"})
            except Exception as error:
                self.send_json({"ok": False, "error": str(error)}, status=400)
            return

        if parsed.path != "/api/trading-mode":
            self.send_json({"error": "Not found"}, status=404)
            return

        length = int(self.headers.get("Content-Length", "0") or 0)
        body = self.rfile.read(length).decode("utf-8") if length else "{}"
        try:
            payload = json.loads(body)
            mode = str(payload.get("mode", "")).upper()
            settings = save_trading_mode(mode)
            apply_runtime_settings(config)
            broker_refresh = refresh_broker_account_status() if mode == "LIVE_AUTO" else None
        except Exception as error:
            self.send_json({"ok": False, "error": str(error)}, status=400)
            return

        self.send_json({
            "ok": True,
            "settings": settings,
            "trading_mode": config.TRADING_MODE,
            "paper_trade": config.PAPER_TRADE,
            "enable_live_auto": config.ENABLE_LIVE_AUTO,
            "broker_refresh": broker_refresh,
        })

    def send_html(self):
        html_path = BASE_DIR / "dashboard.html"
        body = html_path.read_text(encoding="utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def send_login_html(self):
        login_path = BASE_DIR / "login.html"
        body = login_path.read_text(encoding="utf-8") if login_path.exists() else "<h1>Login Required</h1><a href='/fyers/login'>Login with Fyers</a>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def send_logs_html(self):
        logs_path = BASE_DIR / "logs.html"
        body = logs_path.read_text(encoding="utf-8") if logs_path.exists() else "<h1>Logs page not found</h1>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def send_fyers_login_page(self, message: str, success: bool):
        redirect_tag = '<meta http-equiv="refresh" content="1; url=/">' if success else ""
        title = "Fyers Login"
        body = f"""
        <!doctype html>
        <html>
          <head>
            <meta charset="utf-8">
            <title>{title}</title>
            {redirect_tag}
            <style>
              body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; padding: 40px; background: #0f172a; color: #f8fafc; }}
              .box {{ max-width: 640px; margin: 0 auto; border: 1px solid #334155; border-radius: 8px; padding: 24px; background: #1e293b; }}
              a {{ color: #38bdf8; text-decoration: none; }}
            </style>
          </head>
          <body>
            <div class="box">
              <h2>{message}</h2>
              <p>{"Redirecting to dashboard..." if success else "Please retry login from the dashboard."}</p>
              <p><a href="/">Return to dashboard</a></p>
            </div>
          </body>
        </html>
        """
        encoded = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


    def send_json(self, data, status: int = 200):
        body = json.dumps(data, default=str)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def redirect(self, location: str):
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def log_message(self, format, *args):
        return


def create_dashboard_server():
    host = config.DASHBOARD_HOST
    server = None
    port = config.DASHBOARD_PORT

    for candidate_port in range(config.DASHBOARD_PORT, config.DASHBOARD_PORT + 100):
        try:
            server = ThreadingHTTPServer((host, candidate_port), DashboardHandler)
            port = candidate_port
            break
        except OSError:
            continue

    if server is None:
        server = ThreadingHTTPServer((host, 0), DashboardHandler)
        port = server.server_address[1]

    if config.PUBLIC_URL:
        public_url = config.PUBLIC_URL
    elif host == "0.0.0.0":
        public_url = f"http://127.0.0.1:{port}"
    else:
        public_url = f"http://{host}:{port}"

    return server, public_url


def start_dashboard_background():
    server, url = create_dashboard_server()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return url


def main():
    server, url = create_dashboard_server()

    print(f"Dashboard running at {url}")
    print("Press Ctrl+C to stop dashboard.")
    server.serve_forever()


if __name__ == "__main__":
    main()
