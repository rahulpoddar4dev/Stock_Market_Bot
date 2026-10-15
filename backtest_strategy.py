# backtest_strategy.py - Simple candle-based strategy backtest

from datetime import datetime, timedelta

import config
from indicators import enrich_candles
from fyers_login import login
from market_data import _find_index_token
from strategy import build_strategy_signal


def run_backtest(fyers, symbol: str = "NIFTY", days: int = 20) -> dict:
    to_date = datetime.now()
    from_date = to_date - timedelta(days=days)
    candles = fyers.historical_data(
        instrument_token=_find_index_token(fyers, symbol),
        from_date=from_date,
        to_date=to_date,
        interval=config.CANDLE_INTERVAL,
    )

    enriched = enrich_candles(candles)
    trades = []
    open_trade = None

    for index in range(config.MIN_CANDLES_FOR_STRATEGY, len(enriched)):
        current_candles = enriched[: index + 1]
        latest = current_candles[-1]

        if open_trade:
            close = float(latest["close"])
            if open_trade["signal"] == "BUY":
                hit_target = close >= open_trade["target"]
                hit_stop = close <= open_trade["stop_loss"]
                pnl = close - open_trade["entry"]
            else:
                hit_target = close <= open_trade["target"]
                hit_stop = close >= open_trade["stop_loss"]
                pnl = open_trade["entry"] - close

            if hit_target or hit_stop:
                open_trade.update({
                    "exit_time": latest["date"],
                    "exit": close,
                    "result": "SUCCESS" if hit_target else "FAIL",
                    "pnl": round(pnl, 2),
                })
                trades.append(open_trade)
                open_trade = None

            continue

        signal, signal_candle = build_strategy_signal({"symbol": symbol}, current_candles)
        if signal["SIGNAL"] not in {"BUY", "SELL"} or not signal_candle:
            continue

        entry = float(signal_candle["close"])
        atr = float(signal_candle.get("atr_14") or 0)
        if signal["SIGNAL"] == "BUY":
            stop_loss = entry - (atr * 1.2)
            target = entry + (atr * 2.0)
        else:
            stop_loss = entry + (atr * 1.2)
            target = entry - (atr * 2.0)

        open_trade = {
            "symbol": symbol,
            "signal": signal["SIGNAL"],
            "entry_time": signal_candle["date"],
            "entry": entry,
            "stop_loss": round(stop_loss, 2),
            "target": round(target, 2),
            "score": signal.get("SETUP_SCORE"),
            "reason": signal.get("REASON"),
        }

    wins = sum(1 for trade in trades if trade["result"] == "SUCCESS")
    losses = sum(1 for trade in trades if trade["result"] == "FAIL")
    total = len(trades)
    total_pnl = round(sum(trade["pnl"] for trade in trades), 2)

    return {
        "symbol": symbol,
        "days": days,
        "trades": total,
        "wins": wins,
        "losses": losses,
        "win_rate": round((wins / total) * 100, 2) if total else 0,
        "total_points": total_pnl,
        "last_trades": trades[-10:],
    }


def main():
    fyers = login()
    for symbol in config.ANALYZE_INDICES:
        try:
            summary = run_backtest(fyers, symbol=symbol)
        except Exception as error:
            print(f"{symbol} backtest failed: {error}")
            continue

        print()
        print(f"{symbol} BACKTEST")
        print("=" * 40)
        print(f"Days: {summary['days']}")
        print(f"Trades: {summary['trades']}")
        print(f"Wins: {summary['wins']}")
        print(f"Losses: {summary['losses']}")
        print(f"Win rate: {summary['win_rate']}%")
        print(f"Total points: {summary['total_points']}")
        print("Recent trades:")
        for trade in summary["last_trades"]:
            print(
                f"- {trade['entry_time']} {trade['signal']} entry {trade['entry']} "
                f"exit {trade['exit']} {trade['result']} pnl {trade['pnl']}"
            )


if __name__ == "__main__":
    main()
