# candlestick_patterns.py - Candlestick pattern confirmation for chart signals

from __future__ import annotations


def _f(value) -> float:
    return float(value or 0)


def _body(candle: dict) -> float:
    return abs(_f(candle["close"]) - _f(candle["open"]))


def _range(candle: dict) -> float:
    return max(_f(candle["high"]) - _f(candle["low"]), 0.0001)


def _upper_wick(candle: dict) -> float:
    return _f(candle["high"]) - max(_f(candle["open"]), _f(candle["close"]))


def _lower_wick(candle: dict) -> float:
    return min(_f(candle["open"]), _f(candle["close"])) - _f(candle["low"])


def _is_bullish(candle: dict) -> bool:
    return _f(candle["close"]) > _f(candle["open"])


def _is_bearish(candle: dict) -> bool:
    return _f(candle["close"]) < _f(candle["open"])


def _is_doji(candle: dict, threshold: float = 0.12) -> bool:
    return _body(candle) <= _range(candle) * threshold


def _body_high(candle: dict) -> float:
    return max(_f(candle["open"]), _f(candle["close"]))


def _body_low(candle: dict) -> float:
    return min(_f(candle["open"]), _f(candle["close"]))


def _small_body(candle: dict, threshold: float = 0.35) -> bool:
    return _body(candle) <= _range(candle) * threshold


def _large_body(candle: dict, threshold: float = 0.55) -> bool:
    return _body(candle) >= _range(candle) * threshold


def _near(value: float, other: float, tolerance: float) -> bool:
    return abs(value - other) <= tolerance


def _trend(candles: list[dict], direction: str, lookback: int = 4) -> bool:
    if len(candles) < 2:
        return False
    sample_size = min(len(candles), lookback + 1)
    closes = [_f(candle["close"]) for candle in candles[-sample_size:]]
    if direction == "down":
        return closes[-1] < closes[0]
    return closes[-1] > closes[0]


def _add(rows: list[dict], name: str, side: str, strength: int, reason: str) -> None:
    rows.append({
        "name": name,
        "side": side,
        "strength": strength,
        "reason": reason,
    })


def detect_candlestick_patterns(candles: list[dict]) -> list[dict]:
    """Detect practical intraday candlestick patterns on the latest candles."""
    if len(candles) < 2:
        return []

    rows: list[dict] = []
    c1 = candles[-1]
    c2 = candles[-2]
    c3 = candles[-3] if len(candles) >= 3 else None
    c4 = candles[-4] if len(candles) >= 4 else None
    c5 = candles[-5] if len(candles) >= 5 else None
    tolerance = _range(c1) * 0.12

    # Two-candle reversals.
    if _is_bearish(c2) and _is_bullish(c1) and _body_low(c1) <= _body_low(c2) and _body_high(c1) >= _body_high(c2):
        _add(rows, "Bullish Engulfing", "BULLISH", 3, "Latest bullish candle engulfs previous bearish body.")
    if _is_bullish(c2) and _is_bearish(c1) and _body_high(c1) >= _body_high(c2) and _body_low(c1) <= _body_low(c2):
        _add(rows, "Bearish Engulfing", "BEARISH", 3, "Latest bearish candle engulfs previous bullish body.")

    midpoint_c2 = (_f(c2["open"]) + _f(c2["close"])) / 2
    if _is_bearish(c2) and _is_bullish(c1) and _f(c1["open"]) < _f(c2["close"]) and _f(c1["close"]) > midpoint_c2:
        _add(rows, "Piercing Line", "BULLISH", 2, "Bullish close recovered above previous bearish midpoint.")
    if _is_bullish(c2) and _is_bearish(c1) and _f(c1["open"]) > _f(c2["close"]) and _f(c1["close"]) < midpoint_c2:
        _add(rows, "Dark Cloud Cover", "BEARISH", 2, "Bearish close pushed below previous bullish midpoint.")

    if _is_bearish(c2) and _small_body(c1) and _body_high(c1) < _body_high(c2) and _body_low(c1) > _body_low(c2):
        _add(rows, "Bullish Harami", "BULLISH", 1, "Small candle sits inside previous bearish body.")
    if _is_bullish(c2) and _small_body(c1) and _body_high(c1) < _body_high(c2) and _body_low(c1) > _body_low(c2):
        _add(rows, "Bearish Harami", "BEARISH", 1, "Small candle sits inside previous bullish body.")

    if _near(_f(c1["low"]), _f(c2["low"]), tolerance) and _is_bullish(c1):
        _add(rows, "Tweezer Bottom", "BULLISH", 2, "Two candles rejected a similar low/support zone.")
    if _near(_f(c1["high"]), _f(c2["high"]), tolerance) and _is_bearish(c1):
        _add(rows, "Tweezer Top", "BEARISH", 2, "Two candles rejected a similar high/resistance zone.")

    # Single-candle shapes.
    if _lower_wick(c1) >= _body(c1) * 2 and _upper_wick(c1) <= _body(c1) * 0.8 and _trend(candles, "down"):
        _add(rows, "Hammer", "BULLISH", 2, "Long lower wick after a down move shows buyer rejection.")
    if _lower_wick(c1) >= _body(c1) * 2 and _upper_wick(c1) <= _body(c1) * 0.8 and _trend(candles, "up"):
        _add(rows, "Hanging Man", "BEARISH", 2, "Long lower wick after an up move warns of selling pressure.")
    if _upper_wick(c1) >= _body(c1) * 2 and _lower_wick(c1) <= _body(c1) * 0.8 and _trend(candles, "down"):
        _add(rows, "Inverted Hammer", "BULLISH", 1, "Buyers pushed price up after a down move, but need confirmation.")
    if _upper_wick(c1) >= _body(c1) * 2 and _lower_wick(c1) <= _body(c1) * 0.8 and _trend(candles, "up"):
        _add(rows, "Shooting Star", "BEARISH", 2, "Upper wick after up move shows failed buying and rejection.")
    if _is_doji(c1) and _lower_wick(c1) >= _range(c1) * 0.6:
        _add(rows, "Dragonfly Doji Bullish", "BULLISH", 1, "Doji with strong lower rejection suggests support.")
    if _is_doji(c1) and _trend(candles, "up"):
        _add(rows, "Bearish Doji Star", "BEARISH", 1, "Doji after up move shows buyer indecision.")

    if _is_bullish(c1) and _large_body(c1) and _f(c1["open"]) <= _f(c1["low"]) + _range(c1) * 0.1:
        _add(rows, "Bullish Belt Hold", "BULLISH", 2, "Large bullish candle opened near low and closed strong.")
    if _is_bearish(c1) and _large_body(c1) and _f(c1["open"]) >= _f(c1["high"]) - _range(c1) * 0.1:
        _add(rows, "Bearish Belt Hold", "BEARISH", 2, "Large bearish candle opened near high and closed weak.")

    if c3:
        if _is_bearish(c3) and _small_body(c2) and _is_bullish(c1) and _f(c1["close"]) > ((_f(c3["open"]) + _f(c3["close"])) / 2):
            _add(rows, "Morning Star", "BULLISH", 3, "Bearish pressure paused, then bullish candle reclaimed midpoint.")
        if _is_bullish(c3) and _small_body(c2) and _is_bearish(c1) and _f(c1["close"]) < ((_f(c3["open"]) + _f(c3["close"])) / 2):
            _add(rows, "Evening Star", "BEARISH", 3, "Bullish pressure paused, then bearish candle lost midpoint.")
        if _is_bearish(c3) and _is_bullish(c2) and _is_bullish(c1) and _f(c2["close"]) > _f(c3["open"]) and _f(c1["close"]) > _f(c2["close"]):
            _add(rows, "Three Inside Up", "BULLISH", 2, "Harami-like reversal confirmed by follow-up bullish candle.")
        if _is_bullish(c3) and _is_bearish(c2) and _is_bearish(c1) and _f(c2["close"]) < _f(c3["open"]) and _f(c1["close"]) < _f(c2["close"]):
            _add(rows, "Three Inside Down", "BEARISH", 2, "Harami-like reversal confirmed by follow-up bearish candle.")
        if _is_bearish(c3) and _is_bullish(c2) and _is_bullish(c1) and _body_high(c2) >= _body_high(c3) and _f(c1["close"]) > _f(c2["close"]):
            _add(rows, "Three Outside Up", "BULLISH", 3, "Bullish engulfing followed by bullish continuation.")
        if _is_bullish(c3) and _is_bearish(c2) and _is_bearish(c1) and _body_low(c2) <= _body_low(c3) and _f(c1["close"]) < _f(c2["close"]):
            _add(rows, "Three Outside Down", "BEARISH", 3, "Bearish engulfing followed by bearish continuation.")
        if all(_is_bullish(candle) for candle in [c3, c2, c1]) and _f(c1["close"]) > _f(c2["close"]) > _f(c3["close"]):
            _add(rows, "Three White Soldiers", "BULLISH", 3, "Three rising bullish closes show strong buying continuation.")
        if all(_is_bearish(candle) for candle in [c3, c2, c1]) and _f(c1["close"]) < _f(c2["close"]) < _f(c3["close"]):
            _add(rows, "Three Black Crows", "BEARISH", 3, "Three falling bearish closes show strong selling continuation.")
        if _is_bearish(c3) and _is_doji(c2) and _is_bullish(c1) and _f(c1["open"]) > _f(c2["high"]):
            _add(rows, "Bullish Abandoned Baby", "BULLISH", 3, "Doji gap reversal from bearish to bullish pressure.")
        if _is_bullish(c3) and _is_doji(c2) and _is_bearish(c1) and _f(c1["open"]) < _f(c2["low"]):
            _add(rows, "Bearish Abandoned Baby", "BEARISH", 3, "Doji gap reversal from bullish to bearish pressure.")
        if _is_bearish(c2) and _is_bullish(c1) and _f(c1["open"]) > _f(c2["open"]) and _f(c1["close"]) > _f(c2["open"]):
            _add(rows, "Bullish Kicker", "BULLISH", 3, "Sudden bullish candle rejects previous bearish sentiment.")
        if _is_bullish(c2) and _is_bearish(c1) and _f(c1["open"]) < _f(c2["open"]) and _f(c1["close"]) < _f(c2["open"]):
            _add(rows, "Bearish Kicker", "BEARISH", 3, "Sudden bearish candle rejects previous bullish sentiment.")
        if _is_bearish(c3) and _is_bullish(c2) and _is_bullish(c1) and _f(c2["close"]) >= _f(c3["close"]) and _f(c1["close"]) > _f(c2["close"]):
            _add(rows, "Meeting Lines", "BULLISH", 1, "Down move stalled near prior close and buyers followed through.")

    if c5 and c4 and c3:
        middle_three = [c4, c3, c2]
        if _is_bullish(c5) and all(_small_body(candle) for candle in middle_three) and _is_bullish(c1) and _f(c1["close"]) > _f(c5["close"]):
            _add(rows, "Rising Three Methods", "BULLISH", 2, "Uptrend paused in small candles, then resumed higher.")
            _add(rows, "Mat Hold", "BULLISH", 2, "Bullish continuation after a controlled pause.")
        if _is_bearish(c5) and all(_small_body(candle) for candle in middle_three) and _is_bearish(c1) and _f(c1["close"]) < _f(c5["close"]):
            _add(rows, "Bearish Mat Hold", "BEARISH", 2, "Downtrend paused in small candles, then resumed lower.")
        if _trend(candles, "down") and _is_bullish(c1) and _lower_wick(c1) > _body(c1) and _f(c1["close"]) > _f(c2["close"]):
            _add(rows, "Ladder Bottom", "BULLISH", 1, "Downtrend shows first bullish ladder/recovery attempt.")
            _add(rows, "Concealing Baby Swallow", "BULLISH", 1, "Selling pressure appears to be losing control.")
        if _trend(candles, "up") and _is_bearish(c1) and _f(c1["close"]) < _f(c2["close"]):
            _add(rows, "Bearish Three-Line Strike", "BEARISH", 1, "Up move hit a bearish reversal/pullback candle.")
        if _trend(candles, "down") and _is_bullish(c1) and _f(c1["close"]) > _f(c2["close"]):
            _add(rows, "Three-Line Strike", "BULLISH", 1, "Bearish sequence hit a bullish recovery candle.")

    if _is_bullish(c2) and _is_bullish(c1) and _near(_f(c1["open"]), _f(c2["open"]), tolerance) and _f(c1["close"]) > _f(c2["close"]):
        _add(rows, "Bullish Separating Lines", "BULLISH", 1, "Uptrend continuation after similar open and stronger close.")
    if c3 and _is_bullish(c3) and _is_bearish(c2) and _is_bearish(c1) and _f(c1["close"]) < _f(c2["close"]):
        _add(rows, "Upside Gap Two Crows", "BEARISH", 2, "Two bearish candles after up pressure warn of reversal.")

    return rows


def summarize_patterns(candles: list[dict]) -> dict:
    patterns = detect_candlestick_patterns(candles)
    bullish = [row for row in patterns if row["side"] == "BULLISH"]
    bearish = [row for row in patterns if row["side"] == "BEARISH"]
    bullish_score = sum(int(row["strength"]) for row in bullish)
    bearish_score = sum(int(row["strength"]) for row in bearish)

    if bullish_score > bearish_score:
        bias = "BULLISH"
    elif bearish_score > bullish_score:
        bias = "BEARISH"
    else:
        bias = "NEUTRAL"

    top_patterns = sorted(patterns, key=lambda row: int(row["strength"]), reverse=True)[:5]
    return {
        "bias": bias,
        "bullish_score": bullish_score,
        "bearish_score": bearish_score,
        "patterns": patterns,
        "top_patterns": top_patterns,
        "pattern_names": [row["name"] for row in top_patterns],
        "summary": ", ".join(row["name"] for row in top_patterns) or "No strong candle pattern",
    }
