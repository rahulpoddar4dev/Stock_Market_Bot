# market_context.py - Broader market context scoring before final trade approval

from __future__ import annotations

from datetime import date, datetime

import config
from free_market_data import fetch_free_market_quotes, fetch_gift_nifty_quote, quote_change_percent
from index_config import get_index_config
from market_time import today_date


_INSTRUMENT_CACHE: dict[str, list[dict]] = {}


def _component(name: str, status: str, bias: str, score: float, reason: str, data: dict | None = None) -> dict:
    return {
        "name": name,
        "status": status,
        "bias": bias,
        "score": round(float(score), 2),
        "reason": reason,
        "data": data or {},
    }


def _quote_change_percent(quote: dict) -> float:
    last_price = float(quote.get("last_price") or 0)
    previous_close = float((quote.get("ohlc") or {}).get("close") or 0)
    if not last_price or not previous_close:
        return 0.0
    return round(((last_price - previous_close) / previous_close) * 100, 2)


def _bias_from_score(score: float) -> str:
    if score >= config.MARKET_CONTEXT_BULLISH_SCORE:
        return "BULLISH"
    if score <= config.MARKET_CONTEXT_BEARISH_SCORE:
        return "BEARISH"
    return "MIXED"


def _risk_from_score(score: float, components: list[dict]) -> str:
    if any(component["bias"] == "HIGH_RISK" for component in components):
        return "HIGH"
    if score <= config.MARKET_CONTEXT_HIGH_RISK_SCORE:
        return "HIGH"
    if abs(score) < 8:
        return "MEDIUM"
    return "LOW"


def _quote_component(fyers, key: str, name: str, bullish_min: float, bearish_max: float) -> dict:
    try:
        quote = fyers.quote([key])[key]
        change = _quote_change_percent(quote)
    except Exception as error:
        return _component(name, "UNAVAILABLE", "NEUTRAL", 0, f"{name} quote unavailable: {error}")

    if change >= bullish_min:
        return _component(name, "OK", "BULLISH", 8, f"{name} positive {change}%.", {"change_percent": change})
    if change <= -abs(bearish_max):
        return _component(name, "OK", "BEARISH", -8, f"{name} negative {change}%.", {"change_percent": change})
    return _component(name, "OK", "NEUTRAL", 0, f"{name} flat/mixed {change}%.", {"change_percent": change})


def _india_vix_component(fyers) -> dict:
    try:
        from index_config import get_index_config
        vix_key = get_index_config("INDIA_VIX")["quote_key"]
        quotes = fyers.quote([vix_key])
        quote = quotes.get(vix_key) or quotes.get("NSE:INDIAVIX-INDEX") or {}
        vix = float(quote.get("last_price") or 0)
        change = _quote_change_percent(quote)
    except Exception as error:
        return _component("India VIX", "UNAVAILABLE", "NEUTRAL", 0, f"India VIX unavailable: {error}")

    crush_threshold = float(getattr(config, "VIX_CRUSH_CHANGE_PERCENT", 3.0))
    if getattr(config, "VIX_FILTER_ENABLED", True) and getattr(config, "VIX_BLOCK_ON_IV_CRUSH", False) and change <= -abs(crush_threshold):
        return _component(
            "India VIX",
            "OK",
            "HIGH_RISK",
            -25,
            f"India VIX crashing {change}% (<= -{crush_threshold}%); severe IV crush decay risk.",
            {"vix": vix, "change_percent": change, "iv_crush": True},
        )
    if change <= -abs(crush_threshold) and not getattr(config, "VIX_BLOCK_ON_IV_CRUSH", False):
        return _component(
            "India VIX",
            "OK",
            "BULLISH" if vix <= config.MARKET_CONTEXT_VIX_HIGH else "NEUTRAL",
            5 if vix <= config.MARKET_CONTEXT_VIX_HIGH else 0,
            f"India VIX cooled {change}% to {vix}; market fear easing / bullish sentiment supported.",
            {"vix": vix, "change_percent": change, "iv_crush": False},
        )

    if vix >= config.MARKET_CONTEXT_VIX_EXTREME:
        return _component("India VIX", "OK", "HIGH_RISK", -25, f"VIX extreme at {vix}; option volatility risk high.", {"vix": vix, "change_percent": change})
    if vix >= config.MARKET_CONTEXT_VIX_HIGH:
        return _component("India VIX", "OK", "HIGH_RISK", -15, f"VIX high at {vix}; avoid weak setups.", {"vix": vix, "change_percent": change})
    if vix <= config.MARKET_CONTEXT_VIX_LOW:
        return _component("India VIX", "OK", "BULLISH", 5, f"VIX low at {vix}; volatility risk calmer.", {"vix": vix, "change_percent": change})
    return _component("India VIX", "OK", "NEUTRAL", 0, f"VIX normal at {vix}.", {"vix": vix, "change_percent": change})


def _support_indices_component(fyers) -> dict:
    rows = []
    score = 0
    for name, key in config.MARKET_CONTEXT_SUPPORT_INDICES.items():
        try:
            quote = fyers.quote([key])[key]
            change = _quote_change_percent(quote)
            rows.append({"name": name, "key": key, "change_percent": change})
            if change >= config.MARKET_CONTEXT_SUPPORT_INDEX_CHANGE_MIN:
                score += 6
            elif change <= -config.MARKET_CONTEXT_SUPPORT_INDEX_CHANGE_MIN:
                score -= 6
        except Exception as error:
            rows.append({"name": name, "key": key, "error": str(error)})

    available = [row for row in rows if "change_percent" in row]
    if not available:
        return _component("Bank/Fin Nifty", "UNAVAILABLE", "NEUTRAL", 0, "Support indices unavailable.", {"indices": rows})

    avg_change = round(sum(row["change_percent"] for row in available) / len(available), 2)
    bias = "BULLISH" if score > 0 else "BEARISH" if score < 0 else "NEUTRAL"
    return _component("Bank/Fin Nifty", "OK", bias, score, f"Support indices avg change {avg_change}%.", {"indices": rows, "avg_change_percent": avg_change})


def _top_stock_breadth_component(fyers) -> dict:
    keys = list(config.MARKET_CONTEXT_TOP_STOCKS)
    try:
        quotes = fyers.quote(keys)
    except Exception as error:
        return _component("Top Index Stocks", "UNAVAILABLE", "NEUTRAL", 0, f"Top stock quotes unavailable: {error}")

    rows = []
    advances = 0
    declines = 0
    for key in keys:
        quote = quotes.get(key) or {}
        change = _quote_change_percent(quote)
        rows.append({"symbol": key, "change_percent": change})
        if change >= config.MARKET_CONTEXT_TOP_STOCK_CHANGE_MIN:
            advances += 1
        elif change <= -config.MARKET_CONTEXT_TOP_STOCK_CHANGE_MIN:
            declines += 1

    total = len(rows) or 1
    advance_percent = round((advances / total) * 100, 2)
    decline_percent = round((declines / total) * 100, 2)
    if advance_percent >= config.MARKET_CONTEXT_BREADTH_POSITIVE_PERCENT:
        return _component("Top Index Stocks", "OK", "BULLISH", 10, f"Top-stock breadth positive: {advance_percent}% advancing.", {"stocks": rows, "advance_percent": advance_percent, "decline_percent": decline_percent})
    if advance_percent <= config.MARKET_CONTEXT_BREADTH_NEGATIVE_PERCENT and declines > advances:
        return _component("Top Index Stocks", "OK", "BEARISH", -10, f"Top-stock breadth weak: {decline_percent}% declining.", {"stocks": rows, "advance_percent": advance_percent, "decline_percent": decline_percent})
    return _component("Top Index Stocks", "OK", "MIXED", 0, f"Top-stock breadth mixed: {advance_percent}% advancing.", {"stocks": rows, "advance_percent": advance_percent, "decline_percent": decline_percent})


def _cached_instruments(fyers, exchange: str) -> list[dict]:
    if exchange not in _INSTRUMENT_CACHE:
        if hasattr(fyers, "instruments"):
            _INSTRUMENT_CACHE[exchange] = fyers.instruments(exchange)
        else:
            _INSTRUMENT_CACHE[exchange] = []
    return _INSTRUMENT_CACHE[exchange]


def _as_date(value) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            return None
    return None


def _nearest_option_chain_component(fyers, index_symbol: str | None, spot_price: float | None) -> dict:
    if not index_symbol or not spot_price:
        return _component("Option Chain", "UNAVAILABLE", "NEUTRAL", 0, "Selected index/spot not available yet.")

    try:
        index_config = get_index_config(index_symbol)
        exchange = index_config["option_exchange"]
        step = int(index_config["strike_step"])
        atm = int(round(float(spot_price) / step) * step)
        today = today_date()

        call_oi = 0
        put_oi = 0
        call_by_strike = {}
        put_by_strike = {}
        expiry = today

        # 1. Try native Fyers optionchain
        if hasattr(fyers, "optionchain"):
            try:
                res = fyers.optionchain({
                    "symbol": index_config["quote_key"],
                    "strikecount": config.MARKET_CONTEXT_OPTION_CHAIN_STRIKES_EACH_SIDE + 2,
                    "timestamp": "",
                })
                if isinstance(res, dict) and res.get("s") == "ok":
                    chain_list = (res.get("data") or {}).get("optionsChain") or []
                    for item in chain_list:
                        strike = int(float(item.get("strike_price") or 0))
                        opt_t = str(item.get("option_type") or "").upper()
                        oi = int(float(item.get("oi") or 0))
                        if opt_t == "CE":
                            call_oi += oi
                            call_by_strike[strike] = call_by_strike.get(strike, 0) + oi
                        elif opt_t == "PE":
                            put_oi += oi
                            put_by_strike[strike] = put_by_strike.get(strike, 0) + oi
            except Exception:
                pass

        if call_oi <= 0 and put_oi <= 0 and hasattr(fyers, "instruments"):
            instruments = _cached_instruments(fyers, exchange)
            expiries = sorted({
                exp
                for item in instruments
                for exp in [_as_date(item.get("expiry"))]
                if item.get("name") == index_config["option_name"] and exp and exp >= today
            })
            if expiries:
                expiry = expiries[0]
                strikes = {
                    atm + offset * step
                    for offset in range(-config.MARKET_CONTEXT_OPTION_CHAIN_STRIKES_EACH_SIDE, config.MARKET_CONTEXT_OPTION_CHAIN_STRIKES_EACH_SIDE + 1)
                }
                contracts = [
                    item
                    for item in instruments
                    if item.get("name") == index_config["option_name"]
                    and _as_date(item.get("expiry")) == expiry
                    and int(float(item.get("strike") or 0)) in strikes
                    and item.get("instrument_type") in {"CE", "PE"}
                ]
                quote_keys = [f"{item['exchange']}:{item['tradingsymbol']}" for item in contracts]
                quotes = fyers.quote(quote_keys) if quote_keys and hasattr(fyers, "quote") else {}
                for item in contracts:
                    key = f"{item['exchange']}:{item['tradingsymbol']}"
                    oi = int(float((quotes.get(key) or {}).get("oi") or 0))
                    strike = int(float(item.get("strike") or 0))
                    if item.get("instrument_type") == "CE":
                        call_oi += oi
                        call_by_strike[strike] = call_by_strike.get(strike, 0) + oi
                    else:
                        put_oi += oi
                        put_by_strike[strike] = put_by_strike.get(strike, 0) + oi

    except Exception as error:
        return _component("Option Chain", "UNAVAILABLE", "NEUTRAL", 0, f"Option chain unavailable: {error}")

    if call_oi <= 0 and put_oi <= 0:
        return _component("Option Chain", "UNAVAILABLE", "NEUTRAL", 0, "Option OI unavailable in quotes.")

    pcr = round(put_oi / call_oi, 2) if call_oi else 0
    support = max(put_by_strike, key=put_by_strike.get) if put_by_strike else None
    resistance = max(call_by_strike, key=call_by_strike.get) if call_by_strike else None
    if pcr >= config.MARKET_CONTEXT_PCR_BULLISH:
        bias, score = "BULLISH", 10
    elif 0 < pcr <= config.MARKET_CONTEXT_PCR_BEARISH:
        bias, score = "BEARISH", -10
    else:
        bias, score = "NEUTRAL", 0

    return _component("Option Chain", "OK", bias, score, f"Nearby option PCR {pcr}. Support {support}, resistance {resistance}.", {
        "pcr": pcr,
        "call_oi": call_oi,
        "put_oi": put_oi,
        "support": support,
        "resistance": resistance,
        "expiry": str(expiry),
        "atm": atm,
    })


def _event_news_component(news: dict | None) -> dict:
    news = news or {}
    sentiment = news.get("sentiment", "NEUTRAL")
    if sentiment in {"NEGATIVE", "HIGH_RISK"} or news.get("risk_level") == "HIGH":
        return _component("News/Event Calendar", "OK", "BEARISH", -10, news.get("reason") or "Negative/falling market news.", {"headlines": news.get("headlines", [])[:3]})
    if sentiment == "POSITIVE":
        return _component("News/Event Calendar", "OK", "BULLISH", 10, news.get("reason") or "Positive market news.", {"headlines": news.get("headlines", [])[:3]})
    return _component("News/Event Calendar", "OK", "NEUTRAL", 0, news.get("reason") or "No high-risk event found in configured news feeds.", {"headlines": news.get("headlines", [])[:3]})


def _all_free_market_symbols() -> list[str]:
    symbols = []
    for mapping in [
        config.FREE_MARKET_GLOBAL_SYMBOLS,
        config.FREE_MARKET_SINGAPORE_SYMBOLS,
        config.FREE_MARKET_GIFT_NIFTY_SYMBOLS,
        config.FREE_MARKET_CURRENCY_COMMODITY_SYMBOLS,
    ]:
        symbols.extend(mapping.values())
    return symbols


def _quotes_for_mapping(quotes: dict[str, dict], mapping: dict[str, str]) -> list[dict]:
    rows = []
    for name, symbol in mapping.items():
        quote = quotes.get(symbol)
        if not quote:
            rows.append({"name": name, "symbol": symbol, "available": False})
            continue
        rows.append({
            "name": name,
            "symbol": symbol,
            "available": True,
            "price": quote.get("price"),
            "change_percent": quote_change_percent(quote),
            "market_state": quote.get("market_state"),
            "source": quote.get("source"),
        })
    return rows


def _global_markets_component(free_quotes: dict[str, dict], free_error: str = "") -> dict:
    rows = _quotes_for_mapping(free_quotes, config.FREE_MARKET_GLOBAL_SYMBOLS)
    available = [row for row in rows if row.get("available")]
    if not available:
        reason = f"Dow/Nasdaq/S&P free quotes unavailable. {free_error}".strip()
        return _component("Global Markets", "UNAVAILABLE", "NEUTRAL", 0, reason, {"markets": rows})

    score = 0
    for row in available:
        change = float(row.get("change_percent") or 0)
        if change >= config.FREE_MARKET_GLOBAL_CHANGE_MIN:
            score += 4
        elif change <= -config.FREE_MARKET_GLOBAL_CHANGE_MIN:
            score -= 4
    avg_change = round(sum(float(row.get("change_percent") or 0) for row in available) / len(available), 2)
    bias = "BULLISH" if score > 0 else "BEARISH" if score < 0 else "NEUTRAL"
    return _component("Global Markets", "OK", bias, score, f"Global indices avg change {avg_change}%.", {"markets": rows, "avg_change_percent": avg_change})


def _singapore_market_component(free_quotes: dict[str, dict], free_error: str = "") -> dict:
    rows = _quotes_for_mapping(free_quotes, config.FREE_MARKET_SINGAPORE_SYMBOLS)
    available = [row for row in rows if row.get("available")]
    if not available:
        reason = f"Singapore market free quote unavailable. {free_error}".strip()
        return _component("Singapore Market", "UNAVAILABLE", "NEUTRAL", 0, reason, {"markets": rows})

    change = float(available[0].get("change_percent") or 0)
    if change >= config.FREE_MARKET_SINGAPORE_CHANGE_MIN:
        return _component("Singapore Market", "OK", "BULLISH", 5, f"Singapore STI positive {change}%.", {"markets": rows})
    if change <= -config.FREE_MARKET_SINGAPORE_CHANGE_MIN:
        return _component("Singapore Market", "OK", "BEARISH", -5, f"Singapore STI negative {change}%.", {"markets": rows})
    return _component("Singapore Market", "OK", "NEUTRAL", 0, f"Singapore STI flat/mixed {change}%.", {"markets": rows})


def _gift_nifty_component(free_quotes: dict[str, dict], gift_quote: dict | None = None, gift_error: str = "", free_error: str = "") -> dict:
    if gift_quote:
        change = float(gift_quote.get("change_percent") or 0)
        data = {"markets": [{
            "name": "GIFT Nifty",
            "symbol": gift_quote.get("symbol"),
            "available": True,
            "price": gift_quote.get("price"),
            "change_percent": change,
            "expiry": gift_quote.get("expiry"),
            "timestamp": gift_quote.get("timestamp"),
            "source": gift_quote.get("source"),
        }]}
        if change >= config.FREE_MARKET_GLOBAL_CHANGE_MIN:
            return _component("GIFT Nifty", "OK", "BULLISH", 8, f"GIFT Nifty positive {change}% via NSE IX.", data)
        if change <= -config.FREE_MARKET_GLOBAL_CHANGE_MIN:
            return _component("GIFT Nifty", "OK", "BEARISH", -8, f"GIFT Nifty negative {change}% via NSE IX.", data)
        return _component("GIFT Nifty", "OK", "NEUTRAL", 0, f"GIFT Nifty flat/mixed {change}% via NSE IX.", data)

    rows = _quotes_for_mapping(free_quotes, config.FREE_MARKET_GIFT_NIFTY_SYMBOLS)
    available = [row for row in rows if row.get("available")]
    if not available:
        errors = " | ".join(part for part in [gift_error, free_error] if part)
        reason = f"GIFT Nifty free quote unavailable. {errors}".strip()
        return _component("GIFT Nifty", "UNAVAILABLE", "NEUTRAL", 0, reason, {"markets": rows})

    selected = available[0]
    change = float(selected.get("change_percent") or 0)
    if change >= config.FREE_MARKET_GLOBAL_CHANGE_MIN:
        return _component("GIFT Nifty", "OK", "BULLISH", 8, f"{selected['name']} positive {change}%.", {"markets": rows})
    if change <= -config.FREE_MARKET_GLOBAL_CHANGE_MIN:
        return _component("GIFT Nifty", "OK", "BEARISH", -8, f"{selected['name']} negative {change}%.", {"markets": rows})
    return _component("GIFT Nifty", "OK", "NEUTRAL", 0, f"{selected['name']} flat/mixed {change}%.", {"markets": rows})


def _currency_crude_component(free_quotes: dict[str, dict], free_error: str = "") -> dict:
    rows = _quotes_for_mapping(free_quotes, config.FREE_MARKET_CURRENCY_COMMODITY_SYMBOLS)
    available = [row for row in rows if row.get("available")]
    if not available:
        reason = f"USDINR/Dollar/Crude free quotes unavailable. {free_error}".strip()
        return _component("Dollar/USDINR/Crude", "UNAVAILABLE", "NEUTRAL", 0, reason, {"markets": rows})

    score = 0
    reason_bits = []
    by_name = {row["name"]: row for row in available}
    usdinr = by_name.get("USDINR")
    dxy = by_name.get("Dollar Index")
    crude = by_name.get("Crude Oil")

    if usdinr:
        change = float(usdinr.get("change_percent") or 0)
        reason_bits.append(f"USDINR {change}%")
        if change >= config.FREE_MARKET_USDINR_CHANGE_MIN:
            score -= 4
        elif change <= -config.FREE_MARKET_USDINR_CHANGE_MIN:
            score += 4
    if dxy:
        change = float(dxy.get("change_percent") or 0)
        reason_bits.append(f"DXY {change}%")
        if change >= config.FREE_MARKET_DXY_CHANGE_MIN:
            score -= 3
        elif change <= -config.FREE_MARKET_DXY_CHANGE_MIN:
            score += 3
    if crude:
        change = float(crude.get("change_percent") or 0)
        reason_bits.append(f"Crude {change}%")
        if change >= config.FREE_MARKET_CRUDE_CHANGE_MIN:
            score -= 4
        elif change <= -config.FREE_MARKET_CRUDE_CHANGE_MIN:
            score += 4

    bias = "BULLISH" if score > 0 else "BEARISH" if score < 0 else "NEUTRAL"
    reason = ", ".join(reason_bits) if reason_bits else "Currency/commodity context mixed."
    return _component("Dollar/USDINR/Crude", "OK", bias, score, reason, {"markets": rows})


def _free_market_components() -> list[dict]:
    free_quotes, free_error = fetch_free_market_quotes(_all_free_market_symbols())
    gift_quote, gift_error = fetch_gift_nifty_quote()
    return [
        _global_markets_component(free_quotes, free_error),
        _singapore_market_component(free_quotes, free_error),
        _gift_nifty_component(free_quotes, gift_quote, gift_error, free_error),
        _currency_crude_component(free_quotes, free_error),
    ]


def analyze_market_context(fyers, selected_symbol: str | None = None, spot_price: float | None = None, news: dict | None = None) -> dict:
    if not config.MARKET_CONTEXT_ENABLED:
        return {
            "enabled": False,
            "bias": "DISABLED",
            "risk_level": "LOW",
            "score": 0,
            "components": [],
            "reason": "Market context disabled.",
            "confirm_buy": True,
            "confirm_sell": True,
        }

    components = [
        _india_vix_component(fyers),
        _support_indices_component(fyers),
        _top_stock_breadth_component(fyers),
        _nearest_option_chain_component(fyers, selected_symbol, spot_price),
        _event_news_component(news),
        *_free_market_components(),
    ]
    score = round(sum(component["score"] for component in components), 2)
    bias = _bias_from_score(score)
    risk_level = _risk_from_score(score, components)
    reason_bits = [
        f"{component['name']}: {component['bias']} ({component['reason']})"
        for component in components
        if component["status"] != "UNAVAILABLE"
    ]
    reason = " | ".join(reason_bits) or "No market context data available."
    iv_crush = any(bool(component.get("data", {}).get("iv_crush")) for component in components) and bool(getattr(config, "VIX_BLOCK_ON_IV_CRUSH", False))
    vix_change = next((component.get("data", {}).get("change_percent") for component in components if component.get("name") == "India VIX"), 0.0)
    return {
        "enabled": True,
        "bias": bias,
        "risk_level": risk_level,
        "score": score,
        "components": components,
        "reason": reason,
        "confirm_buy": bias in {"BULLISH", "MIXED"} and not iv_crush,
        "confirm_sell": bias in {"BEARISH", "MIXED"} and not iv_crush,
        "iv_crush": iv_crush,
        "vix_change": vix_change,
        "unavailable": [component["name"] for component in components if component["status"] == "UNAVAILABLE"],
    }
