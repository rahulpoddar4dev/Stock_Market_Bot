# option_chain_analyzer.py - Scan nearby option strikes and choose the best trade candidate

from __future__ import annotations

from datetime import date, datetime, timedelta

import config
from index_config import get_index_config
from indicators import enrich_candles
from market_depth import apply_market_depth_to_trade, extract_market_depth
from market_time import market_now_naive, today_date
from option_selector import round_to_nearest_strike
from symbol_formatter import format_option_display


def analyze_option_chain_for_trade(
    fyers,
    index_symbol: str,
    signal: str,
    spot_price: float,
    strategy_signal: dict | None = None,
) -> dict | None:
    if not getattr(config, "OPTION_CHAIN_ANALYZER_ENABLED", True):
        from option_selector import select_index_option

        return select_index_option(fyers, index_symbol, signal, spot_price, strategy_signal)

    if signal not in {"BUY", "SELL"}:
        return None

    index_cfg = get_index_config(index_symbol)
    option_type = "CE" if signal == "BUY" else "PE"
    instruments = _nearest_expiry_candidates(fyers, index_cfg, option_type, spot_price)
    if not instruments:
        raise RuntimeError(f"No nearby {index_symbol} {option_type} option contracts found.")

    quote_keys = [
        item["tradingsymbol"]
        if item["tradingsymbol"].startswith(("NSE:", "BSE:"))
        else f"{item['exchange']}:{item['tradingsymbol']}"
        for item in instruments
    ]
    quotes = fyers.quote(quote_keys) if hasattr(fyers, "quote") else {}
    oi_context = _fetch_nearby_oi_context(fyers, index_cfg, spot_price)

    candidates = []
    for instrument in instruments:
        quote_key = (
            instrument["tradingsymbol"]
            if instrument["tradingsymbol"].startswith(("NSE:", "BSE:"))
            else f"{instrument['exchange']}:{instrument['tradingsymbol']}"
        )
        quote = quotes.get(quote_key) or quotes.get(instrument["tradingsymbol"])
        if not quote:
            continue
        candidate = _build_candidate(
            fyers=fyers,
            index_cfg=index_cfg,
            instrument=instrument,
            quote=quote,
            signal=signal,
            spot_price=spot_price,
            strategy_signal=strategy_signal or {},
            oi_context=oi_context,
        )
        candidates.append(candidate)

    usable = [item for item in candidates if not item.get("hard_reject")]
    if not usable:
        best_rejected = sorted(candidates, key=lambda item: item.get("confidence_score", 0), reverse=True)[0] if candidates else {}
        return _blocked_trade(best_rejected, "All nearby option strikes failed hard rejection rules.")

    selected = sorted(usable, key=lambda item: item["confidence_score"], reverse=True)[0]
    selected["chain_candidates"] = _candidate_summary(candidates)
    selected["chain_oi_context"] = oi_context
    selected["status"] = _status_from_score(selected["confidence_score"])
    selected["option_chain_decision_reason"] = _decision_reason(selected)
    return selected


def _nearest_expiry_candidates(fyers, index_cfg: dict, option_type: str, spot_price: float) -> list[dict]:
    today = today_date()
    step = int(index_cfg["strike_step"])
    atm = round_to_nearest_strike(float(spot_price), step)
    offsets = range(-int(config.OPTION_CHAIN_STRIKES_EACH_SIDE), int(config.OPTION_CHAIN_STRIKES_EACH_SIDE) + 1)
    allowed_strikes = {atm + (offset * step) for offset in offsets}

    # 1. Native Fyers optionchain
    if hasattr(fyers, "optionchain"):
        try:
            chain_payload = {
                "symbol": index_cfg["quote_key"],
                "strikecount": int(config.OPTION_CHAIN_STRIKES_EACH_SIDE) + 2,
                "timestamp": "",
            }
            res = fyers.optionchain(chain_payload)
            if isinstance(res, dict) and res.get("s") == "ok":
                data = res.get("data") or {}
                chain_list = data.get("optionsChain") or []
                matches = []
                for item in chain_list:
                    strike_val = int(float(item.get("strike_price") or 0))
                    opt_t = str(item.get("option_type") or "").upper()
                    if opt_t != option_type or strike_val not in allowed_strikes:
                        continue
                    sym = item.get("symbol") or ""
                    lot_size = 25 if "NIFTY" in index_cfg["symbol"] else 10
                    matches.append({
                        "tradingsymbol": sym,
                        "exchange": index_cfg["option_exchange"],
                        "name": index_cfg["option_name"],
                        "instrument_type": option_type,
                        "strike": strike_val,
                        "lot_size": lot_size,
                        "instrument_token": sym,
                        "expiry": today.isoformat(),
                        "expiry_date": today,
                    })
                if matches:
                    return sorted(matches, key=lambda item: float(item.get("strike", 0)))
        except Exception:
            pass

    # 2. Broker instruments fallback (or mock for unit tests)
    if hasattr(fyers, "instruments"):
        instruments = fyers.instruments(index_cfg["option_exchange"])
        matches = []
        for instrument in instruments:
            expiry = _as_date(instrument.get("expiry"))
            if not expiry or expiry < today:
                continue
            if instrument.get("name") != index_cfg["option_name"]:
                continue
            if instrument.get("instrument_type") != option_type:
                continue
            if int(float(instrument.get("strike", 0))) not in allowed_strikes:
                continue
            matches.append({**instrument, "expiry_date": expiry})

        if matches:
            nearest_expiry = min(item["expiry_date"] for item in matches)
            return sorted(
                [item for item in matches if item["expiry_date"] == nearest_expiry],
                key=lambda item: float(item.get("strike", 0)),
            )

    return []


def _build_candidate(
    fyers,
    index_cfg: dict,
    instrument: dict,
    quote: dict,
    signal: str,
    spot_price: float,
    strategy_signal: dict,
    oi_context: dict,
) -> dict:
    trading_symbol = instrument["tradingsymbol"]
    exchange = instrument["exchange"]
    lot_size = int(instrument.get("lot_size") or 1)
    quantity = lot_size * int(getattr(config, "OPTION_CHAIN_REQUIRED_DEPTH_LOTS", 1) or 1)
    ltp = float(quote.get("last_price") or 0)
    ohlc = quote.get("ohlc") or {}
    depth = extract_market_depth(quote, quantity=quantity)
    token_or_sym = instrument.get("tradingsymbol") or instrument.get("instrument_token")
    candles = _fetch_option_candles(fyers, token_or_sym)
    enriched = enrich_candles(candles) if len(candles) >= 21 else candles
    premium = _premium_structure(enriched, quote, ltp)
    entry = _entry_plan(enriched, ltp, premium)
    stop_plan = _stop_loss_plan(enriched, entry["entry_price"])
    targets = _targets_from_risk(entry["entry_price"], stop_plan["stop_loss"], lot_size)
    strike = int(float(instrument["strike"]))
    strike_info = _strike_info(index_cfg, signal, spot_price, strike)
    hard_rejects = _hard_reject_reasons(
        quote=quote,
        depth=depth,
        premium=premium,
        entry=entry,
        stop_plan=stop_plan,
        targets=targets,
        strike_info=strike_info,
    )
    scores = _score_candidate(depth, quote, premium, entry, stop_plan, targets, strike_info, strategy_signal)
    confidence_score = round(sum(scores.values()), 2)
    expiry = _as_date(instrument["expiry"]).isoformat()
    display_symbol = format_option_display(
        trading_symbol,
        expiry=expiry,
        strike=strike,
        option_type=instrument["instrument_type"],
        underlying=index_cfg["symbol"],
    )

    candidate = {
        "underlying": index_cfg["symbol"],
        "option_symbol": trading_symbol,
        "display_symbol": display_symbol,
        "exchange": exchange,
        "instrument_token": instrument.get("instrument_token", trading_symbol),
        "expiry": expiry,
        "strike": strike,
        "option_type": instrument["instrument_type"],
        "trade_type": "BUY",
        "order_action": f"BUY {instrument['instrument_type']}",
        "selection_mode": "OPTION_CHAIN_ANALYZER",
        "lot_size": lot_size,
        "quantity": lot_size,
        "entry_price": entry["entry_price"],
        "current_price": ltp,
        "entry_logic": entry["entry_logic"],
        "entry_trigger_price": entry["entry_trigger_price"],
        "stop_loss": stop_plan["stop_loss"],
        "stop_loss_source": stop_plan["stop_loss_source"],
        "stop_loss_percent": stop_plan["stop_loss_percent"],
        "target_1": targets["target_1"],
        "target_2": targets["target_2"],
        "target_3": targets["target_3"],
        "target": targets["target"],
        "target_1_percent": targets["target_1_percent"],
        "target_2_percent": targets["target_2_percent"],
        "target_3_percent": targets["target_3_percent"],
        "target_percent": targets["target_percent"],
        "risk_reward": targets["risk_reward"],
        "potential_left_percent": targets["potential_left_percent"],
        "option_open": ohlc.get("open"),
        "option_previous_close": ohlc.get("close"),
        "option_day_high": ohlc.get("high"),
        "option_day_low": ohlc.get("low"),
        "option_vwap": premium.get("vwap"),
        "option_ema_9": premium.get("ema_9"),
        "option_atr": premium.get("atr"),
        "option_swing_low": stop_plan.get("swing_low") or premium.get("last_3_low"),
        "live_option_vwap": premium.get("vwap"),
        "live_option_ema9": premium.get("ema_9"),
        "live_option_atr": premium.get("atr"),
        "live_option_swing_low": stop_plan.get("swing_low") or premium.get("last_3_low"),
        "live_chart_trend_intact": True,
        "option_volume": quote.get("volume") or 0,
        "option_oi": quote.get("oi") or 0,
        "option_oi_day_high": quote.get("oi_day_high") or 0,
        "option_oi_day_low": quote.get("oi_day_low") or 0,
        "option_change_in_oi": _change_in_oi(quote),
        "option_iv": quote.get("iv") or quote.get("implied_volatility") or "",
        "confidence_score": confidence_score,
        "option_chain_score": confidence_score,
        "option_chain_scores": scores,
        "liquidity_score": scores["liquidity_score"],
        "spread_score": scores["spread_score"],
        "oi_volume_score": scores["oi_volume_score"],
        "premium_momentum_score": scores["premium_momentum_score"],
        "entry_freshness_score": scores["entry_freshness_score"],
        "risk_reward_score": scores["risk_reward_score"],
        "hard_reject": bool(hard_rejects),
        "hard_reject_reason": " | ".join(hard_rejects),
        "chain_support": oi_context.get("pe_support"),
        "chain_resistance": oi_context.get("ce_resistance"),
        "chain_context_reason": _chain_context_reason(signal, spot_price, oi_context),
        "reason": _candidate_reason(signal, display_symbol, confidence_score, scores, premium, depth, targets, hard_rejects),
        "status": "PAPER",
        "dynamic_exit_enabled": bool(getattr(config, "DYNAMIC_EXIT_ENABLED", False)),
        "exit_profile": "OPTION_CHAIN_RISK_REWARD",
        "exit_profile_reason": "Targets and stop-loss calculated from option premium structure and risk-reward.",
    }
    return apply_market_depth_to_trade(candidate, quote)


def _fetch_option_candles(fyers, instrument_token: str | int) -> list[dict]:
    to_date = market_now_naive()
    from_date = to_date - timedelta(days=int(config.OPTION_CHAIN_CANDLE_LOOKBACK_DAYS))
    try:
        if hasattr(fyers, "historical_data"):
            return fyers.historical_data(
                instrument_token=instrument_token,
                from_date=from_date,
                to_date=to_date,
                interval=config.OPTION_CHAIN_CANDLE_INTERVAL,
            )
        return []
    except Exception:
        return []


def _premium_structure(candles: list[dict], quote: dict, ltp: float) -> dict:
    ohlc = quote.get("ohlc") or {}
    latest = _latest_ready(candles)
    previous = candles[-2] if len(candles) >= 2 else {}
    lookback = int(config.OPTION_CHAIN_BREAKOUT_LOOKBACK_CANDLES)
    previous_rows = candles[-lookback - 1:-1] if len(candles) > lookback else candles[:-1]
    last_3_high = max([float(row.get("high") or 0) for row in previous_rows] or [float(ohlc.get("high") or ltp or 0)])
    last_3_low = min([float(row.get("low") or 0) for row in previous_rows] or [float(ohlc.get("low") or ltp or 0)])
    latest_close = float((latest or {}).get("close") or ltp)
    latest_open = float((latest or {}).get("open") or latest_close)
    ema_9 = float((latest or {}).get("ema_9") or latest_close)
    vwap = float((latest or {}).get("vwap") or latest_close)
    atr = float((latest or {}).get("atr_14") or max(latest_close * 0.04, 0.05))
    previous_close = float(ohlc.get("close") or 0)
    open_price = float(ohlc.get("open") or 0)
    day_high = float(ohlc.get("high") or 0)
    rising = latest_close > float(previous.get("close") or previous_close or 0)
    above_open = open_price > 0 and latest_close >= open_price
    above_prev_close = previous_close > 0 and latest_close >= previous_close
    breakout = latest_close > last_3_high
    retest = (
        latest_close > ema_9
        and latest_close > vwap
        and min(float((latest or {}).get("low") or latest_close), latest_close) <= max(ema_9, vwap) * (1 + (config.OPTION_CHAIN_RETEST_TOLERANCE_PERCENT / 100))
        and latest_close > latest_open
    )

    return {
        "latest_close": round(latest_close, 2),
        "latest_open": round(latest_open, 2),
        "ema_9": round(ema_9, 2),
        "vwap": round(vwap, 2),
        "atr": round(atr, 2),
        "last_3_high": round(last_3_high, 2),
        "last_3_low": round(last_3_low, 2),
        "rising": rising,
        "above_open": above_open,
        "above_previous_close": above_prev_close,
        "breakout_confirmed": breakout,
        "retest_confirmed": retest,
        "day_high": day_high,
    }


def _entry_plan(candles: list[dict], ltp: float, premium: dict) -> dict:
    reference_price = float(ltp) if ltp and float(ltp) > 0 else float(premium["latest_close"])
    breakout_level = float(premium["last_3_high"] or reference_price)
    if premium["breakout_confirmed"]:
        return {
            "entry_price": round(reference_price, 2),
            "entry_trigger_price": round(breakout_level, 2),
            "entry_logic": "OPTION_PREMIUM_BREAKOUT",
        }
    if premium["retest_confirmed"]:
        return {
            "entry_price": round(reference_price, 2),
            "entry_trigger_price": round(max(float(premium["ema_9"]), float(premium["vwap"])), 2),
            "entry_logic": "OPTION_PREMIUM_RETEST",
        }
    return {
        "entry_price": round(reference_price, 2),
        "entry_trigger_price": round(breakout_level, 2),
        "entry_logic": "OPTION_PREMIUM_CONTINUATION",
    }


def _get_price_tier_risk_bounds(entry_price: float) -> tuple[float, float, float]:
    """
    Dynamically returns (min_sl_percent, max_sl_percent, default_sl_percent) based on option premium tier.
    - Cheap (< Rs 100): 8.0% to 18.0% (default 10.0%) -> room for genuine 5m structure swing low
    - Mid (Rs 100 to Rs 220): 7.0% to 15.0% (default 9.0%) -> allows true 5m candle swing support
    - High (> Rs 220): 5.0% to 12.5% (default 7.0%) -> respects Sensex/Nifty 5m swing low without wick choke
    """
    if entry_price < 100.0:
        return 8.0, 18.0, 10.0
    elif entry_price <= 220.0:
        return 7.0, 15.0, 9.0
    else:
        return 5.0, 12.5, 7.0


def _stop_loss_plan(candles: list[dict], entry_price: float) -> dict:
    entry = float(entry_price or 0)
    if entry <= 0:
        return {"stop_loss": 0.0, "stop_loss_percent": 0.0, "stop_loss_source": "none", "swing_low": 0.0}

    use_price_tier = getattr(config, "OPTION_CHAIN_PRICE_TIER_SL_ENABLED", True)
    if use_price_tier:
        tier_min, tier_max, tier_default = _get_price_tier_risk_bounds(entry)
        min_sl_percent = tier_min
        max_sl_percent = tier_max
        default_sl_percent = tier_default
    else:
        min_sl_percent = float(getattr(config, "OPTION_CHAIN_MIN_STOP_LOSS_PERCENT", 8.0) or 8.0)
        max_sl_percent = float(getattr(config, "OPTION_CHAIN_MAX_STOP_LOSS_PERCENT", 12.0) or 12.0)
        default_sl_percent = float(getattr(config, "OPTION_CHAIN_DEFAULT_STOP_LOSS_PERCENT", 10.0) or 10.0)

    tightest_sl = entry * (1.0 - (min_sl_percent / 100.0))
    widest_sl = entry * (1.0 - (max_sl_percent / 100.0))
    default_sl = entry * (1.0 - (default_sl_percent / 100.0))

    lookback = int(config.OPTION_CHAIN_SWING_LOOKBACK_CANDLES)
    recent = candles[-lookback:] if candles else []
    swing_lows = [float(row.get("low") or 0) for row in recent if float(row.get("low") or 0) > 0]
    candidate_sl = min(swing_lows) if swing_lows else default_sl

    if widest_sl <= candidate_sl <= tightest_sl:
        stop_loss = candidate_sl
        source = f"structure swing low ({round(candidate_sl, 2)})"
    elif candidate_sl > tightest_sl:
        stop_loss = default_sl
        source = f"tier default {default_sl_percent}% (candidate too tight at {round(((entry - candidate_sl)/entry)*100, 1)}%)"
    else:
        stop_loss = widest_sl
        source = f"max allowed tier risk {max_sl_percent}%"

    sl_percent = ((entry - stop_loss) / entry) * 100.0 if entry else 0.0
    return {
        "stop_loss": round(stop_loss, 2),
        "stop_loss_percent": round(sl_percent, 2),
        "stop_loss_source": source,
        "swing_low": round(min(swing_lows), 2) if swing_lows else round(stop_loss, 2),
    }


def _targets_from_risk(entry: float, stop_loss: float, lot_size: int) -> dict:
    risk = max(entry - stop_loss, 0)
    target_1 = entry + (1.25 * risk)
    target_2 = entry + (2.0 * risk)
    target_3 = entry + (3.0 * risk)
    final_target = target_2 if int(config.OPTION_CHAIN_ONE_LOT_TARGET_STAGE) == 2 else target_1
    potential_left = ((final_target - entry) / entry) * 100 if entry else 0
    risk_reward = (final_target - entry) / risk if risk else 0
    return {
        "target_1": round(target_1, 2),
        "target_2": round(target_2, 2),
        "target_3": round(target_3, 2),
        "target": round(final_target, 2),
        "target_1_percent": round(((target_1 - entry) / entry) * 100, 2) if entry else 0,
        "target_2_percent": round(((target_2 - entry) / entry) * 100, 2) if entry else 0,
        "target_3_percent": round(((target_3 - entry) / entry) * 100, 2) if entry else 0,
        "target_percent": round(((final_target - entry) / entry) * 100, 2) if entry else 0,
        "risk_reward": round(risk_reward, 2),
        "potential_left_percent": round(potential_left, 2),
    }


def _score_candidate(depth: dict, quote: dict, premium: dict, entry: dict, stop_plan: dict, targets: dict, strike_info: dict, strategy_signal: dict) -> dict:
    liquidity = 20 if depth["top_depth_sufficient"] else 5
    spread_score = max(0, 15 - (float(depth["spread_percent"] or 0) / config.OPTION_CHAIN_MAX_SPREAD_PERCENT) * 15)
    oi_volume = _oi_volume_score(quote)
    premium_score = 0
    premium_score += 4 if premium["rising"] else 0
    premium_score += 4 if premium["above_open"] else 0
    premium_score += 4 if premium["above_previous_close"] else 0
    premium_score += 4 if premium["breakout_confirmed"] or premium["retest_confirmed"] else 0
    vwap_val = float(premium.get("vwap") or 0)
    if vwap_val > 0 and float(premium.get("latest_close") or 0) >= vwap_val:
        premium_score += 4
    freshness = 15 if entry["entry_logic"] == "OPTION_PREMIUM_BREAKOUT" else 13 if entry["entry_logic"] == "OPTION_PREMIUM_RETEST" else 9
    rr_score = min(15, (float(targets["risk_reward"] or 0) / 2.0) * 15)

    if strike_info["far_otm"]:
        liquidity *= 0.6
        premium_score *= 0.7

    return {
        "liquidity_score": round(liquidity, 2),
        "spread_score": round(spread_score, 2),
        "oi_volume_score": round(oi_volume, 2),
        "premium_momentum_score": round(min(20, premium_score), 2),
        "entry_freshness_score": round(freshness, 2),
        "risk_reward_score": round(rr_score, 2),
    }


def _oi_volume_score(quote: dict) -> float:
    volume = float(quote.get("volume") or 0)
    oi = float(quote.get("oi") or 0)
    volume_score = min(7.5, (volume / max(float(config.OPTION_CHAIN_MIN_VOLUME), 1)) * 7.5)
    oi_score = min(7.5, (oi / max(float(config.OPTION_CHAIN_MIN_OI), 1)) * 7.5)
    return volume_score + oi_score


def _hard_reject_reasons(quote: dict, depth: dict, premium: dict, entry: dict, stop_plan: dict, targets: dict, strike_info: dict) -> list[str]:
    reasons = []
    ltp = float(quote.get("last_price") or 0)
    volume = int(float(quote.get("volume") or 0))
    oi = int(float(quote.get("oi") or 0))
    spread_percent = float(depth.get("spread_percent") or 0)
    if spread_percent > float(config.OPTION_CHAIN_MAX_SPREAD_PERCENT):
        reasons.append(f"spread {spread_percent}% > {config.OPTION_CHAIN_MAX_SPREAD_PERCENT}%")
    if not depth.get("top_depth_sufficient"):
        reasons.append(f"top depth qty {depth.get('ask_qty')} < required {depth.get('top_depth_required_qty')}")
    if volume < int(config.OPTION_CHAIN_MIN_VOLUME):
        reasons.append(f"volume {volume} < {config.OPTION_CHAIN_MIN_VOLUME}")
    if oi < int(config.OPTION_CHAIN_MIN_OI):
        reasons.append(f"OI {oi} < {config.OPTION_CHAIN_MIN_OI}")
    if ltp < float(config.OPTION_CHAIN_MIN_LTP):
        reasons.append(f"LTP {ltp} below minimum {config.OPTION_CHAIN_MIN_LTP}")
    if ltp > float(config.OPTION_CHAIN_MAX_LTP):
        reasons.append(f"LTP {ltp} above maximum {config.OPTION_CHAIN_MAX_LTP}")
    if strike_info["far_otm"]:
        reasons.append("far OTM strike avoided")
    if float(stop_plan["stop_loss_percent"]) > float(config.OPTION_CHAIN_MAX_STOP_LOSS_PERCENT):
        reasons.append(f"SL distance {stop_plan['stop_loss_percent']}% too large")
    if float(targets["potential_left_percent"]) < float(config.OPTION_CHAIN_MIN_POTENTIAL_LEFT_PERCENT):
        reasons.append(f"potential left {targets['potential_left_percent']}% < {config.OPTION_CHAIN_MIN_POTENTIAL_LEFT_PERCENT}%")
    if float(targets["risk_reward"]) < float(config.OPTION_CHAIN_MIN_RISK_REWARD):
        reasons.append(f"risk reward {targets['risk_reward']} < {config.OPTION_CHAIN_MIN_RISK_REWARD}")
    if float(entry["entry_price"]) >= float(targets["target"]):
        reasons.append("current/entry price already at or above target")
    buffer_pct = float(getattr(config, "OPTION_CHAIN_REJECT_NEAR_TARGET_BUFFER_PERCENT", 0.0) or 0.0)
    if buffer_pct > 0 and premium.get("day_high") and float(entry["entry_price"]) >= premium["day_high"] * (1 - (buffer_pct / 100)):
        if float(targets.get("potential_left_percent") or 0) < 15.0:
            reasons.append("premium already near day high and target potential is exhausted")
    if getattr(config, "OPTION_REQUIRE_ABOVE_VWAP", True):
        vwap_val = float(premium.get("vwap") or 0)
        tolerance = float(getattr(config, "OPTION_VWAP_TOLERANCE_PERCENT", 0.5)) / 100
        if vwap_val > 0 and ltp < (vwap_val * (1 - tolerance)):
            reasons.append(f"option LTP Rs {ltp} below its own VWAP Rs {vwap_val} (weak option flow)")
    return reasons


def _strike_info(index_cfg: dict, signal: str, spot_price: float, strike: int) -> dict:
    step = int(index_cfg["strike_step"])
    atm = round_to_nearest_strike(float(spot_price), step)
    steps_from_atm = int(round((strike - atm) / step))
    if signal == "BUY":
        far_otm = steps_from_atm > int(config.OPTION_CHAIN_MAX_OTM_STEPS)
        preferred = steps_from_atm in {-1, 0}
    else:
        far_otm = steps_from_atm < -int(config.OPTION_CHAIN_MAX_OTM_STEPS)
        preferred = steps_from_atm in {0, 1}
    return {
        "atm": atm,
        "steps_from_atm": steps_from_atm,
        "far_otm": far_otm,
        "preferred_strike": preferred,
    }


def _build_oi_context(index_cfg: dict, instruments: list[dict], quotes: dict, spot_price: float) -> dict:
    ce_rows = []
    pe_rows = []
    for item in instruments:
        quote = quotes.get(f"{item['exchange']}:{item['tradingsymbol']}", {})
        row = {"strike": int(float(item.get("strike", 0))), "oi": int(float(quote.get("oi") or 0))}
        if item.get("instrument_type") == "CE":
            ce_rows.append(row)
        elif item.get("instrument_type") == "PE":
            pe_rows.append(row)

    ce_resistance = max(ce_rows, key=lambda row: row["oi"]) if ce_rows else {}
    pe_support = max(pe_rows, key=lambda row: row["oi"]) if pe_rows else {}
    return {
        "ce_resistance": ce_resistance.get("strike"),
        "ce_resistance_oi": ce_resistance.get("oi"),
        "pe_support": pe_support.get("strike"),
        "pe_support_oi": pe_support.get("oi"),
        "spot_price": spot_price,
    }


def _fetch_nearby_oi_context(fyers, index_cfg: dict, spot_price: float) -> dict:
    today = today_date()
    step = int(index_cfg["strike_step"])
    atm = round_to_nearest_strike(float(spot_price), step)
    offsets = range(-int(config.OPTION_CHAIN_STRIKES_EACH_SIDE), int(config.OPTION_CHAIN_STRIKES_EACH_SIDE) + 1)
    allowed_strikes = {atm + (offset * step) for offset in offsets}

    # 1. Native Fyers optionchain
    if hasattr(fyers, "optionchain"):
        try:
            res = fyers.optionchain({
                "symbol": index_cfg["quote_key"],
                "strikecount": int(config.OPTION_CHAIN_STRIKES_EACH_SIDE) + 2,
                "timestamp": "",
            })
            if isinstance(res, dict) and res.get("s") == "ok":
                data = res.get("data") or {}
                chain_list = data.get("optionsChain") or []
                nearby = []
                quotes = {}
                for item in chain_list:
                    strike_val = int(float(item.get("strike_price") or 0))
                    opt_t = str(item.get("option_type") or "").upper()
                    if strike_val not in allowed_strikes or opt_t not in {"CE", "PE"}:
                        continue
                    sym = item.get("symbol") or ""
                    nearby.append({
                        "tradingsymbol": sym,
                        "exchange": index_cfg["option_exchange"],
                        "strike": strike_val,
                        "instrument_type": opt_t,
                    })
                    quotes[sym] = {
                        "oi": int(float(item.get("oi") or item.get("call_oi" if opt_t == "CE" else "put_oi") or 0)),
                        "last_price": float(item.get("ltp") or 0),
                    }
                if nearby:
                    return _build_oi_context(index_cfg, nearby, quotes, spot_price)
        except Exception:
            pass

    # 2. Broker instruments fallback
    if hasattr(fyers, "instruments"):
        instruments = fyers.instruments(index_cfg["option_exchange"])
        nearby = []
        for instrument in instruments:
            expiry = _as_date(instrument.get("expiry"))
            if not expiry or expiry < today:
                continue
            if instrument.get("name") != index_cfg["option_name"]:
                continue
            if instrument.get("instrument_type") not in {"CE", "PE"}:
                continue
            if int(float(instrument.get("strike", 0))) not in allowed_strikes:
                continue
            nearby.append({**instrument, "expiry_date": expiry})

        if nearby:
            nearest_expiry = min(item["expiry_date"] for item in nearby)
            nearby = [item for item in nearby if item["expiry_date"] == nearest_expiry]
            quote_keys = [
                item["tradingsymbol"]
                if item["tradingsymbol"].startswith(("NSE:", "BSE:"))
                else f"{item['exchange']}:{item['tradingsymbol']}"
                for item in nearby
            ]
            quotes = fyers.quote(quote_keys) if hasattr(fyers, "quote") else {}
            return _build_oi_context(index_cfg, nearby, quotes, spot_price)

    return {"spot_price": spot_price}


def _chain_context_reason(signal: str, spot_price: float, oi_context: dict) -> str:
    resistance = oi_context.get("ce_resistance")
    support = oi_context.get("pe_support")
    if signal == "BUY" and resistance:
        return "Spot is above CE OI resistance." if spot_price > resistance else "Spot is below/near CE OI resistance; needs premium confirmation."
    if signal == "SELL" and support:
        return "Spot is below PE OI support." if spot_price < support else "Spot is above/near PE OI support; needs premium confirmation."
    return "OI support/resistance unavailable."


def _status_from_score(score: float) -> str:
    if score >= float(config.OPTION_CHAIN_MIN_CONFIDENCE_SCORE):
        return "PAPER"
    if score >= float(config.OPTION_CHAIN_SMALL_SIZE_SCORE):
        return "PAPER_SMALL_SIZE"
    if score >= float(config.OPTION_CHAIN_LOG_ONLY_SCORE):
        return "LOG_ONLY_OPTION_SCORE"
    return "BLOCKED_OPTION_SCORE"


def _blocked_trade(candidate: dict, reason: str) -> dict:
    blocked = dict(candidate)
    blocked["status"] = "BLOCKED_OPTION_CHAIN"
    blocked["quantity"] = 0
    blocked["hard_reject"] = True
    blocked["hard_reject_reason"] = f"{reason} {candidate.get('hard_reject_reason', '')}".strip()
    return blocked


def _decision_reason(candidate: dict) -> str:
    score = float(candidate.get("confidence_score") or 0)
    if score >= float(config.OPTION_CHAIN_MIN_CONFIDENCE_SCORE):
        return f"Score {score} >= {config.OPTION_CHAIN_MIN_CONFIDENCE_SCORE}; trade allowed."
    if score >= float(config.OPTION_CHAIN_SMALL_SIZE_SCORE):
        return f"Score {score}; small size trade."
    if score >= float(config.OPTION_CHAIN_LOG_ONLY_SCORE):
        return f"Score {score}; paper/log only."
    return f"Score {score}; rejected."


def _candidate_reason(signal: str, display_symbol: str, score: float, scores: dict, premium: dict, depth: dict, targets: dict, hard_rejects: list[str]) -> str:
    if hard_rejects:
        return f"{display_symbol} rejected: {' | '.join(hard_rejects)}."
    direction = "bullish" if signal == "BUY" else "bearish"
    return (
        f"{display_symbol}: {direction} index signal, option premium "
        f"{'breakout' if premium['breakout_confirmed'] else 'retest' if premium['retest_confirmed'] else 'continuation'} confirmation, "
        f"spread {depth['spread_percent']}%, potential left {targets['potential_left_percent']}%, "
        f"risk-reward {targets['risk_reward']}, confidence score {score}. Scores: {scores}."
    )


def _candidate_summary(candidates: list[dict]) -> str:
    rows = []
    for item in sorted(candidates, key=lambda row: row.get("confidence_score", 0), reverse=True):
        rows.append(
            f"{item.get('display_symbol')} score={item.get('confidence_score')} "
            f"status={item.get('status')} reject={item.get('hard_reject_reason') or 'no'}"
        )
    return " | ".join(rows)


def _latest_ready(candles: list[dict]) -> dict | None:
    for candle in reversed(candles):
        if candle.get("close") is not None:
            return candle
    return None


def _as_date(value):
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return datetime.strptime(value, "%Y-%m-%d").date()
    return value


def _change_in_oi(quote: dict) -> int:
    oi = int(float(quote.get("oi") or 0))
    oi_low = int(float(quote.get("oi_day_low") or 0))
    return oi - oi_low if oi and oi_low else 0
