from datetime import date
from pathlib import Path

import pytest

from engine.fund_book_oos import load_mark_price_history_csv, mark_prices_at_dates
from scripts.fund_marks import build_wide_marks, write_wide_marks

# 검증 스냅샷(LONG)은 gitignore — sibling ../trader 에만. 없으면 skip.
TRADER_SNAP = Path(__file__).resolve().parents[2] / "trader" / "data" / "snapshots"
PI = TRADER_SNAP / "prices-ideal-2026-06-01.csv"  # megacap + SPY (05-27까지)
PF = TRADER_SNAP / "prices-2026-06-01.csv"  # sp400-600 (05-29까지)
REBAL = "2026-05-27"  # 펀드 스냅샷 effective (megacap 시리즈 종료일)

pytestmark = pytest.mark.skipif(
    not (PI.exists() and PF.exists()),
    reason="검증 LONG 스냅샷(../trader/data/snapshots) 부재 — 로컬 데이터 있을 때만",
)


def test_marks_cover_both_universes_and_spy_at_rebal():
    # 드릴 사용 방식: rebal_date 에서 per-symbol carry-forward(파일별 종료일이 달라도 정합).
    _dates, table = build_wide_marks([PI, PF], since=date(2026, 5, 1))
    marks = mark_prices_at_dates(table, [REBAL], max_staleness_days=7).get(REBAL, {})
    assert "SPY" in marks and marks["SPY"] > 0.0  # 벤치마크 (prices-ideal)
    assert "MU" in marks  # 메가캡 (모멘텀 슬리브)
    assert any(sym in marks for sym in ("AAL", "ABR", "M", "COTY", "SCHL"))  # sp400-600 코어


def test_write_wide_marks_roundtrips_through_engine_loader(tmp_path):
    dates, table = build_wide_marks([PI, PF], since=date(2026, 5, 1))
    out = tmp_path / "fund-marks.csv"
    n = write_wide_marks(out, dates, table)
    assert n == len(dates)
    # 빌드→작성→엔진 로더→carry-forward 전체 파이프라인 검증
    hist = load_mark_price_history_csv(out)
    marks = mark_prices_at_dates(hist, [REBAL], max_staleness_days=7).get(REBAL, {})
    assert "SPY" in marks and marks["SPY"] > 0.0


def test_since_filters_older_dates():
    dates, _ = build_wide_marks([PI], since=date(2026, 5, 20))
    assert all(d >= "2026-05-20" for d in dates)
