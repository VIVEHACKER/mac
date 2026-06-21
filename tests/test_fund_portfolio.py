from pathlib import Path

import pytest

from dashboard.fund_portfolio import fund_book_payload, resolve_snapshot

ROOT = Path(__file__).resolve().parents[1]  # trader-fund

# 검증 스냅샷(../trader/data/snapshots)은 gitignore — 없으면 데이터 의존 테스트만 스킵.
# graceful-path 테스트(missing/none)는 데이터 없이도 동작하므로 CI 에서도 실행한다.
_HAS_DATA = (
    ROOT.parent / "trader" / "data" / "snapshots" / "fundamentals-2026-06-01-gp2.csv"
).exists()
_needs_data = pytest.mark.skipif(
    not _HAS_DATA, reason="검증 스냅샷(../trader/data/snapshots, gitignore) 부재 — 로컬 한정"
)


@_needs_data
def test_resolve_snapshot_falls_back_to_trader():
    # trader-fund 로컬엔 gitignore로 없음 → ../trader/data/snapshots 폴백
    p = resolve_snapshot(["fundamentals-*-gp2.csv"], ROOT)
    assert p is not None
    assert p.name.endswith("-gp2.csv")
    assert p.exists()


def test_resolve_snapshot_missing_returns_none():
    assert resolve_snapshot(["zzz-does-not-exist-*.csv"], ROOT) is None


@_needs_data
def test_fund_book_payload_shape_with_momentum():
    payload = fund_book_payload(ROOT, momentum_on=True)
    meta = payload["meta"]
    assert meta["available"] is True
    assert dict(meta["sleeve_fractions"]).get("core") == 0.35
    assert 0.55 <= meta["invested"] <= 0.61  # core35 + momentum25
    assert payload["positions"], "should have positions"
    row = payload["positions"][0]
    assert set(row) >= {"종목", "펀드%", "캡", "출처슬리브"}
    assert isinstance(payload["sectors"], list)
    assert payload["oos"]["n_entries"] == 0  # 원장 미생성


def test_fund_book_payload_missing_snapshots_graceful(tmp_path):
    payload = fund_book_payload(tmp_path, momentum_on=True)  # 빈 디렉토리
    assert payload["meta"]["available"] is False
    assert "스냅샷" in payload["meta"]["message"]
