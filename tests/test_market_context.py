import unittest

import config
from decision_engine import build_final_decision
from free_market_data import quote_change_percent as free_quote_change_percent
from market_context import (
    _currency_crude_component,
    _gift_nifty_component,
    _global_markets_component,
    _quote_change_percent,
    _risk_from_score,
    _singapore_market_component,
)


class MarketContextTests(unittest.TestCase):
    def setUp(self):
        self._block_high_risk = config.MARKET_CONTEXT_BLOCK_HIGH_RISK
        self._block_opposite_bias = config.MARKET_CONTEXT_BLOCK_OPPOSITE_BIAS
        self._reduce_on_mixed = config.MARKET_CONTEXT_REDUCE_SIZE_ON_MIXED
        self._single_index_size = config.SINGLE_INDEX_TRADE_SIZE_PERCENT
        self._vix_block_on_iv_crush = getattr(config, "VIX_BLOCK_ON_IV_CRUSH", False)

    def tearDown(self):
        config.MARKET_CONTEXT_BLOCK_HIGH_RISK = self._block_high_risk
        config.MARKET_CONTEXT_BLOCK_OPPOSITE_BIAS = self._block_opposite_bias
        config.MARKET_CONTEXT_REDUCE_SIZE_ON_MIXED = self._reduce_on_mixed
        config.SINGLE_INDEX_TRADE_SIZE_PERCENT = self._single_index_size
        config.VIX_BLOCK_ON_IV_CRUSH = self._vix_block_on_iv_crush

    def test_quote_change_percent_uses_previous_close(self):
        quote = {"last_price": 105, "ohlc": {"close": 100}}

        self.assertEqual(_quote_change_percent(quote), 5.0)

    def test_free_quote_change_percent_uses_direct_change(self):
        quote = {"price": 105, "previous_close": 100, "change_percent": 4.5}

        self.assertEqual(free_quote_change_percent(quote), 4.5)

    def test_high_risk_component_marks_context_high_risk(self):
        components = [{"bias": "HIGH_RISK", "score": -15}]

        self.assertEqual(_risk_from_score(-15, components), "HIGH")

    def test_global_market_component_scores_bullish(self):
        quotes = {
            "^DJI": _free_quote(0.35),
            "^IXIC": _free_quote(0.42),
            "^GSPC": _free_quote(0.31),
        }

        component = _global_markets_component(quotes)

        self.assertEqual(component["status"], "OK")
        self.assertEqual(component["bias"], "BULLISH")
        self.assertGreater(component["score"], 0)

    def test_singapore_market_component_scores_bearish(self):
        component = _singapore_market_component({"^STI": _free_quote(-0.35)})

        self.assertEqual(component["status"], "OK")
        self.assertEqual(component["bias"], "BEARISH")

    def test_gift_nifty_component_uses_nseix_quote(self):
        component = _gift_nifty_component({}, {
            "symbol": "NSEIX:NIFTY_FUT",
            "price": 23000,
            "change_percent": 0.42,
            "source": "test",
        })

        self.assertEqual(component["status"], "OK")
        self.assertEqual(component["bias"], "BULLISH")
        self.assertGreater(component["score"], 0)

    def test_currency_crude_component_treats_crude_up_as_bearish(self):
        quotes = {
            "INR=X": _free_quote(0.25),
            "DX-Y.NYB": _free_quote(0.30),
            "CL=F": _free_quote(1.2),
        }

        component = _currency_crude_component(quotes)

        self.assertEqual(component["status"], "OK")
        self.assertEqual(component["bias"], "BEARISH")
        self.assertLess(component["score"], 0)

    def test_market_context_high_risk_blocks_trade(self):
        config.MARKET_CONTEXT_BLOCK_HIGH_RISK = True
        comparison = _comparison("BUY")
        context = {
            "bias": "MIXED",
            "risk_level": "HIGH",
            "score": -20,
            "reason": "VIX high.",
        }

        result = build_final_decision(comparison, _news(), context)

        self.assertTrue(result["hold"])
        self.assertEqual(result["signal"]["SIGNAL"], "HOLD")
        self.assertEqual(result["signal"]["MARKET_CONTEXT_MODE"], "BLOCK")
        self.assertEqual(result["signal"]["MARKET_CONTEXT_BLOCKED_ORIGINAL_SIGNAL"], "BUY")

    def test_opposite_context_reduces_single_index_size(self):
        config.MARKET_CONTEXT_BLOCK_HIGH_RISK = False
        config.MARKET_CONTEXT_BLOCK_OPPOSITE_BIAS = False
        config.MARKET_CONTEXT_REDUCE_SIZE_ON_MIXED = True
        config.SINGLE_INDEX_TRADE_SIZE_PERCENT = 60
        comparison = _comparison("BUY", single_index_trade=True)
        context = {
            "bias": "BEARISH",
            "risk_level": "LOW",
            "score": -25,
            "reason": "Breadth weak.",
        }

        result = build_final_decision(comparison, _news(), context)

        self.assertFalse(result["hold"])
        self.assertEqual(result["signal"]["SIGNAL"], "BUY")
        self.assertTrue(result["signal"]["MARKET_CONTEXT_CONFLICT"])
        self.assertEqual(result["signal"]["MARKET_CONTEXT_MODE"], "REDUCE_SIZE")
        self.assertEqual(result["signal"]["SIZE_PERCENT"], config.MARKET_CONTEXT_REDUCED_SIZE_PERCENT)

    def test_vix_cooling_does_not_block_buy_when_vix_crush_block_disabled(self):
        config.MARKET_CONTEXT_BLOCK_HIGH_RISK = False
        config.VIX_BLOCK_ON_IV_CRUSH = False
        comparison = _comparison("BUY")
        context = {
            "bias": "BULLISH",
            "risk_level": "LOW",
            "score": 10,
            "reason": "VIX cooled -4.2%, fear easing.",
            "iv_crush": False,
            "vix_change": -4.2,
        }

        result = build_final_decision(comparison, _news(), context)

        self.assertFalse(result["hold"])
        self.assertEqual(result["signal"]["SIGNAL"], "BUY")

    def test_vix_crush_blocks_buy_when_enabled(self):
        config.VIX_BLOCK_ON_IV_CRUSH = True
        comparison = _comparison("BUY")
        context = {
            "bias": "BULLISH",
            "risk_level": "HIGH",
            "score": -15,
            "reason": "VIX crashed.",
            "iv_crush": True,
            "vix_change": -4.5,
        }

        result = build_final_decision(comparison, _news(), context)

        self.assertTrue(result["hold"])
        self.assertEqual(result["signal"]["SIGNAL"], "HOLD")
        self.assertEqual(result["signal"]["MARKET_CONTEXT_MODE"], "BLOCK")


def _comparison(signal: str, single_index_trade: bool = False) -> dict:
    strategy_signal = {
        "SIGNAL": signal,
        "CONFIDENCE": "HIGH",
        "REASON": "Test signal.",
        "STOP_LOSS": 90,
        "TARGET": 120,
    }
    return {
        "selected": {"symbol": "NIFTY"},
        "signal": strategy_signal,
        "market_data": {"symbol": "NIFTY"},
        "analyses": [{"strategy_signal": strategy_signal}],
        "single_index_trade": single_index_trade,
    }


def _news() -> dict:
    return {
        "sentiment": "NEUTRAL",
        "risk_level": "LOW",
        "reason": "No major news conflict.",
        "headlines": [],
    }


def _free_quote(change_percent: float) -> dict:
    return {
        "price": 100 + change_percent,
        "previous_close": 100,
        "change_percent": change_percent,
        "source": "test",
    }


if __name__ == "__main__":
    unittest.main()
