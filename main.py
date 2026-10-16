# main.py - The main bot that runs everything together

import time
from concurrent.futures import ThreadPoolExecutor
from requests.exceptions import ConnectionError, Timeout
from account_status import save_account_status
from analysis_snapshot import save_analysis_snapshot
from dashboard import start_dashboard_background
from decision_engine import build_final_decision
from drive_sync import start_csv_drive_sync_background
from execution_manager import execute_trade
from fyers_login import login
from learning_manager import apply_learning_adjustments
from log_utils import log
from market_comparator import analyze_index, choose_safe_market
from market_context import analyze_market_context
from market_regime import detect_market_regime
from market_time import (
    is_after_no_new_trade_time,
    is_before_no_new_trade_start,
    is_opening_settle_window,
    is_expiry_day_new_trade_blocked,
    is_lunch_avoid_window,
    is_pre_open_reference_window,
    is_square_off_time,
)
from news_analyzer import analyze_news
from order_manager import place_order, get_balance, get_positions
from option_chain_analyzer import analyze_option_chain_for_trade
from paper_account import get_paper_balance
from paper_trader import record_paper_trade
from pre_open_analyzer import analyze_pre_open_market, load_pre_open_context
from position_sizer import apply_position_sizing, calculate_fund_usage_limit, calculate_trade_risk_limit
from risk_manager import check_daily_risk_allowed, check_same_underlying_sl_cooldown
from runtime_settings import apply_runtime_settings
from research_logger import update_ghost_trades
from strategy import build_strategy_signal
from terminal_ui import section, table
from time_quality import evaluate_time_quality
from trade_quality import add_option_premium_to_quality
from trade_state import load_open_trades, monitor_open_trades, square_off_open_trade
from trade_explainer import build_trade_explanation, print_trade_explanation
import config

def check_balance_or_exit(fyers):
    fyers_balance = get_balance(fyers)
    balance = fyers_balance
    paper_balance = None
    if (config.PAPER_TRADE or config.TRADING_MODE == "PAPER") and config.PAPER_BALANCE_OVERRIDE:
        paper_balance = get_paper_balance()
        balance = paper_balance

    profile = None
    try:
        profile = fyers.profile()
    except Exception:
        profile = None
    save_account_status(
        fyers_balance,
        profile,
        active_balance=balance,
        paper_balance=paper_balance,
        mode=config.TRADING_MODE,
    )

    if config.MAX_TRADE_AMOUNT and balance < config.MAX_TRADE_AMOUNT:
        if config.PAPER_TRADE or config.TRADING_MODE == "PAPER":
            table("BALANCE CHECK", [
                ("Fyers Balance", f"Rs {fyers_balance}"),
                ("Paper Balance Used", f"Rs {balance}"),
                ("MAX_TRADE_AMOUNT", f"Rs {config.MAX_TRADE_AMOUNT}"),
                ("Mode", config.TRADING_MODE),
                ("Paper Trade", config.PAPER_TRADE),
                ("Action", "Continuing because no real order will be placed."),
            ])
        else:
            table("BALANCE CHECK", [
                ("Available Balance", f"Rs {balance}"),
                ("MAX_TRADE_AMOUNT", f"Rs {config.MAX_TRADE_AMOUNT}"),
                ("Action", "Insufficient balance. Exiting."),
            ])
            return None

    return balance


def is_trade_risk_free(trade: dict) -> bool:
    return float(trade.get("stop_loss") or 0) >= float(trade.get("entry_price") or 0)


def current_negative_risk_amount(trade: dict) -> float:
    entry_price = float(trade.get("entry_price") or 0)
    stop_loss = float(trade.get("stop_loss") or 0)
    quantity = int(float(trade.get("quantity") or 0))
    return round(max(entry_price - stop_loss, 0) * quantity, 2)


def reduce_second_trade_size(option_trade: dict) -> dict:
    return reduce_trade_size(option_trade, config.SECOND_TRADE_SIZE_PERCENT, "Second same-direction index trade")


def reduce_single_index_trade_size(option_trade: dict) -> dict:
    return reduce_trade_size(option_trade, config.SINGLE_INDEX_TRADE_SIZE_PERCENT, "Single-index trade")


def reduce_lunch_trade_size(option_trade: dict) -> dict:
    return reduce_trade_size(option_trade, config.LUNCH_TRADE_SIZE_PERCENT, "Lunch strong setup trade")


def reduce_quality_trade_size(option_trade: dict, size_percent: float) -> dict:
    return reduce_trade_size(option_trade, size_percent, "Trade quality score reduced-size trade")


def reduce_regime_trade_size(option_trade: dict, size_percent: float, reason: str) -> dict:
    return reduce_trade_size(option_trade, size_percent, f"Market regime reduced-size trade: {reason}")


def reduce_time_trade_size(option_trade: dict, size_percent: float, reason: str) -> dict:
    return reduce_trade_size(option_trade, size_percent, f"Time quality reduced-size trade: {reason}")


def reduce_trade_size(option_trade: dict, size_percent: float, label: str) -> dict:
    reduced = dict(option_trade)
    lot_size = int(reduced.get("lot_size") or 1)
    base_lots = int(reduced.get("base_lots") or reduced.get("lots") or (int(reduced.get("quantity") or 0) // lot_size))
    prev_percent = float(reduced.get("effective_size_percent") or 100.0)
    effective_percent = min(prev_percent, float(size_percent))
    reduced_lots = max(1, round(base_lots * (effective_percent / 100)))
    reduced["base_lots"] = base_lots
    reduced["effective_size_percent"] = effective_percent
    reduced["lots"] = reduced_lots
    reduced["quantity"] = reduced_lots * lot_size
    entry_price = float(reduced["entry_price"])
    stop_loss = float(reduced["stop_loss"])
    reduced["estimated_required_amount"] = round(entry_price * reduced["quantity"], 2)
    reduced["estimated_risk_amount"] = round(max(entry_price - stop_loss, 0) * reduced["quantity"], 2)
    reduced["size_reduction_percent"] = effective_percent
    reduced["sizing_reason"] = (
        f"{reduced.get('sizing_reason', '')} {label} size set to {effective_percent}%."
    ).strip()
    return reduced


def apply_trade_quality_gate(signal: dict, option_trade: dict, market_data: dict) -> tuple[bool, dict, dict]:
    signal = add_option_premium_to_quality(signal, option_trade)
    decision = signal.get("TRADE_QUALITY_DECISION")
    score = signal.get("TRADE_QUALITY_SCORE")
    size_percent = float(signal.get("QUALITY_SIZE_PERCENT") or 0)
    option_trade["trade_quality_score"] = score
    option_trade["trade_quality_decision"] = decision
    option_trade["trade_quality_reason"] = signal.get("TRADE_QUALITY_REASON")
    option_trade["trade_quality_decision_reason"] = signal.get("TRADE_QUALITY_DECISION_REASON")
    option_trade["quality_entry_freshness"] = signal.get("QUALITY_ENTRY_FRESHNESS")
    option_trade["quality_trend_strength"] = signal.get("QUALITY_TREND_STRENGTH")
    option_trade["quality_retest_breakout"] = signal.get("QUALITY_RETEST_BREAKOUT")
    option_trade["quality_option_premium"] = signal.get("QUALITY_OPTION_PREMIUM")
    option_trade["quality_market_context"] = signal.get("QUALITY_MARKET_CONTEXT")
    option_trade["quality_candle_pattern"] = signal.get("QUALITY_CANDLE_PATTERN")

    table("TRADE QUALITY SCORE", [
        ("Score", score),
        ("Decision", decision),
        ("Entry Freshness", signal.get("QUALITY_ENTRY_FRESHNESS")),
        ("Trend Strength", signal.get("QUALITY_TREND_STRENGTH")),
        ("Retest/Breakout", signal.get("QUALITY_RETEST_BREAKOUT")),
        ("Option Premium", signal.get("QUALITY_OPTION_PREMIUM")),
        ("Market Context", signal.get("QUALITY_MARKET_CONTEXT")),
        ("Candle Support", signal.get("QUALITY_CANDLE_PATTERN")),
        ("Reason", signal.get("TRADE_QUALITY_DECISION_REASON")),
    ])

    if decision == "NORMAL_TRADE":
        option_trade["quality_size_percent"] = 100
        return True, signal, option_trade

    if decision == "SMALL_SIZE_TRADE":
        option_trade = reduce_quality_trade_size(option_trade, size_percent)
        option_trade["quality_size_percent"] = size_percent
        return True, signal, option_trade

    if signal.get("AI_TRADE_OVERRIDE"):
        ai_size = float(signal.get("AI_TRADE_SIZE_PERCENT") or config.AI_TRADE_SIZE_PERCENT)
        option_trade = reduce_quality_trade_size(option_trade, ai_size)
        option_trade["quality_size_percent"] = ai_size
        option_trade["ai_trade_quality_override"] = True
        option_trade["sizing_reason"] = (
            f"{option_trade.get('sizing_reason', '')} AI trade override allowed despite "
            f"quality decision {decision} at {ai_size}% size."
        ).strip()
        table("AI TRADE QUALITY OVERRIDE", [
            ("Signal", signal.get("SIGNAL")),
            ("Score", score),
            ("Decision", decision),
            ("Size", f"{ai_size}%"),
            ("Reason", "AI/momentum signal is allowed at reduced size for the enabled mode."),
        ])
        return True, signal, option_trade

    if getattr(config, "AGGRESSIVE_IGNORE_SOFT_BLOCKS", False) and signal.get("SIGNAL") in {"BUY", "SELL"}:
        option_trade["quality_size_percent"] = 100
        option_trade["aggressive_quality_override"] = True
        option_trade["sizing_reason"] = (
            f"{option_trade.get('sizing_reason', '')} Aggressive mode ignored quality decision {decision}."
        ).strip()
        table("AGGRESSIVE QUALITY OVERRIDE", [
            ("Signal", signal.get("SIGNAL")),
            ("Score", score),
            ("Decision", decision),
            ("Reason", signal.get("TRADE_QUALITY_DECISION_REASON")),
            ("Action", "Continuing because aggressive trader mode is enabled."),
        ])
        return True, signal, option_trade

    blocked_trade = {
        **option_trade,
        "status": "BLOCKED_TRADE_QUALITY",
        "quantity": 0,
        "risk_block_reason": signal.get("TRADE_QUALITY_DECISION_REASON"),
    }
    action = "Logged only. No paper/live trade opened." if decision == "PAPER_LOG_ONLY" else "No trade opened."
    table("TRADE QUALITY BLOCK", [
        ("Signal", signal.get("SIGNAL")),
        ("Score", score),
        ("Decision", decision),
        ("Option", option_trade.get("display_symbol", option_trade.get("option_symbol"))),
        ("Reason", signal.get("TRADE_QUALITY_DECISION_REASON")),
        ("Action", action),
    ])
    record_paper_trade(market_data, signal, blocked_trade)
    return False, signal, blocked_trade


def apply_option_chain_decision(option_trade: dict, signal: dict, market_data: dict) -> tuple[bool, dict]:
    status = option_trade.get("status")
    score = float(option_trade.get("confidence_score") or option_trade.get("option_chain_score") or 0)
    table("OPTION CHAIN ANALYZER", [
        ("Selected", option_trade.get("display_symbol", option_trade.get("option_symbol"))),
        ("Confidence Score", score),
        ("Status", status),
        ("Entry Logic", option_trade.get("entry_logic")),
        ("Entry", f"Rs {option_trade.get('entry_price')}"),
        ("Current", f"Rs {option_trade.get('current_price')}"),
        ("Stop Loss", f"Rs {option_trade.get('stop_loss')}"),
        ("Target 1", f"Rs {option_trade.get('target_1')}"),
        ("Target 2", f"Rs {option_trade.get('target_2')}"),
        ("Target 3", f"Rs {option_trade.get('target_3')}"),
        ("Potential Left", f"{option_trade.get('potential_left_percent')}%"),
        ("Risk Reward", option_trade.get("risk_reward")),
        ("Reason", option_trade.get("option_chain_decision_reason") or option_trade.get("reason")),
    ])

    if option_trade.get("hard_reject") or status in {"BLOCKED_OPTION_CHAIN", "BLOCKED_OPTION_SCORE"}:
        if (
            getattr(config, "AGGRESSIVE_ALLOW_LOW_OPTION_SCORE", False)
            and signal.get("SIGNAL") in {"BUY", "SELL"}
        ):
            option_trade["status"] = "AGGRESSIVE_OPTION_OVERRIDE"
            option_trade["aggressive_option_chain_override"] = True
            option_trade["option_chain_decision_reason"] = (
                f"{option_trade.get('option_chain_decision_reason') or option_trade.get('reason')} "
                f"Aggressive mode ignored option-chain block: {option_trade.get('hard_reject_reason') or status}."
            ).strip()
            table("AGGRESSIVE OPTION CHAIN OVERRIDE", [
                ("Signal", signal.get("SIGNAL")),
                ("Option", option_trade.get("display_symbol", option_trade.get("option_symbol"))),
                ("Score", score),
                ("Original Status", status),
                ("Reason", option_trade.get("hard_reject_reason") or option_trade.get("option_chain_decision_reason")),
                ("Action", "Continuing because aggressive trader mode is enabled."),
            ])
            return True, option_trade

        blocked = {
            **option_trade,
            "quantity": 0,
            "risk_block_reason": option_trade.get("hard_reject_reason") or option_trade.get("option_chain_decision_reason"),
        }
        table("OPTION CHAIN BLOCK", [
            ("Signal", signal.get("SIGNAL")),
            ("Option", blocked.get("display_symbol", blocked.get("option_symbol"))),
            ("Score", score),
            ("Reason", blocked.get("risk_block_reason")),
            ("Action", "No trade opened."),
        ])
        record_paper_trade(market_data, signal, blocked)
        return False, blocked

    if status == "LOG_ONLY_OPTION_SCORE":
        if signal.get("AI_TRADE_OVERRIDE"):
            option_trade["status"] = "PAPER_SMALL_SIZE"
            option_trade["ai_trade_option_chain_override"] = True
            option_trade["option_chain_decision_reason"] = (
                f"{option_trade.get('option_chain_decision_reason')} AI trade override allowed this "
                "log-only option-chain score at reduced size."
            )
            table("AI TRADE OPTION SCORE OVERRIDE", [
                ("Signal", signal.get("SIGNAL")),
                ("Option", option_trade.get("display_symbol", option_trade.get("option_symbol"))),
                ("Score", score),
                ("Action", "Continuing as reduced-size paper trade."),
            ])
            return True, option_trade

        if (
            getattr(config, "AGGRESSIVE_ALLOW_LOW_OPTION_SCORE", False)
            and signal.get("SIGNAL") in {"BUY", "SELL"}
        ):
            option_trade["status"] = "AGGRESSIVE_OPTION_OVERRIDE"
            option_trade["aggressive_option_chain_override"] = True
            option_trade["option_chain_decision_reason"] = (
                f"{option_trade.get('option_chain_decision_reason')} "
                "Aggressive mode allowed log-only option-chain score."
            )
            table("AGGRESSIVE OPTION SCORE OVERRIDE", [
                ("Signal", signal.get("SIGNAL")),
                ("Option", option_trade.get("display_symbol", option_trade.get("option_symbol"))),
                ("Score", score),
                ("Action", "Continuing because aggressive trader mode is enabled."),
            ])
            return True, option_trade

        logged = {
            **option_trade,
            "quantity": 0,
            "risk_block_reason": option_trade.get("option_chain_decision_reason"),
        }
        table("OPTION CHAIN LOG ONLY", [
            ("Signal", signal.get("SIGNAL")),
            ("Option", logged.get("display_symbol", logged.get("option_symbol"))),
            ("Score", score),
            ("Reason", logged.get("risk_block_reason")),
            ("Action", "Logged only. No paper/live trade opened."),
        ])
        record_paper_trade(market_data, signal, logged)
        return False, logged

    return True, option_trade


def apply_post_learning_score_gate(option_trade: dict, signal: dict, market_data: dict) -> tuple[bool, dict]:
    if not option_trade.get("learning_score_penalty"):
        return True, option_trade

    score = float(option_trade.get("confidence_score") or option_trade.get("option_chain_score") or 0)
    if score >= float(config.TRADE_QUALITY_SMALL_SIZE_THRESHOLD):
        return True, option_trade

    if getattr(config, "AGGRESSIVE_IGNORE_SOFT_BLOCKS", False) and signal.get("SIGNAL") in {"BUY", "SELL"}:
        option_trade["aggressive_learning_score_override"] = True
        option_trade["sizing_reason"] = (
            f"{option_trade.get('sizing_reason', '')} Aggressive mode ignored learning score penalty."
        ).strip()
        table("AGGRESSIVE LEARNING SCORE OVERRIDE", [
            ("Option", option_trade.get("display_symbol", option_trade.get("option_symbol"))),
            ("Final Score", score),
            ("Penalty", option_trade.get("learning_score_penalty")),
            ("Reason", option_trade.get("learning_penalty_reason")),
            ("Action", "Continuing because aggressive trader mode is enabled."),
        ])
        return True, option_trade

    blocked = {
        **option_trade,
        "status": "BLOCKED_LEARNING_SCORE",
        "quantity": 0,
        "risk_block_reason": (
            f"Learning penalty reduced final score to {score}, below "
            f"{config.TRADE_QUALITY_SMALL_SIZE_THRESHOLD} trade threshold."
        ),
    }
    table("LEARNING SCORE BLOCK", [
        ("Option", blocked.get("display_symbol", blocked.get("option_symbol"))),
        ("Final Score", score),
        ("Penalty", blocked.get("learning_score_penalty")),
        ("Reason", blocked.get("learning_penalty_reason")),
        ("Action", "Logged as ghost/no-trade. No paper/live position opened."),
    ])
    record_paper_trade(market_data, signal, blocked)
    return False, blocked


def apply_regime_and_time_gate(option_trade: dict, signal: dict, market_data: dict) -> tuple[bool, dict]:
    regime_action = signal.get("MARKET_REGIME_ACTION")
    time_action = signal.get("TIME_QUALITY_ACTION")

    if regime_action == "AVOID_OPTION_BUYING":
        if signal.get("AI_TRADE_OVERRIDE"):
            option_trade = reduce_regime_trade_size(
                option_trade,
                float(signal.get("AI_TRADE_SIZE_PERCENT") or config.AI_TRADE_SIZE_PERCENT),
                signal.get("MARKET_REGIME_REASON") or "AI trade override",
            )
            option_trade["ai_trade_regime_override"] = True
            option_trade["market_regime_size_percent"] = signal.get("AI_TRADE_SIZE_PERCENT") or config.AI_TRADE_SIZE_PERCENT
            table("AI TRADE REGIME OVERRIDE", [
                ("Regime", signal.get("MARKET_REGIME")),
                ("Reason", signal.get("MARKET_REGIME_REASON")),
                ("Action", "AI/momentum trade allowed at reduced size for the enabled mode."),
            ])
            return True, option_trade

        blocked = {
            **option_trade,
            "status": "BLOCKED_MARKET_REGIME",
            "quantity": 0,
            "risk_block_reason": signal.get("MARKET_REGIME_REASON"),
        }
        table("MARKET REGIME BLOCK", [
            ("Regime", signal.get("MARKET_REGIME")),
            ("Reason", signal.get("MARKET_REGIME_REASON")),
            ("Action", "No option buying in this regime."),
        ])
        record_paper_trade(market_data, signal, blocked)
        return False, blocked

    if time_action == "LOG_ONLY":
        if getattr(config, "AGGRESSIVE_IGNORE_SOFT_BLOCKS", False) and signal.get("SIGNAL") in {"BUY", "SELL"}:
            option_trade["aggressive_time_override"] = True
            option_trade["time_quality_size_percent"] = 100
            option_trade["sizing_reason"] = (
                f"{option_trade.get('sizing_reason', '')} Aggressive mode ignored time-quality LOG_ONLY."
            ).strip()
            table("AGGRESSIVE TIME OVERRIDE", [
                ("Window", signal.get("TIME_WINDOW")),
                ("Reason", signal.get("TIME_QUALITY_REASON")),
                ("Action", "Continuing because aggressive trader mode is enabled."),
            ])
            return True, option_trade

        blocked = {
            **option_trade,
            "status": "BLOCKED_TIME_QUALITY",
            "quantity": 0,
            "risk_block_reason": signal.get("TIME_QUALITY_REASON"),
        }
        table("TIME QUALITY BLOCK", [
            ("Window", signal.get("TIME_WINDOW")),
            ("Reason", signal.get("TIME_QUALITY_REASON")),
            ("Action", "Logged only. No fresh trade opened."),
        ])
        record_paper_trade(market_data, signal, blocked)
        return False, blocked

    if regime_action in {"SMALL_SIZE", "SCALP_ONLY"} and float(signal.get("MARKET_REGIME_SIZE_PERCENT") or 100) < 100:
        option_trade = reduce_regime_trade_size(
            option_trade,
            float(signal.get("MARKET_REGIME_SIZE_PERCENT") or 50),
            signal.get("MARKET_REGIME_REASON") or regime_action,
        )
        option_trade["market_regime_size_percent"] = signal.get("MARKET_REGIME_SIZE_PERCENT")

    if time_action in {"SMALL_SIZE", "AVOID_WEAK", "HIGH_SCORE_ONLY"} and float(signal.get("TIME_QUALITY_SIZE_PERCENT") or 100) < 100:
        option_trade = reduce_time_trade_size(
            option_trade,
            float(signal.get("TIME_QUALITY_SIZE_PERCENT") or 50),
            signal.get("TIME_QUALITY_REASON") or time_action,
        )
        option_trade["time_quality_size_percent"] = signal.get("TIME_QUALITY_SIZE_PERCENT")

    return True, option_trade


def lunch_signal_check(signal: dict) -> tuple[bool, str]:
    if not config.AVOID_LUNCH_TRADES:
        return True, "Lunch filter disabled."

    if not config.LUNCH_ALLOW_STRONG_SETUP:
        return False, f"Fresh trades are blocked from {config.AVOID_LUNCH_START} to {config.AVOID_LUNCH_END}."

    score = float(signal.get("SETUP_SCORE") or 0)
    adx = float(signal.get("ADX_14") or 0)
    min_score = float(config.LUNCH_MIN_SETUP_SCORE)
    min_adx = float(config.LUNCH_MIN_ADX)

    if score >= min_score and adx >= min_adx:
        return True, (
            f"Strong lunch setup allowed: score {score} >= {min_score} "
            f"and ADX {adx} >= {min_adx}."
        )

    return False, (
        f"Lunch setup is not strong enough: score {score}/{min_score}, "
        f"ADX {adx}/{min_adx}."
    )


def print_pre_open_context(context: dict) -> None:
    if not context:
        return

    rows = [
        ("Overall Bias", context.get("overall_bias", "N/A")),
        ("Updated", context.get("event_time", "N/A")),
        ("Note", context.get("note", "Reference only.")),
    ]
    for item in context.get("indices", []):
        rows.extend([
            (f"{item.get('symbol')} Gap", f"{item.get('gap_percent')}%"),
            (f"{item.get('symbol')} Bias", item.get("bias")),
            (f"{item.get('symbol')} Last", f"Rs {item.get('last_price')}"),
        ])
    if context.get("errors"):
        rows.append(("Warnings", context.get("errors")))
    table("PRE-OPEN MARKET CONTEXT", rows)


def _support_resistance_levels(candles: list[dict]) -> dict:
    recent = candles[-20:] if candles else []
    if not recent:
        return {}

    highs = [float(candle.get("high") or 0) for candle in recent if candle.get("high") is not None]
    lows = [float(candle.get("low") or 0) for candle in recent if candle.get("low") is not None]
    if not highs or not lows:
        return {}

    previous = recent[-2] if len(recent) >= 2 else recent[-1]
    current = recent[-1]
    return {
        "support": round(min(lows), 2),
        "resistance": round(max(highs), 2),
        "breakout_above": round(float(previous.get("high") or current.get("high") or 0), 2),
        "breakdown_below": round(float(previous.get("low") or current.get("low") or 0), 2),
        "range_candles": len(recent),
    }


def _build_analysis_snapshot(
    index_analyses: list[dict],
    comparison: dict,
    market_data: dict,
    latest_candle: dict | None,
    candles: list[dict],
    strategy_signal: dict,
    final_signal: dict,
    news: dict,
    market_context: dict,
    pre_open_context: dict,
) -> dict:
    index_rows = []
    for analysis in index_analyses:
        data = analysis.get("market_data") or {}
        signal = analysis.get("strategy_signal") or {}
        index_rows.append({
            "symbol": analysis.get("symbol"),
            "last_price": data.get("last_price"),
            "change_percent": data.get("change_percent"),
            "signal": signal.get("SIGNAL"),
            "score": signal.get("SETUP_SCORE"),
            "trade_quality_score": signal.get("TRADE_QUALITY_SCORE"),
            "trade_quality_decision": signal.get("TRADE_QUALITY_DECISION"),
            "entry_mode": signal.get("ENTRY_MODE"),
            "reason": signal.get("REASON"),
            "pattern_bias": signal.get("CANDLE_PATTERN_BIAS"),
            "patterns": signal.get("CANDLE_PATTERNS"),
        })

    latest = {}
    if latest_candle:
        latest = {
            "time": latest_candle.get("date"),
            "open": latest_candle.get("open"),
            "high": latest_candle.get("high"),
            "low": latest_candle.get("low"),
            "close": latest_candle.get("close"),
            "ema_9": latest_candle.get("ema_9"),
            "ema_21": latest_candle.get("ema_21"),
            "rsi_14": latest_candle.get("rsi_14"),
            "vwap": latest_candle.get("vwap"),
            "atr_14": latest_candle.get("atr_14"),
            "adx_14": latest_candle.get("adx_14"),
        }

    return {
        "event_time": market_data.get("event_time"),
        "selected_symbol": comparison.get("selected", {}).get("symbol") if comparison.get("selected") else "HOLD",
        "final_signal": final_signal.get("SIGNAL"),
        "final_reason": final_signal.get("REASON"),
        "strategy_signal": strategy_signal.get("SIGNAL"),
        "strategy_reason": strategy_signal.get("REASON"),
        "index_alignment": final_signal.get("INDEX_ALIGNMENT"),
        "indices": index_rows,
        "latest_candle": latest,
        "levels": _support_resistance_levels(candles),
        "pre_open": pre_open_context,
        "news": {
            "sentiment": news.get("sentiment"),
            "risk_level": news.get("risk_level"),
            "reason": news.get("reason"),
            "headlines": news.get("headlines", [])[:3],
        },
        "market_context": {
            "bias": market_context.get("bias"),
            "risk_level": market_context.get("risk_level"),
            "score": market_context.get("score"),
            "reason": market_context.get("reason"),
            "unavailable": market_context.get("unavailable", []),
            "components": market_context.get("components", []),
        },
        "market_regime": {
            "regime": final_signal.get("MARKET_REGIME"),
            "risk": final_signal.get("MARKET_REGIME_RISK"),
            "action": final_signal.get("MARKET_REGIME_ACTION"),
            "reason": final_signal.get("MARKET_REGIME_REASON"),
        },
        "time_quality": {
            "window": final_signal.get("TIME_WINDOW"),
            "action": final_signal.get("TIME_QUALITY_ACTION"),
            "reason": final_signal.get("TIME_QUALITY_REASON"),
        },
    }


def additional_trade_check(open_trades: list[dict], option_trade: dict, news: dict, balance: float) -> tuple[bool, str]:
    if config.TRADING_MODE == "PAPER" or config.PAPER_TRADE:
        return True, "Paper mode: open trade limits disabled for training."

    if not open_trades:
        return True, "No open trade."

    if config.MAX_OPEN_TRADES and len(open_trades) >= config.MAX_OPEN_TRADES:
        return False, f"Max open trades reached: {len(open_trades)}/{config.MAX_OPEN_TRADES}."

    if news.get("risk_level") == "HIGH" or news.get("sentiment") == "HIGH_RISK":
        return False, "Second trade blocked because news risk is HIGH."

    risky_trades = [trade for trade in open_trades if not is_trade_risk_free(trade)]
    if risky_trades:
        first = risky_trades[0]
        return False, (
            f"Second trade blocked because {first.get('display_symbol', first.get('option_symbol'))} "
            f"still has negative risk: SL {first.get('stop_loss')} < entry {first.get('entry_price')}."
        )

    same_underlying = [trade for trade in open_trades if trade.get("underlying") == option_trade.get("underlying")]
    if same_underlying:
        return False, f"Second trade blocked because {option_trade.get('underlying')} already has an open trade."

    conflicting = [
        trade
        for trade in open_trades
        if trade.get("option_type") and trade.get("option_type") != option_trade.get("option_type")
    ]
    if conflicting:
        first = conflicting[0]
        return False, (
            f"Second trade blocked because open trade is {first.get('option_type')} "
            f"but new signal is {option_trade.get('option_type')}."
        )

    open_required = sum(float(trade.get("estimated_required_amount") or 0) for trade in open_trades)
    open_risk = sum(current_negative_risk_amount(trade) for trade in open_trades)
    combined_required = open_required + float(option_trade.get("estimated_required_amount") or 0)
    combined_risk = open_risk + float(option_trade.get("estimated_risk_amount") or 0)
    fund_limit = calculate_fund_usage_limit(balance)
    risk_limit = calculate_trade_risk_limit(balance)

    if combined_required > fund_limit:
        return False, f"Second trade blocked: combined required amount Rs {round(combined_required, 2)} exceeds fund limit Rs {round(fund_limit, 2)}."

    if combined_risk > risk_limit:
        return False, f"Second trade blocked: combined risk Rs {round(combined_risk, 2)} exceeds risk limit Rs {round(risk_limit, 2)}."

    return True, "Second trade allowed: existing trade is risk-free, direction matches, and combined risk is within limits."


def analyze_once(fyers, balance, allow_fresh_analysis: bool = True):
    apply_runtime_settings(config)

    try:
        update_ghost_trades(fyers)
    except Exception as error:
        log(f"Ghost tracker update skipped: {error}")

    if config.PAPER_TRADE or config.TRADING_MODE == "PAPER":
        balance = get_paper_balance()

    open_trades = load_open_trades()
    if open_trades:
        if is_square_off_time():
            section("MANDATORY SQUARE OFF")
            square_off_open_trade(fyers, reason="MARKET_CLOSE_SQUARE_OFF")
            return

        section("OPEN TRADE CHECK")
        monitor_open_trades(fyers)
        open_trades = load_open_trades()
        if open_trades and not allow_fresh_analysis:
            return
        if open_trades and config.MAX_OPEN_TRADES and len(open_trades) >= config.MAX_OPEN_TRADES:
            table("MAX OPEN TRADES", [
                ("Open Trades", len(open_trades)),
                ("Limit", config.MAX_OPEN_TRADES),
                ("Action", "Monitoring existing trades only."),
            ])
            return

    if not allow_fresh_analysis:
        return

    if is_square_off_time():
        section("MARKET CLOSE SAFETY")
        table("NO TRADE", [
            ("Reason", "Square-off time has passed."),
            ("Rule", f"No new trades after {config.SQUARE_OFF_TIME}."),
            ("Action", "Waiting for next day/session."),
        ])
        return

    pre_open_context = {}
    if is_pre_open_reference_window():
        try:
            pre_open_context = analyze_pre_open_market(fyers)
        except Exception as error:
            pre_open_context = {
                "overall_bias": "UNKNOWN",
                "event_time": "N/A",
                "note": f"Could not read pre-open context: {error}",
                "indices": [],
                "errors": [{"error": str(error)}],
            }
        print_pre_open_context(pre_open_context)
    else:
        pre_open_context = load_pre_open_context()

    if is_before_no_new_trade_start():
        table("MARKET OPEN SAFETY", [
            ("Reason", f"Fresh trades are blocked before {config.NO_NEW_TRADE_BEFORE}."),
            ("Pre-open Bias", pre_open_context.get("overall_bias", "N/A")),
            ("Action", "Reading pre-open context, but waiting for cleaner candles after market open."),
        ])
        return

    lunch_window = is_lunch_avoid_window()
    if lunch_window:
        table("LUNCH SOFT FILTER", [
            ("Window", f"{config.AVOID_LUNCH_START} to {config.AVOID_LUNCH_END}"),
            ("Rule", "Normal setups are blocked, strong setups continue to final checks."),
            ("Strong Setup Needs", f"Score >= {config.LUNCH_MIN_SETUP_SCORE}, ADX >= {config.LUNCH_MIN_ADX}"),
        ])

    risk_allowed, risk_reason, risk_summary = check_daily_risk_allowed()
    if not risk_allowed:
        table("DAILY RISK GUARD", [
            ("Reason", risk_reason),
            ("Trades Today", risk_summary["entries"]),
            ("Failed Today", risk_summary["failed"]),
            ("Stop Losses Today", risk_summary.get("stop_losses", 0)),
            ("P&L Today", f"Rs {risk_summary['pnl']}"),
            ("Fund Used Limit", f"Rs {risk_summary.get('fund_usage_limit')}"),
            ("Daily Loss Limit", f"Rs {risk_summary.get('daily_loss_limit')}"),
            ("Daily Profit Target", f"Rs {risk_summary.get('daily_profit_target')}"),
            ("Action", "Analysis-only mode. Signals will be logged but not traded."),
        ])

    section("ANALYSIS CYCLE")
    max_workers = max(2, min(len(config.ANALYZE_INDICES) + 1, 3))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        news_future = executor.submit(analyze_news)
        index_futures = {
            executor.submit(analyze_index, fyers, symbol): symbol
            for symbol in config.ANALYZE_INDICES
        }

        index_results = {}
        analysis_errors = []
        for future, symbol in index_futures.items():
            try:
                index_results[symbol] = future.result()
            except Exception as error:
                analysis_errors.append((symbol, str(error)))

        news = news_future.result()

    index_analyses = [
        index_results[symbol]
        for symbol in config.ANALYZE_INDICES
        if symbol in index_results
    ]
    index_summary = {}
    for analysis in index_analyses:
        signal_data = analysis.get("strategy_signal", {})
        index_summary[analysis.get("symbol")] = {
            "signal": signal_data.get("SIGNAL"),
            "score": signal_data.get("SETUP_SCORE"),
            "reason": signal_data.get("REASON"),
        }

    if analysis_errors:
        table("INDEX ANALYSIS WARNINGS", analysis_errors)

    if not index_analyses:
        table("HOLD DECISION", [
            ("Signal", "HOLD"),
            ("Reason", "Could not fetch market data for any configured index."),
            ("Action", "No trade opened."),
        ])
        return

    comparison = choose_safe_market(index_analyses)
    market_data = comparison["market_data"]
    market_data["index_analyses_summary"] = index_summary
    market_data["pre_open_bias"] = pre_open_context.get("overall_bias")
    market_data["pre_open_event_time"] = pre_open_context.get("event_time")
    market_data["pre_open_context"] = pre_open_context
    strategy_signal = comparison["signal"]
    single_index_trade = comparison.get("single_index_trade", False)
    selected_analysis = comparison["selected"]
    latest_candle = selected_analysis["latest_candle"] if selected_analysis else None
    candles = selected_analysis["candles"] if selected_analysis else index_analyses[0]["candles"]

    comparison_rows = []
    for analysis in index_analyses:
        data = analysis["market_data"]
        signal_data = analysis["strategy_signal"]
        comparison_rows.extend([
            (f"{analysis['symbol']} LTP", f"Rs {data['last_price']}"),
            (f"{analysis['symbol']} Signal", signal_data.get("SIGNAL")),
            (f"{analysis['symbol']} Score", signal_data.get("SETUP_SCORE", 0)),
        ])

    table("NIFTY VS SENSEX COMPARISON", comparison_rows)

    table("SELECTED MARKET", [
        ("Selected", selected_analysis["symbol"] if selected_analysis else "HOLD"),
        ("Decision", strategy_signal["SIGNAL"]),
        ("Reason", strategy_signal["REASON"]),
        ("LTP", f"Rs {market_data.get('last_price', 'N/A')}"),
        ("Change", f"{market_data.get('change_percent', 'N/A')}%"),
        ("Pre-open Bias", market_data.get("pre_open_bias") or "N/A"),
        ("Candles", f"{len(candles)} ({config.CANDLE_INTERVAL})"),
    ])

    if latest_candle:
        table("LATEST INDICATOR CANDLE", [
            ("Candle Time", latest_candle["date"]),
            ("Close", f"Rs {latest_candle['close']}"),
            ("EMA 9", latest_candle["ema_9"]),
            ("EMA 21", latest_candle["ema_21"]),
            ("RSI 14", latest_candle["rsi_14"]),
            ("VWAP", latest_candle["vwap"]),
            ("ATR 14", latest_candle.get("atr_14")),
            ("ADX 14", latest_candle.get("adx_14")),
            ("Pattern Bias", strategy_signal.get("CANDLE_PATTERN_BIAS", "N/A")),
            ("Patterns", strategy_signal.get("CANDLE_PATTERNS", "N/A")),
            ("Secondary TF", strategy_signal.get("SECONDARY_TIMEFRAME_PATTERNS") or "N/A"),
        ])

    table("STRATEGY ANALYSIS", [
        ("Signal", strategy_signal["SIGNAL"]),
        ("Confidence", strategy_signal["CONFIDENCE"]),
        ("Pattern Bias", strategy_signal.get("CANDLE_PATTERN_BIAS", "N/A")),
        ("Pattern Scores", f"Bull {strategy_signal.get('CANDLE_PATTERN_BULLISH_SCORE', 0)} / Bear {strategy_signal.get('CANDLE_PATTERN_BEARISH_SCORE', 0)}"),
        ("Reason", strategy_signal["REASON"]),
    ])

    try:
        market_context = analyze_market_context(
            fyers,
            selected_symbol=market_data.get("symbol"),
            spot_price=market_data.get("last_price"),
            news=news,
        )
    except Exception as error:
        market_context = {
            "bias": "UNAVAILABLE",
            "risk_level": "UNKNOWN",
            "score": 0,
            "reason": f"Market context analysis failed: {error}",
            "components": [],
            "unavailable": [],
        }

    table("MARKET CONTEXT", [
        ("Bias", market_context.get("bias")),
        ("Risk", market_context.get("risk_level")),
        ("Score", market_context.get("score")),
        ("Unavailable", ", ".join(market_context.get("unavailable", [])[:5]) or "None"),
        ("Reason", market_context.get("reason")),
    ])

    market_regime = detect_market_regime(candles, latest_candle, market_context, news)
    market_context.update({
        "market_regime": market_regime.get("regime"),
        "market_regime_risk": market_regime.get("risk_level"),
        "market_regime_action": market_regime.get("action"),
        "market_regime_size_percent": market_regime.get("size_percent"),
        "market_regime_reason": market_regime.get("reason"),
    })
    table("MARKET REGIME", [
        ("Regime", market_regime.get("regime")),
        ("Risk", market_regime.get("risk_level")),
        ("Action", market_regime.get("action")),
        ("Size", f"{market_regime.get('size_percent')}%"),
        ("ATR %", market_regime.get("atr_percent")),
        ("ADX", market_regime.get("adx")),
        ("EMA Gap %", market_regime.get("ema_gap_percent")),
        ("Reason", market_regime.get("reason")),
    ])

    final_decision = build_final_decision(comparison, news, market_context)
    signal = final_decision["signal"]
    time_quality = evaluate_time_quality(signal=signal)
    signal.update({
        "TIME_WINDOW": time_quality.get("time_window"),
        "TIME_QUALITY_ACTION": time_quality.get("action"),
        "TIME_QUALITY_SIZE_PERCENT": time_quality.get("size_percent"),
        "TIME_QUALITY_REASON": time_quality.get("reason"),
        "MARKET_REGIME_SIZE_PERCENT": market_regime.get("size_percent"),
    })
    market_data = final_decision["market_data"]
    market_data["index_analyses_summary"] = index_summary
    market_data["time_window"] = signal.get("TIME_WINDOW")
    market_data["time_quality_action"] = signal.get("TIME_QUALITY_ACTION")
    market_data["time_quality_reason"] = signal.get("TIME_QUALITY_REASON")
    single_index_trade = final_decision.get("single_index_trade", False)
    save_analysis_snapshot(_build_analysis_snapshot(
        index_analyses=index_analyses,
        comparison=final_decision,
        market_data=market_data,
        latest_candle=latest_candle,
        candles=candles,
        strategy_signal=strategy_signal,
        final_signal=signal,
        news=news,
        market_context=market_context,
        pre_open_context=pre_open_context,
    ))

    table("NEWS ANALYZER", [
        ("Status", "Enabled" if config.NEWS_FILTER_ENABLED else "Disabled"),
        ("Sentiment", news.get("sentiment")),
        ("Risk Level", news.get("risk_level")),
        ("Reason", news.get("reason")),
        ("Top Headlines", market_data["news_headlines"] or "N/A"),
    ])

    table("FINAL DECISION ENGINE", [
        ("Final Signal", signal.get("SIGNAL")),
        ("Chart Signal", signal.get("CHART_SIGNAL")),
        ("Index Alignment", signal.get("INDEX_ALIGNMENT")),
        ("News Mode", signal.get("NEWS_MODE")),
        ("News Conflict", "Yes" if signal.get("NEWS_CONFLICT") else "No"),
        ("Context Bias", signal.get("MARKET_CONTEXT_BIAS")),
        ("Context Risk", signal.get("MARKET_CONTEXT_RISK_LEVEL")),
        ("Context Mode", signal.get("MARKET_CONTEXT_MODE")),
        ("Context Conflict", "Yes" if signal.get("MARKET_CONTEXT_CONFLICT") else "No"),
        ("Market Regime", signal.get("MARKET_REGIME")),
        ("Regime Action", signal.get("MARKET_REGIME_ACTION")),
        ("Time Window", signal.get("TIME_WINDOW")),
        ("Time Action", signal.get("TIME_QUALITY_ACTION")),
        ("Quality Score", signal.get("TRADE_QUALITY_SCORE")),
        ("Quality Decision", signal.get("TRADE_QUALITY_DECISION")),
        ("Single Final Gate", "PASS" if signal.get("SIGNAL") in {"BUY", "SELL"} else "HOLD"),
        ("Reason", signal.get("REASON")),
    ])

    table("FINAL SIGNAL", [
        ("Final Signal", signal.get("SIGNAL")),
        ("Confidence", signal.get("CONFIDENCE")),
        ("Reason", signal.get("REASON")),
    ])

    if lunch_window and signal.get("SIGNAL") in {"BUY", "SELL"}:
        lunch_allowed, lunch_reason = lunch_signal_check(signal)
        if not lunch_allowed:
            table("LUNCH SOFT FILTER BLOCK", [
                ("Signal", signal.get("SIGNAL")),
                ("Score", signal.get("SETUP_SCORE", 0)),
                ("ADX", signal.get("ADX_14", "N/A")),
                ("Reason", lunch_reason),
                ("Action", "No fresh trade opened during lunch chop."),
            ])
            blocked_signal = dict(signal)
            blocked_signal.update({
                "SIGNAL": "HOLD",
                "CONFIDENCE": "RULE",
                "STOP_LOSS": "N/A",
                "TARGET": "N/A",
                "LUNCH_BLOCKED_ORIGINAL_SIGNAL": signal.get("SIGNAL"),
                "REASON": lunch_reason,
            })
            record_paper_trade(market_data, blocked_signal, None)
            return

        signal["LUNCH_TRADE_ALLOWED"] = True
        signal["LUNCH_REASON"] = lunch_reason
        table("LUNCH STRONG SETUP ALLOWED", [
            ("Signal", signal.get("SIGNAL")),
            ("Score", signal.get("SETUP_SCORE", 0)),
            ("ADX", signal.get("ADX_14", "N/A")),
            ("Reason", lunch_reason),
            ("Action", "Trade can continue through normal risk/depth checks."),
        ])

    if is_after_no_new_trade_time() and signal.get("SIGNAL") in {"BUY", "SELL"}:
        section("NO NEW TRADE WINDOW")
        table("SIGNAL BLOCKED", [
            ("Signal", signal.get("SIGNAL")),
            ("Reason", f"Fresh trades are blocked after {config.NO_NEW_TRADE_AFTER}."),
            ("Action", "No position opened. This prevents overnight risk."),
        ])
        signal = {
            "SIGNAL": "HOLD",
            "REASON": f"Blocked by no-new-trade rule after {config.NO_NEW_TRADE_AFTER}.",
            "CONFIDENCE": "RULE",
            "STOP_LOSS": "N/A",
            "TARGET": "N/A",
        }

    # Step 5: Act on signal (PAPER TRADE MODE - no real orders yet!)
    section("TRADE DECISION")
    option_trade = analyze_option_chain_for_trade(
        fyers,
        market_data["symbol"],
        signal.get("SIGNAL"),
        market_data["last_price"],
        strategy_signal=strategy_signal,
    )

    if option_trade and is_expiry_day_new_trade_blocked(option_trade.get("expiry")):
        reason = (
            f"Fresh same-expiry option trades are blocked after "
            f"{config.EXPIRY_DAY_NO_NEW_TRADE_AFTER} on expiry day."
        )
        option_trade["status"] = "BLOCKED_EXPIRY_DAY_TIME"
        option_trade["quantity"] = 0
        section("EXPIRY DAY SAFETY")
        table("SIGNAL BLOCKED", [
            ("Signal", signal.get("SIGNAL")),
            ("Option", option_trade.get("display_symbol", option_trade.get("option_symbol"))),
            ("Expiry", option_trade.get("expiry")),
            ("Rule Time", config.EXPIRY_DAY_NO_NEW_TRADE_AFTER),
            ("Reason", reason),
            ("Action", "No fresh buy opened. Existing open trades still follow target/SL."),
        ])
        blocked_signal = {
            "SIGNAL": "HOLD",
            "REASON": reason,
            "CONFIDENCE": "RULE",
            "STOP_LOSS": "N/A",
            "TARGET": "N/A",
        }
        record_paper_trade(market_data, blocked_signal, option_trade)
        return

    if option_trade:
        chain_allowed, option_trade = apply_option_chain_decision(option_trade, signal, market_data)
        if not chain_allowed:
            return

        if lunch_window and signal.get("LUNCH_TRADE_ALLOWED"):
            option_trade["lunch_trade"] = True
            option_trade["lunch_reason"] = signal.get("LUNCH_REASON")

        if is_opening_settle_window() and getattr(config, "DYNAMIC_OPENING_FILTER_ENABLED", True):
            spread_pct = float(option_trade.get("bid_ask_spread_percent") or 0)
            max_opening_spread = float(getattr(config, "OPENING_MAX_BID_ASK_SPREAD_PERCENT", 0.8) or 0.8)
            if spread_pct > max_opening_spread:
                table("OPENING LIQUIDITY FILTER", [
                    ("Option", option_trade.get("display_symbol", option_trade.get("option_symbol"))),
                    ("Spread %", f"{spread_pct}%"),
                    ("Max Allowed", f"{max_opening_spread}%"),
                    ("Reason", f"Opening order book spread {spread_pct}% exceeds {max_opening_spread}% limit."),
                    ("Action", "Waiting for institutional liquidity to settle book before entry."),
                ])
                record_paper_trade(market_data, signal, {**option_trade, "status": "BLOCKED_OPENING_SPREAD", "quantity": 0})
                return

        cooldown_allowed, cooldown_reason = check_same_underlying_sl_cooldown(option_trade, signal=signal)
        if not cooldown_allowed:
            table("SL COOLDOWN GUARD", [
                ("Signal", signal.get("SIGNAL")),
                ("Option", option_trade.get("display_symbol", option_trade.get("option_symbol"))),
                ("Reason", cooldown_reason),
                ("Action", "No re-entry in same index/direction yet."),
            ])
            record_paper_trade(market_data, signal, {**option_trade, "status": "BLOCKED_SL_COOLDOWN", "quantity": 0})
            return

        if signal.get("MARKET_REGIME"):
            option_trade["market_regime"] = signal.get("MARKET_REGIME")
        if signal.get("MARKET_REGIME_ACTION"):
            option_trade["market_regime_action"] = signal.get("MARKET_REGIME_ACTION")
        if signal.get("MARKET_REGIME_REASON"):
            option_trade["market_regime_reason"] = signal.get("MARKET_REGIME_REASON")
        if signal.get("ADX_14") is not None:
            option_trade["adx_14"] = signal.get("ADX_14")
        if signal.get("SETUP_SCORE") is not None:
            option_trade["setup_score"] = signal.get("SETUP_SCORE")

        option_trade = apply_position_sizing(option_trade, balance)
        regime_time_allowed, option_trade = apply_regime_and_time_gate(option_trade, signal, market_data)
        if not regime_time_allowed:
            return
        if option_trade.get("status") == "PAPER_SMALL_SIZE":
            option_trade = reduce_trade_size(
                option_trade,
                config.OPTION_CHAIN_SMALL_SIZE_PERCENT,
                "Option chain confidence reduced-size trade",
            )
            option_trade["option_chain_size_percent"] = config.OPTION_CHAIN_SMALL_SIZE_PERCENT
        quality_allowed, signal, option_trade = apply_trade_quality_gate(signal, option_trade, market_data)
        if not quality_allowed:
            return

        if lunch_window and signal.get("LUNCH_TRADE_ALLOWED") and config.LUNCH_TRADE_SIZE_PERCENT < 100:
            option_trade = reduce_lunch_trade_size(option_trade)
            option_trade["lunch_trade"] = True
            option_trade["lunch_size_percent"] = config.LUNCH_TRADE_SIZE_PERCENT
            option_trade["lunch_reason"] = signal.get("LUNCH_REASON")
            table("LUNCH TRADE SIZE", [
                ("Option", option_trade.get("display_symbol", option_trade.get("option_symbol"))),
                ("Size", f"{config.LUNCH_TRADE_SIZE_PERCENT}%"),
                ("Reason", signal.get("LUNCH_REASON")),
            ])

        if single_index_trade:
            option_trade = reduce_single_index_trade_size(option_trade)
            option_trade["single_index_trade"] = True
            option_trade["single_index_size_percent"] = config.SINGLE_INDEX_TRADE_SIZE_PERCENT
            table("SINGLE-INDEX TRADE", [
                ("Option", option_trade.get("display_symbol", option_trade.get("option_symbol"))),
                ("Size", f"{config.SINGLE_INDEX_TRADE_SIZE_PERCENT}%"),
                ("Reason", strategy_signal.get("REASON")),
            ])

        if not risk_allowed:
            blocked_trade = {
                **option_trade,
                "status": "BLOCKED_DAILY_RISK",
                "risk_block_reason": risk_reason,
            }
            table("ANALYSIS-ONLY SIGNAL", [
                ("Signal", signal.get("SIGNAL")),
                ("Option", blocked_trade.get("display_symbol", blocked_trade.get("option_symbol"))),
                ("Lots", blocked_trade.get("lots")),
                ("Quantity", blocked_trade.get("quantity")),
                ("Entry", f"Rs {blocked_trade.get('entry_price')}"),
                ("Target", f"Rs {blocked_trade.get('target')}"),
                ("Stop Loss", f"Rs {blocked_trade.get('stop_loss')}"),
                ("Required Amount", f"Rs {blocked_trade.get('estimated_required_amount')}"),
                ("Risk Amount", f"Rs {blocked_trade.get('estimated_risk_amount')}"),
                ("Target 1", f"Rs {blocked_trade.get('target_1')}"),
                ("Target 2", f"Rs {blocked_trade.get('target_2')}"),
                ("Final Target", f"Rs {blocked_trade.get('target_3', blocked_trade.get('target'))}"),
                ("Blocked By", risk_reason),
                ("Action", "Logged only. No paper/live trade opened."),
            ])
            record_paper_trade(market_data, signal, blocked_trade)
            return

        option_trade, learning_reasons = apply_learning_adjustments(option_trade, signal)
        if learning_reasons:
            table("LEARNING ADJUSTMENT", [
                ("Signal", signal.get("SIGNAL")),
                ("Option", option_trade.get("display_symbol", option_trade.get("option_symbol"))),
                ("Lots", option_trade.get("lots")),
                ("Quantity", option_trade.get("quantity")),
                ("Target 1", f"Rs {option_trade.get('target_1')}"),
                ("Target 2", f"Rs {option_trade.get('target_2')}"),
                ("Final Target", f"Rs {option_trade.get('target_3', option_trade.get('target'))}"),
                ("Mode", option_trade.get("learning_exit_mode", "SIZE_OR_RISK_ADJUSTED")),
                ("Reason", " | ".join(learning_reasons)),
                (
                    "Action",
                    "Trade blocked by learning guard."
                    if option_trade.get("status") == "BLOCKED_LEARNING_GUARD"
                    else "Trade is still allowed, but exit/size is adjusted from history.",
                ),
            ])
            if option_trade.get("status") == "BLOCKED_LEARNING_GUARD":
                if getattr(config, "AGGRESSIVE_IGNORE_SOFT_BLOCKS", False):
                    option_trade["status"] = "AGGRESSIVE_LEARNING_OVERRIDE"
                    option_trade["aggressive_learning_override"] = True
                    table("AGGRESSIVE LEARNING OVERRIDE", [
                        ("Signal", signal.get("SIGNAL")),
                        ("Option", option_trade.get("display_symbol", option_trade.get("option_symbol"))),
                        ("Reason", option_trade.get("learning_adjustment_reason")),
                        ("Action", "Continuing because aggressive trader mode is enabled."),
                    ])
                else:
                    table("LEARNING GUARD BLOCK", [
                        ("Signal", signal.get("SIGNAL")),
                        ("Option", option_trade.get("display_symbol", option_trade.get("option_symbol"))),
                        ("Reason", option_trade.get("learning_adjustment_reason")),
                        ("Action", "Logged only. No paper/live trade opened."),
                    ])
                    record_paper_trade(market_data, signal, option_trade)
                    return

        learning_allowed, option_trade = apply_post_learning_score_gate(option_trade, signal, market_data)
        if not learning_allowed:
            return

        if option_trade.get("market_depth_block_reason"):
            if (
                getattr(config, "AGGRESSIVE_ALLOW_MARKET_DEPTH_BLOCK", False)
                and signal.get("SIGNAL") in {"BUY", "SELL"}
            ):
                option_trade["aggressive_depth_override"] = True
                option_trade["sizing_reason"] = (
                    f"{option_trade.get('sizing_reason', '')} Aggressive mode ignored market depth block."
                ).strip()
                table("AGGRESSIVE MARKET DEPTH OVERRIDE", [
                    ("Option", option_trade.get("display_symbol", option_trade.get("option_symbol"))),
                    ("Best Bid", f"Rs {option_trade.get('best_bid')}"),
                    ("Best Ask", f"Rs {option_trade.get('best_ask')}"),
                    ("Spread", f"{option_trade.get('bid_ask_spread_percent')}%"),
                    ("Reason", option_trade.get("market_depth_block_reason")),
                    ("Action", "Continuing because aggressive trader mode is enabled."),
                ])
            else:
                blocked_trade = {
                    **option_trade,
                    "status": "BLOCKED_MARKET_DEPTH",
                    "quantity": 0,
                }
                table("MARKET DEPTH BLOCK", [
                    ("Option", blocked_trade.get("display_symbol", blocked_trade.get("option_symbol"))),
                    ("Best Bid", f"Rs {blocked_trade.get('best_bid')}"),
                    ("Best Ask", f"Rs {blocked_trade.get('best_ask')}"),
                    ("Spread", f"{blocked_trade.get('bid_ask_spread_percent')}%"),
                    ("Ask Qty", blocked_trade.get("ask_qty")),
                    ("Reason", blocked_trade.get("market_depth_block_reason")),
                    ("Action", "No trade opened because option depth/liquidity is unsafe."),
                ])
                record_paper_trade(market_data, signal, blocked_trade)
                return

        open_trades = load_open_trades()
        if open_trades:
            if config.TRADING_MODE != "PAPER" and not config.PAPER_TRADE:
                option_trade = reduce_second_trade_size(option_trade)
            allowed, reason = additional_trade_check(open_trades, option_trade, news, balance)
            if not allowed:
                table("SECOND TRADE BLOCKED", [
                    ("Signal", signal.get("SIGNAL")),
                    ("Option", option_trade.get("display_symbol", option_trade.get("option_symbol"))),
                    ("Open Trades", len(open_trades)),
                    ("Reason", reason),
                ])
                record_paper_trade(market_data, signal, {**option_trade, "status": "BLOCKED_SECOND_TRADE", "quantity": 0})
                return
            table("SECOND TRADE APPROVED", [
                ("Option", option_trade.get("display_symbol", option_trade.get("option_symbol"))),
                ("Open Trades", len(open_trades)),
                ("Size", f"{config.SECOND_TRADE_SIZE_PERCENT}%"),
                ("Reason", reason),
            ])

    if signal.get('SIGNAL') == 'BUY':
        if int(option_trade.get("quantity") or 0) <= 0:
            table("BUY SIGNAL BLOCKED", [
                ("Signal", "BUY"),
                ("Option", option_trade["option_symbol"]),
                ("Reason", option_trade.get("sizing_reason")),
            ])
            record_paper_trade(market_data, signal, option_trade)
            return

        explanation = build_trade_explanation(market_data, signal, option_trade)
        table("BUY PAPER TRADE SETUP", [
                ("Signal", "BUY"),
                ("Order Action", option_trade.get("order_action")),
                ("Confidence", signal.get("CONFIDENCE")),
                ("Option", option_trade.get("display_symbol", option_trade["option_symbol"])),
            ("Fyers Symbol", option_trade["option_symbol"]),
            ("Selection", option_trade.get("selection_mode")),
            ("Regime Sizing", option_trade.get("regime_sizing_mode", "NORMAL_BALANCED")),
            ("Entry", f"Rs {option_trade['entry_price']}"),
            ("Lot Size", option_trade.get("lot_size")),
            ("Lots", option_trade.get("lots")),
            ("Quantity", option_trade["quantity"]),
            ("Required Amount", f"Rs {option_trade['estimated_required_amount']}"),
            ("Risk Amount", f"Rs {option_trade['estimated_risk_amount']}"),
            ("Target 1", f"Rs {option_trade.get('target_1')} ({option_trade.get('target_1_percent')}%)"),
            ("Target 2", f"Rs {option_trade.get('target_2')} ({option_trade.get('target_2_percent')}%)"),
            ("Final Target", f"Rs {option_trade.get('target_3', option_trade['target'])} ({option_trade.get('target_3_percent', option_trade.get('target_percent'))}%)"),
            ("Stop Loss", f"Rs {option_trade['stop_loss']}"),
            ("Stop Loss %", f"{option_trade.get('stop_loss_percent')}%"),
            ("Exit Profile", option_trade.get("exit_profile")),
            ("Reason", signal.get("REASON")),
        ])
        print_trade_explanation(explanation)
        if config.TRADING_MODE == "PAPER" or config.PAPER_TRADE:
            print("\nPAPER TRADE MODE: No real order placed")
        record_paper_trade(market_data, signal, option_trade, explanation)
        execute_trade(fyers, option_trade, signal, balance)

    elif signal.get('SIGNAL') == 'SELL':
        if int(option_trade.get("quantity") or 0) <= 0:
            table("SELL SIGNAL BLOCKED", [
                ("Signal", "SELL"),
                ("Order Action", option_trade.get("order_action")),
                ("Option", option_trade["option_symbol"]),
                ("Reason", option_trade.get("sizing_reason")),
            ])
            record_paper_trade(market_data, signal, option_trade)
            return

        explanation = build_trade_explanation(market_data, signal, option_trade)
        table("SELL PAPER TRADE SETUP", [
                ("Signal", "SELL"),
                ("Order Action", option_trade.get("order_action")),
                ("Confidence", signal.get("CONFIDENCE")),
                ("Option", option_trade.get("display_symbol", option_trade["option_symbol"])),
                ("Fyers Symbol", option_trade["option_symbol"]),
            ("Selection", option_trade.get("selection_mode")),
            ("Entry", f"Rs {option_trade['entry_price']}"),
            ("Lot Size", option_trade.get("lot_size")),
            ("Lots", option_trade.get("lots")),
            ("Quantity", option_trade["quantity"]),
            ("Required Amount", f"Rs {option_trade['estimated_required_amount']}"),
            ("Risk Amount", f"Rs {option_trade['estimated_risk_amount']}"),
            ("Target 1", f"Rs {option_trade.get('target_1')} ({option_trade.get('target_1_percent')}%)"),
            ("Target 2", f"Rs {option_trade.get('target_2')} ({option_trade.get('target_2_percent')}%)"),
            ("Final Target", f"Rs {option_trade.get('target_3', option_trade['target'])} ({option_trade.get('target_3_percent', option_trade.get('target_percent'))}%)"),
            ("Stop Loss", f"Rs {option_trade['stop_loss']}"),
            ("Stop Loss %", f"{option_trade.get('stop_loss_percent')}%"),
            ("Exit Profile", option_trade.get("exit_profile")),
            ("Reason", signal.get("REASON")),
        ])
        print_trade_explanation(explanation)
        if config.TRADING_MODE == "PAPER" or config.PAPER_TRADE:
            print("\nPAPER TRADE MODE: No real order placed")
        record_paper_trade(market_data, signal, option_trade, explanation)
        execute_trade(fyers, option_trade, signal, balance)

    else:
        table("HOLD DECISION", [
            ("Signal", "HOLD"),
            ("Confidence", signal.get("CONFIDENCE")),
            ("Reason", signal.get("REASON")),
            ("Action", "No trade today"),
        ])
        record_paper_trade(market_data, signal, option_trade)


def run_bot():
    log("Starting Trading Bot...")
    print("=" * 40)
    dashboard_url = None

    if config.DASHBOARD_ENABLED:
        try:
            dashboard_url = start_dashboard_background()
            table("DASHBOARD", [
                ("URL", dashboard_url),
                ("Status", "Running with main.py"),
                ("Refresh", "Auto-refresh every 5 seconds"),
            ])
        except OSError as error:
            table("DASHBOARD", [
                ("Status", "Could not start dashboard"),
                ("Reason", error),
                ("Action", "Close old dashboard terminals or run dashboard.py manually."),
            ])

    drive_sync_thread = start_csv_drive_sync_background()
    if drive_sync_thread:
        table("GOOGLE DRIVE CSV SYNC", [
            ("Status", "Enabled"),
            ("Remote", config.GOOGLE_DRIVE_RCLONE_REMOTE),
            ("Folder ID", config.GOOGLE_DRIVE_FOLDER_ID),
            ("Interval", f"{config.GOOGLE_DRIVE_SYNC_INTERVAL_SECONDS} seconds"),
            ("Move After Upload", config.GOOGLE_DRIVE_SYNC_MOVE_AFTER_UPLOAD),
        ])

    # Step 1: Login to Fyers
    while True:
        try:
            fyers = login(dashboard_url=dashboard_url)
            break
        except (ConnectionError, Timeout, ConnectionResetError, TimeoutError) as error:
            log(f"Fyers login/profile network issue: {error}")
            log(f"Retrying login in {config.FYERS_LOGIN_RETRY_SLEEP} seconds.")
            time.sleep(config.FYERS_LOGIN_RETRY_SLEEP)
        except KeyboardInterrupt:
            log("Bot stopped by user.")
            return

    # Step 2: Check balance
    balance = check_balance_or_exit(fyers)
    if balance is None:
        return

    if not config.RUN_CONTINUOUSLY:
        analyze_once(fyers, balance)
        return

    print("\nContinuous analysis mode is ON.")
    print(f"Fresh trade analysis interval: {config.ANALYSIS_INTERVAL_SECONDS} seconds")
    print(f"Open trade monitor interval: {config.OPEN_TRADE_MONITOR_INTERVAL_SECONDS} seconds")
    print("Press Ctrl+C to stop.")
    last_fresh_analysis_at = 0

    while True:
        try:
            apply_runtime_settings(config)
            has_open_trade = bool(load_open_trades())
            now = time.monotonic()
            allow_fresh_analysis = (not has_open_trade) or (now - last_fresh_analysis_at >= config.ANALYSIS_INTERVAL_SECONDS)
            analyze_once(fyers, balance, allow_fresh_analysis=allow_fresh_analysis)
            if allow_fresh_analysis:
                last_fresh_analysis_at = now

            if load_open_trades():
                sleep_seconds = config.OPEN_TRADE_MONITOR_INTERVAL_SECONDS
                log("Open trade active. Waiting for next target/SL check...")
            else:
                sleep_seconds = config.ANALYSIS_INTERVAL_SECONDS
                log("No open trade. Waiting for next fresh analysis...")
            time.sleep(sleep_seconds)
        except (ConnectionError, Timeout, ConnectionResetError) as error:
            log(f"Network/Fyers connection issue: {error}")
            log("Bot will retry on the next analysis cycle.")
            time.sleep(config.OPEN_TRADE_MONITOR_INTERVAL_SECONDS if load_open_trades() else config.ANALYSIS_INTERVAL_SECONDS)
        except KeyboardInterrupt:
            log("Bot stopped by user.")
            break
        except Exception as error:
            import traceback
            tb = traceback.format_exc()
            log(f"Unexpected error in cycle: {error}\n{tb}")
            try:
                from database import db_log_event
                db_log_event("CRITICAL", "main_loop", str(error), {"traceback": tb})
            except Exception:
                pass
            time.sleep(10)

if __name__ == "__main__":
    run_bot()
