from datetime import date
import unittest

from trading_copilot.events import NewsRssProvider, SecEdgarProvider, format_events_report, format_news_report


class SecEdgarProviderTests(unittest.TestCase):
    def test_parses_recent_sec_filings_with_archive_source_urls(self):
        provider = SecEdgarProvider(fetch_json=FakeSecResponses())

        events = provider.recent_events("msft", limit=2)

        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].ticker, "MSFT")
        self.assertEqual(events[0].source_type, "SEC")
        self.assertEqual(events[0].form, "8-K")
        self.assertEqual(events[0].published_at, date(2026, 5, 6))
        self.assertIn("/789019/", events[0].source)
        self.assertIn("000095017026000001", events[0].source)
        self.assertIn("msft-20260506.htm", events[0].source)

    def test_format_events_report_includes_no_recommendation_language(self):
        provider = SecEdgarProvider(fetch_json=FakeSecResponses())

        report = format_events_report("MSFT", provider.recent_events("MSFT", limit=1))

        self.assertIn("# Recent SEC Events - MSFT", report)
        self.assertIn("8-K", report)
        self.assertIn("Source:", report)
        self.assertIn("No investment recommendation", report)
        self.assertNotIn("Recommendation:", report)


class NewsRssProviderTests(unittest.TestCase):
    def test_parses_rss_news_items_as_events(self):
        recorder = RecordingTextFetcher(SAMPLE_RSS)
        provider = NewsRssProvider(fetch_text=recorder)

        events = provider.recent_events("msft", limit=2)

        self.assertIn("news.google.com/rss/search", recorder.urls[0])
        self.assertIn("MSFT%20stock", recorder.urls[0])
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].ticker, "MSFT")
        self.assertEqual(events[0].source_type, "NEWS")
        self.assertEqual(events[0].form, "NEWS")
        self.assertEqual(events[0].published_at, date(2026, 5, 6))
        self.assertEqual(events[0].title, "Microsoft shares rise after cloud update")
        self.assertEqual(events[0].source, "https://example.com/msft-cloud")

    def test_format_news_report_is_source_only(self):
        provider = NewsRssProvider(fetch_text=lambda url: SAMPLE_RSS)

        report = format_news_report("MSFT", provider.recent_events("MSFT", limit=1))

        self.assertIn("# Recent News - MSFT", report)
        self.assertIn("Microsoft shares rise", report)
        self.assertIn("Source:", report)
        self.assertIn("No investment recommendation", report)
        self.assertNotIn("Recommendation:", report)


class FakeSecResponses:
    def __call__(self, url: str):
        if url.endswith("/company_tickers.json"):
            return {
                "0": {"cik_str": 789019, "ticker": "MSFT", "title": "MICROSOFT CORP"},
                "1": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
            }
        if url.endswith("/CIK0000789019.json"):
            return {
                "filings": {
                    "recent": {
                        "accessionNumber": [
                            "0000950170-26-000001",
                            "0000789019-26-000002",
                        ],
                        "filingDate": ["2026-05-06", "2026-04-24"],
                        "form": ["8-K", "10-Q"],
                        "primaryDocument": [
                            "msft-20260506.htm",
                            "msft-20260424x10q.htm",
                        ],
                        "primaryDocDescription": [
                            "CURRENT REPORT",
                            "QUARTERLY REPORT",
                        ],
                    }
                }
            }
        raise AssertionError(f"Unexpected URL: {url}")


class RecordingTextFetcher:
    def __init__(self, text: str):
        self.text = text
        self.urls: list[str] = []

    def __call__(self, url: str) -> str:
        self.urls.append(url)
        return self.text


SAMPLE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>MSFT News</title>
    <item>
      <title>Microsoft shares rise after cloud update</title>
      <link>https://example.com/msft-cloud</link>
      <pubDate>Wed, 06 May 2026 20:15:00 GMT</pubDate>
    </item>
    <item>
      <title>Analysts debate Microsoft AI capex</title>
      <link>https://example.com/msft-ai-capex</link>
      <pubDate>Wed, 06 May 2026 18:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>
"""


if __name__ == "__main__":
    unittest.main()
