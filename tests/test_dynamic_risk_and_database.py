# test_dynamic_risk_and_database.py - Tests for percentage risk, cooldown, compounding paper fund, and DB

import pytest
import config
from position_sizer import calculate_trade_risk_limit, apply_position_sizing
from risk_manager import _dynamic_daily_loss_limit, check_same_underlying_sl_cooldown
from paper_account import build_paper_account
from database import db_get_pnl_summary, db_get_distinct_dates, db_get_trades_for_date


def test_calculate_trade_risk_limit_percentage():
    # Balance 10,000 with 3.5% risk limit -> 350
    config.MAX_RISK_PER_TRADE = 0
    config.MAX_RISK_PER_TRADE_PERCENT_OF_FUND = 3.5
    limit = calculate_trade_risk_limit(10000.0)
    assert limit == 350.0


def test_position_sizing_allows_one_lot_on_small_fund():
    config.MAX_RISK_PER_TRADE = 0
    config.MAX_RISK_PER_TRADE_PERCENT_OF_FUND = 3.5
    config.MAX_DAILY_LOSS_PERCENT_OF_FUND = 10
    config.MAX_FUND_USAGE_PERCENT = 40

    # Sensex option: entry 180, stop loss 160 (risk per lot = 20 * 20 = 400, premium = 3600)
    option_trade = {
        "underlying": "SENSEX",
        "option_symbol": "SENSEX74000CE",
        "option_type": "CE",
        "entry_price": 180.0,
        "stop_loss": 160.0,
        "quantity": 20,
        "lot_size": 20,
    }
    # Fund 10,000 covers 3600 premium. Risk is 400 (under 10% daily limit = 1000).
    sized = apply_position_sizing(option_trade, balance=10000.0)
    assert sized["lots"] >= 1
    assert sized.get("status") != "BLOCKED_RISK"


def test_dynamic_daily_loss_limit_percentage():
    config.MAX_DAILY_LOSS = 0
    config.MAX_DAILY_LOSS_PERCENT_OF_FUND = 10.0
    limit = _dynamic_daily_loss_limit(10000.0)
    assert limit == 1000.0

    limit_20k = _dynamic_daily_loss_limit(20000.0)
    assert limit_20k == 2000.0


def test_cooldown_setting_is_twenty_minutes():
    assert getattr(config, "SAME_UNDERLYING_SL_COOLDOWN_MINUTES", 0) == 20


def test_database_queries_work():
    # Database distinct dates should return a list
    dates = db_get_distinct_dates()
    assert isinstance(dates, list)
    if dates:
        test_date = dates[0]
        trades = db_get_trades_for_date(test_date)
        assert isinstance(trades, list)


def test_check_same_underlying_sl_cooldown_timezone_handling(monkeypatch, tmp_path):
    from datetime import date, datetime, timezone, timedelta
    from risk_manager import check_same_underlying_sl_cooldown
    import risk_manager

    fake_csv = tmp_path / "trade_history.csv"
    now_ist = datetime.now(timezone(timedelta(hours=5, minutes=30)))
    recent_exit = (now_ist - timedelta(minutes=5)).isoformat()

    from market_time import today_date
    fake_csv.write_text(
        "date,event,underlying,option_type,pnl,reason,closed_at\n"
        f"{today_date().isoformat()},EXIT,NIFTY,CE,-100.0,STOP_LOSS_HIT,{recent_exit}\n"
    )

    monkeypatch.setattr(risk_manager, "HISTORY_FILE", fake_csv)
    monkeypatch.setattr(config, "SAME_UNDERLYING_SL_COOLDOWN_MINUTES", 20)

    option_trade = {"underlying": "NIFTY", "option_type": "CE"}
    allowed, reason = check_same_underlying_sl_cooldown(option_trade)
    assert allowed is False
    assert "re-entry blocked after negative SL" in reason


def test_paper_account_compounding():
    config.PAPER_BALANCE_OVERRIDE = 10000.0
    account = build_paper_account()
    assert account["starting_balance"] == 10000.0
    assert "available_balance" in account
    assert account["available_balance"] == round(10000.0 + account["realized_pnl"], 2)


def test_dynamic_daily_profit_target():
    from risk_manager import _dynamic_daily_profit_target
    config.MAX_DAILY_PROFIT = 0
    config.MAX_DAILY_PROFIT_PERCENT_OF_FUND = 10.0
    target_10k = _dynamic_daily_profit_target(10000.0)
    assert target_10k == 1000.0
    target_50k = _dynamic_daily_profit_target(50000.0)
    assert target_50k == 5000.0


def test_option_vwap_filter_rejection():
    from option_chain_analyzer import _hard_reject_reasons
    # Case 1: LTP is below VWAP
    quote = {"last_price": 100.0, "volume": 50000, "oi": 50000}
    depth = {"spread_percent": 0.5, "top_depth_sufficient": True, "buy_lots": 10, "sell_lots": 10}
    premium = {"vwap": 110.0, "day_high": 150.0}
    entry = {"entry_price": 100.0}
    stop_plan = {"stop_loss_percent": 10.0}
    targets = {"target": 130.0, "potential_left_percent": 25.0, "risk_reward": 2.0}
    strike_info = {"far_otm": False}

    reasons = _hard_reject_reasons(quote, depth, premium, entry, stop_plan, targets, strike_info)
    assert any("below its own VWAP" in r for r in reasons)

    # Case 2: LTP is at or above VWAP
    quote["last_price"] = 115.0
    entry["entry_price"] = 115.0
    reasons_ok = _hard_reject_reasons(quote, depth, premium, entry, stop_plan, targets, strike_info)
    assert not any("below its own VWAP" in r for r in reasons_ok)


def test_vix_iv_crush_block():
    import config
    from decision_engine import _market_context_block_reason
    old_setting = getattr(config, "VIX_BLOCK_ON_IV_CRUSH", False)
    try:
        config.VIX_BLOCK_ON_IV_CRUSH = True
        signal = {"SIGNAL": "BUY", "CONFIDENCE": 85}
        context_crush = {"bias": "BULLISH", "score": 20, "iv_crush": True, "vix_change": -4.2}
        block_reason = _market_context_block_reason(signal, context_crush)
        assert block_reason is not None
        assert "IV crush decay" in block_reason

        config.VIX_BLOCK_ON_IV_CRUSH = False
        assert _market_context_block_reason(signal, context_crush) is None
    finally:
        config.VIX_BLOCK_ON_IV_CRUSH = old_setting


def test_market_now_naive_returns_ist():
    from zoneinfo import ZoneInfo
    from datetime import datetime
    from market_time import market_now_naive

    naive_now = market_now_naive()
    assert naive_now.tzinfo is None

    # Must match the current hour and minute in Asia/Kolkata
    ist_now = datetime.now(ZoneInfo("Asia/Kolkata"))
    assert naive_now.hour == ist_now.hour
    assert naive_now.minute == ist_now.minute


def test_option_stop_loss_plan_realistic_percent():
    from option_chain_analyzer import _stop_loss_plan, _targets_from_risk

    # 1. Cheap Tier (< Rs 100, e.g. Rs 30)
    plan_30 = _stop_loss_plan([], 30.0)
    assert plan_30["stop_loss"] == 27.0  # 10% default
    assert plan_30["stop_loss_percent"] == 10.0

    # If candidate swing low is Rs 29 (too tight, 3.3% risk < 8%), clamp to default 10%
    candles_tight = [{"low": 29.0}]
    plan_tight = _stop_loss_plan(candles_tight, 30.0)
    assert plan_tight["stop_loss"] == 27.0

    # If candidate swing low is Rs 27.3 (9.0% risk, valid [8%, 15%]), use candidate
    candles_valid = [{"low": 27.3}]
    plan_valid = _stop_loss_plan(candles_valid, 30.0)
    assert plan_valid["stop_loss"] == 27.3

    # Dynamic Targets from Rs 30 entry and Rs 27 SL (risk = 3)
    targets = _targets_from_risk(30.0, 27.0, 20)
    assert targets["target_1"] == 33.75  # 30 + 1.25 * 3
    assert targets["target_2"] == 36.0   # 30 + 2.0 * 3
    assert targets["target_3"] == 39.0   # 30 + 3.0 * 3

    # 2. Mid Tier (Rs 100 - Rs 220, e.g. Rs 150)
    plan_150 = _stop_loss_plan([], 150.0)
    assert plan_150["stop_loss"] == 136.5  # 9% default
    assert plan_150["stop_loss_percent"] == 9.0

    # 3. High Tier (> Rs 220, e.g. Rs 300)
    plan_300 = _stop_loss_plan([], 300.0)
    assert plan_300["stop_loss"] == 279.0  # 7% default
    assert plan_300["stop_loss_percent"] == 7.0



def test_option_entry_plan_uses_live_ltp():
    from option_chain_analyzer import _entry_plan

    # Closed candle was at 50.85, but live LTP is 133.10
    premium = {"latest_close": 50.85, "last_3_high": 52.0, "breakout_confirmed": False, "retest_confirmed": False}
    entry = _entry_plan([], 133.10, premium)
    # Entry price must use live LTP so fill does not mismatch
    assert entry["entry_price"] == 133.10


def test_today_date_returns_ist_date():
    from market_time import today_date
    from datetime import datetime
    from zoneinfo import ZoneInfo
    ist_date = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    assert today_date() == ist_date


def test_position_sizing_multi_lot_on_100k_capital():
    config.MAX_RISK_PER_TRADE = 0
    config.MAX_RISK_PER_TRADE_PERCENT_OF_FUND = 5.0
    config.MAX_FUND_USAGE_PERCENT = 40

    option_trade = {
        "underlying": "SENSEX",
        "option_symbol": "SENSEX75000CE",
        "option_type": "CE",
        "entry_price": 350.0,
        "stop_loss": 280.0,
        "quantity": 20,
        "lot_size": 20,
    }
    sized = apply_position_sizing(option_trade, balance=100000.0)
    assert sized["lots"] == 3
    assert sized["base_lots"] == 3
    assert sized["quantity"] == 60


def test_reduce_trade_size_non_cascading():
    from main import reduce_trade_size

    option_trade = {
        "entry_price": 200.0,
        "stop_loss": 160.0,
        "lot_size": 20,
        "lots": 3,
        "base_lots": 3,
        "quantity": 60,
    }
    # First reduction 50%
    first = reduce_trade_size(option_trade, 50.0, "Regime")
    assert first["lots"] == 2
    assert first["effective_size_percent"] == 50.0

    # Second reduction 50% should NOT compound 2 * 0.5 = 1 lot; it should evaluate 50% against base_lots=3 -> 2 lots
    second = reduce_trade_size(first, 50.0, "Time Window")
    assert second["lots"] == 2
    assert second["effective_size_percent"] == 50.0


def test_target_stage_one_moves_sl_to_entry_plus_two_percent():
    from trade_state import update_target_stage_stop

    trade = {
        "entry_price": 100.0,
        "stop_loss": 80.0,
        "target_1": 120.0,
        "target_2": 135.0,
        "target_stage": 0,
    }
    updated_trade, reason = update_target_stage_stop(trade, 122.0)
    assert updated_trade["target_stage"] == 1
    # Adaptive shadow trail: 35% of (135 - 120) = 5.25 -> peak 122.0 - 5.25 = 116.75 (above 65% parity floor 113.0)
    assert updated_trade["stop_loss"] == 116.75
    assert updated_trade["stop_loss"] >= 113.0  # Guaranteed >= 65% T1 parity floor


def test_position_sizing_tolerance_buffer_allows_two_lots():
    config.MAX_RISK_PER_TRADE = 0
    config.MAX_RISK_PER_TRADE_PERCENT_OF_FUND = 5.0
    config.MAX_RISK_PER_TRADE_BUFFER_PERCENT = 10
    config.MAX_FUND_USAGE_PERCENT = 40

    # Trade like today: balance 97,509, 2-lot risk is 4933.50, slightly above base limit 4875.48
    option_trade = {
        "underlying": "NIFTY",
        "option_symbol": "NIFTY22850PE",
        "option_type": "PE",
        "entry_price": 181.40,
        "stop_loss": 143.45,  # 37.95 risk per unit * 65 = 2466.75 per lot
        "quantity": 65,
        "lot_size": 65,
    }
    sized = apply_position_sizing(option_trade, balance=97509.0)
    assert sized["lots"] == 2
    assert sized["quantity"] == 130


def test_position_sizing_cheap_option_scales_beyond_three_lots():
    config.MAX_RISK_PER_TRADE = 0
    config.MAX_RISK_PER_TRADE_PERCENT_OF_FUND = 5.0
    config.MAX_RISK_PER_TRADE_BUFFER_PERCENT = 10
    config.MAX_FUND_USAGE_PERCENT = 40

    # Cheap option at Rs 50 with Rs 10 risk per unit -> risk per lot = 650
    option_trade = {
        "underlying": "NIFTY",
        "option_symbol": "NIFTY22850PE",
        "option_type": "PE",
        "entry_price": 50.0,
        "stop_loss": 40.0,
        "quantity": 65,
        "lot_size": 65,
    }
    sized = apply_position_sizing(option_trade, balance=100000.0)
    # Risk hard limit = 5,500 / 650 = 8 lots. Capital limit = 40,000 / 3,250 = 12 lots. Min is 8 lots.
    assert sized["lots"] == 8
    assert sized["quantity"] == 520


def test_single_lot_exits_at_target_one(monkeypatch):
    import trade_state

    # Mock broker with weak continuation (or near-T1 stalling) so 1-lot books safely at T1
    class MockFyers:
        def quote(self, keys):
            return {keys[0]: {"last_price": 119.0, "depth": {"buy": [], "sell": []}}}

    trade = {
        "option_symbol": "NIFTY22850PE",
        "exchange": "NFO",
        "mode": "PAPER",
        "status": "OPEN",
        "order_status": "OPEN",
        "entry_price": 100.0,
        "stop_loss": 80.0,
        "target_1": 120.0,
        "target_2": 135.0,
        "target_3": 150.0,
        "target": 150.0,
        "lot_size": 65,
        "quantity": 65,  # Exactly 1 lot
        "remaining_quantity": 65,
        "target_stage": 0,
        "dynamic_exit_enabled": False,
    }

    monkeypatch.setattr(trade_state, "_replace_state_trade", lambda t: None)
    monkeypatch.setattr(trade_state, "append_trade_history", lambda *args, **kwargs: None)
    monkeypatch.setattr(trade_state, "refresh_paper_account", lambda: None)
    monkeypatch.setattr(trade_state, "print_performance_summary", lambda: None)

    # When price is in 94% near-T1 buffer (119.0 < 120.0), 1-lot trade closes at TARGET_1_HIT
    closed = trade_state.monitor_open_trade(MockFyers(), trade)
    assert closed["status"] == "CLOSED"
    assert closed["exit_reason"] == "TARGET_1_HIT"


def test_dynamic_regime_sizing_trending_scales_to_three_lots():
    trade = {
        "underlying": "NIFTY",
        "option_symbol": "NIFTY22850PE",
        "entry_price": 180.0,
        "stop_loss": 144.0,  # 36 pt risk * 65 = 2,340 risk per lot
        "quantity": 65,
        "lot_size": 65,
        "market_regime": "TRENDING",
        "market_regime_action": "ALLOW_BREAKOUT_CONTINUATION",
        "setup_score": 85.0,
        "adx_14": 28.0,
    }
    sized = apply_position_sizing(trade, balance=100000.0)
    assert sized["regime_sizing_mode"] == "TRENDING_AGGRESSIVE"
    assert sized["fund_usage_percent"] == 70.0
    # On 100k capital: 7.5% risk = 7,500 + 10% buffer = 8,250 hard limit.
    # 8,250 // 2,340 = 3 lots.
    assert sized["lots"] == 3
    assert sized["quantity"] == 195


def test_dynamic_regime_sizing_sideways_defensive_restricts_to_one_lot():
    trade = {
        "underlying": "NIFTY",
        "option_symbol": "NIFTY22850PE",
        "entry_price": 180.0,
        "stop_loss": 144.0,  # 2,340 risk per lot
        "quantity": 65,
        "lot_size": 65,
        "market_regime": "SIDEWAYS",
        "market_regime_action": "SCALP_ONLY",
        "setup_score": 60.0,
        "adx_14": 15.0,
    }
    sized = apply_position_sizing(trade, balance=100000.0)
    assert sized["regime_sizing_mode"] == "SIDEWAYS_DEFENSIVE"
    assert sized["fund_usage_percent"] == 30.0
    # On 100k capital: 3.5% risk = 3,500 + 10% buffer = 3,850 hard limit.
    # 3,850 // 2,340 = 1 lot.
    assert sized["lots"] == 1
    assert sized["quantity"] == 65


def test_runner_trailing_stop_protects_peak_profits():
    from trade_state import update_target_stage_stop

    trade = {
        "entry_price": 100.0,
        "stop_loss": 80.0,
        "target_1": 120.0,
        "target_2": 135.0,
        "target_stage": 1,  # Target 1 was already hit
        "highest_price_seen": 122.0,
    }
    # Price explodes to 160 (+60% profit). Adaptive shadow trail:
    # max(35% of 15 = 5.25, 2.5% of 160 = 4.0) = 5.25 -> 160 - 5.25 = 154.75!
    updated_trade, reason = update_target_stage_stop(trade, 160.0)
    assert updated_trade["target_stage"] == 2
    assert updated_trade["stop_loss"] == 154.75
    assert updated_trade["highest_price_seen"] == 160.0


def test_is_target_hit_with_buffer_ratio():
    from trade_state import is_target_hit

    # Sensex CE today: Entry 580.40, Target 1 718.90. High reached 715.0!
    # Without buffer: 715.0 < 718.90 (False).
    # With 94% buffer: distance 138.5 * 0.94 = 130.19. Threshold = 710.59. 715.0 >= 710.59 (True!)
    assert is_target_hit(715.0, 718.90, 580.40) is True

    # Sensex PE today: Entry 409.20, Target 1 526.32. High reached 520.0!
    # With 94% buffer: distance 117.12 * 0.94 = 110.09. Threshold = 519.29. 520.0 >= 519.29 (True!)
    assert is_target_hit(520.0, 526.32, 409.20) is True

    # Price only 10% towards target should NOT trigger
    assert is_target_hit(600.0, 718.90, 580.40) is False


def test_opposite_direction_flip_flop_cooldown_blocks_immediate_reverse(monkeypatch, tmp_path):
    from datetime import datetime, timezone, timedelta
    from risk_manager import check_same_underlying_sl_cooldown
    import risk_manager

    fake_csv = tmp_path / "trade_history.csv"
    now_ist = datetime.now(timezone(timedelta(hours=5, minutes=30)))
    recent_exit = (now_ist - timedelta(seconds=26)).isoformat()  # Just 26 seconds ago!

    from market_time import today_date
    fake_csv.write_text(
        "date,event,underlying,option_type,pnl,reason,closed_at\n"
        f"{today_date().isoformat()},EXIT,NIFTY,CE,-4362.0,STOP_LOSS_HIT,{recent_exit}\n"
    )

    monkeypatch.setattr(risk_manager, "HISTORY_FILE", fake_csv)
    monkeypatch.setattr(config, "OPPOSITE_DIRECTION_FLIP_COOLDOWN_MINUTES", 10)

    # Bot trying to buy PE 26 seconds after CE loss:
    option_trade = {"underlying": "NIFTY", "option_type": "PE"}
    allowed, reason = check_same_underlying_sl_cooldown(option_trade)
    assert allowed is False
    assert "Flip-flop re-entry" in reason


def test_early_breakeven_lock_at_seven_point_five_percent():
    from trade_state import update_trailing_stop

    trade = {
        "entry_price": 100.0,
        "stop_loss": 90.0,  # 10% SL
    }
    # At +8% profit (Rs 108), early breakeven lock must move SL to Entry + 1.5% (Rs 101.50)
    updated, reason = update_trailing_stop(trade, 108.0)
    assert updated["stop_loss"] == 101.5
    assert "Early breakeven lock" in reason


def test_time_based_exit_after_twenty_minutes_without_momentum(monkeypatch):
    from datetime import datetime, timedelta
    from trade_state import check_time_based_exit
    import trade_state

    fixed_now = datetime(2026, 10, 7, 11, 25, 0)
    monkeypatch.setattr(trade_state, "market_now_naive", lambda: fixed_now)

    # Trade opened 22 minutes ago at 100.0, current price is 101.0 (+1% profit, < 4% min threshold)
    trade = {
        "opened_at": (fixed_now - timedelta(minutes=22)).strftime("%Y-%m-%d %H:%M:%S"),
        "entry_price": 100.0,
        "target_stage": 0,
        "partial_exit_t1_done": False,
    }
    due, reason = check_time_based_exit(trade, 101.0)
    assert due is True
    assert "Trade held for 22.0m" in reason
    assert "insufficient momentum" in reason


def test_time_based_exit_skips_when_momentum_or_target_hit(monkeypatch):
    from datetime import datetime, timedelta
    from trade_state import check_time_based_exit
    import trade_state

    fixed_now = datetime(2026, 10, 7, 11, 25, 0)
    monkeypatch.setattr(trade_state, "market_now_naive", lambda: fixed_now)

    # 1. Trade held 22 minutes but has good momentum (+6% profit >= 4% threshold)
    trade_with_profit = {
        "opened_at": (fixed_now - timedelta(minutes=22)).strftime("%Y-%m-%d %H:%M:%S"),
        "entry_price": 100.0,
        "target_stage": 0,
        "partial_exit_t1_done": False,
    }
    due_p, _ = check_time_based_exit(trade_with_profit, 106.0)
    assert due_p is False

    # 2. Trade held 22 minutes, sluggish, but Target 1 already hit (runner trailing)
    trade_with_t1 = {
        "opened_at": (fixed_now - timedelta(minutes=22)).strftime("%Y-%m-%d %H:%M:%S"),
        "entry_price": 100.0,
        "target_stage": 1,
        "partial_exit_t1_done": True,
    }
    due_t, _ = check_time_based_exit(trade_with_t1, 101.0)
    assert due_t is False


def test_dynamic_momentum_stall_exit_triggers_at_ten_minutes(monkeypatch):
    from datetime import datetime, timedelta
    from trade_state import check_time_based_exit
    import trade_state

    fixed_now = datetime(2026, 10, 7, 11, 25, 0)
    monkeypatch.setattr(trade_state, "market_now_naive", lambda: fixed_now)

    # Trade held 11 minutes: stagnant at +0.2%, highest was only +1.2% (< 2.5% threshold)
    trade = {
        "opened_at": (fixed_now - timedelta(minutes=11)).strftime("%Y-%m-%d %H:%M:%S"),
        "entry_price": 100.0,
        "highest_price_seen": 101.2,
        "target_stage": 0,
        "partial_exit_t1_done": False,
    }
    due, reason = check_time_based_exit(trade, 100.2)
    assert due is True
    assert "Momentum stall exit" in reason
    assert "Exiting before theta decay destroys capital" in reason


def test_active_breakout_surge_exemption_at_twenty_minutes(monkeypatch):
    from datetime import datetime, timedelta
    from trade_state import check_time_based_exit
    import trade_state

    fixed_now = datetime(2026, 10, 7, 11, 25, 0)
    monkeypatch.setattr(trade_state, "market_now_naive", lambda: fixed_now)

    # Trade held 21 minutes, profit +3.2% (< 4% threshold), but price is breaking out at highs (103.2 seen, current 103.0)
    trade = {
        "opened_at": (fixed_now - timedelta(minutes=21)).strftime("%Y-%m-%d %H:%M:%S"),
        "entry_price": 100.0,
        "highest_price_seen": 103.2,
        "target_stage": 0,
        "partial_exit_t1_done": False,
    }
    due, reason = check_time_based_exit(trade, 103.0)
    assert due is False


def test_dynamic_structural_flip_bypasses_cooldown(monkeypatch, tmp_path):
    from datetime import datetime, timezone, timedelta
    from risk_manager import check_same_underlying_sl_cooldown
    import risk_manager
    from market_time import today_date

    fake_csv = tmp_path / "trade_history.csv"
    now_ist = datetime.now(timezone(timedelta(hours=5, minutes=30)))
    recent_exit = (now_ist - timedelta(seconds=20)).isoformat()

    fake_csv.write_text(
        "date,event,underlying,option_type,pnl,reason,closed_at\n"
        f"{today_date().isoformat()},EXIT,NIFTY,CE,-4362.0,STOP_LOSS_HIT,{recent_exit}\n"
    )

    monkeypatch.setattr(risk_manager, "HISTORY_FILE", fake_csv)
    monkeypatch.setattr(config, "OPPOSITE_DIRECTION_FLIP_COOLDOWN_MINUTES", 10)
    monkeypatch.setattr(config, "DYNAMIC_FLIP_VALIDATION_ENABLED", True)

    option_trade = {"underlying": "NIFTY", "option_type": "PE", "confidence_score": 75.0}

    # Case 1: Weak signal (Quality 6, Mixed) -> BLOCKED
    weak_signal = {"TRADE_QUALITY_SCORE": 6.0, "SETUP_SCORE": 8.0, "INDEX_ALIGNMENT": "MIXED"}
    allowed_weak, reason_weak = check_same_underlying_sl_cooldown(option_trade, signal=weak_signal)
    assert allowed_weak is False
    assert "Flip-flop re-entry" in reason_weak

    # Case 2: Strong structural reversal (Quality 14, Bearish) -> BYPASSED
    strong_signal = {"TRADE_QUALITY_SCORE": 14.0, "SETUP_SCORE": 16.0, "INDEX_ALIGNMENT": "BEARISH"}
    allowed_strong, reason_strong = check_same_underlying_sl_cooldown(option_trade, signal=strong_signal)
    assert allowed_strong is True
    assert "Structural trend reversal confirmed" in reason_strong


def test_market_close_and_sl_force_market_order(monkeypatch):
    from trade_state import maybe_place_live_exit_order
    import trade_state

    recorded_orders = []

    def mock_place_order(broker, symbol, action, quantity, exchange=None, order_type="MARKET", price=None):
        recorded_orders.append({"symbol": symbol, "action": action, "order_type": order_type, "price": price})
        return "ORD123456"

    monkeypatch.setattr(trade_state, "place_order", mock_place_order)
    monkeypatch.setattr(config, "AUTO_EXIT_ON_TARGET_SL", True)
    monkeypatch.setattr(config, "EXIT_ORDER_TYPE", "LIMIT")
    monkeypatch.setattr(config, "FORCE_MARKET_ON_SQUARE_OFF", True)
    monkeypatch.setattr(config, "FORCE_MARKET_ON_SL", True)

    fake_broker = object()
    trade = {
        "status": "LIVE_OPEN",
        "mode": "LIVE_AUTO",
        "option_symbol": "NIFTY24OCT25000CE",
        "quantity": 50,
        "entry_price": 100.0,
        "best_bid": 95.0,
        "exchange": "NFO",
    }
    monkeypatch.setattr(config, "ENABLE_LIVE_AUTO", True)

    # 1. 15:20 Square-off MUST force MARKET order
    maybe_place_live_exit_order(fake_broker, trade, "MARKET_CLOSE_SQUARE_OFF", current_price=95.0)
    assert recorded_orders[-1]["order_type"] == "MARKET"

    # 2. Stop loss hit MUST force MARKET order
    maybe_place_live_exit_order(fake_broker, trade, "STOP_LOSS_HIT", current_price=90.0)
    assert recorded_orders[-1]["order_type"] == "MARKET"

    # 3. Target 1 Partial exit uses configured LIMIT order
    maybe_place_live_exit_order(fake_broker, trade, "TARGET_1_PARTIAL_EXIT", current_price=112.0)
    assert recorded_orders[-1]["order_type"] == "LIMIT"


def test_is_opening_settle_window(monkeypatch):
    from datetime import time
    import market_time

    # 09:18 -> Before opening settle window (candle 1 still forming)
    monkeypatch.setattr(market_time, "now_time", lambda: time(9, 18))
    assert market_time.is_before_no_new_trade_start() is True
    assert market_time.is_opening_settle_window() is False

    # 09:22 -> Inside opening settle window (09:20 to 09:30)
    monkeypatch.setattr(market_time, "now_time", lambda: time(9, 22))
    assert market_time.is_before_no_new_trade_start() is False
    assert market_time.is_opening_settle_window() is True

    # 09:35 -> Normal session (after 09:30)
    monkeypatch.setattr(market_time, "now_time", lambda: time(9, 35))
    assert market_time.is_before_no_new_trade_start() is False
    assert market_time.is_opening_settle_window() is False











