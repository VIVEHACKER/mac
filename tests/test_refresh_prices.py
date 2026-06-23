from datetime import date

import pandas as pd
import pytest

from scripts import refresh_prices


def _frame(symbols, last_date: str) -> pd.DataFrame:
    idx = pd.to_datetime(["2026-06-01", last_date])
    return pd.DataFrame({s: [100.0, 101.0] for s in symbols}, index=idx)


def test_refresh_aborts_on_mismatched_coverage(monkeypatch, tmp_path):
    # 광역(2026-06-22) vs ideal(2026-06-20) 종료일 불일치 → 어느 파일도 게시 금지
    monkeypatch.setattr(refresh_prices, "SNAP_DIR", tmp_path)

    def fake_dl(symbols, start, end):
        return _frame(symbols, "2026-06-20" if "SPY" in symbols else "2026-06-22")

    monkeypatch.setattr(refresh_prices, "_download_closes", fake_dl)
    with pytest.raises(SystemExit, match="불일치"):
        refresh_prices.refresh("2024-01-01", today=date(2026, 6, 22))
    assert list(tmp_path.glob("prices-*.csv")) == []  # 부분 게시 없음


def test_refresh_aborts_on_empty_download(monkeypatch, tmp_path):
    monkeypatch.setattr(refresh_prices, "SNAP_DIR", tmp_path)
    monkeypatch.setattr(
        refresh_prices, "_download_closes", lambda symbols, start, end: pd.DataFrame()
    )
    with pytest.raises(SystemExit, match="비어"):
        refresh_prices.refresh("2024-01-01", today=date(2026, 6, 22))
    assert list(tmp_path.glob("prices-*.csv")) == []


def test_refresh_uses_realized_dates_not_raw_index(monkeypatch, tmp_path):
    # ideal 마지막 행이 all-NaN → raw index 는 같아도 실제 게시 종료일은 06-20 → 게시 abort (Codex P2)
    monkeypatch.setattr(refresh_prices, "SNAP_DIR", tmp_path)

    def fake_dl(symbols, start, end):
        idx = pd.to_datetime(["2026-06-20", "2026-06-22"])
        if "SPY" in symbols:  # ideal: 당일(06-22) 전부 NaN
            return pd.DataFrame({s: [100.0, float("nan")] for s in symbols}, index=idx)
        return pd.DataFrame(
            {s: [100.0, 101.0] for s in symbols}, index=idx
        )  # broad: 06-22 실데이터

    monkeypatch.setattr(refresh_prices, "_download_closes", fake_dl)
    with pytest.raises(SystemExit, match="불일치"):
        refresh_prices.refresh("2024-01-01", today=date(2026, 6, 22))
    assert list(tmp_path.glob("prices-*.csv")) == []


def test_refresh_publishes_both_when_consistent(monkeypatch, tmp_path):
    monkeypatch.setattr(refresh_prices, "SNAP_DIR", tmp_path)
    monkeypatch.setattr(
        refresh_prices,
        "_download_closes",
        lambda symbols, start, end: _frame(symbols, "2026-06-22"),
    )
    written = refresh_prices.refresh("2024-01-01", today=date(2026, 6, 22))
    assert len(written) == 2
    assert (tmp_path / "prices-2026-06-22.csv").exists()
    assert (tmp_path / "prices-ideal-2026-06-22.csv").exists()
