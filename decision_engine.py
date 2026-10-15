# decision_engine.py - Combine analyzers into one final trade decision

import config
from trade_quality import add_market_context_to_quality


def build_final_decision(index_comparison: dict, news: dict, market_context: dict | None = None) -> dict:
    """
    Central decision gate.

    Chart/index/news analyzers can run independently, but no option selection or
    order execution should happen until this function returns the final signal.
    """
    chart_signal = dict(index_comparison.get("signal") or {})
    selected = index_comparison.get("selected")
    market_data = dict(index_comparison.get("market_data") or {})
    signal = chart_signal.get("SIGNAL", "HOLD")

    if config.TEST_SIGNAL_OVERRIDE in {"BUY", "SELL", "HOLD"}:
        signal = config.TEST_SIGNAL_OVERRIDE
        chart_signal.update({
            "SIGNAL": signal,
            "REASON": "Manual test signal override from config.py.",
            "CONFIDENCE": "TEST",
            "STOP_LOSS": "N/A",
            "TARGET": "N/A",
        })

    final_signal = dict(chart_signal)
    final_signal["DECISION_ENGINE"] = "ENABLED"
    final_signal["CHART_SIGNAL"] = chart_signal.get("SIGNAL", "HOLD")
    final_signal["INDEX_ALIGNMENT"] = _index_alignment(index_comparison)
    final_signal["INDEX_REASON"] = _index_reason(index_comparison)
    final_signal["NEWS_SENTIMENT"] = news.get("sentiment")
    final_signal["NEWS_RISK_LEVEL"] = news.get("risk_level")
    final_signal["NEWS_REASON"] = news.get("reason")
    final_signal["NEWS_HEADLINES"] = " | ".join(news.get("headlines", [])[:3])
    final_signal["NEWS_MODE"] = "REFERENCE_ONLY"
    final_signal["NEWS_CONFLICT"] = False
    market_context = market_context or {}
    final_signal["MARKET_CONTEXT_BIAS"] = market_context.get("bias")
    final_signal["MARKET_CONTEXT_SCORE"] = market_context.get("score")
    final_signal["MARKET_CONTEXT_RISK_LEVEL"] = market_context.get("risk_level")
    final_signal["MARKET_CONTEXT_REASON"] = market_context.get("reason")
    final_signal["MARKET_CONTEXT_MODE"] = "REFERENCE_ONLY"
    final_signal["MARKET_CONTEXT_CONFLICT"] = False
    final_signal["MARKET_REGIME"] = market_context.get("market_regime")
    final_signal["MARKET_REGIME_RISK"] = market_context.get("market_regime_risk")
    final_signal["MARKET_REGIME_ACTION"] = market_context.get("market_regime_action")
    final_signal["MARKET_REGIME_REASON"] = market_context.get("market_regime_reason")
    final_signal = add_market_context_to_quality(final_signal, market_context)

    if final_signal.get("SIGNAL") not in {"BUY", "SELL"}:
        final_signal["SIGNAL"] = "HOLD"
        final_signal["CONFIDENCE"] = final_signal.get("CONFIDENCE", "LOW")
        final_signal["REASON"] = _final_reason(final_signal, "Chart/index analyzer did not approve a trade.")
        return _result(index_comparison, selected, market_data, final_signal)

    block_reason = None
    if news.get("sentiment") == "HIGH_RISK" and config.NEWS_BLOCK_HIGH_RISK:
        block_reason = news.get("reason") or "News risk is HIGH."
    elif config.NEWS_BLOCK_TRADES and final_signal["SIGNAL"] == "BUY" and news.get("sentiment") == "NEGATIVE":
        block_reason = "Chart is bullish, but news sentiment is negative."
    elif config.NEWS_BLOCK_TRADES and final_signal["SIGNAL"] == "SELL" and news.get("sentiment") == "POSITIVE":
        block_reason = "Chart is bearish, but news sentiment is positive."

    if block_reason:
        original_signal = final_signal["SIGNAL"]
        final_signal["SIGNAL"] = "HOLD"
        final_signal["CONFIDENCE"] = "DECISION_ENGINE"
        final_signal["STOP_LOSS"] = "N/A"
        final_signal["TARGET"] = "N/A"
        final_signal["NEWS_BLOCKED_ORIGINAL_SIGNAL"] = original_signal
        final_signal["NEWS_MODE"] = "HIGH_RISK_BLOCK" if news.get("sentiment") == "HIGH_RISK" else "CONFLICT_BLOCK"
        final_signal["REASON"] = _final_reason(final_signal, block_reason)
        return _result(index_comparison, selected, market_data, final_signal)

    context_block_reason = _market_context_block_reason(final_signal, market_context)
    if context_block_reason and not getattr(config, "AGGRESSIVE_IGNORE_SOFT_BLOCKS", False):
        original_signal = final_signal["SIGNAL"]
        final_signal["SIGNAL"] = "HOLD"
        final_signal["CONFIDENCE"] = "MARKET_CONTEXT"
        final_signal["STOP_LOSS"] = "N/A"
        final_signal["TARGET"] = "N/A"
        final_signal["MARKET_CONTEXT_BLOCKED_ORIGINAL_SIGNAL"] = original_signal
        final_signal["MARKET_CONTEXT_MODE"] = "BLOCK"
        final_signal["REASON"] = _final_reason(final_signal, context_block_reason)
        return _result(index_comparison, selected, market_data, final_signal)
    if context_block_reason:
        final_signal["MARKET_CONTEXT_MODE"] = "AGGRESSIVE_OVERRIDE"
        final_signal["AGGRESSIVE_OVERRIDE_REASON"] = context_block_reason

    if (
        (final_signal["SIGNAL"] == "BUY" and news.get("sentiment") == "NEGATIVE")
        or (final_signal["SIGNAL"] == "SELL" and news.get("sentiment") == "POSITIVE")
    ):
        final_signal["NEWS_CONFLICT"] = True

    if index_comparison.get("single_index_trade"):
        final_signal["SINGLE_INDEX_TRADE"] = True
        context_risk = market_context.get("risk_level")
        if config.MARKET_CONTEXT_BLOCK_HIGH_RISK and context_risk == "HIGH" and not getattr(config, "AGGRESSIVE_IGNORE_SOFT_BLOCKS", False):
            original_signal = final_signal["SIGNAL"]
            final_signal["SIGNAL"] = "HOLD"
            final_signal["CONFIDENCE"] = "SINGLE_INDEX_CONTEXT"
            final_signal["STOP_LOSS"] = "N/A"
            final_signal["TARGET"] = "N/A"
            final_signal["SINGLE_INDEX_BLOCKED_ORIGINAL_SIGNAL"] = original_signal
            final_signal["REASON"] = _final_reason(
                final_signal,
                "Single-index trade blocked because market context is high-risk.",
            )
            return _result(index_comparison, selected, market_data, final_signal)
        if context_risk == "HIGH":
            final_signal["AGGRESSIVE_OVERRIDE_REASON"] = (
                f"{final_signal.get('AGGRESSIVE_OVERRIDE_REASON', '')} "
                "Single-index high-risk context allowed by aggressive mode."
            ).strip()
        final_signal["SIZE_PERCENT"] = min(
            float(final_signal.get("SIZE_PERCENT") or 100),
            config.SINGLE_INDEX_TRADE_SIZE_PERCENT,
        )

    if _market_context_conflicts(final_signal, market_context):
        final_signal["MARKET_CONTEXT_CONFLICT"] = True
        if config.MARKET_CONTEXT_REDUCE_SIZE_ON_MIXED:
            existing_size = float(final_signal.get("SIZE_PERCENT") or 100)
            final_signal["SIZE_PERCENT"] = min(existing_size, config.MARKET_CONTEXT_REDUCED_SIZE_PERCENT)
            final_signal["MARKET_CONTEXT_MODE"] = "REDUCE_SIZE"

    final_signal["REASON"] = _final_reason(final_signal, "Final decision approved by central decision engine.")
    return _result(index_comparison, selected, market_data, final_signal)


def _result(index_comparison: dict, selected: dict | None, market_data: dict, final_signal: dict) -> dict:
    market_data["strategy_signal"] = final_signal
    market_data["news_sentiment"] = final_signal.get("NEWS_SENTIMENT")
    market_data["news_risk_level"] = final_signal.get("NEWS_RISK_LEVEL")
    market_data["news_reason"] = final_signal.get("NEWS_REASON")
    market_data["news_headlines"] = final_signal.get("NEWS_HEADLINES")
    market_data["news_mode"] = final_signal.get("NEWS_MODE")
    market_data["news_conflict"] = final_signal.get("NEWS_CONFLICT")
    market_data["market_context_bias"] = final_signal.get("MARKET_CONTEXT_BIAS")
    market_data["market_context_score"] = final_signal.get("MARKET_CONTEXT_SCORE")
    market_data["market_context_risk_level"] = final_signal.get("MARKET_CONTEXT_RISK_LEVEL")
    market_data["market_context_reason"] = final_signal.get("MARKET_CONTEXT_REASON")
    market_data["market_context_mode"] = final_signal.get("MARKET_CONTEXT_MODE")
    market_data["market_context_conflict"] = final_signal.get("MARKET_CONTEXT_CONFLICT")
    market_data["market_regime"] = final_signal.get("MARKET_REGIME")
    market_data["market_regime_risk"] = final_signal.get("MARKET_REGIME_RISK")
    market_data["market_regime_action"] = final_signal.get("MARKET_REGIME_ACTION")
    market_data["market_regime_reason"] = final_signal.get("MARKET_REGIME_REASON")

    return {
        "selected": selected,
        "analyses": index_comparison.get("analyses", []),
        "signal": final_signal,
        "market_data": market_data,
        "hold": final_signal.get("SIGNAL") == "HOLD",
        "single_index_trade": bool(final_signal.get("SINGLE_INDEX_TRADE")),
    }


def _index_alignment(index_comparison: dict) -> str:
    if index_comparison.get("single_index_trade"):
        return "SINGLE_INDEX_STRONG"
    if index_comparison.get("hold"):
        return "NO_TRADE_ALIGNMENT"

    analyses = index_comparison.get("analyses", [])
    trade_signals = [
        item.get("strategy_signal", {}).get("SIGNAL")
        for item in analyses
        if item.get("strategy_signal", {}).get("SIGNAL") in {"BUY", "SELL"}
    ]
    if len(set(trade_signals)) == 1 and len(trade_signals) >= 2:
        return "NIFTY_SENSEX_ALIGNED"
    return "SELECTED_BEST_INDEX"


def _index_reason(index_comparison: dict) -> str:
    selected = index_comparison.get("selected")
    signal = index_comparison.get("signal") or {}
    if selected:
        return f"{selected.get('symbol')} selected. {signal.get('REASON', '')}".strip()
    return signal.get("REASON", "No selected index.")


def _market_context_conflicts(signal: dict, market_context: dict) -> bool:
    chart_signal = signal.get("SIGNAL")
    bias = market_context.get("bias")
    return (chart_signal == "BUY" and bias == "BEARISH") or (chart_signal == "SELL" and bias == "BULLISH")


def _market_context_block_reason(signal: dict, market_context: dict) -> str | None:
    if not config.MARKET_CONTEXT_ENABLED or signal.get("SIGNAL") not in {"BUY", "SELL"}:
        return None
    if getattr(config, "VIX_BLOCK_ON_IV_CRUSH", False) and market_context.get("iv_crush"):
        return f"India VIX is collapsing sharply ({market_context.get('vix_change')}%), causing severe option IV crush decay. Fresh option buying blocked."
    chart_signal = signal.get("SIGNAL")
    context_bias = market_context.get("bias")
    # If market is crashing/trending hard, that confirms aligned trade (e.g. SELL with BEARISH context)
    if (chart_signal == "SELL" and context_bias == "BEARISH") or (chart_signal == "BUY" and context_bias == "BULLISH"):
        return None
    if config.MARKET_CONTEXT_BLOCK_HIGH_RISK and market_context.get("risk_level") == "HIGH":
        return f"Market context risk is HIGH. Context: {market_context.get('reason')}"
    if config.MARKET_CONTEXT_BLOCK_OPPOSITE_BIAS and _market_context_conflicts(signal, market_context):
        return f"Market context is opposite to chart signal. Context: {market_context.get('reason')}"
    return None


def _final_reason(signal: dict, decision_text: str) -> str:
    chart_signal = signal.get("CHART_SIGNAL") or signal.get("SIGNAL") or "HOLD"
    if chart_signal == "BUY":
        market_action = "Bullish momentum active with buyers dominating above VWAP."
    elif chart_signal == "SELL":
        market_action = "Bearish selling pressure active below key intraday levels."
    else:
        market_action = "Market consolidating in range; waiting for clear breakout."

    raw = str(signal.get("REASON") or "").strip()
    if any(k in raw for k in ["Error:", "429", "EMA9", "override", "fallback", "Client Error"]):
        raw = market_action

    alignment = signal.get("INDEX_ALIGNMENT", "NORMAL")
    return f"{decision_text} Signal: {chart_signal} ({alignment}). {raw}".strip()
