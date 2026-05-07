from datetime import date
import unittest

from trading_copilot.macro import (
    FredCsvProvider,
    FredSeries,
    MacroObservation,
    build_macro_dashboard,
    classify_macro_regime,
    format_macro_report,
    inverse_percent_change_since_months,
    parse_fred_csv,
    parse_fred_text_table,
    percent_change_since_months,
)


class MacroTests(unittest.TestCase):
    def test_parse_fred_csv_skips_missing_values(self):
        csv_text = "\n".join(
            [
                "observation_date,CPIAUCSL",
                "2025-01-01,300.0",
                "2025-02-01,.",
                "2026-01-01,312.0",
            ]
        )

        series = parse_fred_csv(csv_text, "CPIAUCSL", "https://fred.test/cpi.csv")

        self.assertEqual(series.series_id, "CPIAUCSL")
        self.assertEqual(len(series.observations), 2)
        self.assertEqual(series.observations[-1].value, 312.0)
        self.assertEqual(series.source, "https://fred.test/cpi.csv")

    def test_parse_fred_text_table_reads_data_endpoint(self):
        text = "\n".join(
            [
                "Title | Global price of Corn",
                "Series ID | PMAIZMTUSDM",
                "DATE | VALUE",
                "2025-12-01 | 205.31507955357140",
                "2026-01-01 | .",
                "2026-02-01 | 210.25",
            ]
        )

        series = parse_fred_text_table(text, "PMAIZMTUSDM", "https://fred.test/data/PMAIZMTUSDM")

        self.assertEqual(series.series_id, "PMAIZMTUSDM")
        self.assertEqual(len(series.observations), 2)
        self.assertAlmostEqual(series.observations[-1].value, 210.25)

    def test_fred_provider_falls_back_to_govspending_json(self):
        requested_urls = []

        def fetch_text(url: str) -> str:
            requested_urls.append(url)
            if "fred.stlouisfed.org" in url:
                raise TimeoutError("fred timed out")
            return (
                '{"seriesId":"CPIAUCSL","title":"Consumer Price Index",'
                '"observations":[{"date":"2025-01-01","value":300.0},'
                '{"date":"2026-01-01","value":312.0}]}'
            )

        provider = FredCsvProvider(fetch_text=fetch_text)

        series = provider.series("CPIAUCSL")

        self.assertEqual(series.observations[-1].value, 312.0)
        self.assertIn("fred.stlouisfed.org", requested_urls[0])
        self.assertIn("govspending.org", requested_urls[1])

    def test_fred_provider_reuses_fallback_after_direct_failure(self):
        requested_urls = []

        def fetch_text(url: str) -> str:
            requested_urls.append(url)
            if "fred.stlouisfed.org" in url:
                raise TimeoutError("fred timed out")
            series_id = url.rsplit("/", 1)[-1].replace(".json", "")
            return (
                f'{{"seriesId":"{series_id}","title":"{series_id}",'
                '"observations":[{"date":"2025-01-01","value":300.0},'
                '{"date":"2026-01-01","value":312.0}]}'
            )

        provider = FredCsvProvider(fetch_text=fetch_text)

        provider.series("CPIAUCSL")
        provider.series("UNRATE")

        fred_requests = [url for url in requested_urls if "fred.stlouisfed.org" in url]
        fallback_requests = [url for url in requested_urls if "govspending.org" in url]
        self.assertEqual(len(fred_requests), 1)
        self.assertEqual(len(fallback_requests), 2)

    def test_fred_provider_tries_text_when_preferred_fallback_lacks_series(self):
        requested_urls = []

        def fetch_text(url: str) -> str:
            requested_urls.append(url)
            if "fred.stlouisfed.org" in url and "CPIAUCSL" in url:
                raise TimeoutError("fred timed out")
            if "govspending.org" in url and "DEXSZUS" in url:
                raise RuntimeError("fallback missing series")
            if "/data/DEXSZUS" in url:
                return "DATE | VALUE\n2026-01-01 | 0.90\n"
            if "govspending.org" in url:
                return (
                    '{"seriesId":"CPIAUCSL","title":"Consumer Price Index",'
                    '"observations":[{"date":"2025-01-01","value":300.0},'
                    '{"date":"2026-01-01","value":312.0}]}'
                )
            return "observation_date,DEXSZUS\n2026-01-01,0.90\n"

        provider = FredCsvProvider(fetch_text=fetch_text)
        provider.series("CPIAUCSL")
        series = provider.series("DEXSZUS")

        self.assertEqual(series.series_id, "DEXSZUS")
        self.assertEqual(series.observations[-1].value, 0.90)
        self.assertTrue(any("govspending.org" in url and "DEXSZUS" in url for url in requested_urls))
        self.assertTrue(any("/data/DEXSZUS" in url for url in requested_urls))

    def test_fred_provider_uses_text_endpoint_when_csv_and_json_fail(self):
        requested_urls = []

        def fetch_text(url: str) -> str:
            requested_urls.append(url)
            if "fredgraph.csv" in url:
                raise TimeoutError("fred csv timed out")
            if "govspending.org" in url:
                raise RuntimeError("fallback missing series")
            return "DATE | VALUE\n2025-12-01 | 205.0\n2026-01-01 | 210.0\n"

        provider = FredCsvProvider(fetch_text=fetch_text)
        series = provider.series("PMAIZMTUSDM")

        self.assertEqual(series.observations[-1].value, 210.0)
        self.assertTrue(any("/data/PMAIZMTUSDM" in url for url in requested_urls))

    def test_percent_change_since_months_calculates_cpi_yoy(self):
        series = FredSeries(
            series_id="CPIAUCSL",
            name="CPI",
            source="fixture",
            observations=(
                MacroObservation("CPIAUCSL", date(2025, 1, 1), 300.0),
                MacroObservation("CPIAUCSL", date(2026, 1, 1), 312.0),
            ),
        )

        change = percent_change_since_months(series, 12)

        self.assertAlmostEqual(change, 4.0)

    def test_inverse_percent_change_since_months_reads_yen_strength(self):
        series = FredSeries(
            series_id="DEXJPUS",
            name="Japanese Yen to U.S. Dollar",
            source="fixture",
            observations=(
                MacroObservation("DEXJPUS", date(2025, 7, 1), 150.0),
                MacroObservation("DEXJPUS", date(2026, 1, 1), 135.0),
            ),
        )

        change = inverse_percent_change_since_months(series, 6)

        self.assertAlmostEqual(change, 11.1111, places=3)

    def test_classify_macro_regime_flags_late_cycle_tightening(self):
        regime = classify_macro_regime(
            headline_cpi_yoy=4.2,
            core_cpi_yoy=3.9,
            headline_cpi_6m_delta=0.6,
            unemployment_6m_delta=0.2,
            fed_funds_6m_delta=0.5,
            yield_curve_spread=-0.35,
            industrial_production_yoy=-1.2,
            retail_sales_yoy=0.4,
        )

        self.assertEqual(regime.name, "Late-Cycle Tightening / Stagflation Risk")
        self.assertIn("inflation", " ".join(regime.watch_items).lower())
        self.assertIn("duration", " ".join(regime.portfolio_implications).lower())

    def test_build_macro_dashboard_and_report_formats_market_structure(self):
        provider = StaticMacroProvider(
            {
                "CPIAUCSL": monthly_series("CPIAUCSL", 300.0, 312.0),
                "CPILFESL": monthly_series("CPILFESL", 310.0, 321.0),
                "UNRATE": monthly_series("UNRATE", 3.8, 4.2),
                "FEDFUNDS": monthly_series("FEDFUNDS", 4.5, 5.0),
                "T10Y2Y": point_series("T10Y2Y", -0.35),
                "INDPRO": monthly_series("INDPRO", 105.0, 103.5),
                "RSAFS": monthly_series("RSAFS", 650000.0, 657000.0),
                "DTWEXBGS": monthly_series("DTWEXBGS", 115.0, 121.0),
                "DEXUSEU": monthly_series("DEXUSEU", 1.05, 1.12),
                "DEXJPUS": monthly_series("DEXJPUS", 150.0, 140.0),
                "DEXUSUK": monthly_series("DEXUSUK", 1.25, 1.32),
                "DEXSZUS": monthly_series("DEXSZUS", 0.90, 0.86),
                "DEXCHUS": monthly_series("DEXCHUS", 7.25, 7.00),
                "PMAIZMTUSDM": monthly_series("PMAIZMTUSDM", 180.0, 225.0),
            }
        )

        dashboard = build_macro_dashboard(provider)
        report = format_macro_report(dashboard)

        self.assertEqual(dashboard.regime.name, "Late-Cycle Tightening / Stagflation Risk")
        self.assertIn("# Macro Cycle Dashboard", report)
        self.assertIn("Consumer Price Index", report)
        self.assertIn("10Y-2Y Treasury Spread", report)
        self.assertIn("Currencies and Commodities", report)
        self.assertIn("Japanese Yen vs USD", report)
        self.assertIn("Global Corn Price", report)
        self.assertIn("Market Structure Read", report)
        self.assertIn("Not investment advice", report)


def monthly_series(series_id: str, previous: float, latest: float) -> FredSeries:
    return FredSeries(
        series_id=series_id,
        name=series_id,
        source=f"https://fred.test/{series_id}.csv",
        observations=(
            MacroObservation(series_id, date(2025, 1, 1), previous),
            MacroObservation(series_id, date(2025, 7, 1), previous * 0.99),
            MacroObservation(series_id, date(2026, 1, 1), latest),
        ),
    )


def point_series(series_id: str, latest: float) -> FredSeries:
    return FredSeries(
        series_id=series_id,
        name=series_id,
        source=f"https://fred.test/{series_id}.csv",
        observations=(MacroObservation(series_id, date(2026, 1, 1), latest),),
    )


class StaticMacroProvider:
    def __init__(self, series_by_id: dict[str, FredSeries]):
        self.series_by_id = series_by_id

    def series(self, series_id: str) -> FredSeries:
        return self.series_by_id[series_id]


if __name__ == "__main__":
    unittest.main()
