# trade_state.py - Track the currently open paper/live trade

import json
from datetime import date, datetime, timedelta
from pathlib import Path

import config
from loss_reason_analyzer import analyze_loss_reason
from log_utils import csv_datetime, log
from market_depth import apply_market_depth_to_trade, is_t1_continuation_strong
from market_time import market_now_naive, today_date
from order_manager import get_order_by_id, place_order, round_to_tick
from paper_account import refresh_paper_account
from paper_fill_engine import apply_paper_exit_fill, estimate_order_charges
from performance_report import print_performance_summary
from terminal_ui import table
from trade_history import append_trade_history


STATE_FILE = Path("open_trade.json")


def _read_state_trades() -> list[dict]:
    if not STATE_FILE.exists():
        return []

    try:
        data = json.loads(STATE_FILE.read_text())
    except json.JSONDecodeError:
        return []

    if isinstance(data, list):
        return [trade for trade in data if isinstance(trade, dict)]

    if isinstance(data, dict):
        return [data]

    return []


def _write_state_trades(trades: list[dict]) -> None:
    STATE_FILE.write_text(json.dumps(trades, indent=2, default=str))


def _trade_key(trade: dict) -> str:
    return trade.get("opened_at") or f"{trade.get('option_symbol')}|{trade.get('entry_price')}"


def _replace_state_trade(updated_trade: dict) -> None:
    trades = _read_state_trades()
    updated_key = _trade_key(updated_trade)
    replaced = False

    for index, trade in enumerate(trades):
        if _trade_key(trade) == updated_key:
            trades[index] = updated_trade
            replaced = True
            break

    if not replaced:
        trades.append(updated_trade)

    _write_state_trades(trades)


def load_open_trades() -> list[dict]:
    return [
        trade
        for trade in _read_state_trades()
        if trade.get("status") in {"OPEN", "LIVE_OPEN", "LIVE_PENDING"}
    ]


def load_open_trade() -> dict | None:
    open_trades = load_open_trades()
    return open_trades[0] if open_trades else None


def _parse_expiry(expiry: str | None) -> date | None:
    if not expiry:
        return None

    try:
        return datetime.strptime(expiry, "%Y-%m-%d").date()
    except ValueError:
        return None


def _paper_risk_capped_exit_price(trade: dict, exit_price: float, exit_reason: str) -> tuple[float, str | None]:
    if trade.get("mode") != "PAPER":
        return exit_price, None

    entry_price = float(trade["entry_price"])
    quantity = int(trade.get("remaining_quantity") or trade["quantity"])
    adjusted_exit_price = float(exit_price)
    cap_reason = None

    if exit_reason in {"STOP_LOSS_HIT", "DYNAMIC_SAFE_SL_HIT"}:
        stop_loss = float(trade["stop_loss"])
        if adjusted_exit_price < stop_loss:
            adjusted_exit_price = stop_loss
            cap_reason = "Paper SL fill capped at current stop_loss."

    estimated_risk = float(trade.get("estimated_risk_amount") or 0)
    if estimated_risk > 0 and quantity > 0:
        pnl = (adjusted_exit_price - entry_price) * quantity
        if pnl < -estimated_risk:
            adjusted_exit_price = entry_price - (estimated_risk / quantity)
            cap_reason = "Paper loss capped at estimated_risk_amount."

    return round(adjusted_exit_price, 2), cap_reason


def save_open_trade(option_trade: dict, signal: dict, mode: str, order_id=None, status: str | None = None) -> dict:
    trade = {
        **option_trade,
        "signal": signal.get("SIGNAL"),
        "signal_reason": signal.get("REASON"),
        "news_sentiment": signal.get("NEWS_SENTIMENT"),
        "news_risk_level": signal.get("NEWS_RISK_LEVEL"),
        "news_reason": signal.get("NEWS_REASON"),
        "news_headlines": signal.get("NEWS_HEADLINES"),
        "news_blocked_original_signal": signal.get("NEWS_BLOCKED_ORIGINAL_SIGNAL"),
        "news_mode": signal.get("NEWS_MODE"),
        "news_conflict": signal.get("NEWS_CONFLICT"),
        "market_context_bias": signal.get("MARKET_CONTEXT_BIAS"),
        "market_context_score": signal.get("MARKET_CONTEXT_SCORE"),
        "market_context_risk_level": signal.get("MARKET_CONTEXT_RISK_LEVEL"),
        "market_context_reason": signal.get("MARKET_CONTEXT_REASON"),
        "market_context_mode": signal.get("MARKET_CONTEXT_MODE"),
        "market_context_conflict": signal.get("MARKET_CONTEXT_CONFLICT"),
        "market_context_blocked_original_signal": signal.get("MARKET_CONTEXT_BLOCKED_ORIGINAL_SIGNAL"),
        "market_regime": signal.get("MARKET_REGIME"),
        "market_regime_risk": signal.get("MARKET_REGIME_RISK"),
        "market_regime_action": signal.get("MARKET_REGIME_ACTION"),
        "market_regime_reason": signal.get("MARKET_REGIME_REASON"),
        "market_regime_size_percent": signal.get("MARKET_REGIME_SIZE_PERCENT") or option_trade.get("market_regime_size_percent"),
        "time_window": signal.get("TIME_WINDOW"),
        "time_quality_action": signal.get("TIME_QUALITY_ACTION"),
        "time_quality_reason": signal.get("TIME_QUALITY_REASON"),
        "time_quality_size_percent": signal.get("TIME_QUALITY_SIZE_PERCENT") or option_trade.get("time_quality_size_percent"),
        "decision_engine": signal.get("DECISION_ENGINE"),
        "chart_signal": signal.get("CHART_SIGNAL"),
        "index_alignment": signal.get("INDEX_ALIGNMENT"),
        "index_reason": signal.get("INDEX_REASON"),
        "candle_pattern_bias": signal.get("CANDLE_PATTERN_BIAS"),
        "candle_patterns": signal.get("CANDLE_PATTERNS"),
        "candle_pattern_bullish_score": signal.get("CANDLE_PATTERN_BULLISH_SCORE"),
        "candle_pattern_bearish_score": signal.get("CANDLE_PATTERN_BEARISH_SCORE"),
        "secondary_timeframe_patterns": signal.get("SECONDARY_TIMEFRAME_PATTERNS"),
        "entry_mode": signal.get("ENTRY_MODE"),
        "entry_freshness": signal.get("ENTRY_FRESHNESS"),
        "entry_reason": signal.get("ENTRY_REASON"),
        "trade_quality_score": signal.get("TRADE_QUALITY_SCORE") or option_trade.get("trade_quality_score"),
        "trade_quality_decision": signal.get("TRADE_QUALITY_DECISION") or option_trade.get("trade_quality_decision"),
        "trade_quality_reason": signal.get("TRADE_QUALITY_REASON") or option_trade.get("trade_quality_reason"),
        "trade_quality_decision_reason": signal.get("TRADE_QUALITY_DECISION_REASON") or option_trade.get("trade_quality_decision_reason"),
        "confidence_score": option_trade.get("confidence_score"),
        "option_chain_score": option_trade.get("option_chain_score"),
        "option_chain_decision_reason": option_trade.get("option_chain_decision_reason"),
        "option_chain_scores": option_trade.get("option_chain_scores"),
        "learning_score_penalty": option_trade.get("learning_score_penalty") or signal.get("LEARNING_SCORE_PENALTY"),
        "learning_penalty_reason": option_trade.get("learning_penalty_reason") or signal.get("LEARNING_PENALTY_REASON"),
        "entry_logic": option_trade.get("entry_logic"),
        "entry_trigger_price": option_trade.get("entry_trigger_price"),
        "current_price": option_trade.get("current_price"),
        "potential_left_percent": option_trade.get("potential_left_percent"),
        "risk_reward": option_trade.get("risk_reward"),
        "hard_reject_reason": option_trade.get("hard_reject_reason"),
        "chain_support": option_trade.get("chain_support"),
        "chain_resistance": option_trade.get("chain_resistance"),
        "chain_context_reason": option_trade.get("chain_context_reason"),
        "htf_bias": signal.get("HTF_BIAS") or option_trade.get("htf_bias"),
        "htf_trend_reason": signal.get("HTF_TREND_REASON") or option_trade.get("htf_trend_reason"),
        "index_stop_loss": signal.get("INDEX_STOP_LOSS") or option_trade.get("index_stop_loss"),
        "live_option_vwap": option_trade.get("live_option_vwap") or option_trade.get("option_vwap"),
        "live_option_ema9": option_trade.get("live_option_ema9") or option_trade.get("option_ema_9"),
        "live_option_atr": option_trade.get("live_option_atr") or option_trade.get("option_atr"),
        "live_option_swing_low": option_trade.get("live_option_swing_low") or option_trade.get("option_swing_low"),
        "live_chart_trend_intact": option_trade.get("live_chart_trend_intact", True),
        "quality_entry_freshness": signal.get("QUALITY_ENTRY_FRESHNESS") or option_trade.get("quality_entry_freshness"),
        "quality_trend_strength": signal.get("QUALITY_TREND_STRENGTH") or option_trade.get("quality_trend_strength"),
        "quality_retest_breakout": signal.get("QUALITY_RETEST_BREAKOUT") or option_trade.get("quality_retest_breakout"),
        "quality_option_premium": signal.get("QUALITY_OPTION_PREMIUM") or option_trade.get("quality_option_premium"),
        "quality_market_context": signal.get("QUALITY_MARKET_CONTEXT") or option_trade.get("quality_market_context"),
        "quality_candle_pattern": signal.get("QUALITY_CANDLE_PATTERN") or option_trade.get("quality_candle_pattern"),
        "quality_size_percent": signal.get("QUALITY_SIZE_PERCENT") or option_trade.get("quality_size_percent"),
        "mode": mode,
        "paper_fill_status": option_trade.get("paper_fill_status"),
        "paper_entry_reference_price": option_trade.get("paper_entry_reference_price"),
        "paper_entry_slippage": option_trade.get("paper_entry_slippage"),
        "paper_entry_charges": (
            option_trade.get("paper_entry_charges")
            if option_trade.get("paper_entry_charges") is not None
            else estimate_order_charges(
                float(option_trade.get("entry_price") or 0),
                int(option_trade.get("quantity") or 0),
            )
        ),
        "paper_missed_fill_reason": option_trade.get("paper_missed_fill_reason"),
        "order_id": order_id,
        "opened_at": csv_datetime(),
        "status": status or ("LIVE_OPEN" if mode != "PAPER" else "OPEN"),
        "remaining_quantity": int(option_trade.get("quantity") or 0),
        "partial_realized_pnl": 0.0,
        "partial_realized_gross_pnl": 0.0,
        "paper_partial_exit_charges": 0.0,
        "partial_exit_t1_done": False,
        "partial_exit_t2_done": False,
    }
    trades = _read_state_trades()
    open_trades = [item for item in trades if item.get("status") in {"OPEN", "LIVE_OPEN", "LIVE_PENDING"}]
    closed_trades = [item for item in trades if item.get("status") not in {"OPEN", "LIVE_OPEN", "LIVE_PENDING"}]
    _write_state_trades(closed_trades[-3:] + open_trades + [trade])
    append_trade_history("ENTRY", trade, result="OPEN", reason=trade["signal_reason"])
    return trade


def update_trade_prices_after_fill(trade: dict, average_price: float) -> dict:
    planned_entry = float(trade.get("entry_price") or 0)
    fill_price = float(average_price or planned_entry)
    if not fill_price or fill_price == planned_entry:
        return trade

    trade["planned_entry_price"] = planned_entry
    trade["entry_price"] = round(fill_price, 2)

    for key, percent_key in [
        ("stop_loss", "stop_loss_percent"),
        ("target_1", "target_1_percent"),
        ("target_2", "target_2_percent"),
        ("target_3", "target_3_percent"),
        ("target", "target_percent"),
    ]:
        percent = trade.get(percent_key)
        if percent in {None, ""}:
            continue
        percent = float(percent)
        if key == "stop_loss":
            trade[key] = round(fill_price * (1 - (percent / 100)), 2)
        else:
            trade[key] = round(fill_price * (1 + (percent / 100)), 2)

    quantity = int(float(trade.get("quantity") or 0))
    stop_loss = float(trade.get("stop_loss") or 0)
    trade["estimated_required_amount"] = round(fill_price * quantity, 2)
    trade["estimated_risk_amount"] = round(max(fill_price - stop_loss, 0) * quantity, 2)
    return trade


def refresh_pending_live_trade(fyers, trade: dict) -> dict:
    order = get_order_by_id(fyers, trade.get("order_id"))
    if not order:
        return trade

    trade["order_status"] = order.get("status")
    trade["order_status_message"] = order.get("status_message") or order.get("status_message_raw")
    trade["filled_quantity"] = order.get("filled_quantity")
    trade["pending_quantity"] = order.get("pending_quantity")
    trade["average_price"] = order.get("average_price")
    trade["order_checked_at"] = csv_datetime()

    status = str(order.get("status") or "").upper()
    if status == "COMPLETE":
        trade = update_trade_prices_after_fill(trade, float(order.get("average_price") or trade.get("entry_price") or 0))
        trade["status"] = "LIVE_OPEN"
        trade["filled_at"] = csv_datetime()
        log(f"Entry order filled. Trade is now LIVE_OPEN: {trade.get('option_symbol')}")
    elif status in {"CANCELLED", "REJECTED"}:
        trade["status"] = "ORDER_CANCELLED" if status == "CANCELLED" else "ORDER_REJECTED"
        trade["closed_at"] = csv_datetime()
        append_trade_history("EXIT", trade, result=status, reason=trade.get("order_status_message") or status)
        log(f"Entry order {status.lower()}: {trade.get('option_symbol')}")

    _replace_state_trade(trade)
    return trade


def close_stale_paper_trade(exit_reason: str, trade: dict | None = None) -> dict | None:
    trade = trade or load_open_trade()
    if not trade:
        return None

    if trade.get("status") == "LIVE_OPEN":
        log("Live trade quote unavailable. Please check position manually in Fyers.")
        return trade

    entry_price = float(trade["entry_price"])
    total_quantity = int(trade.get("quantity") or trade.get("remaining_quantity") or 0)
    quantity = int(trade.get("remaining_quantity") or trade["quantity"])
    exit_price = 0.0
    remaining_pnl = round((exit_price - entry_price) * quantity, 2)
    partial_gross = float(
        trade.get("partial_realized_gross_pnl")
        if trade.get("partial_realized_gross_pnl") is not None
        else (trade.get("partial_realized_pnl") or 0)
    )
    gross_pnl = round(partial_gross + remaining_pnl, 2)
    entry_charges = float(trade.get("paper_entry_charges") or 0)
    if entry_charges <= 0 and entry_price > 0 and total_quantity > 0:
        entry_charges = estimate_order_charges(entry_price, total_quantity)
    partial_charges = float(trade.get("paper_partial_exit_charges") or 0)
    charges = round(entry_charges + partial_charges, 2)
    pnl = round(gross_pnl - charges, 2)

    trade.update(
        {
            "exit_price": exit_price,
            "exit_reason": exit_reason,
            "closed_at": csv_datetime(),
            "gross_pnl": gross_pnl,
            "paper_total_charges": charges,
            "pnl": pnl,
            "status": "CLOSED",
        }
    )
    if pnl < 0:
        trade.update(analyze_loss_reason(trade))
    _replace_state_trade(trade)
    append_trade_history("EXIT", trade, result="FAIL", reason=exit_reason)
    if trade.get("mode") == "PAPER":
        refresh_paper_account()
    return trade


def close_open_trade(exit_price: float, exit_reason: str, trade: dict | None = None) -> dict | None:
    trade = trade or load_open_trade()
    if not trade:
        return None

    raw_exit_price = float(exit_price)
    filled_exit_price, paper_exit = apply_paper_exit_fill(trade, raw_exit_price)
    if paper_exit:
        raw_exit_price = float(exit_price)
        exit_price = filled_exit_price
    else:
        exit_price = raw_exit_price
    exit_price, risk_cap_reason = _paper_risk_capped_exit_price(trade, exit_price, exit_reason)
    entry_price = float(trade["entry_price"])
    total_quantity = int(trade.get("quantity") or trade.get("remaining_quantity") or 0)
    quantity = int(trade.get("remaining_quantity") or trade["quantity"])
    remaining_pnl = round((exit_price - entry_price) * quantity, 2)
    partial_gross = float(
        trade.get("partial_realized_gross_pnl")
        if trade.get("partial_realized_gross_pnl") is not None
        else (trade.get("partial_realized_pnl") or 0)
    )
    gross_pnl = round(partial_gross + remaining_pnl, 2)
    estimated_risk = float(trade.get("estimated_risk_amount") or 0)
    if trade.get("mode") == "PAPER" and estimated_risk > 0 and gross_pnl < -estimated_risk:
        gross_pnl = round(-estimated_risk, 2)
        risk_cap_reason = (risk_cap_reason or "Paper gross loss capped at estimated_risk_amount.")

    entry_charges = float(trade.get("paper_entry_charges") or 0)
    if entry_charges <= 0 and entry_price > 0 and total_quantity > 0:
        entry_charges = estimate_order_charges(entry_price, total_quantity)
    partial_charges = float(trade.get("paper_partial_exit_charges") or 0)
    exit_charges = float(paper_exit.get("paper_exit_charges") or 0)
    if exit_charges <= 0 and exit_price > 0 and quantity > 0:
        exit_charges = estimate_order_charges(exit_price, quantity)
    charges = round(entry_charges + partial_charges + exit_charges, 2)
    pnl = round(gross_pnl - charges, 2)

    close_update = {
        "exit_price": exit_price,
        "exit_reason": exit_reason,
        "closed_at": csv_datetime(),
        "remaining_pnl": remaining_pnl,
        "gross_pnl": gross_pnl,
        "paper_entry_charges": round(entry_charges, 2),
        "paper_exit_charges": round(exit_charges, 2),
        "paper_total_charges": charges,
        "pnl": pnl,
        "remaining_quantity": 0,
        "status": "CLOSED",
    }
    if risk_cap_reason:
        close_update["raw_exit_price"] = raw_exit_price
        close_update["risk_cap_applied"] = risk_cap_reason
    if paper_exit:
        close_update.update(paper_exit)
        close_update["paper_total_charges"] = charges

    trade.update(close_update)
    if pnl < 0:
        trade.update(analyze_loss_reason(trade))
    _replace_state_trade(trade)
    if pnl > 0:
        result = "SUCCESS"
    elif pnl < 0:
        result = "FAIL"
    else:
        result = "BREAKEVEN"
    append_trade_history("EXIT", trade, result=result, reason=exit_reason)
    if trade.get("mode") == "PAPER":
        refresh_paper_account()
    return trade


def partial_exit_quantity(trade: dict, stage: int) -> int:
    if not config.PARTIAL_EXIT_ENABLED:
        return 0

    lot_size = int(trade.get("lot_size") or 1)
    original_quantity = int(trade.get("quantity") or 0)
    remaining_quantity = int(trade.get("remaining_quantity") or original_quantity)
    original_lots = original_quantity // lot_size if lot_size else 0
    if original_lots <= 1:
        return 0

    percent = config.PARTIAL_EXIT_T1_PERCENT if stage == 1 else config.PARTIAL_EXIT_T2_PERCENT
    target_lots = max(1, int(original_lots * (percent / 100)))
    target_quantity = target_lots * lot_size

    if remaining_quantity - target_quantity < lot_size:
        target_quantity = max(0, remaining_quantity - lot_size)

    return min(target_quantity, remaining_quantity)


def close_partial_trade(fyers, trade: dict, current_price: float, stage: int, reason: str) -> dict:
    exit_quantity = partial_exit_quantity(trade, stage)
    if exit_quantity <= 0:
        return trade

    exit_order_id = maybe_place_live_exit_order(fyers, trade, reason, quantity=exit_quantity, current_price=current_price)
    if trade.get("status") == "LIVE_OPEN" and not exit_order_id:
        return trade

    entry_price = float(trade["entry_price"])
    gross_pnl = round((float(current_price) - entry_price) * exit_quantity, 2)
    partial_order_charges = estimate_order_charges(float(current_price), exit_quantity)
    pnl = round(gross_pnl - partial_order_charges, 2)
    remaining_quantity = int(trade.get("remaining_quantity") or trade["quantity"]) - exit_quantity
    prev_partial_gross = float(
        trade.get("partial_realized_gross_pnl")
        if trade.get("partial_realized_gross_pnl") is not None
        else (trade.get("partial_realized_pnl") or 0)
    )
    partial_realized_gross = round(prev_partial_gross + gross_pnl, 2)
    partial_charges_total = round(float(trade.get("paper_partial_exit_charges") or 0) + partial_order_charges, 2)
    partial_realized = round(float(trade.get("partial_realized_pnl") or 0) + pnl, 2)

    trade.update({
        "exit_price": round(float(current_price), 2),
        "exit_quantity": exit_quantity,
        "remaining_quantity": remaining_quantity,
        "partial_realized_gross_pnl": partial_realized_gross,
        "paper_partial_exit_charges": partial_charges_total,
        "partial_realized_pnl": partial_realized,
        "gross_pnl": gross_pnl,
        "pnl": pnl,
        "last_partial_exit_at": csv_datetime(),
        "last_partial_exit_reason": reason,
        "exit_reason": reason,
    })
    if exit_order_id:
        trade["exit_order_id"] = exit_order_id

    if stage == 1:
        trade["partial_exit_t1_done"] = True
    elif stage == 2:
        trade["partial_exit_t2_done"] = True

    _replace_state_trade(trade)
    append_trade_history("PARTIAL_EXIT", trade, result="SUCCESS" if pnl >= 0 else "FAIL", reason=reason)
    if trade.get("mode") == "PAPER":
        refresh_paper_account()
    return trade


def dynamic_partial_exit_quantity(trade: dict) -> int:
    if not config.PARTIAL_EXIT_ENABLED:
        return 0

    lot_size = int(trade.get("lot_size") or 1)
    remaining_quantity = int(trade.get("remaining_quantity") or trade.get("quantity") or 0)
    remaining_lots = remaining_quantity // lot_size if lot_size else 0
    if remaining_lots <= 1:
        return 0

    percent = float(getattr(config, "DYNAMIC_PARTIAL_EXIT_PERCENT", 50) or 50)
    exit_lots = max(1, int(remaining_lots * (percent / 100)))
    exit_quantity = exit_lots * lot_size
    if remaining_quantity - exit_quantity < lot_size:
        exit_quantity = max(0, remaining_quantity - lot_size)
    return min(exit_quantity, remaining_quantity)


def close_dynamic_partial_trade(fyers, trade: dict, current_price: float, reason: str) -> dict:
    exit_quantity = dynamic_partial_exit_quantity(trade)
    if exit_quantity <= 0:
        return trade

    exit_order_id = maybe_place_live_exit_order(fyers, trade, reason, quantity=exit_quantity, current_price=current_price)
    if trade.get("status") == "LIVE_OPEN" and not exit_order_id:
        return trade

    entry_price = float(trade["entry_price"])
    gross_pnl = round((float(current_price) - entry_price) * exit_quantity, 2)
    partial_order_charges = estimate_order_charges(float(current_price), exit_quantity)
    pnl = round(gross_pnl - partial_order_charges, 2)
    remaining_quantity = int(trade.get("remaining_quantity") or trade["quantity"]) - exit_quantity
    prev_partial_gross = float(
        trade.get("partial_realized_gross_pnl")
        if trade.get("partial_realized_gross_pnl") is not None
        else (trade.get("partial_realized_pnl") or 0)
    )
    partial_realized_gross = round(prev_partial_gross + gross_pnl, 2)
    partial_charges_total = round(float(trade.get("paper_partial_exit_charges") or 0) + partial_order_charges, 2)
    partial_realized = round(float(trade.get("partial_realized_pnl") or 0) + pnl, 2)
    trade.update({
        "exit_price": round(float(current_price), 2),
        "exit_quantity": exit_quantity,
        "remaining_quantity": remaining_quantity,
        "partial_realized_gross_pnl": partial_realized_gross,
        "paper_partial_exit_charges": partial_charges_total,
        "partial_realized_pnl": partial_realized,
        "gross_pnl": gross_pnl,
        "pnl": pnl,
        "dynamic_partial_exit_done": True,
        "last_partial_exit_at": csv_datetime(),
        "last_partial_exit_reason": reason,
        "exit_reason": reason,
    })
    if exit_order_id:
        trade["exit_order_id"] = exit_order_id

    _replace_state_trade(trade)
    append_trade_history("PARTIAL_EXIT", trade, result="SUCCESS" if pnl >= 0 else "FAIL", reason=reason)
    if trade.get("mode") == "PAPER":
        refresh_paper_account()
    return trade


def _depth_is_weak_for_long_option(trade: dict) -> bool:
    bid_qty = int(float(trade.get("bid_qty") or 0))
    ask_qty = int(float(trade.get("ask_qty") or 0))
    if bid_qty <= 0 or ask_qty <= 0:
        return False
    ratio = bid_qty / ask_qty
    return ratio <= float(getattr(config, "DYNAMIC_DEPTH_WEAK_RATIO", 0.65) or 0.65)


def update_dynamic_exit_memory(trade: dict, current_price: float) -> tuple[dict, dict]:
    entry_price = float(trade["entry_price"])
    previous_price = float(trade.get("last_monitor_price") or current_price)
    highest_price = max(float(trade.get("highest_price_seen") or entry_price), current_price)
    profit_percent = round(((current_price - entry_price) / entry_price) * 100, 2) if entry_price else 0
    peak_profit_percent = round(((highest_price - entry_price) / entry_price) * 100, 2) if entry_price else 0
    pullback_from_peak = round(peak_profit_percent - profit_percent, 2)
    price_fading = current_price < previous_price
    weak_depth = _depth_is_weak_for_long_option(trade)

    trade["last_monitor_price"] = round(current_price, 2)
    trade["highest_price_seen"] = round(highest_price, 2)
    trade["peak_profit_percent"] = peak_profit_percent
    trade["current_profit_percent"] = profit_percent
    trade["pullback_from_peak_percent"] = pullback_from_peak

    return trade, {
        "previous_price": previous_price,
        "highest_price": highest_price,
        "profit_percent": profit_percent,
        "peak_profit_percent": peak_profit_percent,
        "pullback_from_peak": pullback_from_peak,
        "price_fading": price_fading,
        "weak_depth": weak_depth,
    }


def update_dynamic_safe_stop(trade: dict, current_price: float, metrics: dict) -> tuple[dict, str | None]:
    entry_price = float(trade["entry_price"])
    stop_loss = float(trade["stop_loss"])
    new_stop_loss = stop_loss
    reason = None

    trail_cost_start = float(getattr(config, "TRAIL_TO_COST_PROFIT_PERCENT", 7.5) or 7.5)
    trail_cost_lock = float(getattr(config, "TRAIL_COST_LOCK_PERCENT", 1.5) or 1.5)
    if metrics["profit_percent"] >= trail_cost_start:
        locked_cost = entry_price * (1 + (trail_cost_lock / 100))
        if locked_cost > new_stop_loss:
            new_stop_loss = locked_cost
            reason = (
                f"Dynamic early breakeven lock: profit {metrics['profit_percent']}%, "
                f"SL moved to +{trail_cost_lock}%."
            )

    if metrics["profit_percent"] >= float(getattr(config, "DYNAMIC_PROFIT_LOCK_START_PERCENT", 8) or 8):
        locked_stop = entry_price * (1 + (float(getattr(config, "DYNAMIC_PROFIT_LOCK_PERCENT", 2) or 2) / 100))
        if locked_stop > new_stop_loss:
            new_stop_loss = locked_stop
            reason = (
                f"Dynamic profit lock: profit {metrics['profit_percent']}%, "
                f"SL moved to +{config.DYNAMIC_PROFIT_LOCK_PERCENT}%."
            )

    if metrics["profit_percent"] >= float(getattr(config, "DYNAMIC_TRAIL_START_PERCENT", 14) or 14):
        trail_distance = float(getattr(config, "DYNAMIC_TRAIL_DISTANCE_PERCENT", 6) or 6)
        trailing_stop = current_price * (1 - (trail_distance / 100))
        if trailing_stop > new_stop_loss:
            new_stop_loss = trailing_stop
            reason = (
                f"Dynamic trailing SL: profit {metrics['profit_percent']}%, "
                f"trail distance {trail_distance}%."
            )

    if new_stop_loss > stop_loss:
        trade["stop_loss"] = round(new_stop_loss, 2)
        trade["dynamic_stop_updated_at"] = csv_datetime()
        trade["dynamic_stop_reason"] = reason
        _replace_state_trade(trade)

    return trade, reason


def monitor_dynamic_exit(fyers, trade: dict, current_price: float) -> dict:
    trade, metrics = update_dynamic_exit_memory(trade, current_price)
    trade, stop_reason = update_dynamic_safe_stop(trade, current_price, metrics)

    stop_loss = float(trade["stop_loss"])
    remaining_quantity = int(trade.get("remaining_quantity") or trade.get("quantity") or 0)
    lot_size = int(trade.get("lot_size") or 1)
    remaining_lots = remaining_quantity // lot_size if lot_size else 0
    partial_reason = None
    full_exit_reason = None

    if current_price <= stop_loss:
        full_exit_reason = (
            f"Dynamic emergency/protected SL hit: current Rs {current_price} <= SL Rs {stop_loss}."
        )
        exit_reason = "DYNAMIC_SAFE_SL_HIT"
    elif (
        remaining_lots <= 1
        and metrics["profit_percent"] >= float(getattr(config, "DYNAMIC_QUICK_PROFIT_EXIT_PERCENT", 10) or 10)
    ):
        full_exit_reason = (
            f"One-lot quick profit exit: profit {metrics['profit_percent']}% reached "
            f"{config.DYNAMIC_QUICK_PROFIT_EXIT_PERCENT}% threshold."
        )
        exit_reason = "DYNAMIC_ONE_LOT_QUICK_PROFIT_EXIT"
    elif _time_based_exit_due(trade, metrics):
        full_exit_reason = (
            f"Time-based exit: trade did not reach +{config.DYNAMIC_TIME_EXIT_MIN_PROFIT_PERCENT}% "
            f"within {config.DYNAMIC_TIME_EXIT_MINUTES} minutes."
        )
        exit_reason = "DYNAMIC_TIME_EXIT"
    elif _early_loss_exit_due(trade, metrics):
        full_exit_reason = (
            f"Early loss exit: loss {metrics['profit_percent']}% and no recovery for "
            f"{config.DYNAMIC_EARLY_LOSS_RECOVERY_CANDLES} checks."
        )
        exit_reason = "DYNAMIC_EARLY_LOSS_EXIT"
    elif (
        metrics["profit_percent"] >= float(getattr(config, "DYNAMIC_PARTIAL_MIN_PROFIT_PERCENT", 6) or 6)
        and not trade.get("dynamic_partial_exit_done")
        and (metrics["price_fading"] or metrics["weak_depth"] or metrics["pullback_from_peak"] >= float(getattr(config, "DYNAMIC_PARTIAL_PEAK_PULLBACK_PERCENT", 3) or 3))
        and dynamic_partial_exit_quantity(trade) > 0
    ):
        partial_reason = (
            f"Dynamic partial exit: profit {metrics['profit_percent']}%, "
            f"peak {metrics['peak_profit_percent']}%, pullback {metrics['pullback_from_peak']}%, "
            f"price_fading={metrics['price_fading']}, weak_depth={metrics['weak_depth']}."
        )
        trade["exit_decision_reason"] = partial_reason
        table("DYNAMIC PARTIAL EXIT", [
            ("Option", trade.get("display_symbol", trade["option_symbol"])),
            ("Current", f"Rs {current_price}"),
            ("Profit", f"{metrics['profit_percent']}%"),
            ("Peak Profit", f"{metrics['peak_profit_percent']}%"),
            ("Pullback", f"{metrics['pullback_from_peak']}%"),
            ("Remaining Qty", remaining_quantity),
            ("Reason", partial_reason),
        ])
        trade = close_dynamic_partial_trade(fyers, trade, current_price, "DYNAMIC_PARTIAL_EXIT")
        return trade
    elif (
        metrics["peak_profit_percent"] >= float(getattr(config, "DYNAMIC_FULL_EXIT_MIN_PEAK_PROFIT_PERCENT", 10) or 10)
        and metrics["pullback_from_peak"] >= float(getattr(config, "DYNAMIC_FULL_EXIT_PEAK_PULLBACK_PERCENT", 5) or 5)
        and metrics["profit_percent"] > 0
    ):
        full_exit_reason = (
            f"Dynamic full exit: profit pulled back {metrics['pullback_from_peak']}% from "
            f"peak {metrics['peak_profit_percent']}%."
        )
        exit_reason = "DYNAMIC_PEAK_PULLBACK_EXIT"
    elif (
        metrics["profit_percent"] <= -float(getattr(config, "DYNAMIC_SOFT_LOSS_EXIT_PERCENT", 7) or 7)
    ):
        full_exit_reason = (
            f"Dynamic soft-loss exit: loss {metrics['profit_percent']}% reached "
            f"{config.DYNAMIC_SOFT_LOSS_EXIT_PERCENT}% threshold."
        )
        exit_reason = "DYNAMIC_SOFT_LOSS_EXIT"
    else:
        exit_reason = ""

    exit_decision_reason = (
        f"DYNAMIC HOLD: current Rs {current_price}, SL Rs {stop_loss}, profit {metrics['profit_percent']}%, "
        f"peak {metrics['peak_profit_percent']}%, pullback {metrics['pullback_from_peak']}%, "
        f"price_fading={metrics['price_fading']}, weak_depth={metrics['weak_depth']}, "
        f"remaining qty {remaining_quantity}."
    )

    table("OPEN TRADE MONITOR", [
        ("Option", trade["option_symbol"]),
        ("Display", trade.get("display_symbol", trade["option_symbol"])),
        ("Order Status", trade.get("order_status", trade.get("status"))),
        ("Mode", trade.get("mode")),
        ("Exit Mode", "DYNAMIC"),
        ("Opened At", trade.get("opened_at")),
        ("Entry", f"Rs {trade['entry_price']}"),
        ("Current", f"Rs {current_price}"),
        ("Highest Seen", f"Rs {metrics['highest_price']}"),
        ("Profit", f"{metrics['profit_percent']}%"),
        ("Peak Profit", f"{metrics['peak_profit_percent']}%"),
        ("Pullback", f"{metrics['pullback_from_peak']}%"),
        ("Safe SL", f"Rs {stop_loss}"),
        ("Stop Update", stop_reason or trade.get("dynamic_stop_reason") or "No change"),
        ("Remaining Qty", remaining_quantity),
        ("Decision", full_exit_reason or exit_decision_reason),
    ])

    if full_exit_reason:
        trade["exit_decision_reason"] = full_exit_reason
        exit_order_id = maybe_place_live_exit_order(fyers, trade, exit_reason, current_price=current_price)
        if trade.get("status") == "LIVE_OPEN" and not exit_order_id:
            trade["last_exit_order_failed_at"] = csv_datetime()
            trade["last_exit_order_failed_reason"] = "Broker rejected dynamic exit order. Position may still be open in Fyers."
            _replace_state_trade(trade)
            return trade
        closed = close_open_trade(current_price, exit_reason, trade)
        if exit_order_id:
            closed["exit_order_id"] = exit_order_id
            _replace_state_trade(closed)
        log(f"Dynamic exit closed trade. P&L: Rs {closed['pnl']}")
        print()
        print_performance_summary()
        return closed

    trade["exit_decision_reason"] = exit_decision_reason
    _replace_state_trade(trade)
    log("Dynamic exit hold. No fixed target is active.")
    return trade


def _opened_minutes_ago(trade: dict) -> float | None:
    opened_at = trade.get("opened_at")
    if not opened_at:
        return None
    try:
        opened = datetime.fromisoformat(str(opened_at))
        if opened.tzinfo is not None:
            opened = opened.replace(tzinfo=None)
    except ValueError:
        return None
    return (market_now_naive() - opened).total_seconds() / 60


def _time_based_exit_due(trade: dict, metrics: dict) -> bool:
    minutes_limit = float(getattr(config, "DYNAMIC_TIME_EXIT_MINUTES", 0) or 0)
    if minutes_limit <= 0:
        return False
    opened_minutes = _opened_minutes_ago(trade)
    if opened_minutes is None or opened_minutes < minutes_limit:
        return False
    min_profit = float(getattr(config, "DYNAMIC_TIME_EXIT_MIN_PROFIT_PERCENT", 3) or 3)
    return metrics["peak_profit_percent"] < min_profit


def _early_loss_exit_due(trade: dict, metrics: dict) -> bool:
    loss_threshold = float(getattr(config, "DYNAMIC_EARLY_LOSS_PERCENT", 4) or 4)
    recovery_checks = int(getattr(config, "DYNAMIC_EARLY_LOSS_RECOVERY_CANDLES", 2) or 2)
    if metrics["profit_percent"] > -loss_threshold:
        trade["early_loss_no_recovery_checks"] = 0
        _replace_state_trade(trade)
        return False

    no_recovery = metrics["price_fading"] or metrics["pullback_from_peak"] > 0 or metrics["profit_percent"] <= -loss_threshold
    current_count = int(trade.get("early_loss_no_recovery_checks") or 0)
    trade["early_loss_no_recovery_checks"] = current_count + 1 if no_recovery else 0
    _replace_state_trade(trade)
    return trade["early_loss_no_recovery_checks"] >= recovery_checks


def refresh_live_position_chart_context(fyers, trade: dict, current_price: float) -> dict:
    """
    v5 Active Position Guardian:
    Continuously tracks 5m/15m Index and 5m Option chart structure, VWAP, EMA9/21,
    and confirmed 5m Swing Low Support while a position is open.
    """
    if not getattr(config, "LIVE_POSITION_CHART_TRACKING_ENABLED", True):
        return trade
    if fyers is None or not hasattr(fyers, "historical_data"):
        return trade

    now_dt = market_now_naive()
    now_epoch = now_dt.timestamp()
    refresh_seconds = float(getattr(config, "LIVE_POSITION_CHART_REFRESH_SECONDS", 30) or 30)
    last_epoch = float(trade.get("last_chart_track_epoch") or 0.0)
    if last_epoch > 0 and (now_epoch - last_epoch) < refresh_seconds:
        return trade

    trade["last_chart_track_epoch"] = round(now_epoch, 2)
    option_type = str(trade.get("option_type") or "").upper()
    underlying = trade.get("underlying")
    inst_token = trade.get("instrument_token")

    option_trend_ok = True
    option_broke_support = False
    index_trend_ok = True
    index_reversed_against = False

    # 1. Track 5m Option Chart (Option VWAP, EMA9, ATR14, Confirmed 5m Swing Low Support)
    if inst_token:
        try:
            from indicators import enrich_candles
            from_dt = now_dt - timedelta(days=int(getattr(config, "OPTION_CHAIN_CANDLE_LOOKBACK_DAYS", 3) or 3))
            opt_candles = fyers.historical_data(
                instrument_token=inst_token,
                from_date=from_dt,
                to_date=now_dt,
                interval=getattr(config, "OPTION_CHAIN_CANDLE_INTERVAL", "5minute"),
            ) or []
            if len(opt_candles) >= 4:
                enriched_opt = enrich_candles(opt_candles) if len(opt_candles) >= 21 else opt_candles
                latest_opt = enriched_opt[-1]
                completed_opt = enriched_opt[-4:-1]
                swing_lows = [float(r.get("low") or 0) for r in completed_opt if float(r.get("low") or 0) > 0]
                if swing_lows:
                    new_swing_low = round(min(swing_lows), 2)
                    prev_swing = float(trade.get("live_option_swing_low") or 0)
                    # Ratchet confirmed option swing support upward as option builds higher 5m bases
                    trade["live_option_swing_low"] = max(prev_swing, new_swing_low) if prev_swing > 0 else new_swing_low
                opt_vwap = float(latest_opt.get("vwap") or trade.get("live_option_vwap") or 0)
                opt_ema9 = float(latest_opt.get("ema_9") or trade.get("live_option_ema9") or 0)
                opt_atr = float(latest_opt.get("atr_14") or trade.get("live_option_atr") or 0)
                if opt_vwap > 0:
                    trade["live_option_vwap"] = round(opt_vwap, 2)
                if opt_ema9 > 0:
                    trade["live_option_ema9"] = round(opt_ema9, 2)
                if opt_atr > 0:
                    trade["live_option_atr"] = round(opt_atr, 2)
                    trade["option_atr"] = round(opt_atr, 2)

                active_swing = float(trade.get("live_option_swing_low") or 0)
                if opt_vwap > 0 and active_swing > 0:
                    option_trend_ok = (current_price >= opt_vwap * 0.985) or (current_price >= active_swing)
                    option_broke_support = (current_price < opt_vwap * 0.985) and (current_price < active_swing)
        except Exception:
            pass

    # 2. Track 5m & 15m Underlying Index Chart (EMA9, EMA21, VWAP, ADX, 15m HTF Trend)
    if underlying:
        try:
            from market_data import fetch_index_candles
            from indicators import enrich_candles
            from strategy import _analyze_htf_trend

            idx_5m = fetch_index_candles(fyers, underlying, interval="5minute", lookback_days=3) or []
            if len(idx_5m) >= 21:
                enriched_idx = enrich_candles(idx_5m)
                latest_idx = enriched_idx[-1]
                idx_close = float(latest_idx.get("close") or 0)
                idx_ema9 = float(latest_idx.get("ema_9") or 0)
                idx_ema21 = float(latest_idx.get("ema_21") or 0)
                idx_vwap = float(latest_idx.get("vwap") or 0)
                idx_adx = float(latest_idx.get("adx_14") or 0)
                trade["live_index_close"] = round(idx_close, 2)
                trade["live_index_ema9"] = round(idx_ema9, 2)
                trade["live_index_ema21"] = round(idx_ema21, 2)
                trade["live_index_vwap"] = round(idx_vwap, 2)
                trade["live_index_adx"] = round(idx_adx, 2)

                if option_type == "PE":
                    index_trend_ok = (idx_close <= idx_ema21) or (idx_close <= idx_vwap) or (idx_ema9 <= idx_ema21)
                    index_reversed_against = (idx_close > idx_ema21) and (idx_close > idx_vwap) and (idx_ema9 > idx_ema21)
                elif option_type == "CE":
                    index_trend_ok = (idx_close >= idx_ema21) or (idx_close >= idx_vwap) or (idx_ema9 >= idx_ema21)
                    index_reversed_against = (idx_close < idx_ema21) and (idx_close < idx_vwap) and (idx_ema9 < idx_ema21)

            idx_15m = fetch_index_candles(fyers, underlying, interval="15minute", lookback_days=5) or []
            if idx_15m:
                htf_info = _analyze_htf_trend(idx_15m)
                live_htf_bias = htf_info.get("bias", "UNKNOWN")
                trade["live_htf_bias"] = live_htf_bias
                if option_type == "PE" and live_htf_bias == "BULLISH":
                    index_trend_ok = False
                elif option_type == "CE" and live_htf_bias == "BEARISH":
                    index_trend_ok = False
        except Exception:
            pass

    trade["live_chart_trend_intact"] = bool(index_trend_ok and option_trend_ok)
    trade["live_structure_reversal"] = bool(index_reversed_against and option_broke_support)
    if trade["live_structure_reversal"]:
        trade["live_structure_reversal_reason"] = (
            f"Live 5m {underlying} structure reversed against {option_type} "
            f"(Close {trade.get('live_index_close')}, EMA9 {trade.get('live_index_ema9')}, EMA21 {trade.get('live_index_ema21')}, VWAP {trade.get('live_index_vwap')}) "
            f"and option broke 5m support Rs {trade.get('live_option_swing_low')} & VWAP Rs {trade.get('live_option_vwap')}."
        )
    else:
        trade["live_structure_reversal_reason"] = None

    return trade


def check_time_based_exit(trade: dict, current_price: float) -> tuple[bool, str]:
    if not getattr(config, "TIME_BASED_EXIT_ENABLED", True):
        return False, ""

    # If trade has already hit Target 1 or partial exit is done, let runner trail freely
    if int(trade.get("target_stage") or 0) >= 1 or trade.get("partial_exit_t1_done"):
        return False, ""

    opened_minutes = _opened_minutes_ago(trade)
    if opened_minutes is None:
        return False, ""

    entry_price = float(trade.get("entry_price") or 0)
    if entry_price <= 0:
        return False, ""

    profit_percent = ((current_price - entry_price) / entry_price) * 100
    highest_price = max(float(trade.get("highest_price_seen") or entry_price), current_price)
    trade["highest_price_seen"] = round(highest_price, 2)
    peak_profit = ((highest_price - entry_price) / entry_price) * 100 if entry_price else 0
    trade["peak_profit_percent"] = round(peak_profit, 2)

    # v5 Smart Structure Hold Exemption:
    # When live 5m/10m/15m chart structure & option support are intact and trade is not breaking down,
    # do NOT force-exit on a blind 10m or 20m timer. Hold as long as market structure supports the trade!
    if (
        getattr(config, "LIVE_STRUCTURE_HOLD_EXEMPTION_ENABLED", True)
        and trade.get("live_chart_trend_intact") is True
        and not trade.get("live_structure_reversal", False)
        and profit_percent >= -3.0
    ):
        return False, ""

    # 1. Dynamic Momentum Stall Exit (e.g. 10m):
    # If trade has zero forward momentum progress after stall minutes, exit before theta decay bleeds capital
    stall_minutes = float(getattr(config, "DYNAMIC_STALL_MINUTES", 10) or 10)
    if getattr(config, "DYNAMIC_MOMENTUM_EXIT_ENABLED", True) and opened_minutes >= stall_minutes:
        min_peak = float(getattr(config, "DYNAMIC_STALL_MIN_PEAK_PROFIT_PERCENT", 2.5) or 2.5)
        max_profit = float(getattr(config, "DYNAMIC_STALL_MAX_PROFIT_PERCENT", 0.5) or 0.5)
        if peak_profit < min_peak and profit_percent <= max_profit:
            reason = (
                f"Momentum stall exit: Trade held for {round(opened_minutes, 1)}m with zero progress "
                f"(peak profit {round(peak_profit, 2)}% < +{min_peak}%, current {round(profit_percent, 2)}%). "
                f"Exiting before theta decay destroys capital."
            )
            return True, reason

    # 2. Active Breakout Surge Exemption:
    # If price is within DYNAMIC_BREAKOUT_EXEMPTION_PERCENT of highest price and expanding, do not exit!
    exemption_ratio = float(getattr(config, "DYNAMIC_BREAKOUT_EXEMPTION_PERCENT", 0.8) or 0.8) / 100
    is_active_breakout = (current_price >= highest_price * (1 - exemption_ratio)) and profit_percent > 2.0

    # 3. Maximum Time Limit (e.g. 20m):
    minutes_limit = float(getattr(config, "TIME_BASED_EXIT_MINUTES", 20) or 20)
    if minutes_limit > 0 and opened_minutes >= minutes_limit:
        if is_active_breakout:
            return False, ""
        min_profit = float(getattr(config, "TIME_BASED_EXIT_MIN_PROFIT_PERCENT", 4.0) or 4.0)
        if profit_percent < min_profit:
            reason = (
                f"Trade held for {round(opened_minutes, 1)}m (>={minutes_limit}m) "
                f"with insufficient momentum (profit {round(profit_percent, 2)}% < +{min_profit}%)."
            )
            return True, reason

    return False, ""


def is_target_hit(current_price: float, target_price: float, entry_price: float) -> bool:
    if not target_price or target_price <= 0:
        return False
    if current_price >= target_price:
        return True
    buffer_ratio = float(getattr(config, "TARGET_BUFFER_RATIO", 0.94) or 0.94)
    target_distance = target_price - entry_price
    if target_distance > 0:
        threshold = entry_price + (target_distance * buffer_ratio)
        return current_price >= threshold
    return False


def _should_commit_sl_update(old_sl: float, new_sl: float, stage_changed: bool = False) -> bool:
    rounded_new = round_to_tick(new_sl)
    if rounded_new <= old_sl:
        return False
    if stage_changed:
        return True
    min_step_pct = float(getattr(config, "SHADOW_TRAIL_UPDATE_MIN_STEP_PERCENT", 0.30) or 0.30)
    tick_size = float(getattr(config, "ORDER_PRICE_TICK_SIZE", 0.05) or 0.05)
    min_delta = max(tick_size, old_sl * (min_step_pct / 100.0))
    return (rounded_new - old_sl) >= (min_delta - 1e-6)


def _practical_exit_price(trade: dict, current_price: float) -> float:
    best_bid = float(trade.get("best_bid") or 0.0)
    if best_bid > 0 and best_bid >= current_price * 0.985:
        return round_to_tick(min(current_price, best_bid))
    return round_to_tick(current_price)


def calculate_adaptive_shadow_trail_distance(trade: dict, highest_price: float, stage: int) -> float:
    entry_price = float(trade["entry_price"])
    target_1 = float(trade.get("target_1") or (entry_price * 1.12))
    target_2 = float(trade.get("target_2") or (target_1 + max(1.0, 0.6 * (target_1 - entry_price))))
    target_3 = float(trade.get("target_3") or trade.get("target") or (target_2 + max(1.0, target_2 - target_1)))
    gap = (target_3 - target_2) if stage >= 2 else (target_2 - target_1)
    if gap <= 0:
        gap = max(target_1 - entry_price, entry_price * 0.06)

    gap_ratio = float(getattr(config, "SHADOW_TRAIL_GAP_RATIO", 0.35) or 0.35)
    spread = float(trade.get("bid_ask_spread") or 0.0)
    spread_mult = float(getattr(config, "SHADOW_TRAIL_MIN_SPREAD_MULTIPLIER", 3.0) or 3.0)
    min_peak_pct = float(getattr(config, "SHADOW_TRAIL_MIN_PEAK_PERCENT", 2.5) or 2.5)
    opt_atr = float(trade.get("option_atr") or trade.get("atr_14_option") or 0.0)
    atr_ratio = float(getattr(config, "SHADOW_TRAIL_MIN_ATR_RATIO", 0.25) or 0.25)

    min_floor = max(
        spread_mult * spread,
        (min_peak_pct / 100.0) * highest_price,
        atr_ratio * opt_atr,
    )
    return round(max(gap_ratio * gap, min_floor), 2)


def update_trailing_stop(trade: dict, current_price: float) -> tuple[dict, str | None]:
    if not config.TRAILING_STOP_ENABLED:
        return trade, None

    # Once Stage 1+ is active and v4 Full-Position Ride is enabled, update_target_stage_stop owns trailing SL
    if int(trade.get("target_stage") or 0) >= 1 and getattr(config, "FULL_POSITION_RIDE_ENABLED", False):
        return trade, None

    entry_price = float(trade["entry_price"])
    stop_loss = float(trade["stop_loss"])
    profit_percent = ((current_price - entry_price) / entry_price) * 100 if entry_price else 0
    new_stop_loss = stop_loss
    reason = None

    # v5 Option 5m Swing Support Trail:
    # When the option builds higher 5m swing low support bases, trail SL behind confirmed support
    live_swing_low = float(trade.get("live_option_swing_low") or 0.0)
    if getattr(config, "LIVE_OPTION_SUPPORT_TRAIL_ENABLED", True) and live_swing_low > 0 and profit_percent >= 5.0:
        buffer_pct = float(getattr(config, "LIVE_OPTION_SUPPORT_BUFFER_PERCENT", 1.0) or 1.0)
        support_sl = round_to_tick(live_swing_low * (1.0 - (buffer_pct / 100.0)))
        if support_sl > new_stop_loss and support_sl < current_price * 0.985:
            new_stop_loss = support_sl
            reason = (
                f"Option 5m swing support trail: confirmed support Rs {live_swing_low}, "
                f"SL trailed to Rs {support_sl} ({buffer_pct}% buffer)."
            )

    # Prevent premature +1.5% choke at +7.5% when live chart trend is intact and trade has not yet reached near-T1
    has_target_1 = float(trade.get("target_1") or 0) > entry_price
    chart_trend_intact = trade.get("live_chart_trend_intact") is True

    # Tier 0 (+7% to +8%): Early Breakeven Lock -> Move SL to Entry + 1.5% (Cost + Brokerage covered)
    trail_cost_start = float(getattr(config, "TRAIL_TO_COST_PROFIT_PERCENT", 7.5))
    trail_cost_lock = float(getattr(config, "TRAIL_COST_LOCK_PERCENT", 1.5))
    effective_cost_start = max(trail_cost_start, 11.0) if (has_target_1 and chart_trend_intact) else trail_cost_start
    if profit_percent >= effective_cost_start:
        tier0_stop = round_to_tick(entry_price * (1 + (trail_cost_lock / 100)))
        if tier0_stop > new_stop_loss:
            new_stop_loss = tier0_stop
            reason = (
                f"Early breakeven lock: profit crossed {trail_cost_start}%, "
                f"SL moved to Entry +{trail_cost_lock}% (Rs {round(tier0_stop, 2)})."
            )

    # Tier 1 (+12%): Move SL to Entry + 3.5% (Cost + charges protected)
    trail_tier1_start = float(getattr(config, "TRAIL_TO_ENTRY_PROFIT_PERCENT", 12.0))
    trail_tier1_lock = float(getattr(config, "TRAIL_TIER1_LOCK_PERCENT", 3.5))
    if profit_percent >= trail_tier1_start:
        tier1_stop = round_to_tick(entry_price * (1 + (trail_tier1_lock / 100)))
        if tier1_stop > new_stop_loss:
            new_stop_loss = tier1_stop
            reason = f"Profit crossed {trail_tier1_start}%, SL moved to Entry +{trail_tier1_lock}% (Rs {round(tier1_stop, 2)})."

    # Tier 2 (+20%): Lock in at least +10% profit
    trail_tier2_start = float(getattr(config, "TRAIL_LOCK_PROFIT_PERCENT", 20.0))
    trail_tier2_lock = float(getattr(config, "TRAIL_TIER2_LOCK_PERCENT", 10.0))
    if profit_percent >= trail_tier2_start:
        tier2_stop = round_to_tick(entry_price * (1 + (trail_tier2_lock / 100)))
        if tier2_stop > new_stop_loss:
            new_stop_loss = tier2_stop
            reason = f"Profit crossed {trail_tier2_start}%, SL locked at +{trail_tier2_lock}% (Rs {round(tier2_stop, 2)})."

    # Peak Trailing Step
    trail_step_start = float(getattr(config, "TRAIL_STEP_PROFIT_PERCENT", 25.0))
    trail_distance = float(getattr(config, "TRAIL_DISTANCE_PERCENT", 10.0))
    if profit_percent >= trail_step_start:
        trailing_stop = round_to_tick(current_price * (1 - (trail_distance / 100)))
        if trailing_stop > new_stop_loss:
            new_stop_loss = trailing_stop
            reason = (
                f"Trailing SL updated after {round(profit_percent, 2)}% profit. "
                f"Distance: {trail_distance}%."
            )

    if _should_commit_sl_update(stop_loss, new_stop_loss):
        trade["stop_loss"] = round_to_tick(new_stop_loss)
        trade["trailing_stop_updated_at"] = csv_datetime()
        trade["trailing_stop_reason"] = reason
        _replace_state_trade(trade)
        return trade, reason

    return trade, None


def update_target_stage_stop(trade: dict, current_price: float) -> tuple[dict, str | None]:
    entry_price = float(trade["entry_price"])
    stop_loss = float(trade["stop_loss"])
    target_1 = float(trade.get("target_1") or 0)
    target_2 = float(trade.get("target_2") or 0)
    current_stage = int(trade.get("target_stage") or 0)
    new_stop_loss = stop_loss
    new_stage = current_stage
    reason = None

    full_ride_enabled = bool(getattr(config, "FULL_POSITION_RIDE_ENABLED", False))
    lot_size = int(trade.get("lot_size") or 1)
    original_lots = int(trade.get("quantity") or 0) // lot_size if lot_size else 1

    if target_2 and is_target_hit(current_price, target_2, entry_price) and current_stage < 2:
        new_stage = 2
        if full_ride_enabled and current_stage < 1 and target_1:
            # Price jumped straight past T1 and T2 in one move!
            trade["full_position_ride_active"] = True
            trade["t1_continuation_strong"] = True
            trade["partial_exit_t1_done"] = True
        if full_ride_enabled:
            if trade.get("full_position_ride_active"):
                trade["partial_exit_t2_done"] = True
            d_t2 = max(0.0, target_2 - target_1) if target_1 else 0.0
            floor_ratio = float(getattr(config, "SHADOW_TRAIL_PARITY_FLOOR_RATIO", 0.65) or 0.65)
            stage2_base = entry_price * (1 + (config.STAGE_2_STOP_PROFIT_PERCENT / 100))
            t2_parity_stop = (target_1 + (floor_ratio * d_t2)) if target_1 else stage2_base
            target_2_stop = round_to_tick(max(stage2_base, t2_parity_stop))
            new_stop_loss = max(new_stop_loss, target_2_stop)
            reason = (
                f"Target 2 hit at Rs {current_price}. "
                f"Full-Ride floor locked at Rs {round(target_2_stop, 2)}."
            )
        else:
            target_2_stop = round_to_tick(entry_price * (1 + (config.STAGE_2_STOP_PROFIT_PERCENT / 100)))
            new_stop_loss = max(new_stop_loss, target_2_stop)
            reason = (
                f"Target 2 hit at Rs {target_2}. "
                f"SL moved to +{config.STAGE_2_STOP_PROFIT_PERCENT}% Rs {round(target_2_stop, 2)}."
            )

    elif target_1 and is_target_hit(current_price, target_1, entry_price) and current_stage < 1:
        new_stage = 1
        if full_ride_enabled:
            strong_t1, cont_reason = is_t1_continuation_strong(trade, current_price, target_1)
            trade["t1_continuation_strong"] = strong_t1
            trade["t1_continuation_reason"] = cont_reason
            d_t1 = max(0.0, target_1 - entry_price)
            stage1_base = entry_price * (1 + (config.STAGE_1_STOP_PROFIT_PERCENT / 100))

            if strong_t1:
                trade["full_position_ride_active"] = True
                trade["partial_exit_t1_done"] = True
                floor_ratio = float(
                    getattr(config, "SHADOW_TRAIL_ONE_LOT_PARITY_FLOOR_RATIO", 0.65)
                    if original_lots <= 1
                    else getattr(config, "SHADOW_TRAIL_PARITY_FLOOR_RATIO", 0.65)
                )
                parity_floor_stop = round_to_tick(max(stage1_base, entry_price + (floor_ratio * d_t1)))
                new_stop_loss = max(new_stop_loss, parity_floor_stop)
                reason = (
                    f"Strong T1 breakout at Rs {current_price} ({cont_reason}). "
                    f"Holding 100% lots; Parity Floor SL locked at Rs {parity_floor_stop} ({int(floor_ratio * 100)}% of T1 profit)."
                )
            else:
                trade["full_position_ride_active"] = False
                weak_ratio = float(getattr(config, "SHADOW_TRAIL_WEAK_T1_STOP_RATIO", 0.25) or 0.25)
                weak_t1_stop = round_to_tick(max(stage1_base, entry_price + (weak_ratio * d_t1)))
                new_stop_loss = max(new_stop_loss, weak_t1_stop)
                reason = (
                    f"Weak/Buffer T1 at Rs {current_price} ({cont_reason}). "
                    f"Partial booking active; runner SL moved to Rs {weak_t1_stop}."
                )
        else:
            target_1_stop = round_to_tick(entry_price * (1 + (config.STAGE_1_STOP_PROFIT_PERCENT / 100)))
            new_stop_loss = max(new_stop_loss, target_1_stop)
            reason = (
                f"Target 1 hit at Rs {target_1}. "
                f"SL moved to +{config.STAGE_1_STOP_PROFIT_PERCENT}% Rs {round(target_1_stop, 2)}."
            )

    # Continuous Adaptive Dynamic Shadow Trailing (v4) or Legacy Runner Trailing once Stage 1 is cleared
    if (new_stage >= 1 or current_stage >= 1) and getattr(config, "RUNNER_TRAILING_ENABLED", True):
        highest_price = max(float(trade.get("highest_price_seen") or entry_price), current_price)
        trade["highest_price_seen"] = round(highest_price, 2)
        trade["last_monitor_price"] = round(current_price, 2)

        if full_ride_enabled:
            active_stage = max(new_stage, current_stage)
            trail_dist = calculate_adaptive_shadow_trail_distance(trade, highest_price, active_stage)
            trade["shadow_trail_distance"] = trail_dist
            shadow_sl = round_to_tick(highest_price - trail_dist)
            if shadow_sl > new_stop_loss:
                new_stop_loss = shadow_sl
                reason = (
                    f"Dynamic Shadow Trail (Stage {active_stage}): peak Rs {round(highest_price, 2)} "
                    f"- trail Rs {trail_dist} -> SL Rs {shadow_sl}."
                )
        else:
            peak_profit_percent = ((highest_price - entry_price) / entry_price) * 100 if entry_price else 0
            trail_start = float(getattr(config, "RUNNER_TRAIL_START_PROFIT_PERCENT", 13.5))
            if peak_profit_percent >= trail_start:
                trail_distance = float(getattr(config, "RUNNER_TRAIL_DISTANCE_PERCENT", 12.0))
                peak_trailing_stop = round_to_tick(highest_price * (1 - (trail_distance / 100)))
                if peak_trailing_stop > new_stop_loss:
                    new_stop_loss = peak_trailing_stop
                    reason = (
                        f"Runner trailing SL: peak profit {round(peak_profit_percent, 2)}% (Rs {round(highest_price, 2)}), "
                        f"SL trailed to Rs {round(peak_trailing_stop, 2)} ({trail_distance}% distance)."
                    )
    else:
        trade["last_monitor_price"] = round(current_price, 2)

    stage_changed = new_stage > current_stage
    if stage_changed or _should_commit_sl_update(stop_loss, new_stop_loss, stage_changed=stage_changed):
        trade["target_stage"] = new_stage
        trade["stop_loss"] = round_to_tick(new_stop_loss)
        trade["target_stage_updated_at"] = csv_datetime()
        trade["target_stage_reason"] = reason
        _replace_state_trade(trade)
        return trade, reason

    _replace_state_trade(trade)
    return trade, None


def square_off_open_trade(fyers, reason: str = "MARKET_CLOSE_SQUARE_OFF") -> dict | None:
    closed_trades = []
    for trade in load_open_trades():
        closed = _square_off_trade(fyers, trade, reason)
        if closed:
            closed_trades.append(closed)

    return closed_trades[0] if closed_trades else None


def _square_off_trade(fyers, trade: dict, reason: str = "MARKET_CLOSE_SQUARE_OFF") -> dict | None:
    if not trade:
        return None

    if trade.get("status") == "LIVE_PENDING":
        trade = refresh_pending_live_trade(fyers, trade)
        if trade.get("status") != "LIVE_OPEN":
            table("PENDING ENTRY NOT SQUARED OFF", [
                ("Option", trade.get("display_symbol", trade.get("option_symbol"))),
                ("Order ID", trade.get("order_id")),
                ("Order Status", trade.get("order_status", trade.get("status"))),
                ("Action", "No sell order placed because entry is not filled yet."),
            ])
            return trade

    expiry = _parse_expiry(trade.get("expiry"))
    if expiry and expiry < today_date():
        return close_stale_paper_trade("EXPIRED_CONTRACT", trade)

    quote_key = f"{trade.get('exchange', 'NSE')}:{trade['option_symbol']}" if ":" not in trade.get("option_symbol", "") else trade["option_symbol"]
    quote_response = fyers.quote([quote_key])
    if quote_key not in quote_response:
        return close_stale_paper_trade("QUOTE_NOT_AVAILABLE", trade)

    current_price = float(quote_response[quote_key]["last_price"])
    exit_order_id = maybe_place_live_exit_order(fyers, trade, reason, current_price=current_price)
    closed = close_open_trade(current_price, reason, trade)

    if closed and exit_order_id:
        closed["exit_order_id"] = exit_order_id
        _replace_state_trade(closed)

    table("MANDATORY SQUARE OFF", [
        ("Option", trade["option_symbol"]),
        ("Reason", reason),
        ("Entry", f"Rs {trade['entry_price']}"),
        ("Exit", f"Rs {current_price}"),
        ("P&L", f"Rs {closed['pnl']}" if closed else "N/A"),
        ("Status", "Closed. No overnight holding."),
    ])
    print()
    print_performance_summary()
    return closed


def monitor_open_trades(fyers) -> list[dict]:
    results = []
    for trade in load_open_trades():
        result = monitor_open_trade(fyers, trade)
        if result:
            results.append(result)
    return results


def monitor_open_trade(fyers, trade: dict | None = None) -> dict | None:
    trade = trade or load_open_trade()
    if not trade:
        return None

    if trade.get("status") == "LIVE_PENDING":
        trade = refresh_pending_live_trade(fyers, trade)
        table("PENDING ENTRY ORDER", [
            ("Option", trade.get("display_symbol", trade.get("option_symbol"))),
            ("Order ID", trade.get("order_id")),
            ("Order Type", trade.get("entry_order_type")),
            ("Limit Price", f"Rs {trade.get('entry_order_price')}"),
            ("Order Status", trade.get("order_status", trade.get("status"))),
            ("Filled Qty", trade.get("filled_quantity", "N/A")),
            ("Pending Qty", trade.get("pending_quantity", "N/A")),
            ("Action", "Waiting for entry fill before target/SL monitoring."),
        ])
        return trade

    expiry = _parse_expiry(trade.get("expiry"))
    if expiry and expiry < today_date():
        closed = close_stale_paper_trade("EXPIRED_CONTRACT", trade)
        table("STALE PAPER TRADE CLOSED", [
            ("Option", trade["option_symbol"]),
            ("Expiry", trade.get("expiry")),
            ("Reason", "Contract expired, so Fyers quote is no longer available."),
            ("Result", "FAIL"),
            ("P&L", f"Rs {closed['pnl']}" if closed else "N/A"),
        ])
        print()
        print_performance_summary()
        return closed

    quote_key = f"{trade.get('exchange', 'NSE')}:{trade['option_symbol']}" if ":" not in trade.get("option_symbol", "") else trade["option_symbol"]
    quote_response = fyers.quote([quote_key])
    if quote_key not in quote_response:
        closed = close_stale_paper_trade("QUOTE_NOT_AVAILABLE", trade)
        table("STALE PAPER TRADE CLOSED", [
            ("Option", trade["option_symbol"]),
            ("Reason", "Fyers did not return quote for this option symbol."),
            ("Result", "FAIL"),
            ("P&L", f"Rs {closed['pnl']}" if closed else "N/A"),
        ])
        print()
        print_performance_summary()
        return closed

    option_quote = quote_response[quote_key]
    current_price = float(option_quote["last_price"])
    trade = apply_market_depth_to_trade(trade, option_quote)
    trade = refresh_live_position_chart_context(fyers, trade, current_price)
    if trade.get("dynamic_exit_enabled"):
        return monitor_dynamic_exit(fyers, trade, current_price)

    target = float(trade.get("target_3") or trade["target"])
    target_1 = trade.get("target_1")
    target_2 = trade.get("target_2")
    entry_price = float(trade.get("entry_price") or current_price)

    lot_size = int(trade.get("lot_size") or 1)
    original_quantity = int(trade.get("quantity") or 0)
    original_lots = original_quantity // lot_size if lot_size else 0

    # 1-Lot Trade Handling at Target 1:
    # - In v4 (FULL_POSITION_RIDE_ENABLED): if Strong T1 continuation, hold the 1 lot with 65% Parity Floor & Shadow Trail to T2/T3!
    # - If Weak/Stalling at 94% buffer or weak T1 orderbook (or if v4 disabled), exit 1-lot trade at current_price/best_bid.
    if original_lots <= 1 and target_1 and is_target_hit(current_price, float(target_1), entry_price):
        should_close_one_lot_at_t1 = True
        weak_t1_reason = "Closing 1-lot trade at Target 1."
        if getattr(config, "FULL_POSITION_RIDE_ENABLED", False):
            if int(trade.get("target_stage") or 0) >= 1 and trade.get("full_position_ride_active"):
                should_close_one_lot_at_t1 = False
            else:
                strong_t1, cont_reason = is_t1_continuation_strong(trade, current_price, float(target_1))
                if strong_t1:
                    should_close_one_lot_at_t1 = False
                else:
                    weak_t1_reason = f"Weak/Buffer T1 ({cont_reason}); closing 1-lot trade at current market/bid."

        if should_close_one_lot_at_t1:
            exec_price = _practical_exit_price(trade, current_price) if getattr(config, "FULL_POSITION_RIDE_ENABLED", False) else current_price
            trade["exit_decision_reason"] = (
                f"EXIT: current Rs {current_price} (exec Rs {exec_price}) at Target 1 Rs {target_1}. "
                f"{weak_t1_reason}"
            )
            exit_order_id = maybe_place_live_exit_order(fyers, trade, "TARGET_1_HIT", current_price=exec_price)
            if trade.get("status") == "LIVE_OPEN" and not exit_order_id:
                return trade
            closed = close_open_trade(exec_price, "TARGET_1_HIT", trade)
            if exit_order_id:
                closed["exit_order_id"] = exit_order_id
                _replace_state_trade(closed)
            log(f"Target 1 hit. 1-lot trade closed with profit. P&L: Rs {closed['pnl']}")
            print()
            print_performance_summary()
            return closed

    # Underlying Index Structural Invalidation Check (Exits if Nifty/Sensex breaks 15m structure)
    index_symbol = trade.get("underlying")
    index_sl = trade.get("index_stop_loss")
    if index_symbol and index_sl and str(index_sl) != "N/A":
        try:
            from index_config import get_index_config
            idx_cfg = get_index_config(index_symbol)
            idx_quote_key = idx_cfg["quote_key"]
            idx_quote = fyers.quote([idx_quote_key])
            if idx_quote_key in idx_quote:
                idx_ltp = float(idx_quote[idx_quote_key]["last_price"])
                opt_type = trade.get("option_type")
                index_invalid = False
                inval_reason = ""
                if opt_type == "PE" and idx_ltp > float(index_sl):
                    index_invalid = True
                    inval_reason = f"Underlying {index_symbol} crossed above structural SL Rs {index_sl} (LTP {idx_ltp})."
                elif opt_type == "CE" and idx_ltp < float(index_sl):
                    index_invalid = True
                    inval_reason = f"Underlying {index_symbol} crossed below structural SL Rs {index_sl} (LTP {idx_ltp})."

                if index_invalid:
                    trade["exit_decision_reason"] = f"EXIT: {inval_reason} Structural trend invalidated."
                    table("STRUCTURAL INVALIDATION EXIT", [
                        ("Option", trade.get("display_symbol", trade["option_symbol"])),
                        ("Underlying", index_symbol),
                        ("Index LTP", f"Rs {idx_ltp}"),
                        ("Index SL", f"Rs {index_sl}"),
                        ("Reason", "UNDERLYING_TREND_INVALIDATION"),
                        ("Action", "Placing live exit order."),
                    ])
                    exit_order_id = maybe_place_live_exit_order(fyers, trade, "UNDERLYING_TREND_INVALIDATION", current_price=current_price)
                    if trade.get("status") == "LIVE_OPEN" and not exit_order_id:
                        return trade
                    closed = close_open_trade(current_price, "UNDERLYING_TREND_INVALIDATION", trade)
                    if exit_order_id:
                        closed["exit_order_id"] = exit_order_id
                        _replace_state_trade(closed)
                    log(f"Structural trend invalidated. Trade closed. P&L: Rs {closed['pnl']}")
                    print()
                    print_performance_summary()
                    return closed
        except Exception:
            pass

    # Target 3 Check with Trending Runner Expansion
    is_trending_market = (
        trade.get("market_regime") == "TRENDING"
        or float(trade.get("adx_14") or 0) >= 22
        or float(trade.get("setup_score") or 0) >= 70
    )
    allow_expansion = getattr(config, "TARGET_3_PROFIT_RUNNER_ALLOW_EXPANSION", False)
    has_partial_booked = bool(trade.get("partial_exit_t1_done") or trade.get("partial_exit_t2_done"))

    if current_price >= target:
        if is_trending_market and allow_expansion and has_partial_booked:
            trade["t3_expanded"] = True
            trade["exit_decision_reason"] = (
                f"RUNNER EXPANSION: current Rs {current_price} crossed Target 3 Rs {target}. "
                "Market is strongly TRENDING; runner allowed to ride with Dynamic Peak Trailing!"
            )
        else:
            trade["exit_decision_reason"] = (
                f"EXIT: current Rs {current_price} reached final target Rs {target}. "
                "Closing all remaining quantity at T3/final target."
            )
            exit_order_id = maybe_place_live_exit_order(fyers, trade, "TARGET_3_HIT", current_price=current_price)
            if trade.get("status") == "LIVE_OPEN" and not exit_order_id:
                return trade
            closed = close_open_trade(current_price, "TARGET_3_HIT", trade)
            if exit_order_id:
                closed["exit_order_id"] = exit_order_id
                _replace_state_trade(closed)
            log(f"Target hit. Paper/live tracking closed. P&L: Rs {closed['pnl']}")
            print()
            print_performance_summary()
            return closed

    trade, stage_reason = update_target_stage_stop(trade, current_price)
    if target_1 and is_target_hit(current_price, float(target_1), entry_price) and not trade.get("partial_exit_t1_done"):
        partial_exec_price = _practical_exit_price(trade, current_price) if getattr(config, "FULL_POSITION_RIDE_ENABLED", False) else current_price
        trade = close_partial_trade(fyers, trade, partial_exec_price, 1, "TARGET_1_PARTIAL_EXIT")
        stage_reason = trade.get("target_stage_reason") or stage_reason
    elif target_2 and is_target_hit(current_price, float(target_2), entry_price) and not trade.get("partial_exit_t2_done"):
        partial_exec_price = _practical_exit_price(trade, current_price) if getattr(config, "FULL_POSITION_RIDE_ENABLED", False) else current_price
        trade = close_partial_trade(fyers, trade, partial_exec_price, 2, "TARGET_2_PARTIAL_EXIT")
        stage_reason = trade.get("target_stage_reason") or stage_reason

    trade, trailing_reason = update_trailing_stop(trade, current_price)
    if (stage_reason or trailing_reason) and STATE_FILE == Path("open_trade.json"):
        try:
            from database import db_save_trade
            db_save_trade(trade)
        except Exception:
            pass

    stop_loss = float(trade["stop_loss"])
    entry_price = float(trade["entry_price"])
    target_stage = int(trade.get("target_stage") or 0)
    remaining_quantity = int(trade.get("remaining_quantity") or trade.get("quantity") or 0)
    profit_percent = round(((current_price - entry_price) / entry_price) * 100, 2) if entry_price else 0
    ride_mode = "FULL_RIDE_SHADOW" if trade.get("full_position_ride_active") else "PARTIAL_HYBRID"
    exit_decision_reason = (
        f"HOLD ({ride_mode}): current Rs {current_price}, final target Rs {target}, current SL Rs {stop_loss}, "
        f"stage {target_stage}, profit {profit_percent}%, remaining qty {remaining_quantity}."
    )

    table("OPEN TRADE MONITOR", [
        ("Option", trade["option_symbol"]),
        ("Display", trade.get("display_symbol", trade["option_symbol"])),
        ("Order Status", trade.get("order_status", trade.get("status"))),
        ("Signal", trade.get("signal")),
        ("Order Action", trade.get("order_action", trade.get("trade_type"))),
        ("Mode", trade.get("mode")),
        ("Opened At", trade.get("opened_at")),
        ("Entry", f"Rs {trade['entry_price']}"),
        ("Lots", trade.get("lots", "N/A")),
        ("Quantity", trade.get("quantity")),
        ("Remaining Qty", remaining_quantity),
        ("Booked P&L", f"Rs {trade.get('partial_realized_pnl', 0)}"),
        ("Current", f"Rs {current_price}"),
        ("Target 1", f"Rs {target_1}" if target_1 else "N/A"),
        ("Target 2", f"Rs {target_2}" if target_2 else "N/A"),
        ("Final Target", f"Rs {target}"),
        ("Target Stage", trade.get("target_stage", 0)),
        ("Stop Loss", f"Rs {stop_loss}"),
        ("Stage SL", stage_reason or trade.get("target_stage_reason") or "No change"),
        ("Trailing SL", trailing_reason or trade.get("trailing_stop_reason") or "No change"),
        ("Decision", exit_decision_reason),
    ])

    if current_price <= stop_loss:
        trade["exit_decision_reason"] = (
            f"EXIT: current Rs {current_price} fell to/below current SL Rs {stop_loss}. "
            f"Target stage was {target_stage}. Remaining quantity will be closed."
        )
        table("EXIT TRIGGERED", [
            ("Option", trade.get("display_symbol", trade["option_symbol"])),
            ("Reason", "STOP_LOSS_HIT"),
            ("Current", f"Rs {current_price}"),
            ("Current SL", f"Rs {stop_loss}"),
            ("Remaining Qty", remaining_quantity),
            ("Action", "Placing live exit order."),
        ])
        exit_order_id = maybe_place_live_exit_order(fyers, trade, "STOP_LOSS_HIT", current_price=current_price)
        if trade.get("status") == "LIVE_OPEN" and not exit_order_id:
            trade["last_exit_order_failed_at"] = csv_datetime()
            trade["last_exit_order_failed_reason"] = "Broker rejected exit order. Position may still be open in Fyers."
            _replace_state_trade(trade)
            return trade
        closed = close_open_trade(current_price, "STOP_LOSS_HIT", trade)
        if exit_order_id:
            closed["exit_order_id"] = exit_order_id
            _replace_state_trade(closed)
        log(f"Stop loss hit. Paper/live tracking closed. P&L: Rs {closed['pnl']}")
        print()
        print_performance_summary()
        return closed

    # v5 Live Multi-Candle Structure Reversal Exit:
    # When both 5m Index structure (EMA9/EMA21/VWAP) AND 5m Option structure (Option VWAP + Swing Low Support)
    # break against the position, exit cleanly instead of waiting for full emergency SL drawdown.
    if getattr(config, "LIVE_CHART_REVERSAL_EXIT_ENABLED", True) and trade.get("live_structure_reversal"):
        rev_reason = trade.get("live_structure_reversal_reason") or "Live 5m Index & Option structure reversed against trade."
        trade["exit_decision_reason"] = f"EXIT: {rev_reason}"
        table("EXIT TRIGGERED", [
            ("Option", trade.get("display_symbol", trade["option_symbol"])),
            ("Reason", "LIVE_CHART_STRUCTURE_REVERSAL"),
            ("Current", f"Rs {current_price}"),
            ("Entry", f"Rs {entry_price}"),
            ("Profit", f"{profit_percent}%"),
            ("Remaining Qty", remaining_quantity),
            ("Action", "Placing live exit order (Chart structure reversal)."),
        ])
        exit_order_id = maybe_place_live_exit_order(fyers, trade, "LIVE_CHART_STRUCTURE_REVERSAL", current_price=current_price)
        if trade.get("status") == "LIVE_OPEN" and not exit_order_id:
            trade["last_exit_order_failed_at"] = csv_datetime()
            trade["last_exit_order_failed_reason"] = "Broker rejected exit order. Position may still be open in Fyers."
            _replace_state_trade(trade)
            return trade
        closed = close_open_trade(current_price, "LIVE_CHART_STRUCTURE_REVERSAL", trade)
        if exit_order_id:
            closed["exit_order_id"] = exit_order_id
            _replace_state_trade(closed)
        log(f"Live chart structure reversal closed trade. P&L: Rs {closed['pnl']}")
        print()
        print_performance_summary()
        return closed

    time_exit_due, time_exit_reason = check_time_based_exit(trade, current_price)
    if time_exit_due:
        trade["exit_decision_reason"] = (
            f"EXIT: {time_exit_reason} Remaining quantity will be closed to protect against theta decay."
        )
        table("EXIT TRIGGERED", [
            ("Option", trade.get("display_symbol", trade["option_symbol"])),
            ("Reason", "TIME_BASED_EXIT"),
            ("Current", f"Rs {current_price}"),
            ("Entry", f"Rs {entry_price}"),
            ("Profit", f"{profit_percent}%"),
            ("Remaining Qty", remaining_quantity),
            ("Action", "Placing live exit order (Time decay protection)."),
        ])
        exit_order_id = maybe_place_live_exit_order(fyers, trade, "TIME_BASED_EXIT", current_price=current_price)
        if trade.get("status") == "LIVE_OPEN" and not exit_order_id:
            trade["last_exit_order_failed_at"] = csv_datetime()
            trade["last_exit_order_failed_reason"] = "Broker rejected exit order. Position may still be open in Fyers."
            _replace_state_trade(trade)
            return trade
        closed = close_open_trade(current_price, "TIME_BASED_EXIT", trade)
        if exit_order_id:
            closed["exit_order_id"] = exit_order_id
            _replace_state_trade(closed)
        log(f"Time-based exit closed trade. Paper/live tracking closed. P&L: Rs {closed['pnl']}")
        print()
        print_performance_summary()
        return closed

    trade["exit_decision_reason"] = exit_decision_reason
    _replace_state_trade(trade)
    log("Trade still open. No new trade will be created.")
    return trade


def _exit_limit_price(trade: dict, current_price: float | None = None) -> float | None:
    best_bid = float(trade.get("best_bid") or 0)
    reference_price = best_bid or float(current_price or 0)
    if reference_price <= 0:
        return None

    buffer_percent = float(getattr(config, "EXIT_LIMIT_BUFFER_PERCENT", 1.0) or 0) / 100
    return round_to_tick(reference_price * (1 - buffer_percent))


def maybe_place_live_exit_order(fyers, trade: dict, reason: str, quantity: int | None = None, current_price: float | None = None):
    if trade.get("status") != "LIVE_OPEN":
        return None

    if not config.AUTO_EXIT_ON_TARGET_SL:
        print("Live exit order not placed because AUTO_EXIT_ON_TARGET_SL is False.")
        return None

    if trade.get("mode") == "SEMI_AUTO":
        print(f"\n{reason}: live position exit needs confirmation.")
        sell_quantity = int(quantity or trade.get("remaining_quantity") or trade["quantity"])
        confirm = input(f"Type YES to SELL {sell_quantity} x {trade['option_symbol']}: ").strip()
        if confirm != "YES":
            print("Exit order cancelled by user. Position is still open in broker account.")
            return None

    elif trade.get("mode") == "LIVE_AUTO" and not config.ENABLE_LIVE_AUTO:
        print("Live auto exit blocked because ENABLE_LIVE_AUTO is False.")
        return None

    force_market_reasons = {"MARKET_CLOSE_SQUARE_OFF", "EMERGENCY_STOP_LOSS"}
    if getattr(config, "FORCE_MARKET_ON_SL", True):
        force_market_reasons.add("STOP_LOSS_HIT")

    if reason in force_market_reasons:
        exit_order_type = "MARKET"
        exit_price = None
    else:
        exit_order_type = str(getattr(config, "EXIT_ORDER_TYPE", "LIMIT") or "LIMIT").upper()
        exit_price = _exit_limit_price(trade, current_price) if exit_order_type == "LIMIT" else None
        if exit_order_type == "LIMIT" and exit_price is None:
            # Fallback to MARKET rather than blocking and leaving position open in broker
            exit_order_type = "MARKET"

    return place_order(
        fyers,
        trade["option_symbol"],
        "SELL",
        int(quantity or trade.get("remaining_quantity") or trade["quantity"]),
        exchange=trade.get("exchange"),
        order_type=exit_order_type,
        price=exit_price,
    )

