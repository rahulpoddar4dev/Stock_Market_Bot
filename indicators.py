# indicators.py - Technical indicator calculations


def calculate_ema(values: list[float], period: int) -> list[float | None]:
    if len(values) < period:
        return [None] * len(values)

    multiplier = 2 / (period + 1)
    ema_values = [None] * len(values)
    ema = sum(values[:period]) / period
    ema_values[period - 1] = ema

    for index in range(period, len(values)):
        ema = (values[index] - ema) * multiplier + ema
        ema_values[index] = ema

    return ema_values


def calculate_rsi(values: list[float], period: int = 14) -> list[float | None]:
    if len(values) <= period:
        return [None] * len(values)

    rsi_values = [None] * len(values)
    gains = []
    losses = []

    for index in range(1, period + 1):
        change = values[index] - values[index - 1]
        gains.append(max(change, 0))
        losses.append(abs(min(change, 0)))

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    rsi_values[period] = 100 if avg_loss == 0 else 100 - (100 / (1 + (avg_gain / avg_loss)))

    for index in range(period + 1, len(values)):
        change = values[index] - values[index - 1]
        gain = max(change, 0)
        loss = abs(min(change, 0))

        avg_gain = ((avg_gain * (period - 1)) + gain) / period
        avg_loss = ((avg_loss * (period - 1)) + loss) / period
        rsi_values[index] = 100 if avg_loss == 0 else 100 - (100 / (1 + (avg_gain / avg_loss)))

    return rsi_values


def calculate_vwap(candles: list[dict]) -> list[float | None]:
    vwap_values = []
    cumulative_price_volume = 0
    cumulative_volume = 0

    for candle in candles:
        volume = candle.get("volume", 0) or 0
        typical_price = (candle["high"] + candle["low"] + candle["close"]) / 3
        cumulative_price_volume += typical_price * volume
        cumulative_volume += volume
        vwap_values.append(cumulative_price_volume / cumulative_volume if cumulative_volume else typical_price)

    return vwap_values


def calculate_atr(candles: list[dict], period: int = 14) -> list[float | None]:
    if len(candles) <= period:
        return [None] * len(candles)

    true_ranges = []
    for index, candle in enumerate(candles):
        high = float(candle["high"])
        low = float(candle["low"])
        if index == 0:
            true_ranges.append(high - low)
            continue

        previous_close = float(candles[index - 1]["close"])
        true_ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))

    atr_values = [None] * len(candles)
    atr = sum(true_ranges[1 : period + 1]) / period
    atr_values[period] = atr

    for index in range(period + 1, len(candles)):
        atr = ((atr * (period - 1)) + true_ranges[index]) / period
        atr_values[index] = atr

    return atr_values


def calculate_adx(candles: list[dict], period: int = 14) -> list[float | None]:
    if len(candles) <= period * 2:
        return [None] * len(candles)

    true_ranges = [0.0]
    plus_dm = [0.0]
    minus_dm = [0.0]

    for index in range(1, len(candles)):
        current = candles[index]
        previous = candles[index - 1]
        high = float(current["high"])
        low = float(current["low"])
        previous_high = float(previous["high"])
        previous_low = float(previous["low"])
        previous_close = float(previous["close"])

        up_move = high - previous_high
        down_move = previous_low - low
        plus_dm.append(up_move if up_move > down_move and up_move > 0 else 0.0)
        minus_dm.append(down_move if down_move > up_move and down_move > 0 else 0.0)
        true_ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))

    adx_values = [None] * len(candles)
    tr_sum = sum(true_ranges[1 : period + 1])
    plus_sum = sum(plus_dm[1 : period + 1])
    minus_sum = sum(minus_dm[1 : period + 1])
    dx_values = [None] * len(candles)

    for index in range(period, len(candles)):
        if index > period:
            tr_sum = tr_sum - (tr_sum / period) + true_ranges[index]
            plus_sum = plus_sum - (plus_sum / period) + plus_dm[index]
            minus_sum = minus_sum - (minus_sum / period) + minus_dm[index]

        plus_di = 100 * (plus_sum / tr_sum) if tr_sum else 0
        minus_di = 100 * (minus_sum / tr_sum) if tr_sum else 0
        denominator = plus_di + minus_di
        dx_values[index] = 100 * abs(plus_di - minus_di) / denominator if denominator else 0

        if index == period * 2:
            ready_dx = [value for value in dx_values[period : index + 1] if value is not None]
            adx_values[index] = sum(ready_dx) / len(ready_dx) if ready_dx else None
        elif index > period * 2 and adx_values[index - 1] is not None:
            adx_values[index] = ((adx_values[index - 1] * (period - 1)) + dx_values[index]) / period

    return adx_values


def enrich_candles(candles: list[dict]) -> list[dict]:
    closes = [float(candle["close"]) for candle in candles]
    ema_9 = calculate_ema(closes, 9)
    ema_21 = calculate_ema(closes, 21)
    rsi_14 = calculate_rsi(closes, 14)
    vwap = calculate_vwap(candles)
    atr_14 = calculate_atr(candles, 14)
    adx_14 = calculate_adx(candles, 14)

    enriched = []
    for index, candle in enumerate(candles):
        row = dict(candle)
        row["ema_9"] = round(ema_9[index], 2) if ema_9[index] is not None else None
        row["ema_21"] = round(ema_21[index], 2) if ema_21[index] is not None else None
        row["rsi_14"] = round(rsi_14[index], 2) if rsi_14[index] is not None else None
        row["vwap"] = round(vwap[index], 2) if vwap[index] is not None else None
        row["atr_14"] = round(atr_14[index], 2) if atr_14[index] is not None else None
        row["adx_14"] = round(adx_14[index], 2) if adx_14[index] is not None else None
        enriched.append(row)

    return enriched
