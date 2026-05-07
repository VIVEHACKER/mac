from datetime import date
import unittest

from trading_copilot.earnings_calendar import (
    AlphaVantageEarningsCalendarProvider,
    HybridEarningsCalendarProvider,
    NasdaqEarningsCalendarProvider,
    format_earnings_calendar_report,
    parse_alpha_vantage_earnings_csv,
    parse_nasdaq_earnings_json,
)


class EarningsCalendarTests(unittest.TestCase):
    def test_parse_alpha_vantage_earnings_calendar_csv(self):
        events = parse_alpha_vantage_earnings_csv(ALPHA_CALENDAR_CSV, ticker="MSFT")

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].ticker, "MSFT")
        self.assertEqual(events[0].report_date, date(2026, 7, 23))
        self.assertEqual(events[0].fiscal_date_ending, date(2026, 6, 30))
        self.assertEqual(events[0].estimate, 3.25)
        self.assertIn("alphavantage.co", events[0].source)

    def test_alpha_vantage_provider_requires_api_key(self):
        provider = AlphaVantageEarningsCalendarProvider(api_key="", fetch_text=lambda url: ALPHA_CALENDAR_CSV)

        with self.assertRaises(ValueError):
            provider.earnings("MSFT")

    def test_parse_nasdaq_earnings_json(self):
        events = parse_nasdaq_earnings_json(
            NASDAQ_EARNINGS_JSON,
            ticker="MSFT",
            source="https://api.nasdaq.com/api/calendar/earnings?date=2026-07-23",
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].ticker, "MSFT")
        self.assertEqual(events[0].report_date, date(2026, 7, 23))
        self.assertEqual(events[0].fiscal_date_ending, date(2026, 6, 30))
        self.assertEqual(events[0].estimate, 3.25)
        self.assertIn("nasdaq.com", events[0].source)

    def test_parse_nasdaq_earnings_json_handles_no_record_response(self):
        events = parse_nasdaq_earnings_json(
            '{"data": null, "status": {"rCode": 200}}',
            ticker="MSFT",
            source="https://api.nasdaq.com/api/calendar/earnings?date=2026-07-23",
        )

        self.assertEqual(events, ())

    def test_parse_nasdaq_earnings_json_uses_source_date_when_row_date_missing(self):
        events = parse_nasdaq_earnings_json(
            NASDAQ_EARNINGS_JSON_WITHOUT_ROW_DATE,
            ticker="MSFT",
            source="https://api.nasdaq.com/api/calendar/earnings?date=2026-07-23",
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].report_date, date(2026, 7, 23))

    def test_nasdaq_provider_scans_dates_without_api_key(self):
        requested_urls = []

        def fetch_json(url: str):
            requested_urls.append(url)
            if "2026-07-23" in url:
                return NASDAQ_EARNINGS_PAYLOAD
            return {"data": {"rows": []}}

        provider = NasdaqEarningsCalendarProvider(
            fetch_json=fetch_json,
            today=lambda: date(2026, 7, 22),
        )

        events = provider.earnings("MSFT", horizon="3month")

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].report_date, date(2026, 7, 23))
        self.assertTrue(requested_urls[0].startswith("https://api.nasdaq.com/api/calendar/earnings?date="))

    def test_hybrid_calendar_falls_back_to_nasdaq_when_alpha_key_missing(self):
        provider = HybridEarningsCalendarProvider(
            primary=AlphaVantageEarningsCalendarProvider(api_key="", fetch_text=lambda url: ALPHA_CALENDAR_CSV),
            fallback=NasdaqEarningsCalendarProvider(
                fetch_json=lambda url: NASDAQ_EARNINGS_PAYLOAD,
                today=lambda: date(2026, 7, 23),
            ),
        )

        events = provider.earnings("MSFT")

        self.assertEqual(len(events), 1)
        self.assertIn("nasdaq.com", events[0].source)

    def test_format_earnings_calendar_report(self):
        events = parse_alpha_vantage_earnings_csv(ALPHA_CALENDAR_CSV, ticker="MSFT")

        report = format_earnings_calendar_report("MSFT", events)

        self.assertIn("# Earnings Calendar - MSFT", report)
        self.assertIn("2026-07-23", report)
        self.assertIn("EPS Estimate", report)
        self.assertIn("Not investment advice", report)


ALPHA_CALENDAR_CSV = """symbol,name,reportDate,fiscalDateEnding,estimate,currency
MSFT,Microsoft Corporation,2026-07-23,2026-06-30,3.25,USD
AAPL,Apple Inc,2026-07-30,2026-06-30,1.55,USD
"""


NASDAQ_EARNINGS_PAYLOAD = {
    "data": {
        "rows": [
            {
                "symbol": "MSFT",
                "name": "Microsoft Corporation",
                "reportDate": "07/23/2026",
                "fiscalQuarterEnding": "Jun/2026",
                "epsForecast": "$3.25",
                "time": "time-after-hours",
            },
            {
                "symbol": "AAPL",
                "name": "Apple Inc",
                "reportDate": "07/30/2026",
                "fiscalQuarterEnding": "Jun/2026",
                "epsForecast": "$1.55",
                "time": "time-after-hours",
            },
        ]
    }
}


NASDAQ_EARNINGS_JSON = """{
  "data": {
    "rows": [
      {
        "symbol": "MSFT",
        "name": "Microsoft Corporation",
        "reportDate": "07/23/2026",
        "fiscalQuarterEnding": "Jun/2026",
        "epsForecast": "$3.25",
        "time": "time-after-hours"
      }
    ]
  }
}
"""


NASDAQ_EARNINGS_JSON_WITHOUT_ROW_DATE = """{
  "data": {
    "rows": [
      {
        "symbol": "MSFT",
        "name": "Microsoft Corporation",
        "reportDate": null,
        "fiscalQuarterEnding": "Jun/2026",
        "epsForecast": "$3.25",
        "time": "time-after-hours"
      }
    ]
  }
}
"""


if __name__ == "__main__":
    unittest.main()
