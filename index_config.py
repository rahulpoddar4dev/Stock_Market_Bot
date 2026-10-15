# index_config.py - Index settings for analysis and option selection with Fyers

import config


INDEX_CONFIGS = {
    "NIFTY": {
        "symbol": "NIFTY",
        "name": "NIFTY",
        "quote_key": "NSE:NIFTY50-INDEX",
        "spot_exchange": "NSE",
        "spot_tradingsymbol": "NSE:NIFTY50-INDEX",
        "option_exchange": "NSE",
        "option_name": "NIFTY",
        "strike_step": 50,
    },
    "SENSEX": {
        "symbol": "SENSEX",
        "name": "SENSEX",
        "quote_key": "BSE:SENSEX-INDEX",
        "spot_exchange": "BSE",
        "spot_tradingsymbol": "BSE:SENSEX-INDEX",
        "option_exchange": "BSE",
        "option_name": "SENSEX",
        "strike_step": 100,
    },
    "INDIA_VIX": {
        "symbol": "INDIA_VIX",
        "name": "India VIX",
        "quote_key": "NSE:INDIAVIX-INDEX",
        "spot_exchange": "NSE",
        "spot_tradingsymbol": "NSE:INDIAVIX-INDEX",
        "option_exchange": None,
        "option_name": None,
        "strike_step": None,
    },
}


def get_index_config(symbol: str) -> dict:
    key = symbol.upper()
    if key not in INDEX_CONFIGS:
        raise ValueError(f"Unsupported index: {symbol}")
    return INDEX_CONFIGS[key]
