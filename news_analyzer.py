# news_analyzer.py - Market news safety filter

import ssl
from html import unescape
from time import time
from urllib.error import URLError
from urllib.request import Request, urlopen
from xml.etree import ElementTree

import config


_NEWS_CACHE = {"timestamp": 0, "result": None}


POSITIVE_KEYWORDS = {
    "rally",
    "gains",
    "gain",
    "higher",
    "surge",
    "jumps",
    "up",
    "positive",
    "bullish",
    "record high",
    "growth",
    "beats",
    "strong",
    "eases",
    "cut",
}

NEGATIVE_KEYWORDS = {
    "falls",
    "fall",
    "drops",
    "drop",
    "lower",
    "selloff",
    "sell-off",
    "crash",
    "weak",
    "negative",
    "bearish",
    "losses",
    "decline",
    "slips",
    "tumbles",
    "inflation",
    "rate hike",
    "war",
    "tariff",
}

HIGH_RISK_KEYWORDS = {
    "breaking",
    "rbi policy",
    "fed decision",
    "emergency",
    "war",
    "attack",
    "crash",
    "panic",
    "rate hike",
    "circuit",
    "election result",
}


def analyze_news() -> dict:
    if not config.NEWS_FILTER_ENABLED:
        return _result("DISABLED", "LOW", "News filter is disabled.", [])

    cached = _NEWS_CACHE.get("result")
    if cached and time() - _NEWS_CACHE["timestamp"] < config.NEWS_CACHE_SECONDS:
        return cached

    try:
        headlines = fetch_headlines()
    except Exception as error:
        result = _result(
            "UNAVAILABLE",
            "UNKNOWN",
            f"News fetch failed, so chart-only logic continues. Error: {error}",
            [],
        )
        _save_cache(result)
        return result

    if not headlines:
        result = _result("NEUTRAL", "LOW", "No fresh news headlines found.", [])
        _save_cache(result)
        return result

    positive_score = _score_headlines(headlines, POSITIVE_KEYWORDS)
    negative_score = _score_headlines(headlines, NEGATIVE_KEYWORDS)
    high_risk_score = _score_headlines(headlines, HIGH_RISK_KEYWORDS)

    if high_risk_score:
        result = _result(
            "HIGH_RISK",
            "HIGH",
            "High-risk market/news keywords found. Fresh trades should be avoided.",
            headlines,
        )
        _save_cache(result)
        return result

    if positive_score > negative_score:
        result = _result("POSITIVE", "LOW", "Positive market headlines are stronger than negative headlines.", headlines)
        _save_cache(result)
        return result

    if negative_score > positive_score:
        result = _result("NEGATIVE", "MEDIUM", "Negative market headlines are stronger than positive headlines.", headlines)
        _save_cache(result)
        return result

    result = _result("NEUTRAL", "LOW", "News sentiment is mixed or neutral.", headlines)
    _save_cache(result)
    return result


def apply_news_filter(signal: dict, news: dict) -> dict:
    final_signal = dict(signal)
    chart_signal = final_signal.get("SIGNAL")
    sentiment = news.get("sentiment")
    risk_level = news.get("risk_level")

    final_signal["NEWS_SENTIMENT"] = sentiment
    final_signal["NEWS_RISK_LEVEL"] = risk_level
    final_signal["NEWS_REASON"] = news.get("reason")
    final_signal["NEWS_HEADLINES"] = " | ".join(news.get("headlines", [])[:3])

    if chart_signal not in {"BUY", "SELL"}:
        final_signal["NEWS_MODE"] = "REFERENCE_ONLY"
        return final_signal

    block_reason = None
    if sentiment == "HIGH_RISK" and config.NEWS_BLOCK_HIGH_RISK:
        block_reason = news.get("reason")
    elif config.NEWS_BLOCK_TRADES and chart_signal == "BUY" and sentiment == "NEGATIVE":
        block_reason = "Chart is bullish, but news sentiment is negative."
    elif config.NEWS_BLOCK_TRADES and chart_signal == "SELL" and sentiment == "POSITIVE":
        block_reason = "Chart is bearish, but news sentiment is positive."

    if block_reason:
        final_signal["SIGNAL"] = "HOLD"
        final_signal["CONFIDENCE"] = "NEWS_FILTER"
        final_signal["REASON"] = f"{block_reason} Original chart reason: {signal.get('REASON')}"
        final_signal["STOP_LOSS"] = "N/A"
        final_signal["TARGET"] = "N/A"
        final_signal["NEWS_BLOCKED_ORIGINAL_SIGNAL"] = chart_signal
        final_signal["NEWS_MODE"] = "HIGH_RISK_BLOCK" if sentiment == "HIGH_RISK" else "CONFLICT_BLOCK"
    else:
        final_signal["NEWS_MODE"] = "REFERENCE_ONLY"
        if (
            (chart_signal == "BUY" and sentiment == "NEGATIVE")
            or (chart_signal == "SELL" and sentiment == "POSITIVE")
        ):
            final_signal["NEWS_CONFLICT"] = True

    return final_signal


def fetch_headlines() -> list[str]:
    headlines = []

    for url in config.NEWS_RSS_URLS:
        request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        xml_text = _open_rss(request)

        root = ElementTree.fromstring(xml_text)
        for item in root.findall(".//item"):
            title = item.findtext("title")
            if title:
                cleaned = _clean_title(title)
                if cleaned and cleaned not in headlines:
                    headlines.append(cleaned)
            if len(headlines) >= config.NEWS_MAX_HEADLINES:
                return headlines

    return headlines[: config.NEWS_MAX_HEADLINES]


def _open_rss(request: Request) -> bytes:
    try:
        with urlopen(request, timeout=config.NEWS_FETCH_TIMEOUT) as response:
            return response.read()
    except (ssl.SSLCertVerificationError, URLError) as error:
        if isinstance(error, URLError) and not _is_ssl_certificate_error(error):
            raise

        context = _certifi_context()
        if context:
            with urlopen(request, timeout=config.NEWS_FETCH_TIMEOUT, context=context) as response:
                return response.read()

        if config.NEWS_ALLOW_UNVERIFIED_SSL_FALLBACK:
            fallback_context = ssl._create_unverified_context()
            with urlopen(request, timeout=config.NEWS_FETCH_TIMEOUT, context=fallback_context) as response:
                return response.read()

        raise


def _is_ssl_certificate_error(error: URLError) -> bool:
    reason = getattr(error, "reason", None)
    return isinstance(reason, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(error)


def _certifi_context():
    try:
        import certifi
    except ImportError:
        return None

    return ssl.create_default_context(cafile=certifi.where())


def _score_headlines(headlines: list[str], keywords: set[str]) -> int:
    score = 0
    for headline in headlines:
        lower = headline.lower()
        score += sum(1 for keyword in keywords if keyword in lower)
    return score


def _clean_title(title: str) -> str:
    return " ".join(unescape(title).replace("\n", " ").split())


def _result(sentiment: str, risk_level: str, reason: str, headlines: list[str]) -> dict:
    return {
        "sentiment": sentiment,
        "risk_level": risk_level,
        "reason": reason,
        "headlines": headlines[: config.NEWS_MAX_HEADLINES],
    }


def _save_cache(result: dict) -> None:
    _NEWS_CACHE["timestamp"] = time()
    _NEWS_CACHE["result"] = result
