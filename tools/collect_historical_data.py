#!/usr/bin/env python3
"""Collect backtest-ready historical market data after market close."""

import argparse
import csv
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import config
from index_config import get_index_config
from fyers_login import login
from market_data import _find_index_token


DATA_DIR = BASE_DIR / "data" / "historical"
EVENT_DIR = BASE_DIR / "data" / "events"
OPTION_DIR = BASE_DIR / "data" / "options"
LOG_DIR = BASE_DIR / "logs" / "data_collection"
FIELDS = [
    "datetime",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "oi",
    "previous_close",
    "gap",
    "gap_percent",
]
EVENT_FIELDS = ["date", "event_type", "name", "impact", "expected_bias", "actual_bias", "notes", "source"]
OPTION_MANIFEST_FIELDS = [
    "underlying",
    "expiry",
    "strike",
    "option_type",
    "tradingsymbol",
    "instrument_token",
    "interval",
    "status",
    "notes",
]


def _parse_args():
    parser = argparse.ArgumentParser(description="Collect NIFTY/SENSEX/VIX historical candles for backtesting.")
    parser.add_argument("--symbols", nargs="+", default=[*config.ANALYZE_INDICES, "INDIA_VIX"], help="Symbols to collect.")
    parser.add_argument("--interval", default=config.CANDLE_INTERVAL, help="Fyers candle interval, e.g. 5minute.")
    parser.add_argument("--years", type=int, default=10, help="Lookback years to fetch.")
    parser.add_argument("--days", type=int, default=None, help="Optional exact lookback days; overrides --years.")
    parser.add_argument("--chunk-days", type=int, default=60, help="Fetch in chunks to avoid Fyers range limits.")
    parser.add_argument("--sleep", type=float, default=0.4, help="Sleep seconds between Fyers calls.")
    parser.add_argument("--skip-events-template", action="store_true", help="Do not create/update market events template.")
    parser.add_argument("--skip-option-manifest", action="store_true", help="Do not create/update option manifest template.")
    return parser.parse_args()


def _csv_path(symbol: str, interval: str) -> Path:
    return DATA_DIR / f"{symbol.upper()}_{interval}.csv"


def _read_existing(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}

    with path.open(newline="") as csv_file:
        return {
            row["datetime"]: row
            for row in csv.DictReader(csv_file)
            if row.get("datetime")
        }


def _normalise_candle(candle: dict, previous_close: float | None) -> dict:
    value = candle.get("date")
    if hasattr(value, "isoformat"):
        candle_time = value.isoformat()
    else:
        candle_time = str(value)

    close = candle.get("close", "")
    gap = ""
    gap_percent = ""
    if previous_close not in {None, 0}:
        try:
            open_price = float(candle.get("open") or 0)
            gap = round(open_price - float(previous_close), 2)
            gap_percent = round((gap / float(previous_close)) * 100, 4)
        except (TypeError, ValueError):
            gap = ""
            gap_percent = ""

    return {
        "datetime": candle_time,
        "open": candle.get("open", ""),
        "high": candle.get("high", ""),
        "low": candle.get("low", ""),
        "close": close,
        "volume": candle.get("volume", ""),
        "oi": candle.get("oi", ""),
        "previous_close": previous_close or "",
        "gap": gap,
        "gap_percent": gap_percent,
    }


def _write_rows(path: Path, rows_by_time: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [rows_by_time[key] for key in sorted(rows_by_time)]

    with path.open("w", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def _fetch_symbol(fyers, symbol: str, interval: str, days: int, chunk_days: int, sleep_seconds: float) -> dict:
    token = _find_index_token(fyers, symbol)
    to_date = datetime.now()
    start_date = to_date - timedelta(days=days)
    cursor = start_date
    fetched = []

    while cursor < to_date:
        chunk_to = min(cursor + timedelta(days=chunk_days), to_date)
        candles = fyers.historical_data(
            instrument_token=token,
            from_date=cursor,
            to_date=chunk_to,
            interval=interval,
        )
        fetched.extend(candles)
        cursor = chunk_to
        time.sleep(sleep_seconds)

    path = _csv_path(symbol, interval)
    existing = _read_existing(path)
    before_count = len(existing)

    previous_close = None
    for candle in sorted(fetched, key=lambda item: item.get("date")):
        row = _normalise_candle(candle, previous_close)
        existing[row["datetime"]] = row
        try:
            previous_close = float(row["close"])
        except (TypeError, ValueError):
            previous_close = None

    _write_rows(path, existing)

    return {
        "symbol": symbol,
        "name": get_index_config(symbol)["name"],
        "interval": interval,
        "file": str(path),
        "fetched_rows": len(fetched),
        "previous_rows": before_count,
        "final_rows": len(existing),
        "new_or_updated_rows": len(existing) - before_count,
        "from": start_date.isoformat(),
        "to": to_date.isoformat(),
        "status": "OK",
    }


def _ensure_events_template() -> Path:
    path = EVENT_DIR / "market_events.csv"
    if path.exists():
        return path

    path.parent.mkdir(parents=True, exist_ok=True)
    sample_rows = [
        {
            "date": "",
            "event_type": "RBI/Fed/CPI/Election/Budget/News",
            "name": "",
            "impact": "LOW/MEDIUM/HIGH",
            "expected_bias": "BULLISH/BEARISH/NEUTRAL",
            "actual_bias": "",
            "notes": "Fill this after market close or before backtest import.",
            "source": "",
        }
    ]
    with path.open("w", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=EVENT_FIELDS)
        writer.writeheader()
        writer.writerows(sample_rows)
    return path


def _ensure_option_manifest(interval: str) -> Path:
    path = OPTION_DIR / "option_contract_manifest.csv"
    if path.exists():
        return path

    path.parent.mkdir(parents=True, exist_ok=True)
    sample_rows = [
        {
            "underlying": "NIFTY/SENSEX",
            "expiry": "YYYY-MM-DD",
            "strike": "",
            "option_type": "CE/PE",
            "tradingsymbol": "",
            "instrument_token": "",
            "interval": interval,
            "status": "PENDING",
            "notes": "Option candles need selected expiry/strike contracts; fill manifest first.",
        }
    ]
    with path.open("w", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=OPTION_MANIFEST_FIELDS)
        writer.writeheader()
        writer.writerows(sample_rows)
    return path


def _write_run_log(result: dict) -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = LOG_DIR / f"historical_data_{timestamp}.json"
    path.write_text(json.dumps(result, indent=2))
    (LOG_DIR / "latest.json").write_text(json.dumps(result, indent=2))
    return path


def main():
    args = _parse_args()
    fyers = login()
    started_at = datetime.now().isoformat()
    results = []
    days = args.days if args.days is not None else int(args.years * 365.25)

    for symbol in args.symbols:
        try:
            results.append(
                _fetch_symbol(
                    fyers=fyers,
                    symbol=symbol,
                    interval=args.interval,
                    days=days,
                    chunk_days=args.chunk_days,
                    sleep_seconds=args.sleep,
                )
            )
        except Exception as error:
            results.append({
                "symbol": symbol,
                "interval": args.interval,
                "status": "ERROR",
                "error": str(error),
            })

    events_template = None if args.skip_events_template else str(_ensure_events_template())
    option_manifest = None if args.skip_option_manifest else str(_ensure_option_manifest(args.interval))

    run_result = {
        "started_at": started_at,
        "finished_at": datetime.now().isoformat(),
        "years": args.years,
        "days": days,
        "interval": args.interval,
        "symbols": args.symbols,
        "events_template": events_template,
        "option_manifest": option_manifest,
        "option_candles_note": (
            "Index/VIX candles are collected now. Option candle collection needs a filled "
            "option_contract_manifest.csv with expiry/strike/instrument_token."
        ),
        "results": results,
    }
    log_path = _write_run_log(run_result)

    print("Historical data collection complete.")
    for item in results:
        print(
            f"- {item['symbol']}: {item['status']} "
            f"rows={item.get('final_rows', 0)} file={item.get('file', '-')}"
        )
    print(f"Run log: {log_path}")
    if events_template:
        print(f"Events template: {events_template}")
    if option_manifest:
        print(f"Option manifest: {option_manifest}")


if __name__ == "__main__":
    main()
