from datetime import datetime, timezone
import unittest

from trading_copilot.news_monitor import (
    FastNewsItem,
    MarketauxNewsProvider,
    build_fast_news_report,
    parse_marketaux_news,
)


class NewsMonitorTests(unittest.TestCase):
    def test_parse_marketaux_news_items(self):
        items = parse_marketaux_news(MARKETAUX_SAMPLE, "MSFT")

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].ticker, "MSFT")
        self.assertEqual(items[0].source_type, "MARKETAUX")
        self.assertEqual(items[0].published_at, datetime(2026, 5, 7, 12, 30, tzinfo=timezone.utc))
        self.assertEqual(items[0].sentiment, "positive")
        self.assertEqual(items[0].source, "https://example.com/msft-contract")

    def test_marketaux_provider_requires_api_key(self):
        provider = MarketauxNewsProvider(api_key="", fetch_text=lambda url: MARKETAUX_SAMPLE)

        with self.assertRaises(ValueError):
            provider.recent_news("MSFT")

    def test_fast_news_report_ranks_newest_first_and_includes_sources(self):
        older = FastNewsItem(
            ticker="MSFT",
            source_type="RSS",
            title="Older Microsoft headline",
            published_at=datetime(2026, 5, 7, 10, 0, tzinfo=timezone.utc),
            source="https://example.com/old",
        )
        newer = FastNewsItem(
            ticker="MSFT",
            source_type="MARKETAUX",
            title="Microsoft wins new AI contract",
            published_at=datetime(2026, 5, 7, 12, 30, tzinfo=timezone.utc),
            source="https://example.com/new",
            sentiment="positive",
        )

        report = build_fast_news_report("MSFT", (older, newer), data_gaps=("Missing Benzinga key",))

        self.assertIn("# Fast News Monitor - MSFT", report)
        self.assertLess(report.index("Microsoft wins new AI contract"), report.index("Older Microsoft headline"))
        self.assertIn("Missing Benzinga key", report)
        self.assertIn("Not investment advice", report)


MARKETAUX_SAMPLE = """
{
  "data": [
    {
      "title": "Microsoft wins new AI contract",
      "url": "https://example.com/msft-contract",
      "published_at": "2026-05-07T12:30:00.000000Z",
      "source": "Example Wire",
      "entities": [
        {"symbol": "MSFT", "sentiment_score": 0.61}
      ]
    }
  ]
}
"""


if __name__ == "__main__":
    unittest.main()
