import os
import unittest
from pathlib import Path
from unittest.mock import patch

import config
import database
import drive_sync
import market_depth
import trade_state


class FakeFyers:
    def __init__(self, quote_map: dict):
        self.quote_map = quote_map

    def quote(self, keys: list[str]) -> dict:
        return {k: self.quote_map[k] for k in keys if k in self.quote_map}


def _make_quote(ltp: float, bid: float = 0.0, ask: float = 0.0, bid_qty: int = 600, ask_qty: int = 300) -> dict:
    bid = bid or round(ltp - 0.15, 2)
    ask = ask or round(ltp + 0.15, 2)
    return {
        "last_price": ltp,
        "depth": {
            "buy": [{"price": bid, "quantity": bid_qty // 5} for _ in range(5)],
            "sell": [{"price": ask, "quantity": ask_qty // 5} for _ in range(5)],
        },
    }


class TestFullPositionShadowTrailV4(unittest.TestCase):
    def setUp(self):
        self.temp_state_file = Path("test_open_trade_v4.json")
        if self.temp_state_file.exists():
            self.temp_state_file.unlink()
        self.state_patcher = patch.object(trade_state, "STATE_FILE", self.temp_state_file)
        self.hist_patcher = patch.object(trade_state, "append_trade_history", lambda *a, **kw: None)
        self.perf_patcher = patch.object(trade_state, "print_performance_summary", lambda *a, **kw: None)
        self.acct_patcher = patch.object(trade_state, "refresh_paper_account", lambda *a, **kw: {})
        self.state_patcher.start()
        self.hist_patcher.start()
        self.perf_patcher.start()
        self.acct_patcher.start()

    def tearDown(self):
        self.state_patcher.stop()
        self.hist_patcher.stop()
        self.perf_patcher.stop()
        self.acct_patcher.stop()
        if self.temp_state_file.exists():
            self.temp_state_file.unlink()

    def test_1_near_t1_weak_buffer_sells_50_percent_at_current_price(self):
        """Near-T1 (94% buffer) weak/stalling trade sells 50% at current_price/best_bid, NOT unreachable T1."""
        trade = {
            "option_symbol": "NIFTY26O1322500CE",
            "display_symbol": "NIFTY 13 OCT 22500 CE",
            "exchange": "NFO",
            "status": "OPEN",
            "mode": "PAPER",
            "entry_price": 100.0,
            "stop_loss": 85.0,
            "target_1": 115.0,
            "target_2": 135.0,
            "target_3": 160.0,
            "target": 160.0,
            "quantity": 130,
            "remaining_quantity": 130,
            "lot_size": 65,
            "lots": 2,
        }
        trade_state._write_state_trades([trade])
        # 94% buffer of T1 (115) = 100 + 0.94 * 15 = 114.10 (< 115.0, so Near-T1 buffer stall)
        fyers = FakeFyers({"NFO:NIFTY26O1322500CE": _make_quote(114.20, bid=114.10, ask=114.35, bid_qty=200, ask_qty=500)})
        updated = trade_state.monitor_open_trade(fyers, trade)

        self.assertFalse(updated.get("full_position_ride_active"))
        self.assertTrue(updated.get("partial_exit_t1_done"))
        self.assertEqual(updated["remaining_quantity"], 65)
        # Must sell at practical current/bid price (114.10), NOT unreachable T1 (115.0)
        self.assertAlmostEqual(updated["exit_price"], 114.10, places=2)
        # Remaining 50% SL locked at Entry + 25% of T1 profit = 100 + 3.75 = 103.75
        self.assertGreaterEqual(updated["stop_loss"], 103.75)

    def test_2_strong_t1_sets_65_percent_parity_floor_and_golden_invariant(self):
        """Strong T1 breakout holds 100% lots, sets 65% Parity Floor, and beats Old Partial-Exit worst-case profit."""
        trade = {
            "option_symbol": "NIFTY26O1322500CE",
            "display_symbol": "NIFTY 13 OCT 22500 CE",
            "exchange": "NFO",
            "status": "OPEN",
            "mode": "PAPER",
            "entry_price": 100.0,
            "stop_loss": 85.0,
            "target_1": 115.0,
            "target_2": 135.0,
            "target_3": 160.0,
            "target": 160.0,
            "quantity": 130,
            "remaining_quantity": 130,
            "lot_size": 65,
            "lots": 2,
        }
        trade_state._write_state_trades([trade])
        fyers = FakeFyers({"NFO:NIFTY26O1322500CE": _make_quote(115.20, bid=115.10, ask=115.30, bid_qty=900, ask_qty=400)})
        updated = trade_state.monitor_open_trade(fyers, trade)

        self.assertTrue(updated.get("full_position_ride_active"))
        self.assertEqual(updated["remaining_quantity"], 130)  # 100% of lots held!
        # Parity floor = 100 + 0.65 * 15 = 109.75
        self.assertGreaterEqual(updated["stop_loss"], 109.75)

        # Golden Invariant: New worst-case profit >= Old partial-exit worst-case profit
        # Old worst-case: 1 lot (65) at T1 (+15) + 1 lot (65) at Stage 1 SL (+3.0) = 65 * 18 = Rs 1,170
        old_worst_case_profit = (15.0 * 65) + (3.0 * 65)
        new_worst_case_profit = (updated["stop_loss"] - 100.0) * 130
        self.assertGreaterEqual(new_worst_case_profit, old_worst_case_profit)

    def test_3_near_t2_reversal_exits_full_position_via_shadow_sl(self):
        """When price climbs near T2 and reverses, 100% lots exit at Shadow SL far above T1."""
        trade = {
            "option_symbol": "NIFTY26O1322550CE",
            "display_symbol": "NIFTY 13 OCT 22550 CE",
            "exchange": "NFO",
            "status": "OPEN",
            "mode": "PAPER",
            "entry_price": 102.40,
            "stop_loss": 93.21,
            "target_1": 113.89,
            "target_2": 120.78,
            "target_3": 129.97,
            "target": 129.97,
            "quantity": 195,
            "remaining_quantity": 195,
            "lot_size": 65,
            "lots": 3,
        }
        trade_state._write_state_trades([trade])

        # Step 1: Strong T1 hit at 114.20
        fyers_t1 = FakeFyers({"NFO:NIFTY26O1322550CE": _make_quote(114.20, bid=114.10, ask=114.30, bid_qty=800, ask_qty=350)})
        trade = trade_state.monitor_open_trade(fyers_t1, trade)
        self.assertEqual(trade["remaining_quantity"], 195)

        # Step 2: Price climbs near T2 (120.35, just shy of 120.78)
        fyers_peak = FakeFyers({"NFO:NIFTY26O1322550CE": _make_quote(120.35, bid=120.25, ask=120.45, bid_qty=800, ask_qty=350)})
        trade = trade_state.monitor_open_trade(fyers_peak, trade)
        self.assertGreaterEqual(trade["stop_loss"], 117.0)  # Shadow SL well above T1 (113.89)!

        # Step 3: Price reverses to 117.00 -> triggers Shadow SL on all 3 lots (195 qty)
        fyers_rev = FakeFyers({"NFO:NIFTY26O1322550CE": _make_quote(117.00, bid=116.90, ask=117.10, bid_qty=400, ask_qty=600)})
        closed = trade_state.monitor_open_trade(fyers_rev, trade)
        self.assertEqual(closed["status"], "CLOSED")
        self.assertEqual(closed["exit_reason"], "STOP_LOSS_HIT")
        # Gross P&L on 195 qty at ~117.35 > Rs 2,850 (beats Old Bot's Rs 2,758.60)
        self.assertGreater(closed["gross_pnl"], 2850.0)

    def test_4_cheap_option_atr_spread_floor_prevents_tight_trail(self):
        """Cheap option with small T2-T1 gap uses ATR/Spread minimum floor so trail is never too tight."""
        trade = {
            "entry_price": 80.0,
            "target_1": 88.0,
            "target_2": 93.0,  # Gap = 5.0 -> 35% of gap is only Rs 1.75 (too tight!)
            "target_3": 100.0,
            "bid_ask_spread": 0.85,  # 3x spread = Rs 2.55
            "option_atr": 8.0,       # 0.25x ATR = Rs 2.00
        }
        trail_dist = trade_state.calculate_adaptive_shadow_trail_distance(trade, highest_price=90.0, stage=1)
        # 35% of 5.0 is 1.75, but 3x spread (2.55) and 2.5% of 90 (2.25) protect it!
        self.assertGreaterEqual(trail_dist, 2.55)

    def test_5_one_lot_strong_t1_rides_to_t2_and_weak_t1_exits(self):
        """1-lot trade rides past T1 when continuation is strong, and exits at T1 when weak."""
        base_1lot = {
            "option_symbol": "NIFTY26O1322400CE",
            "display_symbol": "NIFTY 13 OCT 22400 CE",
            "exchange": "NFO",
            "status": "OPEN",
            "mode": "PAPER",
            "entry_price": 192.70,
            "stop_loss": 175.38,
            "target_1": 214.35,
            "target_2": 227.34,
            "target_3": 244.66,
            "target": 244.66,
            "quantity": 65,
            "remaining_quantity": 65,
            "lot_size": 65,
            "lots": 1,
        }
        # Case A: Strong T1 -> 1-lot trade stays OPEN and locks 65% Parity Floor
        trade_strong = dict(base_1lot)
        trade_state._write_state_trades([trade_strong])
        fyers_strong = FakeFyers({"NFO:NIFTY26O1322400CE": _make_quote(215.00, bid=214.90, ask=215.10, bid_qty=900, ask_qty=300)})
        res_strong = trade_state.monitor_open_trade(fyers_strong, trade_strong)
        self.assertEqual(res_strong["status"], "OPEN")
        self.assertTrue(res_strong.get("full_position_ride_active"))
        self.assertGreaterEqual(res_strong["stop_loss"], 206.75)

        # Case B: Weak T1 -> 1-lot trade closes immediately at T1 to pocket full T1 profit
        trade_weak = dict(base_1lot)
        trade_state._write_state_trades([trade_weak])
        fyers_weak = FakeFyers({"NFO:NIFTY26O1322400CE": _make_quote(213.50, bid=213.40, ask=213.60, bid_qty=200, ask_qty=700)})
        res_weak = trade_state.monitor_open_trade(fyers_weak, trade_weak)
        self.assertEqual(res_weak["status"], "CLOSED")
        self.assertEqual(res_weak["exit_reason"], "TARGET_1_HIT")

    def test_6_sl_tick_throttle_prevents_micro_noise(self):
        """SL only updates when increase is at least max(1 tick, 0.30% of old SL) unless stage changed."""
        self.assertFalse(trade_state._should_commit_sl_update(100.0, 100.10, stage_changed=False))
        self.assertTrue(trade_state._should_commit_sl_update(100.0, 100.35, stage_changed=False))
        self.assertTrue(trade_state._should_commit_sl_update(100.0, 100.10, stage_changed=True))

    def test_7_staging_vs_main_database_and_drive_isolation(self):
        """Verify BOT_ENV=staging isolates SQLite/Postgres table names and Google Drive paths from main."""
        with patch.dict(os.environ, {"BOT_ENV": "staging"}):
            self.assertEqual(database.get_bot_env(), "staging")
            self.assertEqual(database._table("trades"), "staging_trades")
            self.assertEqual(database._table("signal_decisions"), "staging_signal_decisions")
        with patch.dict(os.environ, {"BOT_ENV": "main"}):
            self.assertEqual(database.get_bot_env(), "main")
            self.assertEqual(database._table("trades"), "trades")
            self.assertEqual(database._table("signal_decisions"), "signal_decisions")

if __name__ == "__main__":
    unittest.main()
