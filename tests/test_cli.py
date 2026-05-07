from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from trading_copilot import cli


ROOT = Path(__file__).resolve().parents[2]
FINANCIAL_SERVICES = ROOT / "financial-services"


class CliTests(unittest.TestCase):
    def test_macro_command_writes_output_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "macro.md"
            db = Path(tmp) / "copilot.db"

            with patch("trading_copilot.cli.TradingWorkflows", FakeWorkflows):
                code = cli.main(
                    [
                        "--financial-services-dir",
                        str(FINANCIAL_SERVICES),
                        "--db",
                        str(db),
                        "macro",
                        "--output",
                        str(output),
                    ]
                )

            self.assertEqual(code, 0)
            self.assertIn("# Macro Cycle Dashboard", output.read_text(encoding="utf-8"))

    def test_industries_command_writes_report_and_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "industries.md"
            csv_output = Path(tmp) / "industries.csv"
            db = Path(tmp) / "copilot.db"

            with patch("trading_copilot.cli.TradingWorkflows", FakeWorkflows):
                code = cli.main(
                    [
                        "--financial-services-dir",
                        str(FINANCIAL_SERVICES),
                        "--db",
                        str(db),
                        "industries",
                        "--current-limit",
                        "1",
                        "--next-limit",
                        "1",
                        "--output",
                        str(output),
                        "--csv-output",
                        str(csv_output),
                    ]
                )

            self.assertEqual(code, 0)
            self.assertIn("# Industry Leadership Radar", output.read_text(encoding="utf-8"))
            self.assertIn("symbol,name,group,theme", csv_output.read_text(encoding="utf-8"))

    def test_news_fast_calendar_and_fundamentals_commands_write_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "copilot.db"
            commands = [
                ("news-fast", "fast-news.md", "# Fast News Monitor"),
                ("calendar", "calendar.md", "# Earnings Calendar"),
                ("fundamentals", "fundamentals.md", "# Fundamentals Snapshot"),
                ("economic-calendar", "economic-calendar.md", "# Economic Release Calendar"),
            ]

            with patch("trading_copilot.cli.TradingWorkflows", FakeWorkflows):
                for command, filename, expected in commands:
                    output = Path(tmp) / filename
                    argv = [
                        "--financial-services-dir",
                        str(FINANCIAL_SERVICES),
                        "--db",
                        str(db),
                        command,
                    ]
                    if command != "economic-calendar":
                        argv.append("MSFT")
                    argv.extend(["--output", str(output)])
                    code = cli.main(argv)
                    self.assertEqual(code, 0)
                    self.assertIn(expected, output.read_text(encoding="utf-8"))

    def test_screen_all_accepts_kospi_and_writes_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "screen-kospi.md"
            csv_output = Path(tmp) / "screen-kospi.csv"
            db = Path(tmp) / "copilot.db"

            with patch("trading_copilot.cli.TradingWorkflows", FakeWorkflows):
                code = cli.main(
                    [
                        "--financial-services-dir",
                        str(FINANCIAL_SERVICES),
                        "--db",
                        str(db),
                        "screen-all",
                        "--market",
                        "kospi",
                        "--max-tickers",
                        "2",
                        "--output",
                        str(output),
                        "--csv-output",
                        str(csv_output),
                    ]
                )

            self.assertEqual(code, 0)
            self.assertIn("# KOSPI Market Screen", output.read_text(encoding="utf-8"))
            self.assertIn("symbol,name,market", csv_output.read_text(encoding="utf-8"))

    def test_patterns_command_writes_report_and_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "patterns.md"
            csv_output = Path(tmp) / "patterns.csv"
            db = Path(tmp) / "copilot.db"

            with patch("trading_copilot.cli.TradingWorkflows", FakeWorkflows):
                code = cli.main(
                    [
                        "--financial-services-dir",
                        str(FINANCIAL_SERVICES),
                        "--db",
                        str(db),
                        "patterns",
                        "--asset-set",
                        "commodities",
                        "--assets",
                        "SPY,QQQ",
                        "--horizons",
                        "21,63",
                        "--min-samples",
                        "3",
                        "--output",
                        str(output),
                        "--csv-output",
                        str(csv_output),
                    ]
                )

            self.assertEqual(code, 0)
            self.assertIn("# Historical Pattern Mining", output.read_text(encoding="utf-8"))
            self.assertIn("condition,asset,horizon_days", csv_output.read_text(encoding="utf-8"))
            self.assertEqual(FakeWorkflows.last_patterns_assets, ("SPY", "QQQ"))

    def test_patterns_command_uses_asset_set_when_assets_are_not_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "patterns.md"
            db = Path(tmp) / "copilot.db"

            with patch("trading_copilot.cli.TradingWorkflows", FakeWorkflows):
                code = cli.main(
                    [
                        "--financial-services-dir",
                        str(FINANCIAL_SERVICES),
                        "--db",
                        str(db),
                        "patterns",
                        "--asset-set",
                        "coal",
                        "--output",
                        str(output),
                    ]
                )

            self.assertEqual(code, 0)
            self.assertIn("BTU", FakeWorkflows.last_patterns_assets)
            self.assertIn("CNR", FakeWorkflows.last_patterns_assets)

    def test_recommend_ml_command_writes_guarded_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "recommend-ml.md"
            db = Path(tmp) / "copilot.db"

            with patch("trading_copilot.cli.TradingWorkflows", FakeWorkflows):
                code = cli.main(
                    [
                        "--financial-services-dir",
                        str(FINANCIAL_SERVICES),
                        "--db",
                        str(db),
                        "recommend-ml",
                        "MSFT",
                        "--target-price",
                        "520",
                        "--stop-price",
                        "390",
                        "--with-news",
                        "--with-signals",
                        "--pattern-horizon",
                        "63",
                        "--min-pattern-samples",
                        "3",
                        "--output",
                        str(output),
                    ]
                )

            self.assertEqual(code, 0)
            self.assertIn("# ML + AI Recommendation - MSFT", output.read_text(encoding="utf-8"))


class FakeWorkflows:
    last_patterns_assets = ()

    def __init__(self, *args, **kwargs):
        pass

    def macro_report(self) -> str:
        return "# Macro Cycle Dashboard\n\nNot investment advice."

    def industry_leadership_report(self, current_limit: int = 10, next_limit: int = 10) -> str:
        return "# Industry Leadership Radar\n\nNot investment advice."

    def industry_leadership_csv(self) -> str:
        return "symbol,name,group,theme\nSMH,Semiconductors,Technology,offensive\n"

    def fast_news_report(self, ticker: str, limit: int = 20) -> str:
        return f"# Fast News Monitor - {ticker}\n\nNot investment advice."

    def earnings_calendar_report(self, ticker: str, horizon: str = "3month") -> str:
        return f"# Earnings Calendar - {ticker}\n\nNot investment advice."

    def fundamentals_report(self, ticker: str) -> str:
        return f"# Fundamentals Snapshot - {ticker}\n\nNot investment advice."

    def economic_calendar_report(self, days: int = 60) -> str:
        return "# Economic Release Calendar\n\nNot investment advice."

    def screen_all_report(
        self,
        market: str,
        max_tickers: int,
        limit: int,
        include_news: bool,
        include_sec_events: bool,
        include_etfs: bool,
        include_spacs: bool = False,
    ) -> str:
        return f"# {market.upper()} Market Screen\n\nNot investment advice."

    def screen_all_csv(
        self,
        market: str,
        max_tickers: int,
        include_news: bool,
        include_sec_events: bool,
        include_etfs: bool,
        include_spacs: bool = False,
    ) -> str:
        return "symbol,name,market\n005930.KS,Samsung Electronics,KOSPI\n"

    def patterns_report(
        self,
        assets: tuple[str, ...],
        horizons: tuple[int, ...],
        min_samples: int = 5,
        limit: int = 25,
    ) -> str:
        FakeWorkflows.last_patterns_assets = assets
        return "# Historical Pattern Mining\n\nNot investment advice."

    def patterns_csv(
        self,
        assets: tuple[str, ...],
        horizons: tuple[int, ...],
        min_samples: int = 5,
    ) -> str:
        return "condition,asset,horizon_days,outcome,samples,wins,win_rate,wilson_lower_95\n"

    def ml_recommendation_report(
        self,
        ticker: str,
        target_price: float | None,
        stop_price: float | None,
        horizon: str,
        context: str,
        include_sec_events: bool = False,
        include_news: bool = False,
        include_signals: bool = False,
        event_limit: int = 3,
        news_limit: int = 3,
        pattern_horizon: int = 63,
        min_pattern_samples: int = 3,
        risk_budget_pct: float = 2.0,
        max_position_pct: float = 12.0,
        include_fundamentals: bool = True,
        include_patterns: bool = True,
    ) -> str:
        return f"# ML + AI Recommendation - {ticker}\n\nNot investment advice."


if __name__ == "__main__":
    unittest.main()
