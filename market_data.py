# market_data.py - Fetch index quotes and historical candle data with Fyers API v3

from datetime import datetime, timedelta

import config
from index_config import get_index_config
from market_time import market_now_naive


def _map_candle_interval(interval: str | None) -> str:
    val = (interval or config.CANDLE_INTERVAL).lower()
    if "15" in val:
        return "15"
    if "5" in val:
        return "5"
    if "3" in val:
        return "3"
    if "10" in val:
        return "10"
    if "30" in val:
        return "30"
    if "60" in val or "hour" in val:
        return "60"
    if "day" in val:
        return "D"
    return "5"


def fetch_index_market_data(fyers, symbol: str) -> dict:
    index_config = get_index_config(symbol)
    quote_key = index_config["quote_key"]

    res = fyers.quotes({"symbols": quote_key})
    d_list = res.get("d") or []
    item = d_list[0] if d_list else {}
    v = item.get("v") or {}

    last_price = float(v.get("lp") or v.get("last_price") or 0.0)
    previous_close = float(v.get("prev_close_price") or v.get("previous_close") or last_price or 1.0)
    high = float(v.get("high_price") or v.get("high") or last_price)
    low = float(v.get("low_price") or v.get("low") or last_price)
    open_price = float(v.get("open_price") or v.get("open") or last_price)
    volume = int(v.get("volume") or 0)
    chp = float(v.get("chp") or round(((last_price - previous_close) / previous_close) * 100, 2) if previous_close else 0.0)

    return {
        "symbol": index_config["symbol"],
        "quote_key": quote_key,
        "last_price": last_price,
        "high": high,
        "low": low,
        "open": open_price,
        "previous_close": previous_close,
        "volume": volume,
        "change_percent": chp,
    }


def fetch_index_candles(fyers, symbol: str, interval: str | None = None, lookback_days: int | None = None) -> list[dict]:
    index_config = get_index_config(symbol)
    quote_key = index_config["quote_key"]
    resolution = _map_candle_interval(interval)

    to_date = market_now_naive()
    from_date = to_date - timedelta(days=lookback_days or config.CANDLE_LOOKBACK_DAYS)

    payload = {
        "symbol": quote_key,
        "resolution": resolution,
        "date_format": "1",
        "range_from": from_date.strftime("%Y-%m-%d"),
        "range_to": to_date.strftime("%Y-%m-%d"),
        "cont_flag": "1",
    }

    response = fyers.history(payload)
    if not isinstance(response, dict):
        return []

    raw_candles = response.get("candles") or []
    formatted = []
    for c in raw_candles:
        if len(c) >= 5:
            dt = datetime.fromtimestamp(c[0]) if isinstance(c[0], (int, float)) else c[0]
            formatted.append({
                "date": dt,
                "open": float(c[1]),
                "high": float(c[2]),
                "low": float(c[3]),
                "close": float(c[4]),
                "volume": int(c[5]) if len(c) > 5 else 0,
            })

    return formatted


def fetch_nifty_market_data(fyers) -> dict:
    return fetch_index_market_data(fyers, "NIFTY")


def fetch_nifty_candles(fyers) -> list[dict]:
    return fetch_index_candles(fyers, "NIFTY")
