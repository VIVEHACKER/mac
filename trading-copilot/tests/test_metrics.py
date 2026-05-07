import math
import unittest

from trading_copilot.metrics import (
    beta_and_correlation,
    build_technical_profile,
    log_returns,
    max_drawdown,
    momentum_return,
    realized_volatility,
)


class MetricsTests(unittest.TestCase):
    def test_log_returns_skips_invalid_prices(self):
        closes = (100.0, 110.0, 0.0, 121.0)
        result = log_returns(closes)
        # 100->110 is valid, 110->0 invalid, 0->121 invalid (prev<=0)
        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(result[0], math.log(110 / 100), places=6)

    def test_realized_volatility_is_zero_for_flat_returns(self):
        self.assertEqual(realized_volatility((0.0, 0.0, 0.0, 0.0)), 0.0)

    def test_realized_volatility_annualizes_correctly(self):
        # daily std of 0.01 -> annualized sqrt(252)*0.01
        returns = (0.01, -0.01) * 60
        vol = realized_volatility(returns)
        self.assertGreater(vol, 0.10)
        self.assertLess(vol, 0.20)

    def test_momentum_return_uses_lookback_and_skip(self):
        closes = tuple(100.0 * (1.01 ** i) for i in range(260))
        # 12-1m: lookback 231d back, skip 21d most recent
        m = momentum_return(closes, lookback_days=231, skip_days=21)
        self.assertGreater(m, 0)
        end = closes[-22]  # index = len-1-21
        start = closes[-22 - 231]
        self.assertAlmostEqual(m, (end - start) / start, places=6)

    def test_max_drawdown_picks_worst_peak_to_trough(self):
        # 100 -> 150 -> 90 -> 120: worst = (90-150)/150 = -0.4
        closes = (100.0, 110.0, 120.0, 150.0, 130.0, 90.0, 100.0, 120.0)
        mdd = max_drawdown(closes)
        self.assertAlmostEqual(mdd, (90 - 150) / 150, places=6)

    def test_beta_of_identical_series_is_one(self):
        returns = tuple(0.001 * i for i in range(100))
        beta, corr = beta_and_correlation(returns, returns)
        self.assertAlmostEqual(beta, 1.0, places=6)
        self.assertAlmostEqual(corr, 1.0, places=6)

    def test_build_technical_profile_summary_fields(self):
        # Alternating returns -- non-zero variance so beta is well-defined.
        closes = []
        price = 100.0
        for i in range(260):
            ret = 0.012 if i % 2 == 0 else -0.005  # net positive drift, stochastic
            price *= 1.0 + ret
            closes.append(price)
        bench = []
        bp = 100.0
        for i in range(260):
            bret = 0.004 if i % 2 == 0 else -0.002
            bp *= 1.0 + bret
            bench.append(bp)
        profile = build_technical_profile(tuple(closes), tuple(bench))
        self.assertGreater(profile.momentum_12_1, 0)
        self.assertGreater(profile.momentum_3m, 0)
        self.assertGreater(profile.beta_vs_benchmark, 1.0)
        self.assertEqual(profile.last_close, closes[-1])
        self.assertGreater(profile.high_252d, profile.low_252d)


if __name__ == "__main__":
    unittest.main()
