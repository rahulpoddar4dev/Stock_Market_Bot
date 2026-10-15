import unittest

from candlestick_patterns import summarize_patterns


class CandlestickPatternTests(unittest.TestCase):
    def test_bullish_engulfing_is_detected(self):
        candles = [
            {"open": 105, "high": 106, "low": 99, "close": 100},
            {"open": 99, "high": 108, "low": 98, "close": 107},
        ]

        summary = summarize_patterns(candles)

        self.assertIn("Bullish Engulfing", summary["pattern_names"])
        self.assertEqual(summary["bias"], "BULLISH")
        self.assertGreater(summary["bullish_score"], summary["bearish_score"])

    def test_shooting_star_is_detected_after_up_move(self):
        candles = [
            {"open": 100, "high": 103, "low": 99, "close": 102},
            {"open": 102, "high": 106, "low": 101, "close": 105},
            {"open": 106, "high": 112, "low": 105.5, "close": 105.5},
        ]

        summary = summarize_patterns(candles)

        self.assertIn("Shooting Star", summary["pattern_names"])
        self.assertEqual(summary["bias"], "BEARISH")

    def test_three_black_crows_is_detected(self):
        candles = [
            {"open": 110, "high": 111, "low": 104, "close": 105},
            {"open": 105, "high": 106, "low": 99, "close": 100},
            {"open": 100, "high": 101, "low": 94, "close": 95},
        ]

        summary = summarize_patterns(candles)

        self.assertIn("Three Black Crows", summary["pattern_names"])
        self.assertEqual(summary["bias"], "BEARISH")


if __name__ == "__main__":
    unittest.main()
