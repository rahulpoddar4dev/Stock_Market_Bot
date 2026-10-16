# strategy.py - Rule-based market analysis for paper trading

import config
from candlestick_patterns import summarize_patterns
from indicators import enrich_candles
from trade_quality import detect_entry_mode, initial_trade_quality_score, is_price_overextended


def _latest_ready_candle(candles: list[dict]) -> dict | None:
    for candle in reversed(candles):
        if (
            candle.get("ema_9") is not None
            and candle.get("ema_21") is not None
            and candle.get("rsi_14") is not None
            and candle.get("vwap") is not None
            and candle.get("atr_14") is not None
            and candle.get("adx_14") is not None
        ):
            return candle
    return None


def _setup_score(
    signal: str,
    close: float,
    ema_9: float,
    ema_21: float,
    rsi: float,
    vwap: float,
    atr: float,
    adx: float,
) -> float:
    if signal not in {"BUY", "SELL"}:
        return 0.0

    ema_gap = abs(ema_9 - ema_21) / close * 10000
    vwap_gap = abs(close - vwap) / close * 10000
    atr_score = (atr / close) * 10000
    adx_score = adx / 5

    if signal == "BUY":
        rsi_score = max(0, 70 - abs(60 - rsi))
    else:
        rsi_score = max(0, 70 - abs(40 - rsi))

    return round(ema_gap + vwap_gap + atr_score + adx_score + (rsi_score / 10), 2)


def _analyze_htf_trend(htf_candles: list[dict] | None) -> dict:
    if not htf_candles or len(htf_candles) < 10:
        return {
            "bias": "UNKNOWN",
            "reason": "Not enough HTF candles.",
            "swing_high": None,
            "swing_low": None,
            "ema_21": None,
            "vwap": None,
        }

    enriched = enrich_candles(htf_candles)
    latest = None
    for c in reversed(enriched):
        if c.get("ema_21") is not None and c.get("vwap") is not None:
            latest = c
            break

    if not latest:
        if len(htf_candles) >= 5:
            closes = [float(c["close"]) for c in htf_candles]
            sma = sum(closes) / len(closes)
            close = closes[-1]
            bias = "BULLISH" if close > sma else "BEARISH" if close < sma else "NEUTRAL"
            recent_3 = htf_candles[-3:]
            return {
                "bias": bias,
                "reason": f"HTF Trend: close {close} vs baseline {round(sma, 2)}",
                "swing_high": max((float(c.get("high") or 0) for c in recent_3), default=close),
                "swing_low": min((float(c.get("low") or close) for c in recent_3), default=close),
                "ema_21": sma,
                "vwap": sma,
                "atr": 0.0,
                "adx": 0.0,
                "candle_time": htf_candles[-1].get("date"),
            }
        return {
            "bias": "UNKNOWN",
            "reason": "HTF indicators not ready.",
            "swing_high": None,
            "swing_low": None,
            "ema_21": None,
            "vwap": None,
        }

    close = float(latest["close"])
    ema_9 = float(latest.get("ema_9") or close)
    ema_21 = float(latest.get("ema_21") or close)
    vwap = float(latest.get("vwap") or close)
    atr = float(latest.get("atr_14") or 0)
    adx = float(latest.get("adx_14") or 0)

    recent_3 = htf_candles[-3:]
    swing_high = max((float(c.get("high") or 0) for c in recent_3), default=close)
    swing_low = min((float(c.get("low") or close) for c in recent_3), default=close)

    if close < ema_21 and close < vwap:
        bias = "BEARISH"
        reason = f"15m HTF Bearish: close {close} < EMA21 {round(ema_21, 2)} & VWAP {round(vwap, 2)}"
    elif close > ema_21 and close > vwap:
        bias = "BULLISH"
        reason = f"15m HTF Bullish: close {close} > EMA21 {round(ema_21, 2)} & VWAP {round(vwap, 2)}"
    else:
        bias = "NEUTRAL"
        reason = f"15m HTF Neutral/Mixed: close {close}, EMA21 {round(ema_21, 2)}, VWAP {round(vwap, 2)}"

    return {
        "bias": bias,
        "reason": reason,
        "close": close,
        "ema_9": ema_9,
        "ema_21": ema_21,
        "vwap": vwap,
        "atr": atr,
        "adx": adx,
        "swing_high": swing_high,
        "swing_low": swing_low,
        "candle_time": latest.get("date"),
    }


def _hold(
    reason: str,
    confidence: str = "LOW",
    latest: dict | None = None,
    htf_info: dict | None = None,
    extra: dict | None = None,
) -> tuple[dict, dict | None]:
    payload = {
        "SIGNAL": "HOLD",
        "REASON": reason,
        "CONFIDENCE": confidence,
        "SETUP_SCORE": 0,
        "STOP_LOSS": "N/A",
        "TARGET": "N/A",
    }
    if isinstance(htf_info, dict):
        payload["HTF_BIAS"] = htf_info.get("bias", "UNKNOWN")
        payload["HTF_TREND_REASON"] = htf_info.get("reason")
        payload["HTF_SWING_HIGH"] = htf_info.get("swing_high")
        payload["HTF_SWING_LOW"] = htf_info.get("swing_low")
        payload["HTF_CANDLE_TIME"] = htf_info.get("candle_time")
    if isinstance(latest, dict):
        for candle_key, signal_key in (
            ("ema_9", "EMA_9"),
            ("ema_21", "EMA_21"),
            ("rsi_14", "RSI_14"),
            ("vwap", "VWAP"),
            ("atr_14", "ATR_14"),
            ("adx_14", "ADX_14"),
            ("date", "CANDLE_TIME"),
        ):
            if candle_key in latest and latest.get(candle_key) is not None:
                payload[signal_key] = latest.get(candle_key)
    if isinstance(extra, dict):
        payload.update(extra)
    return payload, latest


def _breakout_confirmed(candles: list[dict], latest: dict, signal: str) -> tuple[bool, str]:
    lookback = int(getattr(config, "BREAKOUT_LOOKBACK_CANDLES", 0) or 0)
    if lookback <= 0 or signal not in {"BUY", "SELL"}:
        return True, "Breakout filter disabled."

    try:
        latest_index = candles.index(latest)
    except ValueError:
        latest_index = len(candles) - 1

    previous = candles[max(0, latest_index - lookback):latest_index]
    if len(previous) < lookback:
        return False, f"Breakout filter needs {lookback} previous candles."

    close = float(latest["close"])
    if signal == "BUY":
        previous_high = max(float(candle["high"]) for candle in previous)
        if close > previous_high:
            return True, f"BUY breakout confirmed above previous {lookback}-candle high {round(previous_high, 2)}."
        return False, f"BUY blocked: close {close} did not break previous {lookback}-candle high {round(previous_high, 2)}."

    previous_low = min(float(candle["low"]) for candle in previous)
    if close < previous_low:
        return True, f"SELL breakdown confirmed below previous {lookback}-candle low {round(previous_low, 2)}."
    return False, f"SELL blocked: close {close} did not break previous {lookback}-candle low {round(previous_low, 2)}."


def _pattern_score_for_signal(pattern_summary: dict, signal: str) -> tuple[float, str]:
    bullish_score = float(pattern_summary.get("bullish_score") or 0)
    bearish_score = float(pattern_summary.get("bearish_score") or 0)
    if signal == "BUY":
        return bullish_score - bearish_score, pattern_summary.get("summary", "")
    if signal == "SELL":
        return bearish_score - bullish_score, pattern_summary.get("summary", "")
    return 0, pattern_summary.get("summary", "")


def _apply_pattern_confirmation(
    signal_data: dict,
    signal: str,
    score: float,
    pattern_summary: dict,
    htf_info: dict | None = None,
) -> tuple[dict | None, float, str]:
    if not config.CANDLE_PATTERN_ENABLED or signal not in {"BUY", "SELL"}:
        return None, score, ""

    bullish_score = float(pattern_summary.get("bullish_score") or 0)
    bearish_score = float(pattern_summary.get("bearish_score") or 0)
    pattern_delta, pattern_text = _pattern_score_for_signal(pattern_summary, signal)
    confirms = pattern_delta > 0
    conflicts = pattern_delta < -float(config.CANDLE_PATTERN_CONFLICT_MARGIN)

    if config.CANDLE_PATTERN_BLOCK_CONFLICT and conflicts:
        opposite = "bearish" if signal == "BUY" else "bullish"
        htf_bias = (htf_info or {}).get("bias", "UNKNOWN")
        # Only hard-block if HTF trend is NOT strongly supporting the trade
        if (signal == "BUY" and htf_bias != "BULLISH") or (signal == "SELL" and htf_bias != "BEARISH"):
            return _hold(
                f"Candlestick conflict: {signal} blocked because strong {opposite} pattern is present without HTF override. "
                f"Patterns: {pattern_text}. Bullish score {bullish_score}, bearish score {bearish_score}.",
                latest=signal_data,
                htf_info=htf_info,
            )[0], score, pattern_text
        else:
            # 15m HTF strongly supports trade; penalize score instead of fatal block
            score = max(0.0, score - float(config.CANDLE_PATTERN_SCORE_PENALTY))

    adjusted_score = score
    if confirms:
        adjusted_score += float(config.CANDLE_PATTERN_SCORE_BONUS)
    elif score < float(config.CANDLE_PATTERN_REQUIRE_CONFIRMATION_BELOW_SCORE):
        adjusted_score -= float(config.CANDLE_PATTERN_SCORE_PENALTY)

    if adjusted_score < config.MIN_SETUP_SCORE:
        return _hold(
            f"Candlestick confirmation missing: setup score {score} adjusted to {round(adjusted_score, 2)}. "
            f"Patterns: {pattern_text}.",
            latest=signal_data,
            htf_info=htf_info,
        )[0], adjusted_score, pattern_text

    return None, round(adjusted_score, 2), pattern_text


def build_strategy_signal(
    market_data: dict,
    candles: list[dict],
    secondary_timeframes: dict[str, list[dict]] | None = None,
    htf_candles: list[dict] | None = None,
) -> tuple[dict, dict | None]:
    if len(candles) < config.MIN_CANDLES_FOR_STRATEGY:
        return _hold(f"Need at least {config.MIN_CANDLES_FOR_STRATEGY} candles for indicator analysis.")

    htf_info = _analyze_htf_trend(htf_candles)
    htf_bias = htf_info.get("bias", "UNKNOWN")

    enriched_candles = enrich_candles(candles)
    latest = _latest_ready_candle(enriched_candles)
    pattern_summary = summarize_patterns(enriched_candles) if config.CANDLE_PATTERN_ENABLED else {
        "bias": "DISABLED",
        "bullish_score": 0,
        "bearish_score": 0,
        "summary": "Disabled",
        "pattern_names": [],
    }

    if latest is None:
        return _hold("Indicators are not ready yet.", htf_info=htf_info)

    close = float(latest["close"])
    ema_9 = latest["ema_9"]
    ema_21 = latest["ema_21"]
    rsi = latest["rsi_14"]
    vwap = latest["vwap"]
    atr = latest["atr_14"]
    adx = latest["adx_14"]
    candle_high = float(latest["high"])
    candle_low = float(latest["low"])
    ema_gap_points = abs(ema_9 - ema_21)
    atr_percent = (atr / close) * 100 if close else 0

    if ema_gap_points < config.MIN_EMA_GAP_POINTS:
        return _hold(
            f"Sideways filter: EMA gap {round(ema_gap_points, 2)} is below minimum {config.MIN_EMA_GAP_POINTS}.",
            latest=latest,
            htf_info=htf_info,
        )

    if atr_percent < config.MIN_ATR_PERCENT:
        return _hold(
            f"Low-volatility filter: ATR {round(atr_percent, 3)}% is below minimum {config.MIN_ATR_PERCENT}%.",
            latest=latest,
            htf_info=htf_info,
        )

    if adx < config.MIN_ADX:
        return _hold(
            f"Weak-trend filter: ADX {adx} is below minimum {config.MIN_ADX}.",
            latest=latest,
            htf_info=htf_info,
        )

    bullish = close > ema_9 > ema_21 and close > vwap and 50 <= rsi <= config.MAX_RSI_BUY
    bearish = close < ema_9 < ema_21 and close < vwap and config.MIN_RSI_SELL <= rsi <= 50

    if bullish:
        signal = "BUY"
        reason = (
            f"Bullish setup: close {close} is above EMA9 {ema_9}, EMA21 {ema_21}, "
            f"and VWAP {vwap}; RSI {rsi} supports momentum."
        )
        confidence = "MEDIUM"
    elif bearish:
        signal = "SELL"
        reason = (
            f"Bearish setup: close {close} is below EMA9 {ema_9}, EMA21 {ema_21}, "
            f"and VWAP {vwap}; RSI {rsi} supports downside momentum."
        )
        confidence = "MEDIUM"
    else:
        signal = "HOLD"
        reason = (
            f"No clean setup: close {close}, EMA9 {ema_9}, EMA21 {ema_21}, "
            f"VWAP {vwap}, RSI {rsi}."
        )
        confidence = "LOW"

    # 15-Minute Higher Timeframe Trend Guard (Counter-trend hard block)
    if getattr(config, "HTF_TREND_FILTER_ENABLED", True):
        if signal == "BUY" and htf_bias == "BEARISH":
            return _hold(
                f"Counter-trend BUY blocked: 15-Minute HTF is strictly BEARISH ({htf_info.get('reason')}). "
                "CE trades are forbidden during a market downtrend.",
                latest=latest,
                htf_info=htf_info,
                extra={"COUNTER_TREND_BLOCK": True, "ORIGINAL_SIGNAL": signal},
            )
        if signal == "SELL" and htf_bias == "BULLISH":
            return _hold(
                f"Counter-trend SELL blocked: 15-Minute HTF is strictly BULLISH ({htf_info.get('reason')}). "
                "PE trades are forbidden during a market uptrend.",
                latest=latest,
                htf_info=htf_info,
                extra={"COUNTER_TREND_BLOCK": True, "ORIGINAL_SIGNAL": signal},
            )

    score = _setup_score(signal, close, ema_9, ema_21, rsi, vwap, atr, adx)
    pattern_block, score, pattern_text = _apply_pattern_confirmation(latest, signal, score, pattern_summary, htf_info=htf_info)
    if pattern_block:
        pattern_block["CANDLE_PATTERN_BIAS"] = pattern_summary.get("bias")
        pattern_block["CANDLE_PATTERNS"] = pattern_summary.get("summary")
        pattern_block["CANDLE_PATTERN_BULLISH_SCORE"] = pattern_summary.get("bullish_score")
        pattern_block["CANDLE_PATTERN_BEARISH_SCORE"] = pattern_summary.get("bearish_score")
        return pattern_block, latest

    if signal in {"BUY", "SELL"} and score < config.MIN_SETUP_SCORE:
        return _hold(
            f"Weak setup score: {score} is below minimum {config.MIN_SETUP_SCORE}. "
            f"close {close}, EMA9 {ema_9}, EMA21 {ema_21}, VWAP {vwap}, RSI {rsi}, ADX {adx}.",
            latest=latest,
            htf_info=htf_info,
        )

    entry_info = detect_entry_mode(enriched_candles, latest, signal)
    overextended, distance_reason, distance_metrics = is_price_overextended(latest, signal)
    if signal in {"BUY", "SELL"} and overextended:
        return _hold(
            f"Late/overextended entry blocked: {distance_reason} "
            f"Entry mode was {entry_info.get('entry_mode')}.",
            latest=latest,
            htf_info=htf_info,
            extra={
                "OVEREXTENDED_BLOCK": True,
                "OVEREXTENDED_REASON": distance_reason,
                "ORIGINAL_SIGNAL": signal,
                "ENTRY_DISTANCE_FROM_EMA9_PERCENT": distance_metrics.get("entry_distance_from_ema9_percent"),
                "ENTRY_DISTANCE_FROM_VWAP_PERCENT": distance_metrics.get("entry_distance_from_vwap_percent"),
            },
        )

    if signal in {"BUY", "SELL"} and entry_info.get("entry_mode") == "OLD_SIGNAL_WAIT_RETEST":
        return _hold(
            f"Fresh signal rule: old signal blocked. {entry_info.get('entry_reason')}",
            latest=latest,
            htf_info=htf_info,
            extra={"OLD_SIGNAL_BLOCK": True, "ORIGINAL_SIGNAL": signal},
        )

    breakout_ok, breakout_reason = _breakout_confirmed(enriched_candles, latest, signal)
    if (
        signal in {"BUY", "SELL"}
        and entry_info.get("entry_mode") == "FRESH_BREAKOUT"
        and not breakout_ok
    ):
        return _hold(breakout_reason, latest=latest, htf_info=htf_info)

    quality = initial_trade_quality_score(
        {"SIGNAL": signal, "SETUP_SCORE": score, "ADX_14": adx, "EMA_GAP": round(ema_gap_points, 2)},
        latest,
        entry_info,
        pattern_summary,
    )

    secondary_pattern_rows = []
    if secondary_timeframes and config.CANDLE_PATTERN_ENABLED:
        for interval, rows in secondary_timeframes.items():
            if not rows:
                continue
            interval_summary = summarize_patterns(enrich_candles(rows))
            secondary_pattern_rows.append(f"{interval}: {interval_summary.get('bias')} ({interval_summary.get('summary')})")

    return {
        "SIGNAL": signal,
        "REASON": (
            f"{reason} Entry mode: {entry_info.get('entry_mode')} ({entry_info.get('entry_reason')}). "
            f"Candlestick patterns: {pattern_text}. {breakout_reason}"
            if signal in {"BUY", "SELL"} else reason
        ),
        "CONFIDENCE": confidence,
        "SETUP_SCORE": score,
        "STOP_LOSS": (
            round(max(float(htf_info.get("swing_high") or candle_high), candle_high) + (float(getattr(config, "UNDERLYING_ATR_MULTIPLIER", 1.5)) * atr), 2)
            if signal == "SELL"
            else round(min(float(htf_info.get("swing_low") or candle_low), candle_low) - (float(getattr(config, "UNDERLYING_ATR_MULTIPLIER", 1.5)) * atr), 2)
            if signal == "BUY"
            else "N/A"
        ),
        "INDEX_STOP_LOSS": (
            round(max(float(htf_info.get("swing_high") or candle_high), candle_high) + (float(getattr(config, "UNDERLYING_ATR_MULTIPLIER", 1.5)) * atr), 2)
            if signal == "SELL"
            else round(min(float(htf_info.get("swing_low") or candle_low), candle_low) - (float(getattr(config, "UNDERLYING_ATR_MULTIPLIER", 1.5)) * atr), 2)
            if signal == "BUY"
            else "N/A"
        ),
        "HTF_BIAS": htf_bias,
        "HTF_TREND_REASON": htf_info.get("reason"),
        "HTF_SWING_HIGH": htf_info.get("swing_high"),
        "HTF_SWING_LOW": htf_info.get("swing_low"),
        "HTF_CANDLE_TIME": htf_info.get("candle_time"),
        "TARGET": "Calculated on option premium after option selection" if signal in {"BUY", "SELL"} else "N/A",
        "EMA_9": ema_9,
        "EMA_21": ema_21,
        "RSI_14": rsi,
        "VWAP": vwap,
        "ATR_14": atr,
        "ADX_14": adx,
        "EMA_GAP": round(ema_gap_points, 2),
        "ENTRY_MODE": entry_info.get("entry_mode"),
        "ENTRY_FRESHNESS": entry_info.get("entry_freshness"),
        "ENTRY_REASON": entry_info.get("entry_reason"),
        "FRESH_CROSS": entry_info.get("fresh_cross"),
        "RETEST_ENTRY": entry_info.get("retest_entry"),
        "CONTINUATION_ENTRY": entry_info.get("continuation_entry"),
        "BREAKOUT_QUALITY_SCORE": entry_info.get("breakout_quality_score"),
        "ENTRY_DISTANCE_FROM_EMA9_PERCENT": distance_metrics.get("entry_distance_from_ema9_percent"),
        "ENTRY_DISTANCE_FROM_VWAP_PERCENT": distance_metrics.get("entry_distance_from_vwap_percent"),
        "TRADE_QUALITY_SCORE": quality.get("TRADE_QUALITY_SCORE"),
        "TRADE_QUALITY_COMPONENTS": quality.get("TRADE_QUALITY_COMPONENTS"),
        "QUALITY_ENTRY_FRESHNESS": quality.get("QUALITY_ENTRY_FRESHNESS"),
        "QUALITY_TREND_STRENGTH": quality.get("QUALITY_TREND_STRENGTH"),
        "QUALITY_RETEST_BREAKOUT": quality.get("QUALITY_RETEST_BREAKOUT"),
        "QUALITY_OPTION_PREMIUM": quality.get("QUALITY_OPTION_PREMIUM"),
        "QUALITY_MARKET_CONTEXT": quality.get("QUALITY_MARKET_CONTEXT"),
        "QUALITY_CANDLE_PATTERN": quality.get("QUALITY_CANDLE_PATTERN"),
        "TRADE_QUALITY_REASON": quality.get("TRADE_QUALITY_REASON"),
        "TRADE_QUALITY_DECISION": quality.get("TRADE_QUALITY_DECISION"),
        "QUALITY_SIZE_PERCENT": quality.get("QUALITY_SIZE_PERCENT"),
        "TRADE_QUALITY_DECISION_REASON": quality.get("TRADE_QUALITY_DECISION_REASON"),
        "CANDLE_TIME": latest["date"],
        "CANDLE_PATTERN_BIAS": pattern_summary.get("bias"),
        "CANDLE_PATTERNS": pattern_summary.get("summary"),
        "CANDLE_PATTERN_BULLISH_SCORE": pattern_summary.get("bullish_score"),
        "CANDLE_PATTERN_BEARISH_SCORE": pattern_summary.get("bearish_score"),
        "SECONDARY_TIMEFRAME_PATTERNS": " | ".join(secondary_pattern_rows),
    }, latest
