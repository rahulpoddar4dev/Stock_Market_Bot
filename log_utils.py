# log_utils.py - Shared timestamp helpers for terminal and CSV logs

from datetime import datetime
from zoneinfo import ZoneInfo

MARKET_TIMEZONE = ZoneInfo("Asia/Kolkata")


def now():
    return datetime.now(MARKET_TIMEZONE)


def terminal_time() -> str:
    return now().strftime("%Y-%m-%d %H:%M:%S")


def csv_date() -> str:
    return now().strftime("%Y-%m-%d")


def csv_time() -> str:
    return now().strftime("%H:%M:%S")


def csv_datetime() -> str:
    return now().isoformat(timespec="seconds")


def log(message: str) -> None:
    print(f"[{terminal_time()}] {message}")
