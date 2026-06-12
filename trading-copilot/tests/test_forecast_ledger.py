import math
import os
import tempfile
import unittest
from datetime import date

from trading_copilot.forecast_ledger import (
    ledger_summary,
    read_ledger,
    record_forecasts,
    score_pending,
)
from trading_copilot.macro import FredSeries, MacroObservation
from trading_copilot.macro_forecast import forecast_series, month_add


def _index(n: int, start=(2019, 1), base=100.0):
    rows = []
    value = base
    cur = start
    for _ in range(n):
        value *= 1.0 + 0.003 + 0.002 * math.sin(2 * math.pi * cur[1] / 12.0)
        rows.append((date(cur[0], cur[1], 1).isoformat(), round(value, 4)))
        cur = month_add(cur, 1)
    return rows


class FakeProvider:
    def __init__(self, rows):
        self._rows = rows

    def series(self, series_id):
        obs = tuple(MacroObservation(series_id, date.fromisoformat(d), v) for d, v in self._rows)
        return FredSeries(series_id, series_id, "fake://" + series_id, obs)


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "ledger.jsonl")
        self.rows84 = _index(84)  # ends 2025-12 -> target 2026-01
        self.provider = FakeProvider(self.rows84)
        self.forecast = forecast_series(self.provider, "IDX")

    def test_record_then_read_roundtrip(self):
        record_forecasts(
            (self.forecast,), region="us", recorded_at=date(2026, 1, 5), path=self.path
        )
        rows = read_ledger(self.path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kind"], "forecast")
        self.assertEqual(rows[0]["status"], "pending")
        self.assertEqual(rows[0]["region"], "us")
        self.assertEqual(rows[0]["target"], "2026-01")

    def test_pending_not_scored_when_release_missing(self):
        record_forecasts(
            (self.forecast,), region="us", recorded_at=date(2026, 1, 5), path=self.path
        )
        # provider still ends at 2025-12 -> target month unreleased.
        scored = score_pending({"us": self.provider}, scored_at=date(2026, 1, 10), path=self.path)
        self.assertEqual(scored, [])

    def test_scores_against_released_actual(self):
        record_forecasts(
            (self.forecast,), region="us", recorded_at=date(2026, 1, 5), path=self.path
        )
        # extend the series by one month (the target release).
        target = self.forecast.target
        last_val = self.rows84[-1][1]
        actual_mom = 0.42  # arbitrary realised move, in percent
        released_val = round(last_val * (1 + actual_mom / 100.0), 4)
        extended = self.rows84 + [(date(target[0], target[1], 1).isoformat(), released_val)]
        scored = score_pending(
            {"us": FakeProvider(extended)}, scored_at=date(2026, 1, 15), path=self.path
        )
        self.assertEqual(len(scored), 1)
        s = scored[0]
        self.assertAlmostEqual(s["actual_mom"], actual_mom, places=2)
        self.assertAlmostEqual(s["error_mom"], actual_mom - self.forecast.ensemble_mom, places=2)
        self.assertIn("in_pi80", s)
        self.assertIsInstance(s["in_pi80"], bool)

    def test_no_double_scoring(self):
        record_forecasts(
            (self.forecast,), region="us", recorded_at=date(2026, 1, 5), path=self.path
        )
        target = self.forecast.target
        extended = self.rows84 + [
            (date(target[0], target[1], 1).isoformat(), self.rows84[-1][1] * 1.004)
        ]
        prov = FakeProvider(extended)
        first = score_pending({"us": prov}, scored_at=date(2026, 1, 15), path=self.path)
        second = score_pending({"us": prov}, scored_at=date(2026, 1, 16), path=self.path)
        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])  # already scored

    def test_record_is_idempotent_per_target(self):
        record_forecasts(
            (self.forecast,), region="us", recorded_at=date(2026, 1, 5), path=self.path
        )
        again = record_forecasts(
            (self.forecast,), region="us", recorded_at=date(2026, 1, 6), path=self.path
        )
        self.assertEqual(again, [])  # duplicate (region, series, target) skipped
        rows = [e for e in read_ledger(self.path) if e["kind"] == "forecast"]
        self.assertEqual(len(rows), 1)

    def test_duplicate_pending_rows_score_once_per_run(self):
        # Bypass record-level dedup by writing a duplicate row manually.
        record_forecasts(
            (self.forecast,), region="us", recorded_at=date(2026, 1, 5), path=self.path
        )
        import json as _json

        with open(self.path, encoding="utf-8") as fh:
            first = _json.loads(fh.readline())
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(_json.dumps(first) + "\n")
        target = self.forecast.target
        extended = self.rows84 + [
            (date(target[0], target[1], 1).isoformat(), self.rows84[-1][1] * 1.004)
        ]
        scored = score_pending(
            {"us": FakeProvider(extended)}, scored_at=date(2026, 1, 15), path=self.path
        )
        self.assertEqual(len(scored), 1)  # not 2

    def test_nan_forecast_is_not_recorded(self):
        import dataclasses

        broken = dataclasses.replace(self.forecast, ensemble_mom=float("nan"))
        rows = record_forecasts(
            (broken,), region="us", recorded_at=date(2026, 1, 5), path=self.path
        )
        self.assertEqual(rows, [])
        self.assertEqual(read_ledger(self.path), [])

    def test_summary_reports_pending_and_scored(self):
        record_forecasts(
            (self.forecast,), region="us", recorded_at=date(2026, 1, 5), path=self.path
        )
        summary = ledger_summary(self.path)
        self.assertIn("forecasts recorded: 1", summary)
        self.assertIn("Pending", summary)


if __name__ == "__main__":
    unittest.main()
