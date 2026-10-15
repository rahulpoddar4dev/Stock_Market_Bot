# trade_quality.py - Trade quality scoring and entry classification

from __future__ import annotations

from datetime import datetime

import config


def pct_distance(a: float, b: float) -> float:
    if not b:
        return 0.0
    return abs(float(a) - float(b)) / abs(float(b)) * 100


def candle_is_bullish(candle: dict) -> bool:
    return float(candle.get("close") or 0) > float(candle.get("open") or 0)


def candle_is_bearish(candle: dict) -> bool:
    return float(candle.get("close") or 0) < float(candle.get("open") or 0)


def detect_entry_mode(candles: list[dict], latest: dict, signal: str) -> dict:
    if signal not in {"BUY", "SELL"}:
        return {
            "entry_mode": "NONE",
            "entry_freshness": "NONE",
            "entry_reason": "No trade signal.",
            "fresh_cross": False,
            "retest_entry": False,
            "continuation_entry": False,
            "breakout_quality_score": 0,
        }

    latest_index = _latest_index(candles, latest)
    recent = candles[max(0, latest_index - 3):latest_index + 1]
    previous = recent[-2] if len(recent) >= 2 else None
    fresh_cross = _fresh_cross(recent, signal)
    retest = _retest_entry(recent, signal)
    continuation = _continuation_entry(latest, signal)
    breakout_quality = _breakout_quality(candles, latest, signal)

    if fresh_cross:
        entry_mode = "FRESH_BREAKOUT"
        freshness = "FRESH"
        reason = "EMA/VWAP cross happened in the last configured candles."
    elif retest:
        entry_mode = "RETEST"
        freshness = "RETEST"
        reason = "Price retested EMA9/VWAP after breakout and closed with signal direction."
    elif continuation:
        entry_mode = "CONTINUATION"
        freshness = "OLD_BUT_STRONG"
        reason = "Trend is already active, ADX/score are strong, and price is not overextended."
    else:
        entry_mode = "OLD_SIGNAL_WAIT_RETEST"
        freshness = "OLD"
        reason = "Signal is not fresh; wait for retest or strong continuation."

    if previous:
        reason = f"{reason} Previous close {previous.get('close')}, latest close {latest.get('close')}."

    return {
        "entry_mode": entry_mode,
        "entry_freshness": freshness,
        "entry_reason": reason,
        "fresh_cross": fresh_cross,
        "retest_entry": retest,
        "continuation_entry": continuation,
        "breakout_quality_score": breakout_quality,
    }


def is_price_overextended(latest: dict, signal: str) -> tuple[bool, str, dict]:
    close = float(latest.get("close") or 0)
    ema_9 = float(latest.get("ema_9") or 0)
    vwap = float(latest.get("vwap") or 0)
    ema_distance = pct_distance(close, ema_9)
    vwap_distance = pct_distance(close, vwap)
    max_ema = float(getattr(config, "MAX_ENTRY_DISTANCE_FROM_EMA9_PERCENT", 0.50))
    max_vwap = float(getattr(config, "MAX_ENTRY_DISTANCE_FROM_VWAP_PERCENT", 1.00))

    if signal == "BUY":
        overextended = (close > ema_9 and ema_distance > max_ema) or (close > vwap and vwap_distance > max_vwap)
    elif signal == "SELL":
        overextended = (close < ema_9 and ema_distance > max_ema) or (close < vwap and vwap_distance > max_vwap)
    else:
        overextended = False

    metrics = {
        "entry_distance_from_ema9_percent": round(ema_distance, 3),
        "entry_distance_from_vwap_percent": round(vwap_distance, 3),
        "max_entry_distance_from_ema9_percent": max_ema,
        "max_entry_distance_from_vwap_percent": max_vwap,
    }
    reason = (
        f"Entry distance EMA9 {metrics['entry_distance_from_ema9_percent']}% "
        f"(max {max_ema}%), VWAP {metrics['entry_distance_from_vwap_percent']}% "
        f"(max {max_vwap}%)."
    )
    return overextended, reason, metrics


def initial_trade_quality_score(
    signal_data: dict,
    latest: dict,
    entry_info: dict,
    pattern_summary: dict,
) -> dict:
    signal = signal_data.get("SIGNAL")
    if signal not in {"BUY", "SELL"}:
        return _score_result(0, {}, "No trade signal.")

    trend_component = _trend_strength_component(signal_data)
    freshness_component = _freshness_component(entry_info)
    entry_component = min(20, float(entry_info.get("breakout_quality_score") or 0))
    candle_component = _candle_pattern_component(pattern_summary, signal)

    components = {
        "entry_freshness": freshness_component,
        "trend_strength": trend_component,
        "retest_breakout_quality": entry_component,
        "option_premium_confirmation": 0,
        "market_context": 0,
        "candle_pattern_support": candle_component,
    }
    reason = (
        f"Initial quality: freshness {freshness_component}/20, trend {trend_component}/20, "
        f"entry quality {entry_component}/20, candle support {candle_component}/10."
    )
    return _score_result(sum(components.values()), components, reason)


def add_market_context_to_quality(signal: dict, market_context: dict) -> dict:
    updated = dict(signal)
    components = _components_from_signal(updated)
    context_component = _market_context_component(updated.get("SIGNAL"), market_context)
    components["market_context"] = context_component
    return _write_quality(updated, components, f"Market context component {context_component}/15.")


def add_option_premium_to_quality(signal: dict, option_trade: dict) -> dict:
    updated = dict(signal)
    components = _components_from_signal(updated)
    option_component, reason = _option_premium_component(option_trade)
    components["option_premium_confirmation"] = option_component
    return _write_quality(updated, components, reason)


def quality_decision(score: float) -> tuple[str, float, str]:
    if score >= float(config.TRADE_QUALITY_NORMAL_THRESHOLD):
        return "NORMAL_TRADE", 100.0, f"Quality score {score} >= {config.TRADE_QUALITY_NORMAL_THRESHOLD}."
    if score >= float(config.TRADE_QUALITY_SMALL_SIZE_THRESHOLD):
        return "SMALL_SIZE_TRADE", float(config.TRADE_QUALITY_SMALL_SIZE_PERCENT), (
            f"Quality score {score} is {config.TRADE_QUALITY_SMALL_SIZE_THRESHOLD}-"
            f"{config.TRADE_QUALITY_NORMAL_THRESHOLD - 1}; reduced size trade."
        )
    if score >= float(config.TRADE_QUALITY_LOG_ONLY_THRESHOLD):
        return "PAPER_LOG_ONLY", 0.0, (
            f"Quality score {score} is {config.TRADE_QUALITY_LOG_ONLY_THRESHOLD}-"
            f"{config.TRADE_QUALITY_SMALL_SIZE_THRESHOLD - 1}; log only."
        )
    return "NO_TRADE", 0.0, f"Quality score {score} < {config.TRADE_QUALITY_LOG_ONLY_THRESHOLD}; no trade."


def _latest_index(candles: list[dict], latest: dict) -> int:
    try:
        return candles.index(latest)
    except ValueError:
        return len(candles) - 1


def _fresh_cross(recent: list[dict], signal: str) -> bool:
    lookback = int(getattr(config, "FRESH_SIGNAL_CANDLES", 2) or 2)
    pairs = list(zip(recent[-lookback - 1:-1], recent[-lookback:]))
    for previous, current in pairs:
        prev_close = float(previous.get("close") or 0)
        curr_close = float(current.get("close") or 0)
        prev_ema = float(previous.get("ema_9") or 0)
        curr_ema = float(current.get("ema_9") or 0)
        prev_vwap = float(previous.get("vwap") or 0)
        curr_vwap = float(current.get("vwap") or 0)
        if signal == "BUY" and (prev_close <= prev_ema or prev_close <= prev_vwap) and curr_close > curr_ema and curr_close > curr_vwap:
            return True
        if signal == "SELL" and (prev_close >= prev_ema or prev_close >= prev_vwap) and curr_close < curr_ema and curr_close < curr_vwap:
            return True
    return False


def _retest_entry(recent: list[dict], signal: str) -> bool:
    if len(recent) < 2:
        return False
    latest = recent[-1]
    previous = recent[-2]
    close = float(latest.get("close") or 0)
    low = float(latest.get("low") or 0)
    high = float(latest.get("high") or 0)
    ema_9 = float(latest.get("ema_9") or 0)
    vwap = float(latest.get("vwap") or 0)
    prev_close = float(previous.get("close") or 0)
    prev_ema = float(previous.get("ema_9") or 0)
    prev_vwap = float(previous.get("vwap") or 0)
    tolerance = float(getattr(config, "RETEST_TOUCH_TOLERANCE_PERCENT", 0.08) or 0.08) / 100

    if signal == "BUY":
        prior_breakout = prev_close > prev_ema and prev_close > prev_vwap
        touched = low <= ema_9 * (1 + tolerance) or low <= vwap * (1 + tolerance)
        return prior_breakout and touched and close > ema_9 and close > vwap and candle_is_bullish(latest)

    prior_breakdown = prev_close < prev_ema and prev_close < prev_vwap
    touched = high >= ema_9 * (1 - tolerance) or high >= vwap * (1 - tolerance)
    return prior_breakdown and touched and close < ema_9 and close < vwap and candle_is_bearish(latest)


def _continuation_entry(latest: dict, signal: str) -> bool:
    close = float(latest.get("close") or 0)
    ema_9 = float(latest.get("ema_9") or 0)
    vwap = float(latest.get("vwap") or 0)
    adx = float(latest.get("adx_14") or 0)
    overextended, _, _ = is_price_overextended(latest, signal)
    min_adx = float(getattr(config, "CONTINUATION_MIN_ADX", 16.0))
    if overextended or adx < min_adx:
        return False
    if signal == "BUY":
        return close > ema_9 and close > vwap
    return close < ema_9 and close < vwap


def _breakout_quality(candles: list[dict], latest: dict, signal: str) -> float:
    lookback = int(getattr(config, "BREAKOUT_LOOKBACK_CANDLES", 3) or 3)
    latest_index = _latest_index(candles, latest)
    previous = candles[max(0, latest_index - lookback):latest_index]
    if len(previous) < lookback:
        return 5.0
    close = float(latest.get("close") or 0)
    atr = float(latest.get("atr_14") or 0)
    if signal == "BUY":
        breakout_level = max(float(item.get("high") or 0) for item in previous)
        distance = max(0, close - breakout_level)
    else:
        breakout_level = min(float(item.get("low") or 0) for item in previous)
        distance = max(0, breakout_level - close)
    if atr <= 0:
        return 10.0 if distance > 0 else 5.0
    return round(min(20, 8 + (distance / atr) * 12), 2) if distance > 0 else 8.0


def _trend_strength_component(signal_data: dict) -> float:
    score = float(signal_data.get("SETUP_SCORE") or 0)
    adx = float(signal_data.get("ADX_14") or 0)
    ema_gap = float(signal_data.get("EMA_GAP") or 0)
    return round(min(20, (adx / 2.2) + (score / 3) + min(4, ema_gap / 10)), 2)


def _freshness_component(entry_info: dict) -> float:
    mode = entry_info.get("entry_mode")
    if mode == "FRESH_BREAKOUT":
        return 20.0
    if mode == "RETEST":
        return 18.0
    if mode == "CONTINUATION":
        return 14.0
    return 6.0


def _candle_pattern_component(pattern_summary: dict, signal: str) -> float:
    bullish = float(pattern_summary.get("bullish_score") or 0)
    bearish = float(pattern_summary.get("bearish_score") or 0)
    delta = bullish - bearish if signal == "BUY" else bearish - bullish
    if delta > 4:
        return 10.0
    if delta > 0:
        return 7.0
    if delta == 0:
        return 4.0
    return 1.0


def _market_context_component(signal: str, market_context: dict) -> float:
    bias = market_context.get("bias")
    risk = market_context.get("risk_level")
    if risk == "HIGH":
        return 0.0
    if (signal == "BUY" and bias == "BULLISH") or (signal == "SELL" and bias == "BEARISH"):
        return 15.0
    if bias in {"NEUTRAL", "MIXED", "UNAVAILABLE", None}:
        return 8.0
    return 3.0


def _option_premium_component(option_trade: dict) -> tuple[float, str]:
    entry = float(option_trade.get("entry_price") or 0)
    open_price = float(option_trade.get("option_open") or 0)
    previous_close = float(option_trade.get("option_previous_close") or 0)
    day_high = float(option_trade.get("option_day_high") or 0)
    spread_percent = float(option_trade.get("bid_ask_spread_percent") or 0)
    top_depth = option_trade.get("top_depth_sufficient")

    score = 0.0
    reasons = []
    if open_price and entry >= open_price:
        score += 5
        reasons.append("premium above open")
    if previous_close and entry >= previous_close:
        score += 4
        reasons.append("premium above previous close")
    if day_high and entry <= day_high * 0.98:
        score += 3
        reasons.append("premium not chasing day high")
    if spread_percent <= float(config.MAX_BID_ASK_SPREAD_PERCENT):
        score += 2
        reasons.append("spread acceptable")
    if top_depth is not False:
        score += 1
        reasons.append("top depth acceptable")

    return min(15.0, score), f"Option premium confirmation {min(15.0, score)}/15: {', '.join(reasons) or 'weak premium confirmation'}."


def _components_from_signal(signal: dict) -> dict:
    components = signal.get("TRADE_QUALITY_COMPONENTS")
    if isinstance(components, dict):
        return dict(components)
    return {
        "entry_freshness": float(signal.get("QUALITY_ENTRY_FRESHNESS") or 0),
        "trend_strength": float(signal.get("QUALITY_TREND_STRENGTH") or 0),
        "retest_breakout_quality": float(signal.get("QUALITY_RETEST_BREAKOUT") or 0),
        "option_premium_confirmation": float(signal.get("QUALITY_OPTION_PREMIUM") or 0),
        "market_context": float(signal.get("QUALITY_MARKET_CONTEXT") or 0),
        "candle_pattern_support": float(signal.get("QUALITY_CANDLE_PATTERN") or 0),
    }


def _write_quality(signal: dict, components: dict, reason: str) -> dict:
    score = round(sum(float(value or 0) for value in components.values()), 2)
    signal["TRADE_QUALITY_SCORE"] = min(100.0, score)
    signal["TRADE_QUALITY_COMPONENTS"] = components
    signal["QUALITY_ENTRY_FRESHNESS"] = components.get("entry_freshness")
    signal["QUALITY_TREND_STRENGTH"] = components.get("trend_strength")
    signal["QUALITY_RETEST_BREAKOUT"] = components.get("retest_breakout_quality")
    signal["QUALITY_OPTION_PREMIUM"] = components.get("option_premium_confirmation")
    signal["QUALITY_MARKET_CONTEXT"] = components.get("market_context")
    signal["QUALITY_CANDLE_PATTERN"] = components.get("candle_pattern_support")
    signal["TRADE_QUALITY_REASON"] = (
        f"{signal.get('TRADE_QUALITY_REASON', '')} {reason}".strip()
    )
    decision, size_percent, decision_reason = quality_decision(signal["TRADE_QUALITY_SCORE"])
    signal["TRADE_QUALITY_DECISION"] = decision
    signal["QUALITY_SIZE_PERCENT"] = size_percent
    signal["TRADE_QUALITY_DECISION_REASON"] = decision_reason
    return signal


def _score_result(score: float, components: dict, reason: str) -> dict:
    signal = {"TRADE_QUALITY_REASON": reason}
    return _write_quality(signal, components, reason)
