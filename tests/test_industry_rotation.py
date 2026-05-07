from datetime import date, timedelta
import unittest

from trading_copilot.industry_rotation import (
    IndustryProxy,
    PricePoint,
    YahooHistoryProvider,
    analyze_industries,
    format_industry_report,
    industry_scores_to_csv,
    return_profile,
)


class IndustryRotationTests(unittest.TestCase):
    def test_return_profile_calculates_multiple_lookbacks(self):
        points = points_from_returns("SMH", one_month=5.0, three_month=12.0, six_month=24.0)

        profile = return_profile(points)

        self.assertAlmostEqual(profile.one_month, 5.0, places=2)
        self.assertAlmostEqual(profile.three_month, 12.0, places=2)
        self.assertAlmostEqual(profile.six_month, 24.0, places=2)

    def test_analyze_industries_separates_current_and_next_leaders(self):
        provider = StaticHistoryProvider(
            {
                "SPY": points_from_returns("SPY", one_month=2.0, three_month=6.0, six_month=8.0),
                "SMH": points_from_returns("SMH", one_month=6.0, three_month=25.0, six_month=45.0),
                "COPX": points_from_returns("COPX", one_month=9.0, three_month=10.0, six_month=5.0),
                "XLV": points_from_returns("XLV", one_month=1.0, three_month=2.0, six_month=4.0),
            }
        )
        proxies = (
            IndustryProxy("SMH", "Semiconductors", "Technology", "offensive"),
            IndustryProxy("COPX", "Copper Miners", "Materials", "cyclical"),
            IndustryProxy("XLV", "Health Care", "Defensive", "defensive"),
        )

        result = analyze_industries(
            history_provider=provider,
            proxies=proxies,
            benchmark_symbol="SPY",
            current_limit=1,
            next_limit=2,
        )

        self.assertEqual(result.current_leaders[0].symbol, "SMH")
        self.assertEqual(result.next_leaders[0].symbol, "COPX")
        self.assertGreater(result.next_leaders[0].acceleration, 0)

    def test_format_industry_report_includes_leadership_sections(self):
        provider = StaticHistoryProvider(
            {
                "SPY": points_from_returns("SPY", one_month=2.0, three_month=6.0, six_month=8.0),
                "SMH": points_from_returns("SMH", one_month=6.0, three_month=25.0, six_month=45.0),
                "COPX": points_from_returns("COPX", one_month=9.0, three_month=10.0, six_month=5.0),
            }
        )

        result = analyze_industries(
            history_provider=provider,
            proxies=(
                IndustryProxy("SMH", "Semiconductors", "Technology", "offensive"),
                IndustryProxy("COPX", "Copper Miners", "Materials", "cyclical"),
            ),
            benchmark_symbol="SPY",
            current_limit=1,
            next_limit=1,
        )
        report = format_industry_report(result)

        self.assertIn("# Industry Leadership Radar", report)
        self.assertIn("Current Leaders", report)
        self.assertIn("Next Leader Candidates", report)
        self.assertIn("Not investment advice", report)
        self.assertIn("Semiconductors", report)
        self.assertIn("Copper Miners", report)

    def test_industry_scores_to_csv_exports_scores(self):
        provider = StaticHistoryProvider(
            {
                "SPY": points_from_returns("SPY", one_month=2.0, three_month=6.0, six_month=8.0),
                "SMH": points_from_returns("SMH", one_month=6.0, three_month=25.0, six_month=45.0),
            }
        )
        result = analyze_industries(
            history_provider=provider,
            proxies=(IndustryProxy("SMH", "Semiconductors", "Technology", "offensive"),),
            benchmark_symbol="SPY",
        )

        csv_text = industry_scores_to_csv(result.scores)

        self.assertIn("symbol,name,group,theme", csv_text)
        self.assertIn("SMH", csv_text)
        self.assertIn("leadership_score", csv_text)

    def test_yahoo_history_uses_period_parameters_for_max_daily_history(self):
        requested_urls = []

        def fetch_json(url: str):
            requested_urls.append(url)
            return yahoo_chart_payload()

        provider = YahooHistoryProvider(fetch_json=fetch_json)

        provider.history("SPY", range_period="max", interval="1d")

        self.assertIn("period1=0", requested_urls[0])
        self.assertIn("period2=", requested_urls[0])
        self.assertNotIn("range=max", requested_urls[0])

    def test_yahoo_history_prefers_adjusted_close_when_available(self):
        provider = YahooHistoryProvider(fetch_json=lambda url: yahoo_chart_payload_with_adjclose())

        points = provider.history("SPY", range_period="max", interval="1d")

        self.assertEqual([point.close for point in points], [50.0, 51.0])


class StaticHistoryProvider:
    def __init__(self, points_by_symbol: dict[str, tuple[PricePoint, ...]]):
        self.points_by_symbol = points_by_symbol

    def history(
        self,
        symbol: str,
        range_period: str = "6mo",
        interval: str = "1d",
    ) -> tuple[PricePoint, ...]:
        return self.points_by_symbol[symbol.upper()]


def points_from_returns(
    symbol: str,
    one_month: float,
    three_month: float,
    six_month: float,
) -> tuple[PricePoint, ...]:
    current = 100.0
    anchors = {
        0: current / (1.0 + six_month / 100.0),
        63: current / (1.0 + three_month / 100.0),
        105: current / (1.0 + one_month / 100.0),
        126: current,
    }
    values = interpolate_anchors(anchors, 127)
    start = date(2026, 1, 1)
    return tuple(
        PricePoint(symbol.upper(), start + timedelta(days=index), value)
        for index, value in enumerate(values)
    )


def interpolate_anchors(anchors: dict[int, float], length: int) -> list[float]:
    values = [0.0] * length
    indexes = sorted(anchors)
    for start_index, end_index in zip(indexes, indexes[1:]):
        start_value = anchors[start_index]
        end_value = anchors[end_index]
        span = end_index - start_index
        for offset in range(span + 1):
            ratio = offset / span if span else 0.0
            values[start_index + offset] = start_value + (end_value - start_value) * ratio
    return values


def yahoo_chart_payload():
    return {
        "chart": {
            "result": [
                {
                    "timestamp": [1777766400, 1777852800],
                    "indicators": {"quote": [{"close": [100.0, 101.0]}]},
                }
            ],
            "error": None,
        }
    }


def yahoo_chart_payload_with_adjclose():
    return {
        "chart": {
            "result": [
                {
                    "timestamp": [1777766400, 1777852800],
                    "indicators": {
                        "quote": [{"close": [100.0, 101.0]}],
                        "adjclose": [{"adjclose": [50.0, 51.0]}],
                    },
                }
            ],
            "error": None,
        }
    }


if __name__ == "__main__":
    unittest.main()
