# execution_manager.py - Paper, semi-auto, and live execution controls

import config
from log_utils import log
from live_safety_gate import evaluate_live_safety_gate
from market_depth import depth_based_buy_limit_price
from order_manager import place_order, round_to_tick
from paper_fill_engine import apply_paper_entry_fill
from performance_report import print_performance_summary
from trade_history import append_trade_history
from trade_state import save_open_trade


def estimated_required_amount(option_trade: dict) -> float:
    return round(float(option_trade.get("estimated_required_amount") or 0), 2)


def entry_limit_price(option_trade: dict) -> float:
    depth_price = depth_based_buy_limit_price(option_trade)
    if depth_price:
        return depth_price

    entry_price = float(option_trade.get("entry_price") or 0)
    buffer_percent = config.ENTRY_LIMIT_BUFFER_PERCENT / 100
    return round_to_tick(entry_price * (1 + buffer_percent))


def execute_trade(fyers, option_trade: dict, signal: dict, balance: float):
    required_amount = estimated_required_amount(option_trade)
    print(f"\nEstimated required amount: Rs {required_amount}")

    if int(option_trade.get("quantity") or 0) <= 0:
        print(f"Order blocked: {option_trade.get('sizing_reason')}")
        return None

    if option_trade.get("market_depth_block_reason"):
        print(f"Order blocked by market depth: {option_trade.get('market_depth_block_reason')}")
        option_trade["status"] = "BLOCKED_MARKET_DEPTH"
        return None

    if config.TRADING_MODE == "PAPER" or config.PAPER_TRADE:
        option_trade, fill_result = apply_paper_entry_fill(option_trade)
        if fill_result.get("status") == "MISSED":
            missed_trade = {
                **option_trade,
                "mode": "PAPER",
                "status": "MISSED_PAPER_FILL",
                "paper_missed_fill_reason": fill_result.get("reason"),
            }
            append_trade_history("ENTRY", missed_trade, result="MISSED_FILL", reason=fill_result.get("reason"))
            log(f"PAPER mode: missed fill. {fill_result.get('reason')}")
            print()
            print_performance_summary()
            return None
        save_open_trade(option_trade, signal, mode="PAPER")
        log("PAPER mode: trade tracked locally. No real order placed.")
        print()
        print_performance_summary()
        return None

    if required_amount > balance:
        print("Live order blocked: available balance is lower than estimated option premium.")
        return None

    if config.MAX_TRADE_AMOUNT and required_amount > config.MAX_TRADE_AMOUNT:
        print("Live order blocked: estimated amount exceeds MAX_TRADE_AMOUNT.")
        return None

    if config.TRADING_MODE == "SEMI_AUTO":
        print("\nSEMI_AUTO mode: real order needs your confirmation.")
        print(f"Order: BUY {option_trade['quantity']} x {option_trade['option_symbol']}")
        print(f"Estimated premium: Rs {required_amount}")
        confirm = input("Type YES to place this REAL order: ").strip()
        if confirm != "YES":
            print("Order cancelled by user.")
            return None

    elif config.TRADING_MODE != "LIVE_AUTO":
        print(f"Unknown TRADING_MODE: {config.TRADING_MODE}. Order blocked.")
        return None

    if config.TRADING_MODE == "LIVE_AUTO" and not config.ENABLE_LIVE_AUTO:
        print("Live auto order blocked: ENABLE_LIVE_AUTO is False.")
        return None

    if config.TRADING_MODE == "LIVE_AUTO":
        safety_allowed, safety_reason, safety_metrics = evaluate_live_safety_gate()
        if not safety_allowed:
            print(f"Live auto order blocked: {safety_reason}")
            print(f"Safety metrics: {safety_metrics}")
            return None

    entry_order_type = config.ENTRY_ORDER_TYPE.upper()
    limit_price = entry_limit_price(option_trade) if entry_order_type == "LIMIT" else None
    option_trade["entry_order_type"] = entry_order_type
    option_trade["entry_order_price"] = limit_price or option_trade.get("entry_price")

    order_id = place_order(
        fyers,
        option_trade["option_symbol"],
        option_trade["trade_type"],
        option_trade["quantity"],
        exchange=option_trade.get("exchange"),
        order_type=entry_order_type,
        price=limit_price,
    )

    if order_id:
        save_open_trade(
            option_trade,
            signal,
            mode=config.TRADING_MODE,
            order_id=order_id,
            status="LIVE_PENDING",
        )
        print()
        print_performance_summary()

    return order_id
