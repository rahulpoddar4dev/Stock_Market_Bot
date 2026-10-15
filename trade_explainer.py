# trade_explainer.py - Explain paper trade risk and reward


def build_trade_explanation(market_data: dict, signal: dict, option_trade: dict) -> dict:
    """Build a beginner-friendly explanation for a paper option trade."""
    entry_price = float(option_trade["entry_price"])
    stop_loss = float(option_trade["stop_loss"])
    dynamic_exit_enabled = bool(option_trade.get("dynamic_exit_enabled"))
    target = float(option_trade.get("target") or 0)
    target_1 = float(option_trade.get("target_1") or 0)
    target_2 = float(option_trade.get("target_2") or 0)
    quantity = int(option_trade["quantity"])

    max_profit = None if dynamic_exit_enabled else round((target - entry_price) * quantity, 2)
    max_loss = round((entry_price - stop_loss) * quantity, 2)
    profit_percent = None if dynamic_exit_enabled else round(((target - entry_price) / entry_price) * 100, 2)
    loss_percent = round(((entry_price - stop_loss) / entry_price) * 100, 2)

    underlying = option_trade.get("underlying") or market_data.get("symbol") or "INDEX"

    if signal.get("SIGNAL") == "BUY":
        trade_reason = f"Bullish signal, so the bot selected an ATM {underlying} call option."
        failure_reason = f"This can lose if {underlying} reverses down, stays flat, or option premium decays."
    else:
        trade_reason = f"Bearish signal, so the bot selected an ATM {underlying} put option."
        failure_reason = f"This can lose if {underlying} reverses up, stays flat, or option premium decays."

    return {
        "trade_reason": trade_reason,
        "signal_reason": signal.get("REASON"),
        "sizing_reason": option_trade.get("sizing_reason"),
        "expected_profit": max_profit,
        "expected_profit_percent": profit_percent,
        "max_loss": max_loss,
        "max_loss_percent": loss_percent,
        "when_profit": (
            "Dynamic exit: fixed targets are disabled. Bot will decide partial/full sell from price action, "
            "peak profit pullback, market depth, and momentum fade."
        ) if dynamic_exit_enabled else (
            f"Stage plan: T1 Rs {target_1} books partial profit and moves SL to +3%, "
            f"T2 Rs {target_2} books partial profit and moves SL to +15%, "
            f"T3 Rs {target} exits all remaining quantity."
        ) if target_1 and target_2 else f"Profit target if option price reaches Rs {target}.",
        "when_loss": f"Stop loss if option price falls to Rs {stop_loss}.",
        "failure_reason": failure_reason,
        "risk_reward": round(max_profit / max_loss, 2) if max_profit is not None and max_loss else None,
    }


def print_trade_explanation(explanation: dict) -> None:
    print("\nWHY THIS PAPER TRADE?")
    print(f"Reason: {explanation['trade_reason']}")
    print(f"Signal reason: {explanation['signal_reason']}")
    if explanation.get("sizing_reason"):
        print(f"Position sizing: {explanation['sizing_reason']}")
    if explanation["expected_profit"] is None:
        print("Expected profit: Dynamic, no fixed target")
    else:
        print(f"Expected profit: Rs {explanation['expected_profit']} ({explanation['expected_profit_percent']}%)")
    print(f"Maximum loss: Rs {explanation['max_loss']} ({explanation['max_loss_percent']}%)")
    print(f"When profit: {explanation['when_profit']}")
    print(f"When loss: {explanation['when_loss']}")
    print(f"Why it can fail: {explanation['failure_reason']}")
    print(f"Risk reward: {'Dynamic' if explanation['risk_reward'] is None else '1:' + str(explanation['risk_reward'])}")
