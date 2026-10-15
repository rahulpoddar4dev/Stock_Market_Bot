import unittest

import config
from tools.backtest_from_csv import _close_adaptive_trade


class BacktestExitRuleTests(unittest.TestCase):
    def test_buy_t1_moves_stop_to_configured_profit_lock(self):
        trade = {
            "symbol": "NIFTY",
            "signal": "BUY",
            "entry_time": "2026-06-01T09:30:00+05:30",
            "entry": 100.0,
            "stop_loss": 88.0,
            "target": 130.0,
            "target_1": 112.0,
            "target_2": 121.0,
            "target_3": 130.0,
            "target_stage": 0,
            "partial_realized_points": 0,
            "remaining_fraction": 1.0,
            "adaptive_exit_profile": True,
        }

        closed = _close_adaptive_trade(trade, {
            "date": "2026-06-01T09:35:00+05:30",
            "high": 112.5,
            "low": 99.0,
        })

        self.assertIsNone(closed)
        self.assertEqual(trade["target_stage"], 1)
        self.assertEqual(trade["stop_loss"], round(100 * (1 + config.STAGE_1_STOP_PROFIT_PERCENT / 100), 2))
        self.assertGreater(trade["partial_realized_points"], 0)

    def test_buy_after_t1_stop_is_counted_as_win_when_pnl_positive(self):
        trade = {
            "symbol": "NIFTY",
            "signal": "BUY",
            "entry_time": "2026-06-01T09:30:00+05:30",
            "entry": 100.0,
            "stop_loss": 102.0,
            "target": 130.0,
            "target_1": 112.0,
            "target_2": 121.0,
            "target_3": 130.0,
            "target_stage": 1,
            "partial_realized_points": 4.8,
            "remaining_fraction": 0.6,
            "adaptive_exit_profile": True,
        }

        closed = _close_adaptive_trade(trade, {
            "date": "2026-06-01T09:40:00+05:30",
            "high": 105.0,
            "low": 101.5,
        })

        self.assertIsNotNone(closed)
        self.assertEqual(closed["result"], "SUCCESS")
        self.assertGreater(closed["pnl_points"], 0)

    def test_sell_t1_moves_stop_to_profit_lock(self):
        trade = {
            "symbol": "SENSEX",
            "signal": "SELL",
            "entry_time": "2026-06-01T09:30:00+05:30",
            "entry": 100.0,
            "stop_loss": 112.0,
            "target": 70.0,
            "target_1": 88.0,
            "target_2": 79.0,
            "target_3": 70.0,
            "target_stage": 0,
            "partial_realized_points": 0,
            "remaining_fraction": 1.0,
            "adaptive_exit_profile": True,
        }

        closed = _close_adaptive_trade(trade, {
            "date": "2026-06-01T09:35:00+05:30",
            "high": 101.0,
            "low": 87.5,
        })

        self.assertIsNone(closed)
        self.assertEqual(trade["target_stage"], 1)
        self.assertEqual(trade["stop_loss"], 97.0)


if __name__ == "__main__":
    unittest.main()
