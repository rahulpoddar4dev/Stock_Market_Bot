# paper_trader.py - Save paper trade decisions without placing real orders

import csv
from pathlib import Path

import config
from log_utils import csv_date, csv_datetime, csv_time
from research_logger import record_ghost_trade, record_no_trade_decision, should_start_ghost
from database import db_log_signal_decision


LOG_DIR = Path("logs")
TRADE_LOG_FILE = LOG_DIR / "paper_option_trades.csv"
DATEWISE_DIR = LOG_DIR / "by_date"


def _read_existing_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []

    with path.open() as csv_file:
        return list(csv.DictReader(csv_file))


def _append_row(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = _read_existing_rows(path)
    fieldnames = list(row.keys())

    for existing_row in rows:
        for fieldname in existing_row.keys():
            if fieldname and fieldname not in fieldnames:
                fieldnames.append(fieldname)

    with path.open("w", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        for existing_row in rows:
            writer.writerow(existing_row)
        writer.writerow(row)


def record_paper_trade(
    market_data: dict,
    signal: dict,
    option_trade: dict | None = None,
    explanation: dict | None = None,
) -> None:
    """Record a paper trade decision to logs/paper_trades.csv."""
    LOG_DIR.mkdir(exist_ok=True)
    option_trade = option_trade or {}
    explanation = explanation or {}

    row = {
        "date": csv_date(),
        "time": csv_time(),
        "event_time": csv_datetime(),
        "symbol": market_data.get("symbol"),
        "last_price": market_data.get("last_price"),
        "high": market_data.get("high"),
        "low": market_data.get("low"),
        "volume": market_data.get("volume"),
        "change_percent": market_data.get("change_percent"),
        "pre_open_bias": market_data.get("pre_open_bias"),
        "pre_open_event_time": market_data.get("pre_open_event_time"),
        "trading_mode": config.TRADING_MODE,
        "paper_trade": config.PAPER_TRADE,
        "signal": signal.get("SIGNAL"),
        "reason": signal.get("REASON"),
        "confidence": signal.get("CONFIDENCE"),
        "news_sentiment": signal.get("NEWS_SENTIMENT") or market_data.get("news_sentiment"),
        "news_risk_level": signal.get("NEWS_RISK_LEVEL") or market_data.get("news_risk_level"),
        "news_reason": signal.get("NEWS_REASON") or market_data.get("news_reason"),
        "news_headlines": signal.get("NEWS_HEADLINES") or market_data.get("news_headlines"),
        "news_blocked_original_signal": signal.get("NEWS_BLOCKED_ORIGINAL_SIGNAL"),
        "news_mode": signal.get("NEWS_MODE") or market_data.get("news_mode"),
        "news_conflict": signal.get("NEWS_CONFLICT") or market_data.get("news_conflict"),
        "market_regime": signal.get("MARKET_REGIME") or market_data.get("market_regime"),
        "market_regime_risk": signal.get("MARKET_REGIME_RISK") or market_data.get("market_regime_risk"),
        "market_regime_action": signal.get("MARKET_REGIME_ACTION") or market_data.get("market_regime_action"),
        "market_regime_reason": signal.get("MARKET_REGIME_REASON") or market_data.get("market_regime_reason"),
        "market_regime_size_percent": signal.get("MARKET_REGIME_SIZE_PERCENT") or option_trade.get("market_regime_size_percent"),
        "time_window": signal.get("TIME_WINDOW"),
        "time_quality_action": signal.get("TIME_QUALITY_ACTION"),
        "time_quality_reason": signal.get("TIME_QUALITY_REASON"),
        "time_quality_size_percent": signal.get("TIME_QUALITY_SIZE_PERCENT") or option_trade.get("time_quality_size_percent"),
        "decision_engine": signal.get("DECISION_ENGINE"),
        "chart_signal": signal.get("CHART_SIGNAL"),
        "index_alignment": signal.get("INDEX_ALIGNMENT"),
        "index_reason": signal.get("INDEX_REASON"),
        "entry_mode": signal.get("ENTRY_MODE"),
        "entry_freshness": signal.get("ENTRY_FRESHNESS"),
        "entry_reason": signal.get("ENTRY_REASON"),
        "trade_quality_score": signal.get("TRADE_QUALITY_SCORE") or option_trade.get("trade_quality_score"),
        "trade_quality_decision": signal.get("TRADE_QUALITY_DECISION") or option_trade.get("trade_quality_decision"),
        "trade_quality_reason": signal.get("TRADE_QUALITY_REASON") or option_trade.get("trade_quality_reason"),
        "trade_quality_decision_reason": signal.get("TRADE_QUALITY_DECISION_REASON") or option_trade.get("trade_quality_decision_reason"),
        "confidence_score": option_trade.get("confidence_score"),
        "option_chain_score": option_trade.get("option_chain_score"),
        "option_chain_decision_reason": option_trade.get("option_chain_decision_reason"),
        "option_chain_scores": option_trade.get("option_chain_scores"),
        "learning_score_penalty": option_trade.get("learning_score_penalty") or signal.get("LEARNING_SCORE_PENALTY"),
        "learning_penalty_reason": option_trade.get("learning_penalty_reason") or signal.get("LEARNING_PENALTY_REASON"),
        "entry_logic": option_trade.get("entry_logic"),
        "entry_trigger_price": option_trade.get("entry_trigger_price"),
        "current_price": option_trade.get("current_price"),
        "potential_left_percent": option_trade.get("potential_left_percent"),
        "risk_reward": option_trade.get("risk_reward"),
        "hard_reject_reason": option_trade.get("hard_reject_reason"),
        "chain_support": option_trade.get("chain_support"),
        "chain_resistance": option_trade.get("chain_resistance"),
        "chain_context_reason": option_trade.get("chain_context_reason"),
        "quality_entry_freshness": signal.get("QUALITY_ENTRY_FRESHNESS") or option_trade.get("quality_entry_freshness"),
        "quality_trend_strength": signal.get("QUALITY_TREND_STRENGTH") or option_trade.get("quality_trend_strength"),
        "quality_retest_breakout": signal.get("QUALITY_RETEST_BREAKOUT") or option_trade.get("quality_retest_breakout"),
        "quality_option_premium": signal.get("QUALITY_OPTION_PREMIUM") or option_trade.get("quality_option_premium"),
        "quality_market_context": signal.get("QUALITY_MARKET_CONTEXT") or option_trade.get("quality_market_context"),
        "quality_candle_pattern": signal.get("QUALITY_CANDLE_PATTERN") or option_trade.get("quality_candle_pattern"),
        "quality_size_percent": signal.get("QUALITY_SIZE_PERCENT") or option_trade.get("quality_size_percent"),
        "lunch_trade_allowed": signal.get("LUNCH_TRADE_ALLOWED") or option_trade.get("lunch_trade"),
        "lunch_reason": signal.get("LUNCH_REASON") or option_trade.get("lunch_reason"),
        "lunch_blocked_original_signal": signal.get("LUNCH_BLOCKED_ORIGINAL_SIGNAL"),
        "lunch_size_percent": option_trade.get("lunch_size_percent"),
        "underlying": option_trade.get("underlying"),
        "option_symbol": option_trade.get("option_symbol"),
        "display_symbol": option_trade.get("display_symbol"),
        "exchange": option_trade.get("exchange"),
        "option_type": option_trade.get("option_type"),
        "order_action": option_trade.get("order_action"),
        "expiry": option_trade.get("expiry"),
        "strike": option_trade.get("strike"),
        "trade_type": option_trade.get("trade_type"),
        "selection_mode": option_trade.get("selection_mode"),
        "lot_size": option_trade.get("lot_size"),
        "lots": option_trade.get("lots"),
        "entry_price": option_trade.get("entry_price"),
        "quantity": option_trade.get("quantity"),
        "remaining_quantity": option_trade.get("remaining_quantity"),
        "estimated_required_amount": option_trade.get("estimated_required_amount"),
        "estimated_risk_amount": option_trade.get("estimated_risk_amount"),
        "gross_pnl": option_trade.get("gross_pnl"),
        "paper_total_charges": option_trade.get("paper_total_charges"),
        "target_1": option_trade.get("target_1"),
        "target_2": option_trade.get("target_2"),
        "target_3": option_trade.get("target_3"),
        "stop_loss": option_trade.get("stop_loss") or signal.get("STOP_LOSS"),
        "target": option_trade.get("target") or signal.get("TARGET"),
        "target_1_percent": option_trade.get("target_1_percent"),
        "target_2_percent": option_trade.get("target_2_percent"),
        "target_3_percent": option_trade.get("target_3_percent"),
        "stop_loss_percent": option_trade.get("stop_loss_percent"),
        "target_percent": option_trade.get("target_percent"),
        "exit_profile": option_trade.get("exit_profile"),
        "exit_profile_reason": option_trade.get("exit_profile_reason"),
        "status": option_trade.get("status", "NO_TRADE"),
        "risk_block_reason": option_trade.get("risk_block_reason"),
        "entry_order_type": option_trade.get("entry_order_type"),
        "entry_order_price": option_trade.get("entry_order_price"),
        "order_status": option_trade.get("order_status"),
        "paper_fill_status": option_trade.get("paper_fill_status"),
        "paper_entry_reference_price": option_trade.get("paper_entry_reference_price"),
        "paper_entry_slippage": option_trade.get("paper_entry_slippage"),
        "paper_entry_charges": option_trade.get("paper_entry_charges"),
        "paper_exit_reference_price": option_trade.get("paper_exit_reference_price"),
        "paper_exit_slippage": option_trade.get("paper_exit_slippage"),
        "paper_exit_charges": option_trade.get("paper_exit_charges"),
        "paper_missed_fill_reason": option_trade.get("paper_missed_fill_reason"),
        "best_bid": option_trade.get("best_bid"),
        "best_ask": option_trade.get("best_ask"),
        "bid_qty": option_trade.get("bid_qty"),
        "ask_qty": option_trade.get("ask_qty"),
        "bid_ask_spread": option_trade.get("bid_ask_spread"),
        "bid_ask_spread_percent": option_trade.get("bid_ask_spread_percent"),
        "top_depth_sufficient": option_trade.get("top_depth_sufficient"),
        "market_depth_block_reason": option_trade.get("market_depth_block_reason"),
        "learning_exit_mode": option_trade.get("learning_exit_mode"),
        "learning_adjustment_reason": option_trade.get("learning_adjustments") or option_trade.get("learning_adjustment_reason"),
        "learning_size_reduction_percent": option_trade.get("learning_size_reduction_percent"),
        "trade_reason": explanation.get("trade_reason"),
        "expected_profit": explanation.get("expected_profit"),
        "expected_profit_percent": explanation.get("expected_profit_percent"),
        "max_loss": explanation.get("max_loss"),
        "max_loss_percent": explanation.get("max_loss_percent"),
        "when_profit": explanation.get("when_profit"),
        "when_loss": explanation.get("when_loss"),
        "failure_reason": explanation.get("failure_reason"),
        "risk_reward": explanation.get("risk_reward"),
    }

    _append_row(TRADE_LOG_FILE, row)
    daily_file = DATEWISE_DIR / row["date"] / "paper_option_trades.csv"
    _append_row(daily_file, row)

    status = str(option_trade.get("status") or row.get("status") or "")
    signal_value = str(signal.get("SIGNAL") or "")
    quantity = int(float(option_trade.get("quantity") or 0))
    decision_reason = option_trade.get("risk_block_reason") or signal.get("REASON") or status
    if (
        signal_value == "HOLD"
        or quantity <= 0
        or status.startswith("BLOCKED")
        or "LOG_ONLY" in status
        or status in {"NO_TRADE", "MISSED_PAPER_FILL"}
    ):
        record_no_trade_decision(market_data, signal, option_trade, decision_reason)
        if should_start_ghost(option_trade):
            record_ghost_trade(market_data, signal, option_trade, decision_reason)

    try:
        db_log_signal_decision(
            symbol=market_data.get("symbol", "NIFTY"),
            signal=signal,
            final_decision=status or signal_value,
            reason=decision_reason or "",
            extra={"option": option_trade, "market_data": market_data}
        )
    except Exception:
        pass

    print(f"Paper trade saved to {TRADE_LOG_FILE}")
    print(f"Date-wise paper trade saved to {daily_file}")
