import unittest

import config
from trade_state import dynamic_partial_exit_quantity, update_dynamic_exit_memory


class DynamicExitTests(unittest.TestCase):
    def test_dynamic_partial_exit_keeps_one_lot_running(self):
        trade = {
            "lot_size": 20,
            "quantity": 60,
            "remaining_quantity": 60,
        }

        self.assertEqual(dynamic_partial_exit_quantity(trade), 20)

    def test_dynamic_partial_exit_skips_single_lot(self):
        trade = {
            "lot_size": 65,
            "quantity": 65,
            "remaining_quantity": 65,
        }

        self.assertEqual(dynamic_partial_exit_quantity(trade), 0)

    def test_dynamic_memory_tracks_peak_and_pullback(self):
        trade = {
            "entry_price": 100,
            "last_monitor_price": 115,
            "highest_price_seen": 120,
            "bid_qty": 10,
            "ask_qty": 100,
        }

        updated, metrics = update_dynamic_exit_memory(trade, 112)

        self.assertEqual(updated["highest_price_seen"], 120)
        self.assertEqual(metrics["profit_percent"], 12.0)
        self.assertEqual(metrics["peak_profit_percent"], 20.0)
        self.assertEqual(metrics["pullback_from_peak"], 8.0)
        self.assertTrue(metrics["price_fading"])
        self.assertTrue(metrics["weak_depth"])


if __name__ == "__main__":
    unittest.main()
