import unittest

from trading_copilot.sizing import (
    format_sizing_report,
    kelly_fraction,
    plan_position,
    vol_target_weight,
)


class SizingTests(unittest.TestCase):
    def test_kelly_returns_zero_for_negative_edge(self):
        self.assertEqual(kelly_fraction(0.4, 0.5, 0.5), 0.0)

    def test_kelly_with_positive_edge(self):
        # p=0.6 win 1x, lose 1x -> Kelly = 0.2
        f = kelly_fraction(0.6, 1.0, 1.0)
        self.assertAlmostEqual(f, 0.2, places=6)

    def test_vol_target_caps_at_max_leverage(self):
        # Asset vol 0.10, target 1.0 -> would need 10x but cap=1.0
        self.assertEqual(vol_target_weight(0.10, 1.0, max_leverage=1.0), 1.0)

    def test_vol_target_below_cap(self):
        # Asset vol 0.40, target 0.20 -> 0.5
        self.assertAlmostEqual(vol_target_weight(0.40, 0.20), 0.5, places=6)

    def test_plan_position_picks_min_of_three_constraints(self):
        plan = plan_position(
            aum=100_000,
            price=100.0,
            win_probability=0.6,
            upside_pct=0.5,
            downside_pct=0.2,
            asset_vol_annualized=0.50,
            target_vol_annualized=0.35,
            kelly_multiplier=0.5,
            max_position_pct=0.25,
            max_risk_pct_of_aum=0.02,
        )
        self.assertGreater(plan.shares, 0)
        # 2% risk cap with 20% stop = 10% weight max
        self.assertLessEqual(plan.target_weight_pct, 10.0 + 0.001)
        # Risk should not exceed 2% of AUM
        self.assertLessEqual(plan.risk_pct_of_aum, 2.0 + 0.001)

    def test_plan_position_caps_concentration(self):
        # Force a setup where Kelly is big but we cap at 25%
        plan = plan_position(
            aum=100_000,
            price=10.0,
            win_probability=0.9,
            upside_pct=2.0,
            downside_pct=0.5,
            asset_vol_annualized=0.20,
            target_vol_annualized=1.0,  # would push vol weight high
            kelly_multiplier=1.0,
            max_position_pct=0.25,
            max_risk_pct_of_aum=0.50,  # large so it doesn't bind
        )
        self.assertLessEqual(plan.capped_weight_pct, 25.0 + 0.001)

    def test_format_sizing_report_includes_key_lines(self):
        plan = plan_position(
            aum=100_000,
            price=100.0,
            win_probability=0.6,
            upside_pct=0.5,
            downside_pct=0.2,
            asset_vol_annualized=0.50,
        )
        report = format_sizing_report(plan)
        self.assertIn("Position Sizing", report)
        self.assertIn("Hard stop", report)
        self.assertIn("Max loss if stopped out", report)


if __name__ == "__main__":
    unittest.main()
