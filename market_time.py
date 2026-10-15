# market_time.py - Intraday trading time rules

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import config

MARKET_TIMEZONE = ZoneInfo("Asia/Kolkata")


def _parse_time(value: str) -> time:
    hour, minute = value.split(":")
    return time(hour=int(hour), minute=int(minute))


def now_datetime() -> datetime:
    return datetime.now(MARKET_TIMEZONE)


def market_now_naive() -> datetime:
    """
    Returns current Indian Standard Time (Asia/Kolkata) as a naive datetime.
    Guarantees correct IST timestamps when calling APIs like Fyers API from cloud
    environments (e.g. Render, AWS, Docker containers) that default to UTC system time.
    """
    return now_datetime().replace(tzinfo=None)


def now_time() -> time:
    return now_datetime().time()


def today_date() -> date:
    return now_datetime().date()


def is_after_no_new_trade_time() -> bool:
    return now_time() >= _parse_time(config.NO_NEW_TRADE_AFTER)


def is_before_no_new_trade_start() -> bool:
    return now_time() < _parse_time(config.NO_NEW_TRADE_BEFORE)


def is_opening_settle_window() -> bool:
    """Between NO_NEW_TRADE_BEFORE (09:20) and OPENING_WINDOW_END (09:30)."""
    current = now_time()
    opening_end = getattr(config, "OPENING_WINDOW_END", "09:30")
    return _parse_time(config.NO_NEW_TRADE_BEFORE) <= current <= _parse_time(opening_end)


def is_pre_open_reference_window() -> bool:
    current = now_time()
    return _parse_time(config.PRE_OPEN_START) <= current <= _parse_time(config.PRE_OPEN_REFERENCE_END)


def is_pre_open_market_window() -> bool:
    current = now_time()
    return _parse_time(config.PRE_OPEN_START) <= current <= _parse_time(config.PRE_OPEN_END)


def is_lunch_avoid_window() -> bool:
    if not config.AVOID_LUNCH_TRADES:
        return False
    current = now_time()
    return _parse_time(config.AVOID_LUNCH_START) <= current <= _parse_time(config.AVOID_LUNCH_END)


def is_after_expiry_day_no_new_trade_time() -> bool:
    return now_time() >= _parse_time(config.EXPIRY_DAY_NO_NEW_TRADE_AFTER)


def is_today(value: str | date | None) -> bool:
    if not value:
        return False

    if isinstance(value, date):
        value_date = value
    else:
        try:
            value_date = datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            return False

    return value_date == today_date()


def is_expiry_day_new_trade_blocked(expiry: str | date | None) -> bool:
    return is_today(expiry) and is_after_expiry_day_no_new_trade_time()


def is_square_off_time() -> bool:
    return now_time() >= _parse_time(config.SQUARE_OFF_TIME)


def is_after_market_close() -> bool:
    return now_time() >= _parse_time(config.MARKET_CLOSE_TIME)
