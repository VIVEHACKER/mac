from datetime import datetime, timezone
import unittest

from trading_copilot.market_data import MarketSnapshot
from trading_copilot.portfolio import build_aggressive_portfolio, format_portfolio_report


class PortfolioTests(unittest.TestCase):
    def test_builds_100_percent_target_portfolio_with_three_single_stocks(self):
        draft = build_aggressive_portfolio(
            target_annual_return=100,
            market_data=StaticMarketData(),
            single_stock_pool=("LOW", "MID", "HIGH", "BEST"),
        )

        self.assertEqual(sum(position.weight for position in draft.positions), 100.0)
        single_stocks = [position for position in draft.positions if position.asset_class == "single_stock"]
        self.assertEqual([position.symbol for position in single_stocks], ["BEST", "HIGH", "MID"])
        self.assertEqual(len(single_stocks), 3)
        self.assertTrue(any(position.symbol == "TQQQ" for position in draft.positions))
        self.assertTrue(any(position.asset_class == "bond" for position in draft.positions))
        self.assertTrue(any(position.asset_class == "commodity" for position in draft.positions))

    def test_portfolio_report_warns_target_is_not_guaranteed(self):
        draft = build_aggressive_portfolio(
            target_annual_return=100,
            market_data=StaticMarketData(),
            single_stock_pool=("LOW", "MID", "HIGH", "BEST"),
        )

        report = format_portfolio_report(draft)

        self.assertIn("# Aggressive 100% Target Portfolio Draft", report)
        self.assertIn("Target annual return: 100%", report)
        self.assertIn("not guaranteed", report.lower())
        self.assertIn("Single Stocks: 3", report)
        self.assertIn("TQQQ", report)
        self.assertIn("WTI", report)
        self.assertIn("Rare Earth", report)


class StaticMarketData:
    def snapshot(self, ticker: str) -> MarketSnapshot:
        scores = {
            "BEST": 15.0,
            "HIGH": 10.0,
            "MID": 5.0,
            "LOW": -2.0,
        }
        change = scores.get(ticker, 0.0)
        return MarketSnapshot(
            ticker=ticker,
            price=100.0,
            previous_close=100.0 - change,
            change=change,
            change_percent=change,
            currency="USD",
            as_of=datetime(2026, 5, 7, 5, 20, tzinfo=timezone.utc),
            source=f"quote fixture {ticker}",
        )


if __name__ == "__main__":
    unittest.main()

