import unittest

from trading_copilot.fundamentals import (
    HybridFundamentalsProvider,
    SecCompanyFactsProvider,
    YahooFundamentalsProvider,
    analyze_company_facts,
    format_fundamentals_report,
)


class FundamentalsTests(unittest.TestCase):
    def test_analyze_company_facts_calculates_basic_statement_metrics(self):
        analysis = analyze_company_facts("MSFT", COMPANY_FACTS_SAMPLE, source="SEC fixture")

        self.assertEqual(analysis.ticker, "MSFT")
        self.assertAlmostEqual(analysis.revenue_growth, 10.0)
        self.assertAlmostEqual(analysis.net_margin, 40.0)
        self.assertAlmostEqual(analysis.liabilities_to_equity, 0.5)
        self.assertEqual(analysis.free_cash_flow, 55000)
        self.assertIn("Revenue growth", analysis.reads[0])

    def test_sec_company_facts_provider_fetches_by_cik(self):
        provider = SecCompanyFactsProvider(fetch_json=FakeSecFundamentalResponses())

        analysis = provider.analysis("msft")

        self.assertEqual(analysis.ticker, "MSFT")
        self.assertEqual(analysis.source, "https://data.sec.gov/api/xbrl/companyfacts/CIK0000789019.json")

    def test_yahoo_fundamentals_provider_parses_timeseries_fallback(self):
        captured_urls = []

        def fetch_json(url: str):
            captured_urls.append(url)
            return YAHOO_TIMESERIES_SAMPLE

        provider = YahooFundamentalsProvider(fetch_json=fetch_json)

        analysis = provider.analysis("MSFT")

        self.assertEqual(analysis.ticker, "MSFT")
        self.assertAlmostEqual(analysis.revenue_growth, 10.0)
        self.assertAlmostEqual(analysis.net_margin, 40.0)
        self.assertEqual(analysis.free_cash_flow, 55000)
        self.assertIn("query1.finance.yahoo.com", analysis.source)
        self.assertIn("type=annualTotalRevenue,annualNetIncome", captured_urls[0])
        self.assertIn("period1=1577836800", captured_urls[0])
        self.assertIn("period2=1893456000", captured_urls[0])

    def test_hybrid_provider_falls_back_when_sec_is_unavailable(self):
        provider = HybridFundamentalsProvider(
            primary=FailingFundamentalsProvider(),
            fallback=YahooFundamentalsProvider(fetch_json=lambda url: YAHOO_TIMESERIES_SAMPLE),
        )

        analysis = provider.analysis("MSFT")

        self.assertEqual(analysis.ticker, "MSFT")
        self.assertIn("query1.finance.yahoo.com", analysis.source)

    def test_yahoo_provider_retries_single_type_requests_when_combined_has_no_values(self):
        captured_urls = []

        def fetch_json(url: str):
            captured_urls.append(url)
            if "," in url:
                return {"timeseries": {"result": [{"meta": {"symbol": ["MSFT"]}}]}}
            requested_type = url.split("type=", 1)[1].split("&", 1)[0]
            return yahoo_single_type_payload(requested_type)

        provider = YahooFundamentalsProvider(fetch_json=fetch_json)

        analysis = provider.analysis("MSFT")

        self.assertGreater(len(captured_urls), 1)
        self.assertAlmostEqual(analysis.revenue_growth, 10.0)
        self.assertEqual(analysis.free_cash_flow, 55000)

    def test_format_fundamentals_report(self):
        analysis = analyze_company_facts("MSFT", COMPANY_FACTS_SAMPLE, source="SEC fixture")

        report = format_fundamentals_report(analysis)

        self.assertIn("# Fundamentals Snapshot - MSFT", report)
        self.assertIn("Revenue", report)
        self.assertIn("Free Cash Flow", report)
        self.assertIn("Not investment advice", report)


class FakeSecFundamentalResponses:
    def __call__(self, url: str):
        if url.endswith("/company_tickers.json"):
            return {
                "0": {"cik_str": 789019, "ticker": "MSFT", "title": "MICROSOFT CORP"},
            }
        if url.endswith("/CIK0000789019.json"):
            return COMPANY_FACTS_SAMPLE
        raise AssertionError(f"Unexpected URL: {url}")


class FailingFundamentalsProvider:
    def analysis(self, ticker: str):
        raise RuntimeError("SEC blocked")


def fact(values):
    return {"units": {"USD": values}}


COMPANY_FACTS_SAMPLE = {
    "facts": {
        "us-gaap": {
            "Revenues": fact(
                [
                    {"form": "10-K", "fy": 2025, "fp": "FY", "filed": "2025-07-30", "end": "2025-06-30", "val": 200000},
                    {"form": "10-K", "fy": 2026, "fp": "FY", "filed": "2026-07-30", "end": "2026-06-30", "val": 220000},
                ]
            ),
            "NetIncomeLoss": fact(
                [
                    {"form": "10-K", "fy": 2026, "fp": "FY", "filed": "2026-07-30", "end": "2026-06-30", "val": 88000},
                ]
            ),
            "Assets": fact(
                [
                    {"form": "10-K", "fy": 2026, "fp": "FY", "filed": "2026-07-30", "end": "2026-06-30", "val": 500000},
                ]
            ),
            "Liabilities": fact(
                [
                    {"form": "10-K", "fy": 2026, "fp": "FY", "filed": "2026-07-30", "end": "2026-06-30", "val": 160000},
                ]
            ),
            "StockholdersEquity": fact(
                [
                    {"form": "10-K", "fy": 2026, "fp": "FY", "filed": "2026-07-30", "end": "2026-06-30", "val": 320000},
                ]
            ),
            "NetCashProvidedByUsedInOperatingActivities": fact(
                [
                    {"form": "10-K", "fy": 2026, "fp": "FY", "filed": "2026-07-30", "end": "2026-06-30", "val": 70000},
                ]
            ),
            "PaymentsToAcquirePropertyPlantAndEquipment": fact(
                [
                    {"form": "10-K", "fy": 2026, "fp": "FY", "filed": "2026-07-30", "end": "2026-06-30", "val": 15000},
                ]
            ),
        }
    }
}


def yahoo_item(as_of_date: str, value: float):
    return {
        "asOfDate": as_of_date,
        "periodType": "12M",
        "currencyCode": "USD",
        "reportedValue": {"raw": value, "fmt": str(value)},
    }


YAHOO_TIMESERIES_SAMPLE = {
    "timeseries": {
        "result": [
            {"annualTotalRevenue": [yahoo_item("2025-06-30", 200000), yahoo_item("2026-06-30", 220000)]},
            {"annualNetIncome": [yahoo_item("2026-06-30", 88000)]},
            {"annualTotalAssets": [yahoo_item("2026-06-30", 500000)]},
            {"annualTotalLiabilitiesNetMinorityInterest": [yahoo_item("2026-06-30", 160000)]},
            {"annualStockholdersEquity": [yahoo_item("2026-06-30", 320000)]},
            {"annualOperatingCashFlow": [yahoo_item("2026-06-30", 70000)]},
            {"annualCapitalExpenditure": [yahoo_item("2026-06-30", -15000)]},
        ]
    }
}


def yahoo_single_type_payload(requested_type: str):
    for item in YAHOO_TIMESERIES_SAMPLE["timeseries"]["result"]:
        if requested_type in item:
            return {"timeseries": {"result": [item]}}
    return {"timeseries": {"result": [{"meta": {"symbol": ["MSFT"], "type": [requested_type]}}]}}


if __name__ == "__main__":
    unittest.main()
