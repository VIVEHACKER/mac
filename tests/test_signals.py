from datetime import date
import unittest

from trading_copilot.events import EventItem
from trading_copilot.signals import detect_forecast_signals, format_signals_report


class ForecastSignalTests(unittest.TestCase):
    def test_detects_contract_win_as_positive_earnings_signal(self):
        signals = detect_forecast_signals(
            (
                EventItem(
                    ticker="MSFT",
                    source_type="NEWS",
                    title="Microsoft wins multi-year cloud contract with Contoso",
                    published_at=date(2026, 5, 6),
                    source="https://example.com/contract-win",
                    form="NEWS",
                ),
            )
        )

        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].direction, "positive")
        self.assertEqual(signals[0].category, "contract")
        self.assertEqual(signals[0].earnings_impact, "revenue visibility")

    def test_detects_guidance_cut_and_demand_slowdown_as_negative_signal(self):
        signals = detect_forecast_signals(
            (
                EventItem(
                    ticker="MSFT",
                    source_type="NEWS",
                    title="Microsoft cuts guidance as Azure demand slows",
                    published_at=date(2026, 5, 6),
                    source="https://example.com/guidance-cut",
                    form="NEWS",
                ),
            )
        )

        self.assertEqual(signals[0].direction, "negative")
        self.assertEqual(signals[0].category, "guidance")
        self.assertEqual(signals[0].earnings_impact, "estimate downside")

    def test_signals_report_groups_positive_risks_and_watch_items(self):
        signals = detect_forecast_signals(
            (
                EventItem(
                    ticker="MSFT",
                    source_type="NEWS",
                    title="Microsoft wins multi-year cloud contract with Contoso",
                    published_at=date(2026, 5, 6),
                    source="https://example.com/contract-win",
                    form="NEWS",
                ),
                EventItem(
                    ticker="MSFT",
                    source_type="SEC",
                    title="10-Q filed 2026-04-29 - 10-Q",
                    published_at=date(2026, 4, 29),
                    source="https://example.com/10q",
                    form="10-Q",
                ),
            )
        )

        report = format_signals_report("MSFT", signals)

        self.assertIn("# Earnings Forecast Signals - MSFT", report)
        self.assertIn("## Positive Catalysts", report)
        self.assertIn("multi-year cloud contract", report)
        self.assertIn("## Watch Items", report)
        self.assertIn("10-Q", report)
        self.assertIn("Earnings Forecast Checklist", report)


if __name__ == "__main__":
    unittest.main()

