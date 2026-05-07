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


class FakeWorkflows:
    def __init__(self, *args, **kwargs):
        pass

    def macro_report(self) -> str:
        return "# Macro Cycle Dashboard\n\nNot investment advice."


if __name__ == "__main__":
    unittest.main()
