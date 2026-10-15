# market_regime.py - Classify current market behavior before taking a trade

from __future__ import annotations

import config
from indicators import enrich_candles


def detect_market_regime(
    candles: list[dict],
    latest_candle: dict | None,
    market_context: dict | None,
    news: dict | None,
) -> dict:
    latest = latest_candle or _latest_ready(candles)
    if latest and _missing_indicator_values(latest):
        enriched = enrich_candles(candles or [])
        latest = _latest_ready(enriched) or latest
    market_context = market_context or {}
    news = news or {}

    if not latest:
        return _result("UNKNOWN", "MEDIUM", "LOG_ONLY", 0, "No latest candle available for regime detection.")

    close = float(latest.get("close") or 0)
    atr = float(latest.get("atr_14") or 0)
    adx = float(latest.get("adx_14") or 0)
    ema_9 = float(latest.get("ema_9") or 0)
    ema_21 = float(latest.get("ema_21") or 0)
    atr_percent = round((atr / close) * 100, 3) if close else 0
    ema_gap_percent = round((abs(ema_9 - ema_21) / close) * 100, 3) if close else 0
    context_risk = market_context.get("risk_level")
    news_risk = news.get("risk_level")
    news_sentiment = news.get("sentiment")

    if news_risk == "HIGH" or news_sentiment == "HIGH_RISK":
        return _result(
            "NEWS_EVENT",
            "HIGH",
            "SMALL_SIZE",
            float(getattr(config, "REGIME_NEWS_EVENT_SIZE_PERCENT", 50)),
            f"High-risk news/event detected. News: {news.get('reason')}",
            atr_percent,
            adx,
            ema_gap_percent,
        )

    if context_risk == "HIGH":
        return _result(
            "HIGH_VOLATILITY",
            "HIGH",
            "SMALL_SIZE",
            float(getattr(config, "REGIME_HIGH_VOL_SIZE_PERCENT", 50)),
            f"Market context risk is HIGH. Context: {market_context.get('reason')}",
            atr_percent,
            adx,
            ema_gap_percent,
        )

    if atr_percent >= float(getattr(config, "REGIME_HIGH_ATR_PERCENT", 0.45)):
        return _result(
            "HIGH_VOLATILITY",
            "HIGH",
            "SMALL_SIZE",
            float(getattr(config, "REGIME_HIGH_VOL_SIZE_PERCENT", 50)),
            f"ATR {atr_percent}% is high; allow only reduced-size trades.",
            atr_percent,
            adx,
            ema_gap_percent,
        )

    if atr_percent <= float(getattr(config, "REGIME_LOW_ATR_PERCENT", 0.05)):
        return _result(
            "LOW_VOLATILITY",
            "MEDIUM",
            "AVOID_OPTION_BUYING",
            0,
            f"ATR {atr_percent}% is too low for option buying.",
            atr_percent,
            adx,
            ema_gap_percent,
        )

    if adx >= float(getattr(config, "REGIME_TRENDING_ADX", 22)) and ema_gap_percent >= float(getattr(config, "REGIME_TRENDING_EMA_GAP_PERCENT", 0.025)):
        return _result(
            "TRENDING",
            "LOW",
            "ALLOW_BREAKOUT_CONTINUATION",
            100,
            f"Trending regime: ADX {adx}, EMA gap {ema_gap_percent}%, ATR {atr_percent}%.",
            atr_percent,
            adx,
            ema_gap_percent,
        )

    if adx <= float(getattr(config, "REGIME_SIDEWAYS_ADX", 18)) or ema_gap_percent <= float(getattr(config, "REGIME_SIDEWAYS_EMA_GAP_PERCENT", 0.018)):
        return _result(
            "SIDEWAYS",
            "MEDIUM",
            "SCALP_ONLY",
            float(getattr(config, "REGIME_SIDEWAYS_SIZE_PERCENT", 50)),
            f"Sideways regime: ADX {adx}, EMA gap {ema_gap_percent}%. Avoid weak/late entries.",
            atr_percent,
            adx,
            ema_gap_percent,
        )

    return _result(
        "NORMAL",
        "LOW",
        "NORMAL",
        100,
        f"Normal regime: ADX {adx}, EMA gap {ema_gap_percent}%, ATR {atr_percent}%.",
        atr_percent,
        adx,
        ema_gap_percent,
    )


def _result(
    regime: str,
    risk_level: str,
    action: str,
    size_percent: float,
    reason: str,
    atr_percent: float = 0,
    adx: float = 0,
    ema_gap_percent: float = 0,
) -> dict:
    return {
        "regime": regime,
        "risk_level": risk_level,
        "action": action,
        "size_percent": size_percent,
        "reason": reason,
        "atr_percent": atr_percent,
        "adx": adx,
        "ema_gap_percent": ema_gap_percent,
    }


def _latest_ready(candles: list[dict]) -> dict | None:
    for candle in reversed(candles or []):
        if candle.get("close") is not None and not _missing_indicator_values(candle):
            return candle
    for candle in reversed(candles or []):
        if candle.get("close") is not None:
            return candle
    return None


def _missing_indicator_values(candle: dict) -> bool:
    return (
        candle.get("atr_14") is None
        or candle.get("adx_14") is None
        or candle.get("ema_9") is None
        or candle.get("ema_21") is None
    )
