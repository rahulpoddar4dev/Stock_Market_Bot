# symbol_formatter.py - Human-readable option contract names

from datetime import datetime


def format_expiry(expiry: str | None) -> str:
    if not expiry:
        return ""

    try:
        expiry_date = datetime.strptime(expiry, "%Y-%m-%d")
    except ValueError:
        return expiry

    return expiry_date.strftime("%d %b").upper().lstrip("0")


def format_option_display(
    option_symbol: str | None,
    expiry=None,
    strike=None,
    option_type=None,
    underlying="NIFTY",
) -> str:
    if not option_symbol:
        return ""

    expiry_text = format_expiry(str(expiry)) if expiry else ""
    strike_text = str(int(float(strike))) if strike not in {None, ""} else ""
    option_type_text = str(option_type or "")
    underlying_text = str(underlying or "").upper()

    if expiry_text and strike_text and option_type_text:
        return f"{underlying_text} {expiry_text} {strike_text} {option_type_text}"

    return option_symbol
