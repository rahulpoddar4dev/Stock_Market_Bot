# runtime_settings.py - Persistent runtime controls changed from dashboard

import json
from pathlib import Path


SETTINGS_FILE = Path("runtime_settings.json")
ALLOWED_MODES = {"PAPER", "LIVE_AUTO"}

MODE_INTERVALS = {
    "PAPER": {
        "ANALYSIS_INTERVAL_SECONDS": 60,
        "OPEN_TRADE_MONITOR_INTERVAL_SECONDS": 10,
    },
    "LIVE_AUTO": {
        "ANALYSIS_INTERVAL_SECONDS": 15,
        "OPEN_TRADE_MONITOR_INTERVAL_SECONDS": 1,
    },
}


def load_runtime_settings() -> dict:
    if not SETTINGS_FILE.exists():
        return {}

    try:
        data = json.loads(SETTINGS_FILE.read_text())
    except json.JSONDecodeError:
        return {}

    return data if isinstance(data, dict) else {}


def save_trading_mode(mode: str) -> dict:
    mode = mode.upper()
    if mode not in ALLOWED_MODES:
        raise ValueError(f"Unsupported trading mode: {mode}")

    settings = load_runtime_settings()
    settings["TRADING_MODE"] = mode
    settings["PAPER_TRADE"] = mode == "PAPER"
    settings["ENABLE_LIVE_AUTO"] = mode == "LIVE_AUTO"
    settings.update(MODE_INTERVALS[mode])
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2))
    return settings


def apply_runtime_settings(config_module) -> dict:
    settings = load_runtime_settings()
    mode = settings.get("TRADING_MODE")
    if mode in ALLOWED_MODES:
        config_module.TRADING_MODE = mode
        config_module.PAPER_TRADE = mode == "PAPER"
        config_module.ENABLE_LIVE_AUTO = mode == "LIVE_AUTO"

        if mode == "PAPER":
            config_module.MAX_FUND_USAGE_PERCENT = config_module.PAPER_MAX_FUND_USAGE_PERCENT
            config_module.MAX_SINGLE_TRADE_FUND_USAGE_PERCENT = config_module.PAPER_MAX_SINGLE_TRADE_FUND_USAGE_PERCENT
            config_module.MAX_OPEN_TRADES = config_module.PAPER_MAX_OPEN_TRADES
            config_module.MAX_DAILY_STOP_LOSSES = config_module.PAPER_MAX_DAILY_STOP_LOSSES
            config_module.MAX_DAILY_FAILED_TRADES = config_module.PAPER_MAX_DAILY_FAILED_TRADES
            config_module.MAX_RISK_PER_TRADE = config_module.PAPER_MAX_RISK_PER_TRADE
            config_module.MAX_DAILY_LOSS = config_module.PAPER_MAX_DAILY_LOSS
            config_module.MAX_DAILY_PROFIT = config_module.PAPER_MAX_DAILY_PROFIT
            config_module.MIN_SETUP_SCORE = config_module.PAPER_MIN_SETUP_SCORE
            config_module.MIN_ADX = config_module.PAPER_MIN_ADX
        else:
            config_module.MAX_FUND_USAGE_PERCENT = config_module.LIVE_MAX_FUND_USAGE_PERCENT
            config_module.MAX_SINGLE_TRADE_FUND_USAGE_PERCENT = config_module.LIVE_MAX_SINGLE_TRADE_FUND_USAGE_PERCENT
            config_module.MAX_OPEN_TRADES = config_module.LIVE_MAX_OPEN_TRADES
            config_module.MAX_DAILY_STOP_LOSSES = config_module.LIVE_MAX_DAILY_STOP_LOSSES
            config_module.MAX_DAILY_FAILED_TRADES = config_module.LIVE_MAX_DAILY_FAILED_TRADES
            config_module.MAX_RISK_PER_TRADE = config_module.LIVE_MAX_RISK_PER_TRADE
            config_module.MAX_DAILY_LOSS = config_module.LIVE_MAX_DAILY_LOSS
            config_module.MAX_DAILY_PROFIT = config_module.LIVE_MAX_DAILY_PROFIT

    config_module.ANALYSIS_INTERVAL_SECONDS = int(
        settings.get(
            "ANALYSIS_INTERVAL_SECONDS",
            config_module.AUTO_ANALYSIS_INTERVAL_SECONDS
            if config_module.TRADING_MODE == "LIVE_AUTO"
            else config_module.PAPER_ANALYSIS_INTERVAL_SECONDS,
        )
    )
    config_module.OPEN_TRADE_MONITOR_INTERVAL_SECONDS = int(
        settings.get(
            "OPEN_TRADE_MONITOR_INTERVAL_SECONDS",
            config_module.AUTO_OPEN_TRADE_MONITOR_INTERVAL_SECONDS
            if config_module.TRADING_MODE == "LIVE_AUTO"
            else config_module.PAPER_OPEN_TRADE_MONITOR_INTERVAL_SECONDS,
        )
    )
    return settings
