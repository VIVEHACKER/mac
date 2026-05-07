import unittest

from trading_copilot.sector_rankings import (
    CompanyMetrics,
    format_sector_ranking_report,
    parse_korean_tickers_stocks_html,
    rank_sector_companies,
    sector_rankings_to_csv,
)


class SectorRankingTests(unittest.TestCase):
    def test_rank_sector_companies_scores_companies_and_sectors(self):
        result = rank_sector_companies(
            market="kr",
            metrics=(
                CompanyMetrics(
                    symbol="005930.KS",
                    name="Samsung Electronics",
                    market="KOSPI",
                    sector="Semiconductors",
                    revenue_growth=10.88,
                    net_margin=18.0,
                    three_month_return=67.56,
                    ytd_return=106.81,
                    report_count=66,
                    market_cap=1_100_000_000_000,
                    pe=41.36,
                    peg=1.32,
                    sources=("https://www.koreantickers.com/stocks",),
                ),
                CompanyMetrics(
                    symbol="000660.KS",
                    name="SK hynix",
                    market="KOSPI",
                    sector="Semiconductors",
                    revenue_growth=46.76,
                    net_margin=28.0,
                    three_month_return=92.73,
                    ytd_return=138.85,
                    report_count=31,
                    market_cap=809_100_000_000,
                    pe=28.02,
                    peg=0.24,
                    sources=("https://www.koreantickers.com/stocks",),
                ),
                CompanyMetrics(
                    symbol="005380.KS",
                    name="Hyundai Motor",
                    market="KOSPI",
                    sector="Automobiles",
                    revenue_growth=6.29,
                    net_margin=8.0,
                    three_month_return=21.71,
                    ytd_return=90.62,
                    report_count=17,
                    market_cap=80_100_000_000,
                    pe=16.10,
                    peg=None,
                    sources=("https://www.koreantickers.com/stocks",),
                ),
            ),
            per_sector_limit=2,
        )

        self.assertEqual(result.sectors[0].sector, "Semiconductors")
        self.assertEqual([company.symbol for company in result.sectors[0].leaders], ["000660.KS", "005930.KS"])
        self.assertGreater(result.sectors[0].composite_score, result.sectors[1].composite_score)
        self.assertGreater(result.companies[0].technology_score, result.companies[-1].technology_score)

    def test_format_sector_ranking_report_includes_model_and_sources(self):
        result = rank_sector_companies(
            market="kr",
            metrics=(
                CompanyMetrics(
                    symbol="005930.KS",
                    name="Samsung Electronics",
                    market="KOSPI",
                    sector="Semiconductors",
                    revenue_growth=10.88,
                    net_margin=18.0,
                    three_month_return=67.56,
                    report_count=66,
                    sources=(
                        "https://www.koreantickers.com/stocks",
                        "https://www.koreantickers.com/reports?q=005930",
                    ),
                ),
            ),
        )

        report = format_sector_ranking_report(result)

        self.assertIn("# Sector Company Rankings - KR", report)
        self.assertIn("Technology Capability", report)
        self.assertIn("Business Viability", report)
        self.assertIn("Semiconductors", report)
        self.assertIn("https://www.koreantickers.com/reports?q=005930", report)
        self.assertIn("Not investment advice", report)

    def test_parse_korean_tickers_stocks_html_extracts_sector_metrics_and_reports(self):
        rows = parse_korean_tickers_stocks_html(KOREAN_TICKERS_SAMPLE)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].symbol, "005930.KS")
        self.assertEqual(rows[0].name, "Samsung Electronics Co., Ltd.")
        self.assertEqual(rows[0].market, "KOSPI")
        self.assertEqual(rows[0].sector, "Semiconductors")
        self.assertAlmostEqual(rows[0].revenue_growth, 10.88)
        self.assertAlmostEqual(rows[0].three_month_return, 71.19)
        self.assertEqual(rows[0].report_count, 66)
        self.assertIn("https://www.koreantickers.com/reports?q=005930", rows[0].sources)

    def test_sector_rankings_to_csv_exports_company_scores(self):
        result = rank_sector_companies(
            market="kr",
            metrics=(
                CompanyMetrics(
                    symbol="005930.KS",
                    name="Samsung Electronics",
                    market="KOSPI",
                    sector="Semiconductors",
                    revenue_growth=10.88,
                    net_margin=18.0,
                    three_month_return=67.56,
                    report_count=66,
                ),
            ),
        )

        csv_text = sector_rankings_to_csv(result)

        self.assertIn("sector_rank,company_rank,sector,symbol,name", csv_text)
        self.assertIn("005930.KS", csv_text)
        self.assertIn("technology_score", csv_text)


KOREAN_TICKERS_SAMPLE = """
<table><tbody>
<tr class="group transition">
  <td><a href="/stock/005930">005930</a></td>
  <td><span>KOSPI</span></td>
  <td><a href="/stock/005930">Samsung Electronics Co., Ltd.</a><div>Samsung local</div></td>
  <td><div>$187</div><div>KRW 271,500</div></td>
  <td><div>$1.1T</div><div>KRW 1587.3T</div></td>
  <td>+2.07%</td>
  <td>+71.19%</td>
  <td>+111.28%</td>
  <td>40.6M</td>
  <td>$7.6B</td>
  <td>41.36</td>
  <td>4.24</td>
  <td>4.76</td>
  <td>1.32</td>
  <td>+10.88%</td>
  <td><a href="/stocks?sector=semiconductors">Semiconductors</a></td>
  <td>EOD</td>
  <td><a href="/reports?q=005930">66<!-- --> reports</a><a href="/reports/mirae-asset-005930-2026-05-07">May 07, 2026</a></td>
</tr>
</tbody></table>
"""


if __name__ == "__main__":
    unittest.main()
