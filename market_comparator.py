# market_comparator.py - Compare NIFTY and SENSEX before choosing a trade

import config
from ai_trade_analyst import ai_trade_signal_for_analysis
from market_data import fetch_index_candles, fetch_index_market_data
from strategy import build_strategy_signal


def analyze_index(fyers, symbol: str) -> dict:
    market_data = fetch_index_market_data(fyers, symbol)
    candles = fetch_index_candles(fyers, symbol)
    secondary_timeframes = {}
    if config.CANDLE_PATTERN_MULTI_TIMEFRAME_ENABLED:
        for interval in config.CANDLE_PATTERN_SECONDARY_INTERVALS:
            try:
                secondary_timeframes[interval] = fetch_index_candles(
                    fyers,
                    symbol,
                    interval=interval,
                    lookback_days=config.CANDLE_PATTERN_SECONDARY_LOOKBACK_DAYS,
                )
            except Exception as error:
                secondary_timeframes[interval] = []
                market_data[f"{interval}_pattern_error"] = str(error)

    htf_candles = []
    if getattr(config, "HTF_TREND_FILTER_ENABLED", True):
        try:
            htf_candles = fetch_index_candles(
                fyers,
                symbol,
                interval=getattr(config, "HTF_CANDLE_INTERVAL", "15minute"),
                lookback_days=getattr(config, "HTF_LOOKBACK_DAYS", 5),
            )
        except Exception as error:
            htf_candles = []
            market_data["htf_candle_error"] = str(error)

    strategy_signal, latest_candle = build_strategy_signal(
        market_data,
        candles,
        secondary_timeframes,
        htf_candles=htf_candles,
    )
    market_data["strategy_signal"] = strategy_signal
    market_data["latest_candle"] = latest_candle
    market_data["secondary_timeframe_patterns"] = strategy_signal.get("SECONDARY_TIMEFRAME_PATTERNS")
    market_data["htf_bias"] = strategy_signal.get("HTF_BIAS")
    market_data["htf_trend_reason"] = strategy_signal.get("HTF_TREND_REASON")
    market_data["index_stop_loss"] = strategy_signal.get("INDEX_STOP_LOSS")

    return {
        "symbol": symbol,
        "market_data": market_data,
        "candles": candles,
        "secondary_timeframes": secondary_timeframes,
        "htf_candles": htf_candles,
        "strategy_signal": strategy_signal,
        "latest_candle": latest_candle,
    }


def choose_safe_market(analyses: list[dict]) -> dict:
    if not analyses:
        return _hold_result(analyses, "No index analysis data available.")

    # If some index timed out, do not block the available ones unless strictly mandated and single index is disabled
    if config.REQUIRE_INDEX_CONFIRMATION and len(analyses) < len(config.ANALYZE_INDICES) and not config.ALLOW_SINGLE_INDEX_TRADE:
        return _hold_result(
            analyses,
            "Could not analyze all configured indices. Waiting instead of taking a partial signal.",
        )

    trade_candidates = [
        item
        for item in analyses
        if item["strategy_signal"].get("SIGNAL") in {"BUY", "SELL"}
    ]

    if not trade_candidates:
        ai_result = _ai_trade_override_result(analyses, "strict chart rules stayed HOLD")
        if ai_result:
            return ai_result
        return _hold_result(analyses, "No clean setup found in analyzed indices.")

    signal_set = {item["strategy_signal"].get("SIGNAL") for item in trade_candidates}
    if len(signal_set) > 1:
        ai_result = _ai_trade_override_result(analyses, "NIFTY and SENSEX gave opposite trade signals")
        if ai_result:
            return ai_result
        return _hold_result(
            analyses,
            "NIFTY and SENSEX are not aligned, so safer decision is HOLD.",
        )

    if len(trade_candidates) < len(analyses) or len(analyses) < len(config.ANALYZE_INDICES):
        single_index_result = _single_index_result(analyses, trade_candidates)
        if single_index_result:
            return single_index_result

        if config.REQUIRE_INDEX_CONFIRMATION:
            ai_result = _ai_trade_override_result(analyses, "only one index had a normal strategy signal")
            if ai_result:
                return ai_result

            return _hold_result(
                analyses,
                "Waiting for index confirmation.",
            )

    selected = sorted(
        trade_candidates,
        key=lambda item: float(item["strategy_signal"].get("SETUP_SCORE") or 0),
        reverse=True,
    )[0]

    selected_signal = selected["strategy_signal"]
    selected_signal["REASON"] = (
        f"Selected {selected['symbol']} after comparing NIFTY and SENSEX. "
        f"{selected_signal['REASON']}"
    )

    return {
        "selected": selected,
        "analyses": analyses,
        "signal": selected_signal,
        "market_data": selected["market_data"],
        "hold": False,
    }


def _single_index_result(analyses: list[dict], trade_candidates: list[dict]) -> dict | None:
    if not config.ALLOW_SINGLE_INDEX_TRADE or len(trade_candidates) != 1:
        return None

    selected = trade_candidates[0]
    selected_signal = dict(selected["strategy_signal"])
    score = float(selected_signal.get("SETUP_SCORE") or 0)
    adx = float(selected_signal.get("ADX_14") or 0)

    min_score = min(float(config.MIN_SINGLE_INDEX_SETUP_SCORE), float(config.MIN_SETUP_SCORE))
    min_adx = min(float(config.MIN_SINGLE_INDEX_ADX), float(config.MIN_ADX))

    if score < min_score or adx < min_adx:
        return None

    selected_signal["REASON"] = (
        f"Single-index setup in {selected['symbol']} approved (score {score}, ADX {adx}). "
        f"{selected_signal['REASON']}"
    )
    selected_signal["SINGLE_INDEX_TRADE"] = True
    selected_signal["SIZE_PERCENT"] = config.SINGLE_INDEX_TRADE_SIZE_PERCENT
    selected["market_data"]["strategy_signal"] = selected_signal

    return {
        "selected": selected,
        "analyses": analyses,
        "signal": selected_signal,
        "market_data": selected["market_data"],
        "hold": False,
        "single_index_trade": True,
    }


def _ai_trade_override_result(analyses: list[dict], blocked_reason: str) -> dict | None:
    if not getattr(config, "AI_TRADE_OVERRIDE_ENABLED", False):
        return None
    if not _ai_trade_allowed_for_mode():
        return None

    candidates = []
    for analysis in analyses:
        ai_signal = ai_trade_signal_for_analysis(analysis)
        if ai_signal.get("SIGNAL") not in {"BUY", "SELL"}:
            analysis["ai_trade_signal"] = ai_signal
            continue
        score = float(ai_signal.get("AI_TRADE_SCORE") or ai_signal.get("SETUP_SCORE") or 0)
        candidate = dict(analysis)
        candidate["strategy_signal"] = ai_signal
        candidate["market_data"] = dict(analysis["market_data"])
        candidate["market_data"]["strategy_signal"] = ai_signal
        candidate["market_data"]["latest_candle"] = analysis.get("latest_candle")
        candidate["ai_trade_signal"] = ai_signal
        candidates.append((score, candidate))

    if not candidates:
        return None

    _, selected = sorted(candidates, key=lambda item: item[0], reverse=True)[0]
    selected_signal = dict(selected["strategy_signal"])
    selected_signal["REASON"] = (
        f"AI trade override selected {selected['symbol']} after {blocked_reason}. "
        f"{selected_signal['REASON']}"
    )
    selected_signal["SINGLE_INDEX_TRADE"] = True
    selected_signal["SIZE_PERCENT"] = min(
        float(selected_signal.get("SIZE_PERCENT") or 100),
        float(getattr(config, "AI_TRADE_SIZE_PERCENT", 35)),
    )
    selected["market_data"]["strategy_signal"] = selected_signal

    return {
        "selected": selected,
        "analyses": analyses,
        "signal": selected_signal,
        "market_data": selected["market_data"],
        "hold": False,
        "single_index_trade": True,
        "ai_trade_override": True,
    }


def _ai_trade_allowed_for_mode() -> bool:
    mode = getattr(config, "TRADING_MODE", "PAPER")
    allowed_modes = getattr(config, "AI_TRADE_ALLOWED_MODES", ["PAPER"])
    if isinstance(allowed_modes, str):
        allowed_modes = [item.strip().upper() for item in allowed_modes.split(",") if item.strip()]
    return mode in set(allowed_modes)


def _hold_result(analyses: list[dict], reason: str) -> dict:
    signal = {
        "SIGNAL": "HOLD",
        "REASON": reason,
        "CONFIDENCE": "LOW",
        "SETUP_SCORE": 0,
        "STOP_LOSS": "N/A",
        "TARGET": "N/A",
    }
    market_data = analyses[0]["market_data"] if analyses else {"symbol": "MARKET"}
    market_data["strategy_signal"] = signal
    market_data["latest_candle"] = analyses[0].get("latest_candle") if analyses else None

    return {
        "selected": None,
        "analyses": analyses,
        "signal": signal,
        "market_data": market_data,
        "hold": True,
    }
