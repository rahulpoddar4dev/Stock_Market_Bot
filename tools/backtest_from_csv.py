#!/usr/bin/env python3
"""Run the current rule strategy on collected historical CSV candles."""

import argparse
import csv
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import config
from candlestick_patterns import summarize_patterns
from indicators import enrich_candles


DATA_DIR = BASE_DIR / "data" / "historical"
REPORT_DIR = BASE_DIR / "reports" / "backtests"
TRADE_FIELDS = [
    "symbol",
    "signal",
    "entry_time",
    "exit_time",
    "entry",
    "exit",
    "stop_loss",
    "target",
    "target_1",
    "target_2",
    "target_3",
    "target_stage",
    "exit_profile",
    "adaptive_exit_profile",
    "target_1_percent",
    "target_2_percent",
    "target_3_percent",
    "stop_loss_percent",
    "partial_realized_points",
    "remaining_fraction",
    "result",
    "pnl_points",
    "score",
    "adx",
    "rsi",
    "gap_percent",
    "vix_close",
    "regime",
    "time_bucket",
    "rule_action",
    "reason",
]


def _parse_args():
    parser = argparse.ArgumentParser(description="Backtest current strategy from collected CSV candles.")
    parser.add_argument("--symbols", nargs="+", default=config.ANALYZE_INDICES)
    parser.add_argument("--interval", default=config.CANDLE_INTERVAL)
    parser.add_argument("--atr-target", type=float, default=2.0)
    parser.add_argument("--atr-stop", type=float, default=1.2)
    parser.add_argument(
        "--adaptive-exit-profile",
        action="store_true",
        help="Simulate Strong/Normal/Weak T1/T2/SL ratios on index candles using ATR as base risk.",
    )
    parser.add_argument("--rules-file", default="", help="Optional JSON rules from analyze_backtest_results.py.")
    parser.add_argument("--min-setup-score", type=float, default=None)
    parser.add_argument("--min-adx", type=float, default=None)
    parser.add_argument("--breakout-lookback", type=int, default=None)
    return parser.parse_args()


def _read_candles(path: Path) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(f"Missing historical CSV: {path}")

    candles = []
    with path.open(newline="") as csv_file:
        for row in csv.DictReader(csv_file):
            candles.append({
                "date": row["datetime"],
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row.get("volume") or 0),
                "oi": float(row.get("oi") or 0),
                "gap_percent": float(row.get("gap_percent") or 0),
            })
    return candles


def _vix_by_datetime(interval: str) -> dict[str, float]:
    path = DATA_DIR / f"INDIA_VIX_{interval}.csv"
    if not path.exists():
        return {}
    with path.open(newline="") as csv_file:
        return {
            row["datetime"]: float(row["close"])
            for row in csv.DictReader(csv_file)
            if row.get("datetime") and row.get("close")
        }


def _time_bucket(value: str) -> str:
    try:
        hour = datetime.fromisoformat(value).hour
        minute = datetime.fromisoformat(value).minute
    except ValueError:
        return "UNKNOWN"

    minutes = hour * 60 + minute
    if minutes < 10 * 60:
        return "OPENING"
    if minutes < 12 * 60:
        return "MORNING"
    if minutes < 13 * 60 + 15:
        return "LUNCH"
    if minutes < 15 * 60:
        return "AFTERNOON"
    return "CLOSE"


def _regime(signal: dict, candle: dict, vix_close: float | None) -> str:
    adx = float(signal.get("ADX_14") or 0)
    gap = abs(float(candle.get("gap_percent") or 0))
    if vix_close and vix_close >= 20:
        return "HIGH_VIX"
    if gap >= 0.5:
        return "GAP_DAY"
    if adx >= 25:
        return "TRENDING"
    if adx < 18:
        return "SIDEWAYS"
    return "NORMAL"


def _setup_score(signal: str, close: float, ema_9: float, ema_21: float, rsi: float, vwap: float, atr: float, adx: float) -> float:
    if signal not in {"BUY", "SELL"}:
        return 0.0

    ema_gap = abs(ema_9 - ema_21) / close * 10000
    vwap_gap = abs(close - vwap) / close * 10000
    atr_score = (atr / close) * 10000
    adx_score = adx / 5
    rsi_score = max(0, 70 - abs((60 if signal == "BUY" else 40) - rsi))
    return round(ema_gap + vwap_gap + atr_score + adx_score + (rsi_score / 10), 2)


def _breakout_confirmed(candles: list[dict], index: int, signal: str, lookback: int) -> tuple[bool, str]:
    if lookback <= 0 or signal not in {"BUY", "SELL"}:
        return True, "Breakout filter disabled."

    previous = candles[max(0, index - lookback):index]
    if len(previous) < lookback:
        return False, f"Breakout filter needs {lookback} previous candles."

    close = float(candles[index]["close"])
    if signal == "BUY":
        previous_high = max(float(candle["high"]) for candle in previous)
        if close > previous_high:
            return True, f"BUY breakout above previous {lookback}-candle high."
        return False, f"BUY did not break previous {lookback}-candle high."

    previous_low = min(float(candle["low"]) for candle in previous)
    if close < previous_low:
        return True, f"SELL breakdown below previous {lookback}-candle low."
    return False, f"SELL did not break previous {lookback}-candle low."


def _apply_pattern_confirmation(candles: list[dict], index: int, signal: str, score: float) -> tuple[bool, float, str]:
    if not config.CANDLE_PATTERN_ENABLED or signal not in {"BUY", "SELL"}:
        return True, score, "Candlestick pattern layer disabled."

    pattern_summary = summarize_patterns(candles[max(0, index - 8):index + 1])
    bullish_score = float(pattern_summary.get("bullish_score") or 0)
    bearish_score = float(pattern_summary.get("bearish_score") or 0)
    pattern_text = pattern_summary.get("summary", "")
    pattern_delta = bullish_score - bearish_score if signal == "BUY" else bearish_score - bullish_score

    if config.CANDLE_PATTERN_BLOCK_CONFLICT and pattern_delta < -float(config.CANDLE_PATTERN_CONFLICT_MARGIN):
        return False, score, f"Candlestick conflict. Patterns: {pattern_text}."

    adjusted_score = score
    if pattern_delta > 0:
        adjusted_score += float(config.CANDLE_PATTERN_SCORE_BONUS)
    elif score < float(config.CANDLE_PATTERN_REQUIRE_CONFIRMATION_BELOW_SCORE):
        adjusted_score -= float(config.CANDLE_PATTERN_SCORE_PENALTY)

    if adjusted_score < config.MIN_SETUP_SCORE:
        return False, adjusted_score, f"Candlestick confirmation missing. Patterns: {pattern_text}."

    return True, round(adjusted_score, 2), f"Candlestick patterns: {pattern_text}."


def _signal_from_enriched_candle(symbol: str, candle: dict, candles: list[dict] | None = None, index: int | None = None) -> tuple[dict, dict | None]:
    required = ["ema_9", "ema_21", "rsi_14", "vwap", "atr_14", "adx_14"]
    if any(candle.get(key) is None for key in required):
        return {"SIGNAL": "HOLD", "REASON": "Indicators are not ready yet.", "SETUP_SCORE": 0}, None

    close = float(candle["close"])
    ema_9 = float(candle["ema_9"])
    ema_21 = float(candle["ema_21"])
    rsi = float(candle["rsi_14"])
    vwap = float(candle["vwap"])
    atr = float(candle["atr_14"])
    adx = float(candle["adx_14"])
    ema_gap_points = abs(ema_9 - ema_21)
    atr_percent = (atr / close) * 100 if close else 0

    if ema_gap_points < config.MIN_EMA_GAP_POINTS:
        return {"SIGNAL": "HOLD", "REASON": "Sideways EMA gap filter.", "SETUP_SCORE": 0}, candle
    if atr_percent < config.MIN_ATR_PERCENT:
        return {"SIGNAL": "HOLD", "REASON": "Low ATR filter.", "SETUP_SCORE": 0}, candle
    if adx < config.MIN_ADX:
        return {"SIGNAL": "HOLD", "REASON": "Weak ADX filter.", "SETUP_SCORE": 0}, candle

    bullish = close > ema_9 > ema_21 and close > vwap and 50 <= rsi <= config.MAX_RSI_BUY
    bearish = close < ema_9 < ema_21 and close < vwap and config.MIN_RSI_SELL <= rsi <= 50

    if bullish:
        signal = "BUY"
        reason = f"{symbol} bullish setup."
    elif bearish:
        signal = "SELL"
        reason = f"{symbol} bearish setup."
    else:
        return {"SIGNAL": "HOLD", "REASON": "No clean setup.", "SETUP_SCORE": 0}, candle

    score = _setup_score(signal, close, ema_9, ema_21, rsi, vwap, atr, adx)
    pattern_ok, score, pattern_reason = _apply_pattern_confirmation(candles or [], index or 0, signal, score)
    if not pattern_ok:
        return {"SIGNAL": "HOLD", "REASON": pattern_reason, "SETUP_SCORE": score}, candle

    if score < config.MIN_SETUP_SCORE:
        return {"SIGNAL": "HOLD", "REASON": f"Weak setup score {score}.", "SETUP_SCORE": score}, candle

    lookback = int(getattr(config, "BREAKOUT_LOOKBACK_CANDLES", 0) or 0)
    if candles is not None and index is not None and lookback > 0:
        breakout_ok, breakout_reason = _breakout_confirmed(candles, index, signal, lookback)
        if not breakout_ok:
            return {"SIGNAL": "HOLD", "REASON": breakout_reason, "SETUP_SCORE": score}, candle
        reason = f"{reason} {pattern_reason} {breakout_reason}"
    else:
        reason = f"{reason} {pattern_reason}"

    return {
        "SIGNAL": signal,
        "REASON": reason,
        "CONFIDENCE": "MEDIUM",
        "SETUP_SCORE": score,
        "ADX_14": adx,
        "RSI_14": rsi,
        "ATR_14": atr,
    }, candle


def _result_from_pnl(pnl: float, fallback: str) -> str:
    if pnl > 0:
        return "SUCCESS"
    if pnl < 0:
        return "FAIL"
    return "BREAKEVEN" if fallback == "FAIL" else fallback


def _adaptive_exit_profile(signal: dict) -> dict:
    score = float(signal.get("SETUP_SCORE") or 0)
    adx = float(signal.get("ADX_14") or 0)

    if score >= config.STRONG_MOMENTUM_SCORE and adx >= config.STRONG_MOMENTUM_ADX:
        profile = {
            "exit_profile": "STRONG_MOMENTUM",
            "target_1_percent": config.STRONG_TARGET_STAGE_1_PERCENT,
            "target_2_percent": config.STRONG_TARGET_STAGE_2_PERCENT,
            "target_3_percent": config.STRONG_TARGET_PROFIT_PERCENT,
            "stop_loss_percent": config.STRONG_STOP_LOSS_PERCENT,
        }
    elif score < config.WEAK_MOMENTUM_SCORE or adx < config.WEAK_MOMENTUM_ADX:
        profile = {
            "exit_profile": "WEAK_OR_RISKY",
            "target_1_percent": config.WEAK_TARGET_STAGE_1_PERCENT,
            "target_2_percent": config.WEAK_TARGET_STAGE_2_PERCENT,
            "target_3_percent": config.WEAK_TARGET_PROFIT_PERCENT,
            "stop_loss_percent": config.WEAK_STOP_LOSS_PERCENT,
        }
    else:
        profile = {
            "exit_profile": "NORMAL_MOMENTUM",
            "target_1_percent": config.NORMAL_TARGET_STAGE_1_PERCENT,
            "target_2_percent": config.NORMAL_TARGET_STAGE_2_PERCENT,
            "target_3_percent": config.TARGET_PROFIT_PERCENT,
            "stop_loss_percent": config.STOP_LOSS_PERCENT,
        }

    final_target = float(profile["target_3_percent"])
    stop_loss = float(profile["stop_loss_percent"])
    if stop_loss > final_target:
        profile["stop_loss_percent"] = final_target
    return profile


def _close_adaptive_trade(open_trade: dict, candle: dict) -> dict | None:
    high = float(candle["high"])
    low = float(candle["low"])
    entry = float(open_trade["entry"])
    stop_loss = float(open_trade["stop_loss"])
    target_1 = float(open_trade.get("target_1") or 0)
    target_2 = float(open_trade.get("target_2") or 0)
    target_3 = float(open_trade.get("target_3") or open_trade["target"])
    stage = int(open_trade.get("target_stage") or 0)
    realized = float(open_trade.get("partial_realized_points") or 0)
    remaining = float(open_trade.get("remaining_fraction") or 1.0)
    t1_fraction = config.PARTIAL_EXIT_T1_PERCENT / 100
    t2_fraction = config.PARTIAL_EXIT_T2_PERCENT / 100

    def close(exit_price: float, fallback: str) -> dict:
        pnl = realized + ((exit_price - entry) * remaining if open_trade["signal"] == "BUY" else (entry - exit_price) * remaining)
        closed = dict(open_trade)
        closed.update({
            "exit_time": candle["date"],
            "exit": round(exit_price, 2),
            "result": _result_from_pnl(pnl, fallback),
            "pnl_points": round(pnl, 2),
            "partial_realized_points": round(realized, 2),
            "remaining_fraction": round(remaining, 2),
        })
        return closed

    if open_trade["signal"] == "BUY":
        if low <= stop_loss:
            return close(stop_loss, "FAIL")

        if stage < 1 and target_1 and high >= target_1:
            realized += (target_1 - entry) * t1_fraction
            remaining = max(0.0, remaining - t1_fraction)
            stop_loss = max(stop_loss, entry * (1 + (config.STAGE_1_STOP_PROFIT_PERCENT / 100)))
            stage = 1

        if stage < 2 and target_2 and high >= target_2:
            realized += (target_2 - entry) * min(t2_fraction, remaining)
            remaining = max(0.0, remaining - min(t2_fraction, remaining))
            stop_loss = max(stop_loss, entry * (1 + (config.STAGE_2_STOP_PROFIT_PERCENT / 100)))
            stage = 2

        if high >= target_3:
            return close(target_3, "SUCCESS")

    else:
        if high >= stop_loss:
            return close(stop_loss, "FAIL")

        if stage < 1 and target_1 and low <= target_1:
            realized += (entry - target_1) * t1_fraction
            remaining = max(0.0, remaining - t1_fraction)
            stop_loss = min(stop_loss, entry * 0.97)
            stage = 1

        if stage < 2 and target_2 and low <= target_2:
            realized += (entry - target_2) * min(t2_fraction, remaining)
            remaining = max(0.0, remaining - min(t2_fraction, remaining))
            stop_loss = min(stop_loss, entry * 0.85)
            stage = 2

        if low <= target_3:
            return close(target_3, "SUCCESS")

    open_trade["target_stage"] = stage
    open_trade["stop_loss"] = round(stop_loss, 2)
    open_trade["partial_realized_points"] = round(realized, 2)
    open_trade["remaining_fraction"] = round(remaining, 2)
    return None


def _close_trade(open_trade: dict, candle: dict) -> dict | None:
    if open_trade.get("adaptive_exit_profile"):
        return _close_adaptive_trade(open_trade, candle)

    high = float(candle["high"])
    low = float(candle["low"])
    close = float(candle["close"])

    if open_trade["signal"] == "BUY":
        if open_trade.get("target_1") and open_trade.get("target_stage", 0) < 1 and low <= open_trade["stop_loss"]:
            exit_price = open_trade["stop_loss"]
            pnl = exit_price - open_trade["entry"]
            closed = dict(open_trade)
            closed.update({
                "exit_time": candle["date"],
                "exit": round(exit_price, 2),
                "result": _result_from_pnl(pnl, "FAIL"),
                "pnl_points": round(pnl, 2),
            })
            return closed

        if open_trade.get("target_1") and high >= open_trade["target_1"] and open_trade.get("target_stage", 0) < 1:
            open_trade["target_stage"] = 1
            open_trade["stop_loss"] = max(open_trade["stop_loss"], open_trade["entry"])

        hit_stop = low <= open_trade["stop_loss"]
        hit_target = high >= open_trade["target"]
        if hit_stop:
            exit_price = open_trade["stop_loss"]
            result = "FAIL"
        elif hit_target:
            exit_price = open_trade["target"]
            result = "SUCCESS"
        else:
            return None
        pnl = exit_price - open_trade["entry"]
    else:
        if open_trade.get("target_1") and open_trade.get("target_stage", 0) < 1 and high >= open_trade["stop_loss"]:
            exit_price = open_trade["stop_loss"]
            pnl = open_trade["entry"] - exit_price
            closed = dict(open_trade)
            closed.update({
                "exit_time": candle["date"],
                "exit": round(exit_price, 2),
                "result": _result_from_pnl(pnl, "FAIL"),
                "pnl_points": round(pnl, 2),
            })
            return closed

        if open_trade.get("target_1") and low <= open_trade["target_1"] and open_trade.get("target_stage", 0) < 1:
            open_trade["target_stage"] = 1
            open_trade["stop_loss"] = min(open_trade["stop_loss"], open_trade["entry"])

        hit_stop = high >= open_trade["stop_loss"]
        hit_target = low <= open_trade["target"]
        if hit_stop:
            exit_price = open_trade["stop_loss"]
            result = "FAIL"
        elif hit_target:
            exit_price = open_trade["target"]
            result = "SUCCESS"
        else:
            return None
        pnl = open_trade["entry"] - exit_price

    closed = dict(open_trade)
    closed.update({
        "exit_time": candle["date"],
        "exit": round(exit_price, 2),
        "result": _result_from_pnl(pnl, result),
        "pnl_points": round(pnl, 2),
    })
    return closed


def _load_rules(path: str) -> dict:
    if not path:
        return {}
    with Path(path).open() as json_file:
        return json.load(json_file)


def _bucket_key(symbol: str, regime: str, time_bucket: str, signal: str) -> str:
    return f"{symbol}|{regime}|{time_bucket}|{signal}"


def _rule_action(rules: dict, symbol: str, regime: str, time_bucket: str, signal: str) -> str:
    if not rules:
        return "ALLOW"
    return rules.get("rules", {}).get(_bucket_key(symbol, regime, time_bucket, signal), "ALLOW")


def run_symbol_backtest(
    symbol: str,
    interval: str,
    atr_target: float,
    atr_stop: float,
    vix_map: dict[str, float],
    rules: dict | None = None,
    adaptive_exit_profile: bool = False,
) -> dict:
    print(f"Loading {symbol} {interval} candles...", flush=True)
    candles = enrich_candles(_read_candles(DATA_DIR / f"{symbol}_{interval}.csv"))
    print(f"Backtesting {symbol}: {len(candles)} candles", flush=True)
    return run_symbol_backtest_on_candles(
        symbol=symbol,
        interval=interval,
        candles=candles,
        atr_target=atr_target,
        atr_stop=atr_stop,
        vix_map=vix_map,
        rules=rules,
        adaptive_exit_profile=adaptive_exit_profile,
        progress=True,
    )


def run_symbol_backtest_on_candles(
    symbol: str,
    interval: str,
    candles: list[dict],
    atr_target: float,
    atr_stop: float,
    vix_map: dict[str, float],
    rules: dict | None = None,
    adaptive_exit_profile: bool = False,
    progress: bool = False,
) -> dict:
    trades = []
    open_trade = None

    for index in range(config.MIN_CANDLES_FOR_STRATEGY, len(candles)):
        latest = candles[index]
        if progress and index % 25000 == 0:
            print(f"{symbol}: processed {index}/{len(candles)} candles", flush=True)

        if open_trade:
            closed = _close_trade(open_trade, latest)
            if closed:
                trades.append(closed)
                open_trade = None
            continue

        signal, signal_candle = _signal_from_enriched_candle(symbol, latest, candles, index)
        if signal.get("SIGNAL") not in {"BUY", "SELL"} or not signal_candle:
            continue

        entry = float(signal_candle["close"])
        atr = float(signal_candle.get("atr_14") or 0)
        if atr <= 0:
            continue

        target_1 = ""
        target_2 = ""
        target_3 = ""
        target_1_percent = ""
        target_2_percent = ""
        target_3_percent = ""
        stop_loss_percent = ""
        exit_profile = ""
        stop_distance = atr * atr_stop
        target_distance = atr * atr_target

        if adaptive_exit_profile:
            profile = _adaptive_exit_profile(signal)
            exit_profile = profile["exit_profile"]
            target_1_percent = float(profile["target_1_percent"])
            target_2_percent = float(profile["target_2_percent"])
            target_3_percent = float(profile["target_3_percent"])
            stop_loss_percent = float(profile["stop_loss_percent"])
            target_1_distance = stop_distance * (target_1_percent / stop_loss_percent)
            target_2_distance = stop_distance * (target_2_percent / stop_loss_percent)
            target_distance = stop_distance * (target_3_percent / stop_loss_percent)

        if signal["SIGNAL"] == "BUY":
            stop_loss = entry - stop_distance
            target = entry + target_distance
            if adaptive_exit_profile:
                target_1 = entry + target_1_distance
                target_2 = entry + target_2_distance
                target_3 = target
        else:
            stop_loss = entry + stop_distance
            target = entry - target_distance
            if adaptive_exit_profile:
                target_1 = entry - target_1_distance
                target_2 = entry - target_2_distance
                target_3 = target

        vix_close = vix_map.get(str(signal_candle["date"]))
        regime = _regime(signal, latest, vix_close)
        time_bucket = _time_bucket(str(signal_candle["date"]))
        action = _rule_action(rules or {}, symbol, regime, time_bucket, signal["SIGNAL"])
        if action == "BLOCK":
            continue

        open_trade = {
            "symbol": symbol,
            "signal": signal["SIGNAL"],
            "entry_time": signal_candle["date"],
            "exit_time": "",
            "entry": round(entry, 2),
            "exit": "",
            "stop_loss": round(stop_loss, 2),
            "target": round(target, 2),
            "target_1": round(target_1, 2) if target_1 != "" else "",
            "target_2": round(target_2, 2) if target_2 != "" else "",
            "target_3": round(target_3, 2) if target_3 != "" else "",
            "target_stage": 0,
            "exit_profile": exit_profile,
            "adaptive_exit_profile": adaptive_exit_profile,
            "target_1_percent": target_1_percent,
            "target_2_percent": target_2_percent,
            "target_3_percent": target_3_percent,
            "stop_loss_percent": stop_loss_percent,
            "partial_realized_points": 0,
            "remaining_fraction": 1.0,
            "result": "OPEN",
            "pnl_points": "",
            "score": signal.get("SETUP_SCORE"),
            "adx": signal.get("ADX_14"),
            "rsi": signal.get("RSI_14"),
            "gap_percent": latest.get("gap_percent", 0),
            "vix_close": vix_close or "",
            "regime": regime,
            "time_bucket": time_bucket,
            "rule_action": action,
            "reason": signal.get("REASON"),
        }

    wins = sum(1 for trade in trades if float(trade["pnl_points"] or 0) > 0)
    losses = sum(1 for trade in trades if float(trade["pnl_points"] or 0) < 0)
    breakevens = sum(1 for trade in trades if float(trade["pnl_points"] or 0) == 0)
    total = len(trades)
    pnl = round(sum(float(trade["pnl_points"] or 0) for trade in trades), 2)
    return {
        "symbol": symbol,
        "trades": trades,
        "summary": {
            "symbol": symbol,
            "interval": interval,
            "total_trades": total,
            "wins": wins,
            "losses": losses,
            "breakevens": breakevens,
            "win_rate": round((wins / total) * 100, 2) if total else 0,
            "total_points": pnl,
        },
    }


def _write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _pattern_summary(trades: list[dict]) -> list[dict]:
    buckets = defaultdict(list)
    for trade in trades:
        buckets[(trade["symbol"], trade["regime"], trade["time_bucket"], trade["signal"])].append(trade)

    rows = []
    for (symbol, regime, time_bucket, signal), group in sorted(buckets.items()):
        wins = sum(1 for trade in group if float(trade["pnl_points"] or 0) > 0)
        total = len(group)
        losses = sum(1 for trade in group if float(trade["pnl_points"] or 0) < 0)
        breakevens = sum(1 for trade in group if float(trade["pnl_points"] or 0) == 0)
        pnl = round(sum(float(trade["pnl_points"] or 0) for trade in group), 2)
        rows.append({
            "symbol": symbol,
            "regime": regime,
            "time_bucket": time_bucket,
            "signal": signal,
            "trades": total,
            "wins": wins,
            "losses": losses,
            "breakevens": breakevens,
            "win_rate": round((wins / total) * 100, 2) if total else 0,
            "total_points": pnl,
        })
    return rows


def main():
    args = _parse_args()
    if args.min_setup_score is not None:
        config.MIN_SETUP_SCORE = args.min_setup_score
    if args.min_adx is not None:
        config.MIN_ADX = args.min_adx
    if args.breakout_lookback is not None:
        config.BREAKOUT_LOOKBACK_CANDLES = args.breakout_lookback

    started_at = datetime.now()
    run_dir = REPORT_DIR / started_at.strftime("%Y%m%d_%H%M%S")
    vix_map = _vix_by_datetime(args.interval)
    rules = _load_rules(args.rules_file)
    all_trades = []
    summaries = []

    for symbol in args.symbols:
        result = run_symbol_backtest(
            symbol,
            args.interval,
            args.atr_target,
            args.atr_stop,
            vix_map,
            rules,
            adaptive_exit_profile=args.adaptive_exit_profile,
        )
        all_trades.extend(result["trades"])
        summaries.append(result["summary"])

    pattern_rows = _pattern_summary(all_trades)
    _write_csv(run_dir / "trades.csv", all_trades, TRADE_FIELDS)
    _write_csv(run_dir / "pattern_summary.csv", pattern_rows, [
        "symbol", "regime", "time_bucket", "signal", "trades", "wins", "losses", "breakevens", "win_rate", "total_points"
    ])
    (run_dir / "summary.json").write_text(json.dumps({
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now().isoformat(),
        "interval": args.interval,
        "rules_file": args.rules_file,
        "adaptive_exit_profile": args.adaptive_exit_profile,
        "summaries": summaries,
        "trade_file": str(run_dir / "trades.csv"),
        "pattern_summary_file": str(run_dir / "pattern_summary.csv"),
    }, indent=2))

    print("Backtest complete.")
    for summary in summaries:
        print(
            f"- {summary['symbol']}: trades={summary['total_trades']} "
            f"win_rate={summary['win_rate']}% points={summary['total_points']}"
        )
    print(f"Report folder: {run_dir}")


if __name__ == "__main__":
    main()
