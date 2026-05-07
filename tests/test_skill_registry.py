from pathlib import Path
import tempfile
import unittest

from trading_copilot.skill_registry import SkillRegistry


ROOT = Path(__file__).resolve().parents[2]
FINANCIAL_SERVICES = ROOT / "financial-services"


class SkillRegistryTests(unittest.TestCase):
    def test_loads_core_trading_workflow_sources(self):
        registry = SkillRegistry(FINANCIAL_SERVICES)

        bundle = registry.bundle_for("pretrade")

        self.assertIn("model-builder", bundle.names)
        self.assertIn("thesis-tracker", bundle.names)
        self.assertIn("portfolio-rebalance", bundle.names)
        self.assertIn("Every output is a formula", bundle.text)
        self.assertIn("A thesis should be falsifiable", bundle.text)

    def test_missing_financial_services_repo_fails_with_actionable_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing"

            with self.assertRaisesRegex(FileNotFoundError, "financial-services"):
                SkillRegistry(missing).bundle_for("morning")


if __name__ == "__main__":
    unittest.main()

