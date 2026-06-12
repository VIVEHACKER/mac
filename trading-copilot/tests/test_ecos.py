import json
import unittest
from datetime import date

from trading_copilot.ecos import EcosProvider, ecos_time_to_date, parse_ecos_json
from trading_copilot.macro import MacroDataError


def _payload(rows):
    return json.dumps(
        {
            "StatisticSearch": {
                "list_total_count": len(rows),
                "row": [{"TIME": t, "DATA_VALUE": v, "ITEM_NAME1": "총지수"} for t, v in rows],
            }
        }
    )


class EcosParseTests(unittest.TestCase):
    def test_time_to_date_monthly(self):
        self.assertEqual(ecos_time_to_date("202604"), date(2026, 4, 1))
        self.assertEqual(ecos_time_to_date("2026"), date(2026, 1, 1))
        self.assertEqual(ecos_time_to_date("20260415"), date(2026, 4, 15))

    def test_parse_sorts_and_skips_blanks(self):
        text = _payload([("202604", "119.37"), ("202602", "118.4"), ("202603", ".")])
        series = parse_ecos_json(text, "901Y009/0", "ecos://test")
        self.assertEqual(len(series.observations), 2)
        self.assertEqual(series.observations[0].observed_at, date(2026, 2, 1))
        self.assertEqual(series.observations[-1].value, 119.37)
        self.assertIn("Korea Consumer Price Index", series.name)

    def test_error_response_raises(self):
        text = json.dumps({"RESULT": {"CODE": "ERROR-301", "MESSAGE": "bad"}})
        with self.assertRaises(MacroDataError):
            parse_ecos_json(text, "901Y009/0", "ecos://test")

    def test_empty_rows_raises(self):
        text = json.dumps({"StatisticSearch": {"row": []}})
        with self.assertRaises(MacroDataError):
            parse_ecos_json(text, "404Y014/*AA", "ecos://test")


class EcosProviderTests(unittest.TestCase):
    def test_sample_key_caps_rows_to_ten(self):
        prov = EcosProvider(api_key="sample")
        self.assertEqual(prov.max_rows, 10)
        self.assertTrue(prov.is_sample)

    def test_real_key_keeps_max_rows(self):
        prov = EcosProvider(api_key="REALKEY123", max_rows=5000)
        self.assertEqual(prov.max_rows, 5000)
        self.assertFalse(prov.is_sample)

    def test_series_builds_url_with_stat_and_item(self):
        captured = {}

        def fake_fetch(url):
            captured["url"] = url
            return _payload([("202603", "118.8"), ("202604", "119.37")])

        prov = EcosProvider(api_key="KEY", fetch_text=fake_fetch, end="202604")
        series = prov.series("901Y009/0")
        self.assertIn("/901Y009/M/", captured["url"])
        self.assertTrue(captured["url"].rstrip("/").endswith("/0"))
        self.assertEqual(series.observations[-1].value, 119.37)

    def test_item_with_special_chars_is_encoded(self):
        captured = {}

        def fake_fetch(url):
            captured["url"] = url
            return _payload([("202604", "128.43")])

        prov = EcosProvider(api_key="KEY", fetch_text=fake_fetch)
        prov.series("404Y014/*AA")
        self.assertIn("404Y014", captured["url"])
        self.assertIn("%2AAA", captured["url"])  # '*AA' url-encoded


class EcosSampleStitchTests(unittest.TestCase):
    """Sample key stitches 10-month windows into a full series."""

    def _fake_fetch_for(self, all_rows):
        calls = []

        def fake_fetch(url):
            calls.append(url)
            # URL ends .../STAT/M/START/END[/ITEM]; pull the window bounds.
            parts = url.split("/")
            idx = parts.index("M")
            start, end = parts[idx + 1], parts[idx + 2]
            window = [(t, v) for t, v in all_rows if start <= t <= end]
            if not window:
                return json.dumps({"RESULT": {"CODE": "INFO-200", "MESSAGE": "no data"}})
            return _payload(window)

        return fake_fetch, calls

    @staticmethod
    def _months(start_y, start_m, n):
        out = []
        cur = start_y * 12 + start_m - 1
        for i in range(n):
            y, m = (cur + i) // 12, (cur + i) % 12 + 1
            out.append((f"{y}{m:02d}", str(100.0 + i)))
        return out

    def test_stitch_merges_windows_into_full_history(self):
        rows = self._months(2024, 1, 28)  # 28 months -> 3 windows under sample cap
        fake_fetch, calls = self._fake_fetch_for(rows)
        prov = EcosProvider(api_key="sample", fetch_text=fake_fetch, start="202401", end="202604")
        series = prov.series("901Y009/0")
        self.assertEqual(len(series.observations), 28)
        self.assertEqual(series.observations[0].observed_at, date(2024, 1, 1))
        self.assertEqual(series.observations[-1].observed_at, date(2026, 4, 1))
        self.assertEqual(len(calls), 3)  # 10 + 10 + 8

    def test_stitch_skips_empty_windows(self):
        rows = self._months(2025, 1, 16)  # data starts 2025-01
        fake_fetch, _ = self._fake_fetch_for(rows)
        prov = EcosProvider(api_key="sample", fetch_text=fake_fetch, start="202401", end="202604")
        series = prov.series("901Y009/0")  # first window (2024) has no data
        self.assertEqual(len(series.observations), 16)
        self.assertEqual(series.observations[0].observed_at, date(2025, 1, 1))

    def test_stitch_all_empty_raises(self):
        fake_fetch, _ = self._fake_fetch_for([])
        prov = EcosProvider(api_key="sample", fetch_text=fake_fetch, start="202401", end="202412")
        with self.assertRaises(MacroDataError):
            prov.series("901Y009/0")

    def test_sample_default_start_is_bounded(self):
        prov = EcosProvider(api_key="sample")
        self.assertEqual(prov.start, "201501")
        real = EcosProvider(api_key="KEY")
        self.assertEqual(real.start, "200001")


if __name__ == "__main__":
    unittest.main()
