# tests/test_multi_timeframe_decision_engine.py - Unit tests for 15m HTF, Structural SL, and Runner Trailing Engine

import pytest
import config
from datetime import datetime
from strategy import build_strategy_signal, _analyze_htf_trend
from option_selector import calculate_option_stop_loss_percent, select_index_option
from position_sizer import apply_position_sizing
from trade_state import update_target_stage_stop, monitor_open_trade


def _make_dummy_candle(date_str, close, high=None, low=None, open_val=None, volume=1000):
    high = high if high is not None else close + 5
    low = low if low is not None else close - 5
    open_val = open_val if open_val is not None else close
    return {
        "date": date_str,
        "open": open_val,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


def _make_trending_candles(count=35, start_price=22500, direction="DOWN"):
    candles = []
    price = start_price
    for i in range(count):
        step = -15 if direction == "DOWN" else 15
        price += step
        dt = f"2026-10-08T09:{i:02d}:00"
        candles.append(_make_dummy_candle(dt, price, high=price + 8, low=price - 8, open_val=price - step))
    return candles


def test_htf_trend_analysis_detects_bearish_and_bullish():
    # 1. Bearish 15m candles (falling price)
    bearish_htf = _make_trending_candles(count=20, start_price=22500, direction="DOWN")
    bearish_analysis = _analyze_htf_trend(bearish_htf)
    assert bearish_analysis["bias"] == "BEARISH"
    assert bearish_analysis["swing_high"] is not None

    # 2. Bullish 15m candles (rising price)
    bullish_htf = _make_trending_candles(count=20, start_price=22000, direction="UP")
    bullish_analysis = _analyze_htf_trend(bullish_htf)
    assert bullish_analysis["bias"] == "BULLISH"
    assert bullish_analysis["swing_low"] is not None


def _make_bullish_setup_5m(count=35):
    candles = []
    for i in range(25):
        dt = f"2026-10-08T09:{i:02d}:00"
        p = 22000 + (2 if i % 2 == 0 else -2)
        candles.append(_make_dummy_candle(dt, p, p + 15, p - 15, p))
    p = 22000
    for i in range(25, count):
        p += 5
        dt = f"2026-10-08T09:{i:02d}:00"
        candles.append(_make_dummy_candle(dt, p, p + 16, p - 12, p - 5))
    return candles


def _make_bearish_setup_5m(count=35):
    candles = []
    for i in range(25):
        dt = f"2026-10-08T09:{i:02d}:00"
        p = 22500 + (2 if i % 2 == 0 else -2)
        candles.append(_make_dummy_candle(dt, p, p + 15, p - 15, p))
    p = 22500
    for i in range(25, count):
        p -= 5
        dt = f"2026-10-08T09:{i:02d}:00"
        candles.append(_make_dummy_candle(dt, p, p + 12, p - 16, p + 5))
    return candles


def test_counter_trend_ce_blocked_when_htf_is_bearish():
    # Setup: 5-minute candles form a valid bullish setup
    # But 15m HTF candles are deeply bearish
    bearish_htf = _make_trending_candles(count=25, start_price=22500, direction="DOWN")
    candles_5m = _make_bullish_setup_5m(count=35)
    market_data = {"symbol": "NIFTY", "last_price": 22050, "change_percent": -1.2}

    signal, _ = build_strategy_signal(
        market_data,
        candles_5m,
        htf_candles=bearish_htf,
    )
    # Even though 5m setup is bullish, HTF is strictly BEARISH -> BUY CE MUST BE BLOCKED!
    assert signal["SIGNAL"] == "HOLD"
    assert "Counter-trend BUY blocked" in signal["REASON"]
    assert "15-Minute HTF is strictly BEARISH" in signal["REASON"]


def test_counter_trend_pe_blocked_when_htf_is_bullish():
    # Setup: 15m HTF candles are deeply bullish
    bullish_htf = _make_trending_candles(count=25, start_price=22000, direction="UP")
    candles_5m = _make_bearish_setup_5m(count=35)
    market_data = {"symbol": "NIFTY", "last_price": 22450, "change_percent": 1.5}

    signal, _ = build_strategy_signal(
        market_data,
        candles_5m,
        htf_candles=bullish_htf,
    )
    # Even though 5m setup is bearish, HTF is strictly BULLISH -> SELL PE MUST BE BLOCKED!
    assert signal["SIGNAL"] == "HOLD"
    assert "Counter-trend SELL blocked" in signal["REASON"]
    assert "15-Minute HTF is strictly BULLISH" in signal["REASON"]


def test_calculate_option_stop_loss_percent_tiers():
    # Cheap option (< 100): minimum 18% floor to avoid noise wicks
    sl_cheap, reason_cheap = calculate_option_stop_loss_percent(entry_price=75.0, spot_price=22400)
    assert sl_cheap >= 18.0

    # Medium option (100 - 220): minimum 15% floor
    sl_med, reason_med = calculate_option_stop_loss_percent(entry_price=130.0, spot_price=22400)
    assert sl_med >= 15.0

    # Expensive option (> 220): 12.0% floor
    sl_exp, reason_exp = calculate_option_stop_loss_percent(entry_price=300.0, spot_price=22400)
    assert sl_exp == 12.0

    # Structural SL with index stop loss:
    strategy_signal = {"INDEX_STOP_LOSS": 22445} # 45 pts away on Nifty
    # 45 pts * 0.52 delta = 23.4 option pts / 130 entry = 18.0%
    sl_struct, reason_struct = calculate_option_stop_loss_percent(
        entry_price=130.0,
        spot_price=22400,
        strategy_signal=strategy_signal,
    )
    assert sl_struct == 18.0
    assert "Multi-timeframe structural SL" in reason_struct


def test_runner_peak_trailing_protects_profit_without_choke():
    # Entry at 100, Target 1 at 114 (+14%), Target 2 at 125
    trade = {
        "entry_price": 100.0,
        "stop_loss": 85.0,
        "target_1": 114.0,
        "target_2": 125.0,
        "target_stage": 0,
        "highest_price_seen": 100.0,
    }
    # Current price moves to 115 (T1 hit)
    updated, reason = update_target_stage_stop(trade, 115.0)
    assert updated["target_stage"] == 1
    # Adaptive shadow trail: 35% of (125-114) = 3.85 -> 115 - 3.85 = 111.15 (above 65% parity floor 109.10)
    assert updated["stop_loss"] >= 109.10

    # Option runs to 150 (+50%)
    updated_run, _ = update_target_stage_stop(updated, 150.0)
    # Adaptive shadow trail: max(3.85, 2.5% of 150 = 3.75) = 3.85 -> 150 - 3.85 = 146.15
    assert updated_run["stop_loss"] == 146.15
    assert updated_run["highest_price_seen"] == 150.0

    # Option runs to 250 (+150%)
    updated_mega, _ = update_target_stage_stop(updated_run, 250.0)
    # Adaptive shadow trail: max(3.85, 2.5% of 250 = 6.25) = 6.25 -> 250 - 6.25 = 243.75
    assert updated_mega["stop_loss"] == 243.75


def test_position_sizing_adapts_to_wider_sl_without_increasing_rupee_risk():
    balance = 100000.0
    # Case A: Narrow 10% SL on Rs 120 option (SL = 108, risk per lot = 12 * 25 = Rs 300)
    trade_narrow = {
        "entry_price": 120.0,
        "stop_loss": 108.0,
        "lot_size": 25,
        "market_regime": "NORMAL",
    }
    sized_narrow = apply_position_sizing(trade_narrow, balance)

    # Case B: Structural 16% SL on Rs 120 option (SL = 100.80, risk per lot = 19.20 * 25 = Rs 480)
    trade_wide = {
        "entry_price": 120.0,
        "stop_loss": 100.80,
        "lot_size": 25,
        "market_regime": "NORMAL",
    }
    sized_wide = apply_position_sizing(trade_wide, balance)

    # Lots automatically decrease so rupee risk NEVER exceeds risk hard limit
    assert sized_wide["lots"] <= sized_narrow["lots"]
    assert sized_wide["estimated_risk_amount"] <= sized_wide["risk_hard_limit"]


def test_v5_strategy_hold_preserves_htf_bias_and_blocks_ai_override_counter_trend():
    from ai_trade_analyst import ai_trade_signal_for_analysis

    # 15m HTF is strictly BEARISH, while 5m candles have a sideways/hold setup
    bearish_htf = _make_trending_candles(count=25, start_price=22500, direction="DOWN")
    candles_5m = _make_bullish_setup_5m(count=35)
    market_data = {"symbol": "NIFTY", "last_price": 22050, "change_percent": 0.4}

    signal, latest = build_strategy_signal(market_data, candles_5m, htf_candles=bearish_htf)
    assert signal["SIGNAL"] == "HOLD"
    # v5: _hold() must preserve HTF_BIAS so AI override never goes blind
    assert signal["HTF_BIAS"] == "BEARISH"
    assert signal.get("EMA_9") is not None

    analysis = {
        "symbol": "NIFTY",
        "market_data": {**market_data, "htf_bias": signal["HTF_BIAS"]},
        "candles": candles_5m,
        "strategy_signal": signal,
        "latest_candle": latest,
    }
    ai_sig = ai_trade_signal_for_analysis(analysis)
    assert ai_sig["SIGNAL"] == "HOLD"


def test_v5_strong_near_t1_buffer_triggers_full_ride_instead_of_false_stall():
    from market_depth import is_t1_continuation_strong

    # Price is at 114.40 (in 94% buffer for T1=115.0 on Entry=100.0), climbing from 113.80 with strong 5-level depth
    trade = {
        "entry_price": 100.0,
        "target_1": 115.0,
        "last_monitor_price": 113.80,
        "bid_ask_spread_percent": 0.35,
        "total_bid_qty_5": 1200,
        "total_ask_qty_5": 800,
        "prev_total_bid_qty_5": 1100,
    }
    strong, reason = is_t1_continuation_strong(trade, 114.40, 115.0)
    assert strong is True
    assert "Strong near-T1 orderbook & momentum continuation confirmed" in reason


def test_v5_post_buy_live_support_trail_and_structure_hold_exemption(monkeypatch):
    from datetime import datetime, timedelta
    import trade_state
    from trade_state import update_trailing_stop, check_time_based_exit

    monkeypatch.setattr(trade_state, "_replace_state_trade", lambda t: None)
    fixed_now = datetime(2026, 10, 12, 11, 30, 0)
    monkeypatch.setattr(trade_state, "market_now_naive", lambda: fixed_now)

    # 1. Trade with live_chart_trend_intact=True and target_1=115.0 at +8% profit (Rs 108.0):
    # Should NOT prematurely choke at Entry + 1.5% (101.50), but SHOULD trail behind confirmed option swing low (96.0 -> 95.05)
    trade = {
        "opened_at": (fixed_now - timedelta(minutes=24)).strftime("%Y-%m-%d %H:%M:%S"),
        "entry_price": 100.0,
        "stop_loss": 90.0,
        "target_1": 115.0,
        "target_stage": 0,
        "live_chart_trend_intact": True,
        "live_option_swing_low": 96.0,
    }
    updated, reason = update_trailing_stop(trade, 108.0)
    assert updated["stop_loss"] == 95.05
    assert "Option 5m swing support trail" in reason

    # 2. Held for 24 minutes (> 20m limit) with +2.0% profit, but live_chart_trend_intact is True:
    # Must NOT force time-based exit while chart structure supports the trade!
    due, _ = check_time_based_exit(updated, 102.0)
    assert due is False

    # 3. Once live_chart_trend_intact turns False, 20m time exit activates normally
    updated["live_chart_trend_intact"] = False
    due_broken, reason_broken = check_time_based_exit(updated, 102.0)
    assert due_broken is True
    assert "insufficient momentum" in reason_broken


def test_v5_post_profit_wave_and_cross_index_sl_cooldowns(monkeypatch, tmp_path):
    from datetime import datetime, timezone, timedelta
    import risk_manager
    from risk_manager import check_same_underlying_sl_cooldown
    from market_time import today_date

    fake_csv = tmp_path / "trade_history.csv"
    now_ist = datetime.now(timezone(timedelta(hours=5, minutes=30)))
    recent_win_exit = (now_ist - timedelta(minutes=2)).isoformat()

    fake_csv.write_text(
        "date,event,underlying,option_type,entry_price,exit_price,pnl,reason,closed_at\n"
        f"{today_date().isoformat()},EXIT,NIFTY,PE,100.0,115.0,975.0,TARGET_1_HIT,{recent_win_exit}\n"
    )
    monkeypatch.setattr(risk_manager, "HISTORY_FILE", fake_csv)

    # Trying to jump into SENSEX PE 2 minutes after booking a NIFTY PE wave target -> BLOCKED by wave exhaustion guard
    allowed_wave, reason_wave = check_same_underlying_sl_cooldown({"underlying": "SENSEX", "option_type": "PE"})
    assert allowed_wave is False
    assert "Post-profit wave exhaustion guard" in reason_wave

    # Cross-index SL 2 minutes ago on NIFTY PE -> blocks immediate SENSEX PE whipsaw
    fake_csv.write_text(
        "date,event,underlying,option_type,entry_price,exit_price,pnl,reason,closed_at\n"
        f"{today_date().isoformat()},EXIT,NIFTY,PE,100.0,88.0,-780.0,STOP_LOSS_HIT,{recent_win_exit}\n"
    )
    allowed_cross, reason_cross = check_same_underlying_sl_cooldown({"underlying": "SENSEX", "option_type": "PE"})
    assert allowed_cross is False
    assert "Cross-index SL cooldown" in reason_cross


def test_brokerage_and_taxes_deducted_even_on_stop_loss_hit(monkeypatch):
    import trade_state
    from trade_state import close_open_trade

    monkeypatch.setattr(trade_state, "_replace_state_trade", lambda t: None)
    monkeypatch.setattr(trade_state, "append_trade_history", lambda *a, **kw: None)
    monkeypatch.setattr(trade_state, "refresh_paper_account", lambda: {})

    # Trade like today's SL: entry 332.90, SL 302.74, qty 130, estimated_risk = 3920.80, entry charges = 71.93
    trade = {
        "option_symbol": "NIFTY26O1323100PE",
        "mode": "PAPER",
        "status": "OPEN",
        "entry_price": 332.90,
        "stop_loss": 302.74,
        "quantity": 130,
        "remaining_quantity": 130,
        "estimated_risk_amount": 3920.80,
        "paper_entry_charges": 71.93,
    }
    closed = close_open_trade(302.74, "STOP_LOSS_HIT", trade)
    assert closed["status"] == "CLOSED"
    assert closed["gross_pnl"] == -3920.80
    assert closed["paper_total_charges"] > 135.0
    # Net P&L must be gross_pnl minus total charges (never overwritten back to -3920.80!)
    assert closed["pnl"] == round(closed["gross_pnl"] - closed["paper_total_charges"], 2)
    assert closed["pnl"] < -4050.0


def test_profit_based_win_rate_reflects_net_profit_and_loss():
    from dashboard import calculate_performance

    # 3 small wins (+Rs 200 each = +Rs 600) and 2 larger losses (-Rs 500 each = -Rs 1000) -> Net Loss -Rs 400
    rows = [
        {"event": "EXIT", "pnl": "200.0", "gross_pnl": "250.0", "paper_total_charges": "50.0"},
        {"event": "EXIT", "pnl": "200.0", "gross_pnl": "250.0", "paper_total_charges": "50.0"},
        {"event": "EXIT", "pnl": "200.0", "gross_pnl": "250.0", "paper_total_charges": "50.0"},
        {"event": "EXIT", "pnl": "-500.0", "gross_pnl": "-450.0", "paper_total_charges": "50.0"},
        {"event": "EXIT", "pnl": "-500.0", "gross_pnl": "-450.0", "paper_total_charges": "50.0"},
    ]
    perf = calculate_performance(rows)
    # Trade count win rate is 60.0%, but since net P&L is negative (-Rs 400), Profit-Based Win Rate is 600 / (600 + 1000) = 37.5%!
    assert perf["total_pnl"] == -400.0
    assert perf["total_charges"] == 250.0
    assert perf["trade_win_rate"] == 60.0
    assert perf["win_rate"] == 37.5


