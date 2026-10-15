# ai_trade_analyst.py - AI-style momentum override shared by trading modes

from __future__ import annotations

import json

import requests

import config
from indicators import enrich_candles
from trade_quality import is_price_overextended


def ai_trade_signal_for_analysis(analysis: dict) -> dict:
    """
    Analyst that can override an overly conservative HOLD.

    Uses the OpenAI API when configured, with a deterministic local momentum
    fallback so Render keeps trading/researching even if the API is unavailable.
    """
    candles = enrich_candles(analysis.get("candles") or [])
    latest = _latest_ready(candles)
    if not latest:
        return _hold("Indicators/candles are not ready for AI trade analysis.")

    strategy_signal = analysis.get("strategy_signal") or {}
    market_data = analysis.get("market_data") or {}
    htf_bias = strategy_signal.get("HTF_BIAS") or market_data.get("htf_bias")

    # Never allow AI override when primary strategy already blocked due to overextension or counter-trend
    if strategy_signal.get("OVEREXTENDED_BLOCK"):
        return _hold(
            f"AI Trade Override blocked: chart is overextended ({strategy_signal.get('OVEREXTENDED_REASON') or strategy_signal.get('REASON')}).",
            latest,
        )
    if strategy_signal.get("COUNTER_TREND_BLOCK"):
        return _hold(
            f"AI Trade Override blocked: primary strategy flagged counter-trend ({strategy_signal.get('REASON')}).",
            latest,
        )

    openai_signal = _openai_trade_signal(analysis, candles, latest)
    if openai_signal:
        if (
            openai_signal.get("SIGNAL") == "HOLD"
            and (
                not config.AI_TRADE_OPENAI_RESPECT_HOLD
                or getattr(config, "AGGRESSIVE_FORCE_AI_HOLD_FALLBACK", False)
            )
        ):
            sig = _deterministic_trade_signal(analysis, candles, latest)
        else:
            sig = openai_signal
    else:
        sig = _deterministic_trade_signal(analysis, candles, latest)

    sig_dir = sig.get("SIGNAL")
    if sig_dir in {"BUY", "SELL"}:
        # 1. 15m HTF alignment check
        if getattr(config, "HTF_TREND_FILTER_ENABLED", True) and htf_bias in {"BEARISH", "BULLISH"}:
            if sig_dir == "BUY" and htf_bias == "BEARISH":
                return _hold("AI Trade Override BUY blocked: 15m HTF trend is strictly BEARISH. CE forbidden.", latest)
            if sig_dir == "SELL" and htf_bias == "BULLISH":
                return _hold("AI Trade Override SELL blocked: 15m HTF trend is strictly BULLISH. PE forbidden.", latest)

        # 2. 5m EMA9 vs EMA21 structural alignment check (prevents 1-candle counter-trend bounces)
        ema_9 = float(latest.get("ema_9") or 0)
        ema_21 = float(latest.get("ema_21") or 0)
        if ema_9 > 0 and ema_21 > 0:
            if sig_dir == "BUY" and ema_9 < ema_21:
                return _hold(
                    f"AI Trade Override BUY blocked: 5m EMA9 ({ema_9}) is below EMA21 ({ema_21}); counter-trend bounce ignored.",
                    latest,
                )
            if sig_dir == "SELL" and ema_9 > ema_21:
                return _hold(
                    f"AI Trade Override SELL blocked: 5m EMA9 ({ema_9}) is above EMA21 ({ema_21}); counter-trend dip ignored.",
                    latest,
                )

        # 3. Overextension check on the AI signal direction
        overextended, overext_reason, _ = is_price_overextended(latest, sig_dir)
        if overextended:
            return _hold(f"AI Trade Override {sig_dir} blocked: {overext_reason}", latest)

        # Preserve HTF context on the returned AI signal
        if htf_bias:
            sig["HTF_BIAS"] = htf_bias
        if strategy_signal.get("HTF_TREND_REASON"):
            sig["HTF_TREND_REASON"] = strategy_signal.get("HTF_TREND_REASON")
        if strategy_signal.get("HTF_SWING_HIGH") is not None:
            sig["HTF_SWING_HIGH"] = strategy_signal.get("HTF_SWING_HIGH")
        if strategy_signal.get("HTF_SWING_LOW") is not None:
            sig["HTF_SWING_LOW"] = strategy_signal.get("HTF_SWING_LOW")

    return sig


def _deterministic_trade_signal(analysis: dict, candles: list[dict], latest: dict) -> dict:
    """Local fallback used when OpenAI is disabled or unavailable."""

    recent = candles[-4:]
    previous = recent[-2] if len(recent) >= 2 else None
    previous_rows = candles[-(int(getattr(config, "AI_TRADE_BREAKOUT_LOOKBACK_CANDLES", 6)) + 1):-1]
    close = float(latest.get("close") or 0)
    open_price = float(latest.get("open") or close)
    high = float(latest.get("high") or close)
    low = float(latest.get("low") or close)
    ema_9 = float(latest.get("ema_9") or 0)
    ema_21 = float(latest.get("ema_21") or 0)
    vwap = float(latest.get("vwap") or 0)
    rsi = float(latest.get("rsi_14") or 50)
    atr = float(latest.get("atr_14") or 0)
    adx = float(latest.get("adx_14") or 0)
    candle_change = _pct(close - open_price, open_price)
    recent_change = _pct(close - float(recent[0].get("open") or open_price), float(recent[0].get("open") or open_price)) if recent else 0
    day_change = float((analysis.get("market_data") or {}).get("change_percent") or 0)
    prev_high = max((float(row.get("high") or 0) for row in previous_rows), default=high)
    prev_low = min((float(row.get("low") or 0) for row in previous_rows), default=low)
    atr_percent = _pct(atr, close)
    ema_gap_percent = _pct(abs(ema_9 - ema_21), close) if ema_9 and ema_21 else 0
    indicator_suspicious = _indicator_suspicious(latest, candles)

    buy_score = 0.0
    sell_score = 0.0
    reasons = []

    if close > ema_9:
        buy_score += 14
        reasons.append("close above EMA9")
    if close < ema_9:
        sell_score += 14
        reasons.append("close below EMA9")
    if ema_9 > ema_21:
        buy_score += 10
        reasons.append("EMA9 above EMA21")
    if ema_9 < ema_21:
        sell_score += 10
        reasons.append("EMA9 below EMA21")
    if close > vwap:
        buy_score += 14
        reasons.append("close above VWAP")
    if close < vwap:
        sell_score += 14
        reasons.append("close below VWAP")
    if candle_change >= float(config.AI_TRADE_MIN_CANDLE_BODY_PERCENT):
        buy_score += 14
        reasons.append(f"latest candle strong green {round(candle_change, 3)}%")
    if candle_change <= -float(config.AI_TRADE_MIN_CANDLE_BODY_PERCENT):
        sell_score += 14
        reasons.append(f"latest candle strong red {round(candle_change, 3)}%")
    if recent_change >= float(config.AI_TRADE_MIN_RECENT_MOVE_PERCENT):
        buy_score += 16
        reasons.append(f"recent momentum up {round(recent_change, 3)}%")
    if recent_change <= -float(config.AI_TRADE_MIN_RECENT_MOVE_PERCENT):
        sell_score += 16
        reasons.append(f"recent momentum down {round(recent_change, 3)}%")
    if close > prev_high:
        buy_score += 16
        reasons.append(f"breakout above recent high {round(prev_high, 2)}")
    if close < prev_low:
        sell_score += 16
        reasons.append(f"breakdown below recent low {round(prev_low, 2)}")
    if day_change >= float(config.AI_TRADE_MIN_DAY_CHANGE_PERCENT):
        buy_score += 8
        reasons.append(f"positive day change {day_change}%")
    if day_change <= -float(config.AI_TRADE_MIN_DAY_CHANGE_PERCENT):
        sell_score += 8
        reasons.append(f"negative day change {day_change}%")
    if rsi >= 55:
        buy_score += 8
        reasons.append(f"RSI bullish {rsi}")
    if rsi <= 45:
        sell_score += 8
        reasons.append(f"RSI bearish {rsi}")
    if adx >= float(config.AI_TRADE_MIN_ADX):
        buy_score += 6
        sell_score += 6
        reasons.append(f"ADX usable {adx}")
    elif indicator_suspicious:
        buy_score += 4
        sell_score += 4
        reasons.append("indicator sanity fallback: ATR/ADX look suspicious, using price action")
    if atr_percent >= float(config.AI_TRADE_MIN_ATR_PERCENT) or indicator_suspicious:
        buy_score += 4
        sell_score += 4
    if ema_gap_percent >= float(config.AI_TRADE_MIN_EMA_GAP_PERCENT) or indicator_suspicious:
        buy_score += 4
        sell_score += 4

    # Dominant Trend Guard: Never buy calls in a daily downtrend below VWAP
    if (day_change < 0 and close < vwap) or (close < ema_21 and close < vwap):
        buy_score = 0.0
        reasons.append("counter-trend buy strictly blocked: below VWAP and EMA21")
    elif (day_change > 0 and close > vwap) or (close > ema_21 and close > vwap):
        sell_score = 0.0
        reasons.append("counter-trend sell strictly blocked: above VWAP and EMA21")

    signal = "BUY" if buy_score >= sell_score else "SELL"
    score = round(max(buy_score, sell_score), 2)
    opposite = round(min(buy_score, sell_score), 2)
    if (
        not getattr(config, "AGGRESSIVE_TRADER_MODE", False)
        and (score < float(config.AI_TRADE_MIN_SCORE) or score - opposite < float(config.AI_TRADE_MIN_SCORE_GAP))
    ):
        return _hold(
            f"AI momentum did not find a clear edge. Buy score {round(buy_score, 2)}, "
            f"sell score {round(sell_score, 2)}. Reasons: {_compact_reasons(reasons)}",
            latest,
        )

    confidence = "HIGH" if score >= float(config.AI_TRADE_HIGH_CONFIDENCE_SCORE) else "MEDIUM"
    if score < float(config.AI_TRADE_MIN_SCORE):
        confidence = "LOW"
    stop_loss = low if signal == "BUY" else high
    if signal == "BUY":
        clean_reason = "Bullish momentum: buyers dominating above VWAP with strong green candles and upward breakout."
    elif signal == "SELL":
        clean_reason = "Bearish breakdown: heavy institutional selling below VWAP with expanding red candles."
    else:
        clean_reason = "Market consolidating in range, waiting for directional edge."

    return {
        "SIGNAL": signal,
        "REASON": clean_reason,
        "CONFIDENCE": confidence,
        "SETUP_SCORE": score,
        "STOP_LOSS": stop_loss,
        "TARGET": "Calculated on option premium after option selection",
        "EMA_9": latest.get("ema_9"),
        "EMA_21": latest.get("ema_21"),
        "RSI_14": latest.get("rsi_14"),
        "VWAP": latest.get("vwap"),
        "ATR_14": latest.get("atr_14"),
        "ADX_14": latest.get("adx_14"),
        "EMA_GAP": round(abs(ema_9 - ema_21), 2) if ema_9 and ema_21 else 0,
        "CANDLE_TIME": latest.get("date"),
        "AI_OVERRIDE": True,
        "AI_TRADE_OVERRIDE": True,
        "AI_TRADE_SCORE": score,
        "AI_TRADE_SIZE_PERCENT": config.AI_TRADE_SIZE_PERCENT,
        "AI_TRADE_INDICATOR_SANITY": "SUSPICIOUS" if indicator_suspicious else "OK",
        "AGGRESSIVE_TRADER_MODE": bool(getattr(config, "AGGRESSIVE_TRADER_MODE", False)),
    }


def _latest_ready(candles: list[dict]) -> dict | None:
    for candle in reversed(candles):
        if candle.get("ema_9") is not None and candle.get("vwap") is not None and candle.get("rsi_14") is not None:
            return candle
    return candles[-1] if candles else None


def _indicator_suspicious(latest: dict, candles: list[dict]) -> bool:
    if not candles:
        return False
    atr = float(latest.get("atr_14") or 0)
    adx = float(latest.get("adx_14") or 0)
    ema_9 = float(latest.get("ema_9") or 0)
    ema_21 = float(latest.get("ema_21") or 0)
    recent = candles[-10:]
    avg_range = sum(max(float(row.get("high") or 0) - float(row.get("low") or 0), 0) for row in recent) / max(len(recent), 1)
    return avg_range > 0 and (atr <= 0 or adx <= 0 or (ema_9 and ema_21 and abs(ema_9 - ema_21) == 0))


def _pct(value: float, base: float) -> float:
    return (float(value) / abs(float(base)) * 100) if base else 0.0


def _compact_reasons(reasons: list[str]) -> str:
    unique = []
    for reason in reasons:
        if reason not in unique:
            unique.append(reason)
    return "; ".join(unique[:8]) or "No strong reason."


def _hold(reason: str, latest: dict | None = None) -> dict:
    signal = {
        "SIGNAL": "HOLD",
        "REASON": reason,
        "CONFIDENCE": "LOW",
        "SETUP_SCORE": 0,
        "STOP_LOSS": "N/A",
        "TARGET": "N/A",
        "AI_TRADE_OVERRIDE": False,
    }
    if latest:
        signal["CANDLE_TIME"] = latest.get("date")
    return signal


def _openai_trade_signal(analysis: dict, candles: list[dict], latest: dict) -> dict | None:
    if not getattr(config, "AI_TRADE_USE_OPENAI", False):
        return None
    if not getattr(config, "OPENAI_API_KEY", ""):
        return None

    try:
        payload = _build_openai_payload(analysis, candles)
        response = requests.post(
            "https://api.openai.com/v1/responses",
            headers={
                "Authorization": f"Bearer {config.OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=int(getattr(config, "AI_TRADE_OPENAI_TIMEOUT_SECONDS", 20)),
        )
        response.raise_for_status()
        result = _extract_openai_json(response.json())
        return _normalize_openai_signal(result, latest)
    except Exception as error:
        fallback = _deterministic_trade_signal(analysis, candles, latest)
        fallback["AI_RESEARCH_PROVIDER"] = "LOCAL_FALLBACK"
        return fallback


def _build_openai_payload(analysis: dict, candles: list[dict]) -> dict:
    max_candles = int(getattr(config, "AI_TRADE_OPENAI_MAX_CANDLES", 30))
    trimmed_candles = [_compact_candle(row) for row in candles[-max_candles:]]
    market_data = analysis.get("market_data") or {}
    strategy_signal = analysis.get("strategy_signal") or {}
    latest_candle = analysis.get("latest_candle") or {}

    research_context = {
        "symbol": analysis.get("symbol"),
        "trading_mode": getattr(config, "TRADING_MODE", "PAPER"),
        "candle_interval": getattr(config, "CANDLE_INTERVAL", "5minute"),
        "market_data": _compact_market_data(market_data),
        "strict_strategy_signal": {
            "signal": strategy_signal.get("SIGNAL"),
            "confidence": strategy_signal.get("CONFIDENCE"),
            "score": strategy_signal.get("SETUP_SCORE"),
            "reason": strategy_signal.get("REASON"),
        },
        "latest_candle": _compact_candle(latest_candle),
        "recent_candles": trimmed_candles,
        "rules": {
            "buy_means": "Bullish underlying move. Bot will normally look for a CE/options long setup.",
            "sell_means": "Bearish underlying move. Bot will normally look for a PE/options long setup.",
            "hold_means": "No clean intraday edge right now.",
            "score_range": "0 to 100",
        },
    }

    if getattr(config, "AGGRESSIVE_TRADER_MODE", False):
        analyst_instruction = (
            "You are an aggressive intraday index-options trader for the Indian market. "
            "Analyze only the supplied OHLCV/indicator data. Do not invent live prices, news, "
            "or option-chain facts that are not provided. Choose BUY or SELL when there is any "
            "reasonable directional intraday edge; use HOLD only when the data is unusable or fully contradictory. "
            "Return structured JSON."
        )
    else:
        analyst_instruction = (
            "You are an intraday index-options research analyst for the Indian market. "
            "Analyze only the supplied OHLCV/indicator data. Do not invent live prices, news, "
            "or option-chain facts that are not provided. Return conservative, structured JSON. "
            "Prefer HOLD if the edge is unclear, candles are noisy, or risk/reward is poor."
        )

    return {
        "model": getattr(config, "OPENAI_MODEL", "gpt-5"),
        "input": [
            {
                "role": "system",
                "content": [
                    {
                        "type": "input_text",
                        "text": analyst_instruction,
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": json.dumps(research_context, default=str),
                    }
                ],
            },
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "trade_research_signal",
                "strict": True,
                "schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "signal": {"type": "string", "enum": ["BUY", "SELL", "HOLD"]},
                        "confidence": {"type": "string", "enum": ["LOW", "MEDIUM", "HIGH"]},
                        "score": {"type": "number", "minimum": 0, "maximum": 100},
                        "reason": {"type": "string"},
                        "research_summary": {"type": "string"},
                        "key_observations": {
                            "type": "array",
                            "items": {"type": "string"},
                            "maxItems": 6,
                        },
                        "invalidation_level": {"type": "number"},
                        "stop_loss": {"type": "number"},
                    },
                    "required": [
                        "signal",
                        "confidence",
                        "score",
                        "reason",
                        "research_summary",
                        "key_observations",
                        "invalidation_level",
                        "stop_loss",
                    ],
                },
            }
        },
    }


def _normalize_openai_signal(result: dict, latest: dict) -> dict:
    signal = str(result.get("signal") or "HOLD").upper()
    if signal not in {"BUY", "SELL", "HOLD"}:
        signal = "HOLD"

    confidence = str(result.get("confidence") or "LOW").upper()
    if confidence not in {"LOW", "MEDIUM", "HIGH"}:
        confidence = "LOW"

    score = max(0.0, min(float(result.get("score") or 0), 100.0))
    observations = result.get("key_observations") or []
    reason_parts = [
        f"OpenAI research: {result.get('reason') or 'No reason provided.'}",
        f"Summary: {result.get('research_summary') or 'N/A'}",
    ]
    if observations:
        reason_parts.append("Key observations: " + "; ".join(str(item) for item in observations[:6]))

    if signal == "HOLD":
        hold_signal = _hold(" ".join(reason_parts), latest)
        hold_signal["AI_RESEARCH_PROVIDER"] = "OPENAI"
        hold_signal["AI_TRADE_SCORE"] = score
        hold_signal["AI_TRADE_OVERRIDE"] = False
        return hold_signal

    if (
        not getattr(config, "AGGRESSIVE_TRADER_MODE", False)
        and (score < float(config.AI_TRADE_MIN_SCORE) or confidence == "LOW")
    ):
        hold_signal = _hold(
            f"OpenAI research score/confidence too low for trade. Signal {signal}, "
            f"score {round(score, 2)}, confidence {confidence}. {' '.join(reason_parts)}",
            latest,
        )
        hold_signal["AI_RESEARCH_PROVIDER"] = "OPENAI"
        hold_signal["AI_TRADE_SCORE"] = score
        return hold_signal

    return {
        "SIGNAL": signal,
        "REASON": " ".join(reason_parts),
        "CONFIDENCE": confidence,
        "SETUP_SCORE": round(score, 2),
        "STOP_LOSS": float(result.get("stop_loss") or result.get("invalidation_level") or 0),
        "TARGET": "Calculated on option premium after option selection",
        "EMA_9": latest.get("ema_9"),
        "EMA_21": latest.get("ema_21"),
        "RSI_14": latest.get("rsi_14"),
        "VWAP": latest.get("vwap"),
        "ATR_14": latest.get("atr_14"),
        "ADX_14": latest.get("adx_14"),
        "EMA_GAP": round(abs(float(latest.get("ema_9") or 0) - float(latest.get("ema_21") or 0)), 2),
        "CANDLE_TIME": latest.get("date"),
        "AI_OVERRIDE": True,
        "AI_TRADE_OVERRIDE": True,
        "AI_TRADE_SCORE": round(score, 2),
        "AI_TRADE_SIZE_PERCENT": config.AI_TRADE_SIZE_PERCENT,
        "AI_RESEARCH_PROVIDER": "OPENAI",
        "AI_INVALIDATION_LEVEL": result.get("invalidation_level"),
        "AGGRESSIVE_TRADER_MODE": bool(getattr(config, "AGGRESSIVE_TRADER_MODE", False)),
    }


def _extract_openai_json(payload: dict) -> dict:
    if isinstance(payload.get("output_text"), str):
        return json.loads(payload["output_text"])

    for item in payload.get("output") or []:
        for content in item.get("content") or []:
            text = content.get("text")
            if text:
                return json.loads(text)
    raise ValueError("OpenAI response did not include JSON output")


def _compact_market_data(market_data: dict) -> dict:
    keys = [
        "symbol",
        "last_price",
        "change",
        "change_percent",
        "volume",
        "oi",
        "open",
        "high",
        "low",
        "close",
    ]
    return {key: market_data.get(key) for key in keys if key in market_data}


def _compact_candle(row: dict) -> dict:
    keys = [
        "date",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "ema_9",
        "ema_21",
        "rsi_14",
        "vwap",
        "atr_14",
        "adx_14",
    ]
    return {key: row.get(key) for key in keys if key in row}
