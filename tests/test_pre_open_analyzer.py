import unittest

from pre_open_analyzer import _bias_from_gap, _overall_bias


class PreOpenAnalyzerTests(unittest.TestCase):
    def test_bias_from_gap_uses_configured_thresholds(self):
        self.assertEqual(_bias_from_gap(0.55), "STRONG_BULLISH")
        self.assertEqual(_bias_from_gap(0.25), "BULLISH")
        self.assertEqual(_bias_from_gap(0.05), "FLAT")
        self.assertEqual(_bias_from_gap(-0.25), "BEARISH")
        self.assertEqual(_bias_from_gap(-0.55), "STRONG_BEARISH")

    def test_overall_bias_detects_mixed_and_flat(self):
        self.assertEqual(_overall_bias([{"bias": "BULLISH"}, {"bias": "BEARISH"}]), "MIXED")
        self.assertEqual(_overall_bias([{"bias": "FLAT"}, {"bias": "FLAT"}]), "FLAT")
        self.assertEqual(_overall_bias([{"bias": "BULLISH"}, {"bias": "BULLISH"}]), "BULLISH")


if __name__ == "__main__":
    unittest.main()
