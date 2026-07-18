"""KR 유니버스 검증/적재 — 이름 대조, 불일치 제외, OHLCV 적재, 백오프, validated 출력."""

from __future__ import annotations

from datetime import date

from scripts.kr_universe_ingest import (
    is_hard_failure,
    load_curated,
    load_previous_validated,
    names_match,
    reconcile_validated,
    verify_and_ingest,
    write_validated,
)


class _Bar:
    def __init__(self, code: str) -> None:
        self.symbol = code


class _FakeCatalog:
    def __init__(self, fail: set[str] | None = None) -> None:
        self.stored: list[str] = []
        self._fail = fail or set()

    def put_bars(self, bars: list) -> int:
        code = bars[0].symbol
        if code in self._fail:
            raise RuntimeError("lock")
        self.stored.append(code)
        return len(bars)


def test_names_match_tolerates_cosmetic_only():
    assert names_match("JYP Ent.", "JYP Ent")  # 구두점
    assert names_match("삼성전자", "삼성전자")
    assert names_match("삼성전자", "삼성전자보통주")  # 보통주 = 표기 차이
    assert not names_match("한미반도체", "한미약품")  # 다른 회사
    assert not names_match("", "삼성전자")


def test_names_match_rejects_preferred_share():
    # 코드를 잘못 적어 우선주로 매핑되면 반드시 걸러져야 한다 (접두 허용 금지)
    assert not names_match("삼성전자", "삼성전자우")
    assert not names_match("현대차", "현대차2우B")
    assert not names_match("LG화학", "LG화학우")


def test_is_hard_failure_classification():
    assert is_hard_failure("name mismatch: curated=X official=Y")
    assert is_hard_failure("unknown code (empty name)")
    assert not is_hard_failure("no ohlcv bars")
    assert not is_hard_failure("put_bars failed after retries")


def test_reconcile_retains_previous_on_soft_failure_only():
    curated_ok = ("semi", "005930", "삼성전자", "kospi")
    curated_soft = ("bio", "207940", "삼성바이오로직스", "kospi")  # 일시 실패
    curated_hard = ("game", "036570", "NC", "kospi")  # 이름 불일치(진짜 오류)
    validated = [curated_ok]
    failed = [
        (curated_soft, "no ohlcv bars"),  # soft → 이전 유효분 보존
        (curated_hard, "name mismatch: ..."),  # hard → 버림
    ]
    previous = {
        "207940": curated_soft,
        "036570": ("game", "036570", "엔씨소프트", "kospi"),  # 이전엔 유효했어도 하드면 버림
    }
    final, retained = reconcile_validated(validated, failed, previous)
    codes = {r[1] for r in final}
    assert codes == {"005930", "207940"}  # soft 보존, hard 제외
    assert retained == ["207940"]


def test_load_previous_validated_and_atomic_write(tmp_path):
    out = tmp_path / "v.csv"
    write_validated([("semi", "005930", "삼성전자", "kospi")], out)
    assert not (tmp_path / "v.csv.tmp").exists()  # temp 정리됨
    prev = load_previous_validated(out)
    assert prev["005930"] == ("semi", "005930", "삼성전자", "kospi")
    assert load_previous_validated(tmp_path / "none.csv") == {}


def test_load_curated_zfills_code(tmp_path):
    csv_path = tmp_path / "u.csv"
    csv_path.write_text("theme_key,code,name,market\nsemi,5930,삼성전자,kospi\n", encoding="utf-8")
    rows = load_curated(csv_path)
    assert rows == [("semi", "005930", "삼성전자", "kospi")]


def test_verify_drops_name_mismatch_and_unknown():
    curated = [
        ("semi", "005930", "삼성전자", "kospi"),
        ("semi", "999999", "없는회사", "kosdaq"),  # 이름 조회 빈값 → 제외
        ("bio", "000100", "엉뚱제약", "kospi"),  # 이름 불일치 → 제외
    ]
    official = {"005930": "삼성전자", "999999": "", "000100": "유한양행"}
    catalog = _FakeCatalog()
    validated, failed = verify_and_ingest(
        curated,
        catalog=catalog,  # type: ignore[arg-type]
        name_of=lambda c: official[c],
        fetch_bars=lambda code, mkt, s, e: [_Bar(code)],
        end=date(2026, 7, 18),
        sleep=lambda _s: None,
        retry_delays=(),
        throttle_s=0.0,
    )
    assert [v[1] for v in validated] == ["005930"]  # 유효한 것만
    assert validated[0][2] == "삼성전자"  # 공식명으로 교체
    assert {f[0][1] for f in failed} == {"999999", "000100"}
    assert catalog.stored == ["005930"]  # 제외된 코드는 적재 안 함


def test_verify_retries_ohlcv_then_counts_failure():
    calls = {"n": 0}
    sleeps: list[float] = []

    def fetch(code, mkt, s, e):
        calls["n"] += 1
        raise RuntimeError("krx empty")

    validated, failed = verify_and_ingest(
        [("semi", "005930", "삼성전자", "kospi")],
        catalog=_FakeCatalog(),  # type: ignore[arg-type]
        name_of=lambda c: "삼성전자",
        fetch_bars=fetch,
        end=date(2026, 7, 18),
        sleep=sleeps.append,
        retry_delays=(1.0, 2.0),
        throttle_s=0.0,
    )
    assert validated == [] and len(failed) == 1
    assert calls["n"] == 3  # 최초 + 재시도 2
    assert sleeps == [1.0, 2.0]


def test_write_validated_roundtrips(tmp_path):
    out = tmp_path / "v.csv"
    write_validated([("semi", "005930", "삼성전자", "kospi")], out)
    text = out.read_text(encoding="utf-8")
    assert "theme_key,code,name,market" in text
    assert "semi,005930,삼성전자,kospi" in text
