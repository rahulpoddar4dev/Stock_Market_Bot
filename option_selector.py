# option_selector.py - Select index option contracts for paper trading

from datetime import date, datetime

from index_config import get_index_config
from market_depth import apply_market_depth_to_trade
from market_time import today_date
from symbol_formatter import format_option_display
import config


def round_to_nearest_strike(price: float, step: int = 50) -> int:
    """Round spot price to nearest available strike step."""
    return int(round(price / step) * step)


def _as_date(value):
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return datetime.strptime(value, "%Y-%m-%d").date()
    return value


def _choose_strike(index_config: dict, signal: str, spot_price: float, confidence: str | None = None) -> int:
    step = index_config["strike_step"]
    atm = round_to_nearest_strike(spot_price, step)
    mode = config.OPTION_SELECTION_MODE.upper()

    if mode == "ATM":
        return atm

    use_itm = mode in {"ITM", "AUTO"} or confidence == "LOW"
    if not use_itm:
        return atm

    if signal == "BUY":
        return atm - step
    return atm + step


def _adaptive_exit_percentages(strategy_signal: dict | None) -> dict:
    strategy_signal = strategy_signal or {}
    score = float(strategy_signal.get("SETUP_SCORE") or 0)
    adx = float(strategy_signal.get("ADX_14") or 0)

    if score >= config.STRONG_MOMENTUM_SCORE and adx >= config.STRONG_MOMENTUM_ADX:
        return _cap_stop_loss_to_final_target({
            "profile": "STRONG_MOMENTUM",
            "target_1_percent": config.STRONG_TARGET_STAGE_1_PERCENT,
            "target_2_percent": config.STRONG_TARGET_STAGE_2_PERCENT,
            "target_percent": config.STRONG_TARGET_PROFIT_PERCENT,
            "stop_loss_percent": config.STRONG_STOP_LOSS_PERCENT,
            "reason": f"Strong momentum: score {score}, ADX {adx}.",
        })

    if score < config.WEAK_MOMENTUM_SCORE or adx < config.WEAK_MOMENTUM_ADX:
        return _cap_stop_loss_to_final_target({
            "profile": "WEAK_OR_RISKY",
            "target_1_percent": config.WEAK_TARGET_STAGE_1_PERCENT,
            "target_2_percent": config.WEAK_TARGET_STAGE_2_PERCENT,
            "target_percent": config.WEAK_TARGET_PROFIT_PERCENT,
            "stop_loss_percent": config.WEAK_STOP_LOSS_PERCENT,
            "reason": f"Weak/risky momentum: score {score}, ADX {adx}.",
        })

    return _cap_stop_loss_to_final_target({
        "profile": "NORMAL_MOMENTUM",
        "target_1_percent": config.NORMAL_TARGET_STAGE_1_PERCENT,
        "target_2_percent": config.NORMAL_TARGET_STAGE_2_PERCENT,
        "target_percent": config.TARGET_PROFIT_PERCENT,
        "stop_loss_percent": config.STOP_LOSS_PERCENT,
        "reason": f"Normal momentum: score {score}, ADX {adx}.",
    })


def _cap_stop_loss_to_final_target(profile: dict) -> dict:
    final_target = float(profile.get("target_percent") or profile.get("target_2_percent") or 0)
    stop_loss = float(profile.get("stop_loss_percent") or 0)
    if final_target > 0 and stop_loss > final_target:
        profile["stop_loss_percent"] = final_target
        profile["reason"] = (
            f"{profile.get('reason', '')} Stop loss capped to final target percent so risk is not bigger than reward."
        ).strip()
    return profile


def calculate_option_stop_loss_percent(
    entry_price: float,
    spot_price: float,
    strategy_signal: dict | None = None,
) -> tuple[float, str]:
    strategy_signal = strategy_signal or {}

    # 1. Base floor based on option premium tier:
    # Cheap options (< 100) have high % noise, expensive (> 220) have high rupee impact
    if entry_price < 100.0:
        base_sl_pct = float(getattr(config, "OPTION_SL_CHEAP_PERCENT", 18.0))
    elif entry_price <= 220.0:
        base_sl_pct = float(getattr(config, "OPTION_SL_MIN_PERCENT", 15.0))
    else:
        base_sl_pct = float(getattr(config, "OPTION_SL_EXPENSIVE_PERCENT", 12.5))

    index_sl = strategy_signal.get("INDEX_STOP_LOSS")
    if index_sl and index_sl != "N/A" and spot_price > 0:
        try:
            index_dist = abs(float(spot_price) - float(index_sl))
            delta = 0.52
            est_option_sl_pts = index_dist * delta
            structural_pct = (est_option_sl_pts / entry_price) * 100 if entry_price else base_sl_pct

            min_cap = float(getattr(config, "OPTION_SL_MIN_PERCENT", 14.0))
            max_cap = float(getattr(config, "OPTION_SL_MAX_PERCENT", 20.0))
            final_sl_pct = max(base_sl_pct, min(structural_pct, max_cap))
            reason = (
                f"Multi-timeframe structural SL: index distance {round(index_dist, 1)} pts "
                f"({round(structural_pct, 1)}%), calibrated to {round(final_sl_pct, 1)}%."
            )
            return round(final_sl_pct, 1), reason
        except Exception:
            pass

    return base_sl_pct, f"Tier-based SL for premium Rs {entry_price}: {base_sl_pct}%."


def select_index_option(
    fyers,
    index_symbol: str,
    signal: str,
    spot_price: float,
    strategy_signal: dict | None = None,
) -> dict | None:
    """
    Select nearest-expiry ATM index option using Fyers.

    BUY signal  -> buy CE.
    SELL signal -> buy PE.
    HOLD signal -> no option selected
    """
    if signal not in {"BUY", "SELL"}:
        return None

    index_config = get_index_config(index_symbol)
    option_type = "CE" if signal == "BUY" else "PE"
    strike = _choose_strike(index_config, signal, spot_price)
    today = today_date()

    selected = None
    instruments = []

    # 1. Native Fyers optionchain
    if hasattr(fyers, "optionchain"):
        try:
            res = fyers.optionchain({
                "symbol": index_config["quote_key"],
                "strikecount": 10,
                "timestamp": "",
            })
            if isinstance(res, dict) and res.get("s") == "ok":
                data = res.get("data") or {}
                chain_list = data.get("optionsChain") or []
                for item in chain_list:
                    strike_val = int(float(item.get("strike_price") or 0))
                    opt_t = str(item.get("option_type") or "").upper()
                    if strike_val == strike and opt_t == option_type:
                        sym = item.get("symbol") or ""
                        lot_size = 25 if "NIFTY" in index_config["symbol"] else 10
                        selected = {
                            "tradingsymbol": sym,
                            "exchange": index_config["option_exchange"],
                            "name": index_config["option_name"],
                            "instrument_type": option_type,
                            "strike": strike_val,
                            "lot_size": lot_size,
                            "expiry": today.isoformat(),
                            "last_price": float(item.get("ltp") or 0),
                        }
                        break
        except Exception:
            pass

    # 2. Instruments fallback (if provided by broker or mock)
    if not selected and hasattr(fyers, "instruments"):
        instruments = fyers.instruments(index_config["option_exchange"])
        matches = []
        for instrument in instruments:
            expiry = _as_date(instrument.get("expiry"))
            if not expiry or expiry < today:
                continue

            if (
                instrument.get("name") == index_config["option_name"]
                and instrument.get("instrument_type") == option_type
                and float(instrument.get("strike", 0)) == float(strike)
            ):
                matches.append(instrument)

        if matches:
            selected = sorted(matches, key=lambda item: _as_date(item["expiry"]))[0]

    if not selected:
        return None

    trading_symbol = selected["tradingsymbol"]
    quote_key = trading_symbol if trading_symbol.startswith(("NSE:", "BSE:")) else f"{selected['exchange']}:{trading_symbol}"
    quotes = fyers.quote([quote_key]) if hasattr(fyers, "quote") else {}
    option_quote = quotes.get(quote_key) or quotes.get(trading_symbol) or {}
    entry_price = float(option_quote.get("last_price") or selected.get("last_price") or 0)

    # Delta / Premium Sweet-Spot Optimization
    if getattr(config, "OPTION_SWEET_SPOT_ENABLED", True) and entry_price > float(getattr(config, "OPTION_MAX_RECOMMENDED_PREMIUM", 350.0)):
        atm_strike = round_to_nearest_strike(spot_price, index_config["strike_step"])
        if atm_strike != strike and instruments:
            atm_matches = [
                inst for inst in instruments
                if _as_date(inst.get("expiry")) == _as_date(selected.get("expiry"))
                and inst.get("name") == index_config["option_name"]
                and inst.get("instrument_type") == option_type
                and float(inst.get("strike", 0)) == float(atm_strike)
            ]
            if atm_matches:
                atm_inst = atm_matches[0]
                atm_quote_key = f"{atm_inst['exchange']}:{atm_inst['tradingsymbol']}"
                try:
                    atm_q = fyers.quote([atm_quote_key]).get(atm_quote_key)
                    if atm_q and float(atm_q.get("last_price") or 0) > 0:
                        selected = atm_inst
                        trading_symbol = selected["tradingsymbol"]
                        quote_key = atm_quote_key
                        option_quote = atm_q
                        entry_price = float(option_quote["last_price"])
                        strike = atm_strike
                except Exception:
                    pass

    option_ohlc = option_quote.get("ohlc", {})
    lot_size = int(selected["lot_size"])
    exit_profile = _adaptive_exit_percentages(strategy_signal)
    dynamic_exit_enabled = bool(getattr(config, "DYNAMIC_EXIT_ENABLED", False))
    calculated_sl_pct, sl_reason = calculate_option_stop_loss_percent(
        entry_price,
        spot_price,
        strategy_signal,
    )
    stop_loss_percent = (
        config.DYNAMIC_EMERGENCY_STOP_LOSS_PERCENT
        if dynamic_exit_enabled
        else calculated_sl_pct
    )
    target_1_percent = "" if dynamic_exit_enabled else max(float(exit_profile.get("target_1_percent") or 14), 14.0)
    target_2_percent = "" if dynamic_exit_enabled else max(float(exit_profile.get("target_2_percent") or 24), 24.0)
    target_percent = "" if dynamic_exit_enabled else max(float(exit_profile.get("target_percent") or 35), 35.0)

    expiry_text = str(_as_date(selected["expiry"]))
    display_symbol = format_option_display(
        trading_symbol,
        expiry=expiry_text,
        strike=selected["strike"],
        option_type=option_type,
        underlying=index_config["symbol"],
    )

    option_trade = {
        "underlying": index_config["symbol"],
        "option_symbol": trading_symbol,
        "display_symbol": display_symbol,
        "exchange": selected["exchange"],
        "instrument_token": selected["instrument_token"],
        "expiry": expiry_text,
        "strike": int(float(selected["strike"])),
        "option_type": option_type,
        "trade_type": "BUY",
        "entry_price": entry_price,
        "option_open": option_ohlc.get("open"),
        "option_previous_close": option_ohlc.get("close"),
        "option_day_high": option_ohlc.get("high"),
        "option_day_low": option_ohlc.get("low"),
        "lot_size": lot_size,
        "quantity": lot_size,
        "stop_loss": round(entry_price * (1 - (stop_loss_percent / 100)), 2),
        "target_1": "" if dynamic_exit_enabled else round(entry_price * (1 + (target_1_percent / 100)), 2),
        "target_2": "" if dynamic_exit_enabled else round(entry_price * (1 + (target_2_percent / 100)), 2),
        "target_3": "" if dynamic_exit_enabled else round(entry_price * (1 + (target_percent / 100)), 2),
        "target": "" if dynamic_exit_enabled else round(entry_price * (1 + (target_percent / 100)), 2),
        "target_1_percent": target_1_percent,
        "target_2_percent": target_2_percent,
        "target_3_percent": target_percent,
        "target_stage": 0,
        "stop_loss_percent": stop_loss_percent,
        "stop_loss_reason": sl_reason,
        "index_stop_loss": strategy_signal.get("INDEX_STOP_LOSS") if strategy_signal else None,
        "htf_bias": strategy_signal.get("HTF_BIAS") if strategy_signal else None,
        "htf_trend_reason": strategy_signal.get("HTF_TREND_REASON") if strategy_signal else None,
        "target_percent": target_percent,
        "exit_profile": "DYNAMIC_EXIT" if dynamic_exit_enabled else exit_profile["profile"],
        "exit_profile_reason": (
            "Dynamic exit enabled: fixed targets are disabled. Bot will decide partial/full exit from price action, "
            "peak profit pullback, market depth, and emergency SL."
            if dynamic_exit_enabled
            else exit_profile["reason"]
        ),
        "dynamic_exit_enabled": dynamic_exit_enabled,
        "order_action": f"BUY {option_type}",
        "selection_mode": config.OPTION_SELECTION_MODE,
        "status": "PAPER",
    }
    return apply_market_depth_to_trade(option_trade, option_quote)


def select_nifty_option(fyers, signal: str, spot_price: float) -> dict | None:
    return select_index_option(fyers, "NIFTY", signal, spot_price)
