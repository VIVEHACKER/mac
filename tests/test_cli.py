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

    def test_sector_rank_command_writes_report_and_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "sector-rank.md"
            csv_output = Path(tmp) / "sector-rank.csv"
            db = Path(tmp) / "copilot.db"

            with patch("trading_copilot.cli.TradingWorkflows", FakeWorkflows):
                code = cli.main(
                    [
                        "--financial-services-dir",
                        str(FINANCIAL_SERVICES),
                        "--db",
                        str(db),
                        "sector-rank",
                        "--market",
                        "kr",
                        "--tickers",
                        "005930,000660",
                        "--per-sector-limit",
                        "2",
                        "--output",
                        str(output),
                        "--csv-output",
                        str(csv_output),
                    ]
                )

            self.assertEqual(code, 0)
            self.assertIn("# Sector Company Rankings", output.read_text(encoding="utf-8"))
            self.assertIn("sector_rank,company_rank,sector", csv_output.read_text(encoding="utf-8"))


class FakeWorkflows:
    def __init__(self, *args, **kwargs):
        pass

    def macro_report(self) -> str:
        return "# Macro Cycle Dashboard\n\nNot investment advice."

    def sector_ranking_report(
        self,
        market: str,
        max_tickers: int,
        per_sector_limit: int,
        sector: str,
        tickers: tuple[str, ...],
        use_korean_tickers: bool = True,
    ) -> str:
        return "# Sector Company Rankings - KR\n\nNot investment advice."

    def sector_ranking_csv(
        self,
        market: str,
        max_tickers: int,
        per_sector_limit: int,
        sector: str,
        tickers: tuple[str, ...],
        use_korean_tickers: bool = True,
    ) -> str:
        return "sector_rank,company_rank,sector,symbol,name\n1,1,Semiconductors,005930.KS,Samsung\n"


if __name__ == "__main__":
    unittest.main()
