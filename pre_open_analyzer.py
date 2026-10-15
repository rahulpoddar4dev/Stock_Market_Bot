# pre_open_analyzer.py - Capture pre-open market context before fresh trades start

import json
from pathlib import Path

import config
from log_utils import csv_date, csv_datetime
from market_data import fetch_index_market_data


LOG_DIR = Path("logs")
PRE_OPEN_FILE = LOG_DIR / "pre_open_market.json"
DATEWISE_DIR = LOG_DIR / "by_date"


def _bias_from_gap(gap_percent: float) -> str:
    if gap_percent >= config.PRE_OPEN_STRONG_GAP_PERCENT:
        return "STRONG_BULLISH"
    if gap_percent >= config.PRE_OPEN_GAP_PERCENT:
        return "BULLISH"
    if gap_percent <= -config.PRE_OPEN_STRONG_GAP_PERCENT:
        return "STRONG_BEARISH"
    if gap_percent <= -config.PRE_OPEN_GAP_PERCENT:
        return "BEARISH"
    return "FLAT"


def _overall_bias(rows: list[dict]) -> str:
    biases = [row.get("bias") for row in rows]
    if any(bias == "STRONG_BULLISH" for bias in biases):
        return "STRONG_BULLISH"
    if any(bias == "STRONG_BEARISH" for bias in biases):
        return "STRONG_BEARISH"
    if biases and all(bias in {"BULLISH", "STRONG_BULLISH"} for bias in biases):
        return "BULLISH"
    if biases and all(bias in {"BEARISH", "STRONG_BEARISH"} for bias in biases):
        return "BEARISH"
    if "BULLISH" in biases and "BEARISH" in biases:
        return "MIXED"
    return "FLAT"


def analyze_pre_open_market(fyers) -> dict:
    rows = []
    errors = []

    for symbol in config.ANALYZE_INDICES:
        try:
            data = fetch_index_market_data(fyers, symbol)
            previous_close = float(data.get("previous_close") or 0)
            last_price = float(data.get("last_price") or 0)
            open_price = float(data.get("open") or 0)
            reference_price = open_price or last_price
            gap_percent = round(((reference_price - previous_close) / previous_close) * 100, 2) if previous_close else 0
            rows.append({
                "symbol": symbol,
                "last_price": last_price,
                "open": open_price,
                "previous_close": previous_close,
                "gap_percent": gap_percent,
                "bias": _bias_from_gap(gap_percent),
            })
        except Exception as error:
            errors.append({"symbol": symbol, "error": str(error)})

    result = {
        "date": csv_date(),
        "event_time": csv_datetime(),
        "status": "OK" if rows else "ERROR",
        "overall_bias": _overall_bias(rows),
        "indices": rows,
        "errors": errors,
        "note": "Pre-open is reference only. It helps bias awareness, but does not place trades by itself.",
    }
    save_pre_open_context(result)
    return result


def save_pre_open_context(result: dict) -> None:
    LOG_DIR.mkdir(exist_ok=True)
    PRE_OPEN_FILE.write_text(json.dumps(result, indent=2))

    daily_dir = DATEWISE_DIR / result["date"]
    daily_dir.mkdir(parents=True, exist_ok=True)
    (daily_dir / "pre_open_market.json").write_text(json.dumps(result, indent=2))


def load_pre_open_context() -> dict:
    if not PRE_OPEN_FILE.exists():
        return {}
    try:
        return json.loads(PRE_OPEN_FILE.read_text())
    except json.JSONDecodeError:
        return {}
