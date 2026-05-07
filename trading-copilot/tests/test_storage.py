from pathlib import Path
import tempfile
import unittest

from trading_copilot.storage import TradingStore


class TradingStoreTests(unittest.TestCase):
    def test_watchlist_and_thesis_are_persisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TradingStore(Path(tmp) / "copilot.db")
            store.initialize()

            store.add_watchlist_item("NVDA", "AI infrastructure exposure")
            store.upsert_thesis(
                ticker="NVDA",
                direction="long",
                statement="Demand for accelerated compute remains stronger than consensus.",
                invalidation="Data center capex slows for two consecutive quarters.",
            )

            self.assertEqual(store.list_watchlist()[0].ticker, "NVDA")
            thesis = store.get_thesis("NVDA")
            self.assertIsNotNone(thesis)
            self.assertEqual(thesis.direction, "long")
            self.assertIn("accelerated compute", thesis.statement)


if __name__ == "__main__":
    unittest.main()

