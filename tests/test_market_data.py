from datetime import datetime, timezone
import unittest

from trading_copilot.market_data import MarketSnapshot, YahooChartProvider, format_quote_report


class MarketDataTests(unittest.TestCase):
    def test_parses_yahoo_chart_snapshot_with_source_and_timestamp(self):
        provider = YahooChartProvider(fetch_json=lambda url: SAMPLE_CHART_RESPONSE)

        snapshot = provider.snapshot("msft")

        self.assertEqual(snapshot.ticker, "MSFT")
        self.assertEqual(snapshot.price, 421.5)
        self.assertEqual(snapshot.previous_close, 420.0)
        self.assertAlmostEqual(snapshot.change, 1.5)
        self.assertAlmostEqual(snapshot.change_percent, 0.3571428571)
        self.assertEqual(snapshot.currency, "USD")
        self.assertIn("query1.finance.yahoo.com", snapshot.source)
        self.assertEqual(snapshot.as_of, datetime(2026, 5, 7, 5, 20, tzinfo=timezone.utc))

    def test_format_quote_report_never_turns_snapshot_into_recommendation(self):
        snapshot = MarketSnapshot(
            ticker="MSFT",
            price=421.5,
            previous_close=420.0,
            change=1.5,
            change_percent=0.3571428571,
            currency="USD",
            as_of=datetime(2026, 5, 7, 5, 20, tzinfo=timezone.utc),
            source="https://query1.finance.yahoo.com/v8/finance/chart/MSFT",
        )

        report = format_quote_report(snapshot)

        self.assertIn("# Quote Snapshot - MSFT", report)
        self.assertIn("421.50 USD", report)
        self.assertIn("+0.36%", report)
        self.assertIn("Source:", report)
        self.assertIn("No trade recommendations", report)
        self.assertNotIn("Recommendation:", report)


SAMPLE_CHART_RESPONSE = {
    "chart": {
        "result": [
            {
                "meta": {
                    "currency": "USD",
                    "regularMarketPrice": 421.5,
                    "previousClose": 420.0,
                    "regularMarketTime": 1778131200,
                }
            }
        ],
        "error": None,
    }
}


if __name__ == "__main__":
    unittest.main()

