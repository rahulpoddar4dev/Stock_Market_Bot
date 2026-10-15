import unittest
from datetime import date

from tools.walk_forward_backtest import _add_months, _aggregate, _metrics


class WalkForwardBacktestTests(unittest.TestCase):
    def test_add_months_rolls_year_boundary(self):
        self.assertEqual(_add_months(date(2025, 11, 1), 3), date(2026, 2, 1))

    def test_metrics_count_positive_pnl_as_win(self):
        trades = [
            {"pnl_points": 10, "target_stage": 1, "result": "SUCCESS"},
            {"pnl_points": -5, "target_stage": 0, "result": "FAIL"},
            {"pnl_points": 2, "target_stage": 2, "result": "SUCCESS"},
        ]

        metrics = _metrics(trades)

        self.assertEqual(metrics["trades"], 3)
        self.assertEqual(metrics["wins"], 2)
        self.assertEqual(metrics["win_rate"], 66.67)
        self.assertEqual(metrics["profit_factor"], 2.4)
        self.assertEqual(metrics["t1_hits"], 2)
        self.assertEqual(metrics["t2_hits"], 1)

    def test_aggregate_uses_trade_weighted_win_rate(self):
        rows = [
            {"test_trades": 10, "test_win_rate": 60, "test_points": 100, "test_t1_hits": 4, "test_t2_hits": 2, "test_t3_hits": 1},
            {"test_trades": 30, "test_win_rate": 40, "test_points": -20, "test_t1_hits": 8, "test_t2_hits": 3, "test_t3_hits": 1},
        ]

        summary = _aggregate(rows)

        self.assertEqual(summary["test_trades"], 40)
        self.assertEqual(summary["weighted_test_win_rate"], 45.0)
        self.assertEqual(summary["test_points"], 80.0)


if __name__ == "__main__":
    unittest.main()
