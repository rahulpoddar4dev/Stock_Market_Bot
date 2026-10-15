# free_market_data.py - Best-effort public market context quotes

from __future__ import annotations

import json
import ssl
import time
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import config


_CACHE = {
    "timestamp": 0.0,
    "quotes": {},
    "error": "",
}
_GIFT_NIFTY_CACHE = {
    "timestamp": 0.0,
    "quote": {},
    "error": "",
}


def fetch_free_market_quotes(symbols: list[str]) -> tuple[dict[str, dict], str]:
    """
    Fetch public quote snapshots for external context data.

    This uses a free public Yahoo Finance quote endpoint. It is intentionally
    best-effort: failures return an empty quote map and an error string so live
    trading never crashes because an external context source is slow/unavailable.
    """
    unique_symbols = sorted({symbol for symbol in symbols if symbol})
    if not unique_symbols or not config.FREE_MARKET_DATA_ENABLED:
        return {}, "Free market data disabled or no symbols configured."

    now = time.time()
    if now - float(_CACHE["timestamp"] or 0) <= config.FREE_MARKET_DATA_CACHE_SECONDS:
        cached_quotes = _CACHE.get("quotes") or {}
        if all(symbol in cached_quotes for symbol in unique_symbols):
            return dict(cached_quotes), str(_CACHE.get("error") or "")

    quotes = {}
    errors = []
    for symbol in unique_symbols:
        try:
            quotes[symbol] = _fetch_yahoo_chart_quote(symbol)
        except Exception as error:
            errors.append(f"{symbol}: {error}")

    missing = [symbol for symbol in unique_symbols if symbol not in quotes]
    error = f"Missing symbols: {', '.join(missing)}" if missing else ""
    if errors:
        error = f"{error}; {'; '.join(errors)}".strip("; ")
    _CACHE.update({"timestamp": now, "quotes": quotes, "error": error})
    return quotes, error


def _fetch_yahoo_chart_quote(symbol: str) -> dict:
    encoded = quote(symbol, safe="")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}?range=5d&interval=1d"
    request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    payload = _open_json(request)
    result = ((payload.get("chart") or {}).get("result") or [None])[0]
    if not result:
        raise ValueError("empty chart response")

    meta = result.get("meta") or {}
    price = meta.get("regularMarketPrice")
    previous_close = meta.get("previousClose") or meta.get("chartPreviousClose")
    close_values = ((result.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
    valid_closes = [float(value) for value in close_values if value not in {None, ""}]
    if (not previous_close or previous_close == price) and len(valid_closes) >= 2:
        previous_close = valid_closes[-2]
    if not price and valid_closes:
        price = valid_closes[-1]

    change_percent = None
    if price and previous_close:
        change_percent = ((float(price) - float(previous_close)) / float(previous_close)) * 100

    return {
        "symbol": symbol,
        "name": meta.get("shortName") or meta.get("longName") or symbol,
        "price": round(float(price), 4) if price is not None else None,
        "previous_close": round(float(previous_close), 4) if previous_close is not None else None,
        "change_percent": round(float(change_percent), 2) if change_percent is not None else None,
        "market_state": meta.get("marketState"),
        "exchange": meta.get("fullExchangeName") or meta.get("exchangeName"),
        "currency": meta.get("currency"),
        "source": "Yahoo Finance public chart",
    }


def fetch_gift_nifty_quote() -> tuple[dict, str]:
    """
    Fetch GIFT/NSE IX Nifty futures from NSE IX public derivative market-rate API.

    NSE IX publishes this as SYMBOL=NIFTY, INSTRUMENTTYPE=FUTIDX. We use the
    nearest expiry row as the GIFT Nifty context reference.
    """
    if not config.FREE_GIFT_NIFTY_NSEIX_ENABLED:
        return {}, "NSE IX GIFT Nifty source disabled."

    now = time.time()
    if now - float(_GIFT_NIFTY_CACHE["timestamp"] or 0) <= config.FREE_MARKET_DATA_CACHE_SECONDS:
        return dict(_GIFT_NIFTY_CACHE.get("quote") or {}), str(_GIFT_NIFTY_CACHE.get("error") or "")

    request = Request(
        config.FREE_GIFT_NIFTY_NSEIX_URL,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.nseix.com/markets/derivatives-watch",
        },
    )

    try:
        payload = _open_json(request)
        rows = [
            row for row in payload.get("data", [])
            if row.get("SYMBOL") == "NIFTY" and row.get("INSTRUMENTTYPE") == "FUTIDX"
        ]
        if not rows:
            raise ValueError("NSE IX NIFTY futures row not found")
        row = rows[0]
        quote = {
            "symbol": "NSEIX:NIFTY_FUT",
            "name": "GIFT Nifty",
            "price": _float_or_none(row.get("LASTPRICE")),
            "previous_close": None,
            "change": _float_or_none(row.get("DAYCHANGE")),
            "change_percent": _float_or_none(row.get("PERCHANGE")),
            "expiry": row.get("EXPIRYDATE"),
            "timestamp": row.get("TIMESTMP"),
            "contracts_traded": row.get("CONTRACTSTRADED"),
            "source": "NSE IX public market-rate",
        }
        _GIFT_NIFTY_CACHE.update({"timestamp": now, "quote": quote, "error": ""})
        return quote, ""
    except Exception as error:
        _GIFT_NIFTY_CACHE.update({"timestamp": now, "quote": {}, "error": str(error)})
        return {}, str(error)


def _float_or_none(value):
    if value in {None, "", "-"}:
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _open_json(request: Request) -> dict:
    try:
        with urlopen(request, timeout=config.FREE_MARKET_DATA_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except (ssl.SSLCertVerificationError, URLError) as error:
        if not _should_retry_without_ssl_verification(error):
            raise
        context = ssl._create_unverified_context()
        with urlopen(request, timeout=config.FREE_MARKET_DATA_TIMEOUT_SECONDS, context=context) as response:
            return json.loads(response.read().decode("utf-8"))


def _should_retry_without_ssl_verification(error: Exception) -> bool:
    if not config.FREE_MARKET_ALLOW_UNVERIFIED_SSL_FALLBACK:
        return False
    return "CERTIFICATE_VERIFY_FAILED" in str(error) or isinstance(error, ssl.SSLCertVerificationError)


def quote_change_percent(quote: dict) -> float:
    change = quote.get("change_percent")
    if change not in {None, ""}:
        return round(float(change), 2)

    price = float(quote.get("price") or 0)
    previous_close = float(quote.get("previous_close") or 0)
    if not price or not previous_close:
        return 0.0
    return round(((price - previous_close) / previous_close) * 100, 2)
