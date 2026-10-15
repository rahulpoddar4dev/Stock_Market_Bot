import math

import config
from market_time import is_opening_settle_window


def get_dynamic_regime_limits(option_trade: dict, balance: float) -> tuple[float, float, str]:
    """Determine dynamic (fund_usage_percent, risk_percent_of_fund, regime_mode) from market regime."""
    if not getattr(config, "DYNAMIC_REGIME_SIZING_ENABLED", True):
        base_fund = float(getattr(config, "MAX_SINGLE_TRADE_FUND_USAGE_PERCENT", config.MAX_FUND_USAGE_PERCENT))
        base_risk = float(getattr(config, "MAX_RISK_PER_TRADE_PERCENT_OF_FUND", 5.0))
        return base_fund, base_risk, "STATIC"

    regime = str(option_trade.get("market_regime") or "").upper()
    regime_action = str(option_trade.get("market_regime_action") or "").upper()
    setup_score = float(option_trade.get("setup_score") or 0)
    adx = float(option_trade.get("adx_14") or 0)

    # 1. Trending / Strong Momentum (Clear directional market: scale up to 70% fund, 7.5% risk -> 3-4 lots)
    if (
        regime == "TRENDING"
        or regime_action == "ALLOW_BREAKOUT_CONTINUATION"
        or (adx >= float(getattr(config, "REGIME_TRENDING_ADX", 22)) and setup_score >= 70)
        or setup_score >= 80
    ):
        fund_percent = float(getattr(config, "REGIME_TRENDING_FUND_PERCENT", 70.0))
        risk_percent = float(getattr(config, "REGIME_TRENDING_RISK_PERCENT", 7.5))
        mode = "TRENDING_AGGRESSIVE"

    # 2. Sideways / Low Volatility / Struggling Chop (Defensive: 30% fund, 3.5% risk -> 1 lot)
    elif (
        regime in {"SIDEWAYS", "LOW_VOLATILITY"}
        or regime_action in {"SCALP_ONLY", "AVOID_OPTION_BUYING"}
        or (0 < adx <= float(getattr(config, "REGIME_SIDEWAYS_ADX", 18)) and regime != "")
    ):
        fund_percent = float(getattr(config, "REGIME_SIDEWAYS_FUND_PERCENT", 30.0))
        risk_percent = float(getattr(config, "REGIME_SIDEWAYS_RISK_PERCENT", 3.5))
        mode = "SIDEWAYS_DEFENSIVE"

    # 3. High Volatility / News Event (Defensive capital protection)
    elif regime in {"HIGH_VOLATILITY", "NEWS_EVENT"} or regime_action == "SMALL_SIZE":
        fund_percent = float(getattr(config, "REGIME_HIGH_VOL_FUND_PERCENT", 30.0))
        risk_percent = float(getattr(config, "REGIME_HIGH_VOL_RISK_PERCENT", 3.5))
        mode = "VOLATILITY_DEFENSIVE"

    # 4. Normal / Default (Standard 40% fund, 5.0% risk -> 1-2 lots)
    else:
        fund_percent = float(getattr(config, "REGIME_NORMAL_FUND_PERCENT", getattr(config, "MAX_SINGLE_TRADE_FUND_USAGE_PERCENT", config.MAX_FUND_USAGE_PERCENT)))
        risk_percent = float(getattr(config, "REGIME_NORMAL_RISK_PERCENT", getattr(config, "MAX_RISK_PER_TRADE_PERCENT_OF_FUND", 5.0)))
        mode = "NORMAL_BALANCED"

    return fund_percent, risk_percent, mode


def calculate_fund_usage_limit(balance: float, fund_percent: float | None = None) -> float:
    pct = fund_percent if fund_percent is not None else config.MAX_FUND_USAGE_PERCENT
    return float(balance) * (pct / 100)


def calculate_single_trade_fund_limit(balance: float, fund_percent: float | None = None) -> float:
    pct = fund_percent if fund_percent is not None else getattr(config, "MAX_SINGLE_TRADE_FUND_USAGE_PERCENT", config.MAX_FUND_USAGE_PERCENT)
    return float(balance) * (pct / 100)


def calculate_single_trade_hard_fund_limit(balance: float, fund_percent: float | None = None) -> float:
    soft_percent = fund_percent if fund_percent is not None else getattr(config, "MAX_SINGLE_TRADE_FUND_USAGE_PERCENT", config.MAX_FUND_USAGE_PERCENT)
    buffer_percent = getattr(config, "MAX_SINGLE_TRADE_FUND_USAGE_BUFFER_PERCENT", 0)
    return float(balance) * ((soft_percent + buffer_percent) / 100)


def calculate_trade_risk_limit(balance: float, risk_percent: float | None = None) -> float:
    if config.MAX_RISK_PER_TRADE and config.MAX_RISK_PER_TRADE > 0:
        return float(config.MAX_RISK_PER_TRADE)
    percent = risk_percent if risk_percent is not None else getattr(config, "MAX_RISK_PER_TRADE_PERCENT_OF_FUND", 5.0)
    return round(float(balance) * (percent / 100), 2)


def calculate_trade_risk_hard_limit(balance: float, risk_percent: float | None = None) -> float:
    base_limit = calculate_trade_risk_limit(balance, risk_percent=risk_percent)
    buffer_percent = float(getattr(config, "MAX_RISK_PER_TRADE_BUFFER_PERCENT", 10))
    return round(float(base_limit) * (1 + (buffer_percent / 100)), 2)


def apply_position_sizing(option_trade: dict, balance: float) -> dict:
    """Set quantity based on available fund percentage and risk limits."""
    sized_trade = dict(option_trade)
    entry_price = float(sized_trade["entry_price"])
    stop_loss = float(sized_trade["stop_loss"])
    lot_size = int(sized_trade.get("lot_size") or sized_trade["quantity"])

    amount_per_lot = entry_price * lot_size
    risk_per_lot = max(entry_price - stop_loss, 0) * lot_size

    fund_percent, risk_percent, regime_mode = get_dynamic_regime_limits(sized_trade, balance)

    total_fund_limit = calculate_fund_usage_limit(balance, fund_percent=max(fund_percent, config.MAX_FUND_USAGE_PERCENT))
    single_trade_fund_limit = calculate_single_trade_fund_limit(balance, fund_percent=fund_percent)
    single_trade_hard_fund_limit = calculate_single_trade_hard_fund_limit(balance, fund_percent=fund_percent)
    risk_limit = calculate_trade_risk_limit(balance, risk_percent=risk_percent)
    risk_hard_limit = calculate_trade_risk_hard_limit(balance, risk_percent=risk_percent)
    trade_cap = float(config.MAX_TRADE_AMOUNT)
    capital_limit = min(trade_cap, single_trade_hard_fund_limit) if trade_cap > 0 else single_trade_hard_fund_limit

    lots_by_amount = math.floor(capital_limit / amount_per_lot) if amount_per_lot else 0
    lots_by_risk = math.floor(risk_hard_limit / risk_per_lot) if risk_hard_limit > 0 and risk_per_lot else lots_by_amount
    lots = max(0, min(lots_by_amount, lots_by_risk))

    # Morning Opening Sizing Shield (09:20 - 09:30): Calibrate to test size to protect against opening wicks
    morning_shield_applied = False
    if is_opening_settle_window():
        morning_max_lots = int(getattr(config, "MORNING_OPENING_MAX_LOTS", 2))
        if lots > morning_max_lots:
            lots = morning_max_lots
            morning_shield_applied = True

    # Allow at least 1 lot if available balance covers the 1-lot premium
    # and 1-lot risk is within daily loss limit (ensures accounts can execute 1 lot)
    if lots == 0 and balance >= amount_per_lot:
        daily_loss_limit = float(balance) * (getattr(config, "MAX_DAILY_LOSS_PERCENT_OF_FUND", 12) / 100)
        if risk_per_lot <= daily_loss_limit:
            lots = 1

    sized_trade["amount_per_lot"] = round(amount_per_lot, 2)
    sized_trade["risk_per_lot"] = round(risk_per_lot, 2)
    sized_trade["available_balance"] = round(float(balance), 2)
    sized_trade["fund_usage_percent"] = fund_percent
    sized_trade["single_trade_fund_usage_percent"] = fund_percent
    sized_trade["total_fund_usage_percent"] = max(fund_percent, config.MAX_FUND_USAGE_PERCENT)
    sized_trade["fund_usage_limit"] = round(single_trade_fund_limit, 2)
    sized_trade["single_trade_fund_usage_limit"] = round(single_trade_fund_limit, 2)
    sized_trade["single_trade_fund_usage_buffer_percent"] = config.MAX_SINGLE_TRADE_FUND_USAGE_BUFFER_PERCENT
    sized_trade["single_trade_hard_fund_usage_limit"] = round(single_trade_hard_fund_limit, 2)
    sized_trade["total_fund_usage_limit"] = round(total_fund_limit, 2)
    sized_trade["risk_limit"] = round(risk_limit, 2)
    sized_trade["risk_hard_limit"] = round(risk_hard_limit, 2)
    sized_trade["risk_limit_percent_of_used_fund"] = config.MAX_RISK_PER_TRADE_PERCENT_OF_USED_FUND
    sized_trade["trade_cap"] = trade_cap if trade_cap > 0 else "NO_FIXED_CAP"
    sized_trade["max_lots_by_amount"] = lots_by_amount
    sized_trade["max_lots_by_risk"] = lots_by_risk
    sized_trade["lots"] = lots
    sized_trade["base_lots"] = lots
    sized_trade["morning_shield_applied"] = morning_shield_applied
    sized_trade["quantity"] = lots * lot_size
    sized_trade["estimated_required_amount"] = round(entry_price * sized_trade["quantity"], 2)
    sized_trade["estimated_risk_amount"] = round((entry_price - stop_loss) * sized_trade["quantity"], 2)
    sized_trade["regime_sizing_mode"] = regime_mode
    shield_note = f" (Morning shield: capped to {lots} test lots)" if morning_shield_applied else ""
    sized_trade["sizing_reason"] = (
        f"Quantity sized in {regime_mode} mode{shield_note} ({fund_percent}% fund target Rs {round(single_trade_fund_limit, 2)} "
        f"with {config.MAX_SINGLE_TRADE_FUND_USAGE_BUFFER_PERCENT}% lot buffer up to Rs {round(single_trade_hard_fund_limit, 2)}, "
        f"{risk_percent}% risk limit Rs {round(risk_limit, 2)} with buffer Rs {round(risk_hard_limit, 2)})."
    )

    if lots <= 0:
        sized_trade["status"] = "BLOCKED_RISK"
        sized_trade["sizing_reason"] = (
            f"Trade blocked: one lot needs Rs {round(amount_per_lot, 2)} premium "
            f"and Rs {round(risk_per_lot, 2)} risk, above {regime_mode} limits."
        )

    return sized_trade
