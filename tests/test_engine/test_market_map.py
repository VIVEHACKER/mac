"""마켓 히트맵 테스트 — 순수 계산/색 램프/렌더/조립/CLI 배선."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from data.catalog import MarketDataCatalog
from data.models import PriceBar
from engine.market_map import build_market_map
from engine.market_map.compute import (
    MACRO_SCALE,
    PAPER,
    THEME_SCALE,
    MacroCell,
    MacroRow,
    ThemeRow,
    build_weeks,
    macro_change_cells,
    pct_pair,
    sector_rotation_cells,
    theme_row,
    week_monday,
    weekly_last_closes,
    weekly_returns_pct,
)
from engine.market_map.render import (
    render_macro_table,
    render_page,
    render_theme_table,
    render_ticker_chips,
)
from trader.cli import CORE_COMMANDS, build_parser

AS_OF = date(2026, 7, 10)  # 금요일 — 주 시작 월요일은 7/6


def monday_series(values: dict[str, float]) -> list[tuple[date, float]]:
    """{"2026-06-01": 100.0} 형태를 (date, close) 목록으로."""
    return sorted((date.fromisoformat(k), v) for k, v in values.items())


class TestPctPair:
    def test_none_is_paper(self):
        assert pct_pair(None, THEME_SCALE) == PAPER

    def test_zero_is_paper(self):
        assert pct_pair(0.0, THEME_SCALE) == PAPER

    def test_positive_ramp_thresholds(self):
        # n = pct/scale, 경계 0.05/0.15/0.35/0.65
        assert pct_pair(0.1, 4)[0] == "#FBF7EE"  # n=0.025
        assert pct_pair(0.4, 4)[0] == "#F4D5C7"  # n=0.10
        assert pct_pair(0.6, 4)[0] == "#D88A65"  # n=0.15 (경계는 다음 구간)
        assert pct_pair(2.0, 4)[0] == "#C96442"  # n=0.50
        assert pct_pair(3.0, 4)[0] == "#A44E30"  # n=0.75

    def test_negative_ramp_thresholds(self):
        assert pct_pair(-0.1, 4)[0] == "#FBF7EE"
        assert pct_pair(-0.4, 4)[0] == "#EFE6D6"
        assert pct_pair(-1.0, 4)[0] == "#BDB5A3"  # n=-0.25
        assert pct_pair(-2.0, 4)[0] == "#6B5F52"
        assert pct_pair(-3.0, 4)[0] == "#3D342C"

    def test_clamps_beyond_scale(self):
        assert pct_pair(100.0, MACRO_SCALE)[0] == "#A44E30"
        assert pct_pair(-100.0, MACRO_SCALE)[0] == "#3D342C"


class TestWeeks:
    def test_build_weeks_monday_aligned_ascending(self):
        weeks = build_weeks(AS_OF, 6)
        assert len(weeks) == 6
        assert weeks[-1] == date(2026, 7, 6)
        assert all(w.weekday() == 0 for w in weeks)
        assert all((b - a).days == 7 for a, b in zip(weeks, weeks[1:], strict=False))

    def test_build_weeks_rejects_zero(self):
        with pytest.raises(ValueError):
            build_weeks(AS_OF, 0)

    def test_week_monday(self):
        assert week_monday(date(2026, 7, 10)) == date(2026, 7, 6)
        assert week_monday(date(2026, 7, 6)) == date(2026, 7, 6)
        assert week_monday(date(2026, 7, 12)) == date(2026, 7, 6)  # 일요일도 같은 주


class TestWeeklyLastCloses:
    def test_last_close_in_week_wins_and_gaps_are_none(self):
        weeks = build_weeks(AS_OF, 2)  # 6/29, 7/6
        series = [
            (date(2026, 6, 29), 10.0),
            (date(2026, 7, 1), 11.0),  # 같은 주 뒤 데이터가 이긴다
            (date(2026, 7, 8), 20.0),
        ]
        assert weekly_last_closes(series, weeks) == [11.0, 20.0]
        assert weekly_last_closes([], weeks) == [None, None]

    def test_as_of_blocks_future_data(self):
        weeks = build_weeks(AS_OF, 1)  # 7/6 주
        series = [(date(2026, 7, 8), 95.0), (date(2026, 7, 11), 999.0)]
        assert weekly_last_closes(series, weeks, as_of=AS_OF) == [95.0]


class TestMacroChangeCells:
    def make_series(self) -> list[tuple[date, float]]:
        return monday_series(
            {
                "2026-05-04": 100.0,
                "2026-05-11": 100.0,
                "2026-05-18": 100.0,
                "2026-05-25": 100.0,
                "2026-06-01": 110.0,
                "2026-06-08": 121.0,
                "2026-06-15": 100.0,
                "2026-06-22": 100.0,
                "2026-06-29": 100.0,
                "2026-07-06": 90.0,
            }
        )

    def test_four_week_change_math_and_interp(self):
        weeks = build_weeks(AS_OF, 6)
        cells = macro_change_cells(self.make_series(), weeks, direction=+1, as_of=AS_OF)
        assert [c.pct if c else None for c in cells] == [10.0, 21.0, 0.0, 0.0, -9.1, -25.6]
        assert [c.interp if c else None for c in cells] == [
            "risk_on",
            "risk_on",
            "neutral",
            "neutral",
            "risk_off",
            "risk_off",
        ]

    def test_direction_inverts_interp(self):
        weeks = build_weeks(AS_OF, 6)
        cells = macro_change_cells(self.make_series(), weeks, direction=-1, as_of=AS_OF)
        assert cells[0].interp == "risk_off"  # +10% 인데 상승=risk_off 지표
        assert cells[-1].interp == "risk_on"

    def test_missing_base_yields_none(self):
        weeks = build_weeks(AS_OF, 2)
        series = monday_series({"2026-06-29": 100.0, "2026-07-06": 110.0})  # 4주 전 없음
        assert macro_change_cells(series, weeks, direction=+1) == [None, None]

    def test_min_base_guards_near_zero_spread(self):
        weeks = build_weeks(AS_OF, 1)
        series = monday_series({"2026-06-08": 0.02, "2026-07-06": 0.5})
        assert macro_change_cells(series, weeks, +1, min_base=0.05) == [None]
        ok = macro_change_cells(
            monday_series({"2026-06-08": 0.5, "2026-07-06": 0.6}), weeks, +1, min_base=0.05
        )
        assert ok[0].pct == 20.0

    def test_signed_change_preserves_inversion_regime(self):
        weeks = build_weeks(AS_OF, 1)
        # 역전 구간(둘 다 음수): -0.8 → -0.6 은 스티프닝(+0.2) — 부호가 +25% 여야 한다
        # (naive cur/base-1 은 -25% 로 부호 반전되어 risk_off 로 오분류)
        inverted = monday_series({"2026-06-08": -0.8, "2026-07-06": -0.6})
        cells = macro_change_cells(inverted, weeks, +1, min_base=0.05)
        assert cells[0].pct == 25.0
        assert cells[0].interp == "risk_on"
        # 0 관통: +0.5 → -0.5 = -200% (대규모 플래트닝) — 값 유지, 부호 정확
        crossing = monday_series({"2026-06-08": 0.5, "2026-07-06": -0.5})
        cells = macro_change_cells(crossing, weeks, +1, min_base=0.05)
        assert cells[0].pct == -200.0
        assert cells[0].interp == "risk_off"

    def test_signed_values(self):
        assert MacroCell(3.0, "risk_on").signed == 3.0
        assert MacroCell(3.0, "risk_off").signed == -3.0
        assert MacroCell(-3.0, "risk_off").signed == -3.0
        assert MacroCell(9.0, "neutral").signed == 0.0


class TestWeeklyReturnsAndThemes:
    def test_weekly_returns_pct(self):
        weeks = build_weeks(AS_OF, 2)
        series = monday_series({"2026-06-22": 100.0, "2026-06-29": 110.0, "2026-07-06": 99.0})
        returns = weekly_returns_pct(series, weeks)
        assert returns[0] == pytest.approx(10.0)
        assert returns[1] == pytest.approx(-10.0)

    def test_theme_row_averages_and_counts(self):
        weeks = build_weeks(AS_OF, 2)
        symbol_series = {
            "AAA": monday_series({"2026-06-22": 100.0, "2026-06-29": 102.0, "2026-07-06": 104.04}),
            "BBB": monday_series({"2026-06-22": 100.0, "2026-06-29": 104.0, "2026-07-06": 108.16}),
            "CCC": [],  # 데이터 없음 — n 에서 제외
        }
        row = theme_row("🤖 테스트", symbol_series, weeks)
        assert row.n == 2
        assert row.tickers == "AAA, BBB"
        assert row.series == [3.0, 3.0]
        assert row.avg == pytest.approx(3.0)

    def test_theme_row_empty(self):
        weeks = build_weeks(AS_OF, 2)
        row = theme_row("빈 테마", {}, weeks)
        assert row.n == 0
        assert row.series == [None, None]
        assert row.avg is None

    def test_sector_rotation_diff_and_deadband(self):
        weeks = build_weeks(AS_OF, 2)
        cyc = {
            "XLY": monday_series({"2026-06-22": 100.0, "2026-06-29": 103.0, "2026-07-06": 103.3})
        }
        dfs = {
            "XLP": monday_series({"2026-06-22": 100.0, "2026-06-29": 101.0, "2026-07-06": 101.2})
        }
        cells = sector_rotation_cells(cyc, dfs, weeks)
        assert cells[0].pct == pytest.approx(2.0)
        assert cells[0].interp == "risk_on"
        assert cells[1].interp == "neutral"  # 0.3 - 0.2 ≈ 0.1 < deadband

    def test_sector_rotation_classifies_on_raw_not_rounded(self):
        # 원시 diff 0.47(<0.5=neutral)이지만, 표시용 반올림값을 쓰면 0.3-(-0.2)=0.5(risk_on)로 뒤집힘
        weeks = build_weeks(AS_OF, 1)
        cyc = {"XLY": monday_series({"2026-06-29": 10000.0, "2026-07-06": 10026.0})}  # +0.26%
        dfs = {"XLP": monday_series({"2026-06-29": 10000.0, "2026-07-06": 9979.0})}  # -0.21%
        cells = sector_rotation_cells(cyc, dfs, weeks)
        assert cells[0].interp == "neutral"
        assert cells[0].pct == pytest.approx(0.5)  # 표시값은 반올림


class TestRender:
    def test_ticker_chips_colors_and_format(self):
        html = render_ticker_chips([("코스피", 6807.0, 7476.0), ("VIX", 16.46, 15.03)])
        assert "코스피" in html and "6,807" in html
        assert "#2563EB" in html  # 하락 파랑
        assert "#D92F2F" in html  # 상승 빨강
        assert "16.46" in html

    def test_macro_table_cells_titles_empty_dot(self):
        weeks = build_weeks(AS_OF, 2)
        rows = [MacroRow("KOSPI", [MacroCell(10.0, "risk_on"), None])]
        html = render_macro_table(rows, weeks)
        assert "4주Δ +10.0%" in html
        assert "·" in html  # 빈 셀
        assert 'class="avgcol"' in html
        assert "#A44E30" in html  # 웜 램프 적용 (10/12 = 0.83 → 최심 구간)

    def test_macro_avg_cell_value_is_avg_pct_color_is_avg_signed(self):
        # 값은 4주Δ 단순평균(+6.0), 색은 risk 방향 평균(-4.0 → 쿨 램프) — 분리 규약
        weeks = build_weeks(AS_OF, 2)
        rows = [MacroRow("DXY", [MacroCell(10.0, "risk_off"), MacroCell(2.0, "risk_on")])]
        html = render_macro_table(rows, weeks)
        assert (
            '<td class="avgcol" style="background:#BDB5A3;color:#1A1612;font-weight:800">+6.0</td>'
            in html
        )

    def test_avg_dash_is_visible(self):
        # 데이터 없는 행의 '—' 가 흰 배경/흰 글자로 사라지지 않아야 한다
        weeks = build_weeks(AS_OF, 1)
        html = render_macro_table([MacroRow("빈 지표", [None])], weeks)
        assert 'style="background:#fff;color:#5A6672">—</td>' in html

    def test_theme_table_partial_last_week_marked_wtd(self):
        weeks = build_weeks(AS_OF, 2)
        rows = [ThemeRow("🤖 AI", 1, "NVDA", [1.0, 2.0])]
        html = render_theme_table(rows, weeks, partial_last=True)
        assert "W2*" in html
        assert "주중(WTD) +2.0%" in html
        assert "평균 5d +1.0%" in html  # 마지막 주가 아닌 셀은 그대로

    def test_theme_table_escapes_and_counts(self):
        weeks = build_weeks(AS_OF, 1)
        rows = [ThemeRow("🎬 미디어&엔터", 2, "A, B", [1.0])]
        html = render_theme_table(rows, weeks)
        assert "미디어&amp;엔터" in html
        assert "(2)" in html

    def test_render_page_smoke(self):
        weeks = build_weeks(AS_OF, 2)
        html = render_page(
            as_of=AS_OF,
            generated_at=datetime(2026, 7, 10, 9, 0, 0),
            chips=[("코스피", 6807.0, 7476.0)],
            weeks=weeks,
            macro_rows=[MacroRow("KOSPI", [MacroCell(1.0, "risk_on"), None])],
            us_rows=[ThemeRow("🤖 AI", 1, "NVDA", [1.0, None])],
            kr_rows=[],
            dashboard_url="http://localhost:8501",
        )
        assert "돈이 어디로 흐르는지" in html
        assert 'id="macro"' in html and 'id="us-themes"' in html
        assert "국장 (KR)" not in html  # kr_rows 비면 섹션 생략
        assert "scrollHeatsRight" in html
        assert "http://localhost:8501" in html


def _seed_catalog(db_path, symbols: list[str], weeks: list[date]) -> None:
    catalog = MarketDataCatalog(db_path)
    bars: list[PriceBar] = []
    for symbol in symbols:
        price = 100.0
        for monday in [weeks[0] - timedelta(weeks=2), weeks[0] - timedelta(weeks=1), *weeks]:
            price *= 1.02
            bars.append(
                PriceBar(
                    symbol=symbol,
                    market="us",
                    source_symbol=symbol,
                    ts=monday + timedelta(days=2),  # 수요일 종가
                    open=price,
                    high=price,
                    low=price,
                    close=price,
                    volume=1000.0,
                )
            )
    catalog.put_bars(bars)


class TestBuildMarketMap:
    def test_full_pipeline_with_injected_fetchers(self, tmp_path):
        weeks = build_weeks(AS_OF, 4)
        db = tmp_path / "cat.duckdb"
        _seed_catalog(db, ["NVDA", "MSFT", "TLT"], weeks)

        base = weeks[0] - timedelta(weeks=5)
        long_series = monday_series(
            {(base + timedelta(weeks=i)).isoformat(): 100.0 + 2.0 * i for i in range(10)}
        )

        def fake_closes(symbols, start):
            data = {
                "^KS11": long_series,
                "091160.KS": long_series,
                "XLY": long_series,
                "XLP": long_series,
            }
            return {s: data.get(s, []) for s in symbols}

        def fake_fred(series_id, start):
            return long_series

        html, stats = build_market_map(
            weeks_count=4,
            catalog_db=db,
            as_of=AS_OF,
            now=datetime(2026, 7, 10, 9, 0),
            fetch_closes=fake_closes,
            fetch_fred=fake_fred,
        )
        assert stats["weeks"] == 4
        assert stats["us_themes"] >= 1  # NVDA/MSFT → AI 테마
        assert stats["kr_themes"] == 1  # 091160.KS 만 데이터 존재
        assert stats["chips"] == 1  # ^KS11 만
        assert stats["macro_rows"] == 10  # 9 지표 + 섹터 로테이션
        assert "KOSPI" in html and "수익률곡선" in html and "섹터 로테이션" in html
        assert "🤖 AI/빅테크/반도체" in html
        assert "NVDA" in html  # 테마 툴팁 종목 나열
        assert "W4*" in html  # as_of 금요일 → 마지막 주 WTD (US 세션 미종료 보수 처리)
        assert stats["catalog_last_bar"] == "2026-07-08"  # 테마 심볼 수요일 종가 (TLT 제외)

    def test_offline_uses_catalog_only(self, tmp_path):
        weeks = build_weeks(AS_OF, 4)
        db = tmp_path / "cat.duckdb"
        _seed_catalog(db, ["NVDA"], weeks)

        def must_not_call(*args, **kwargs):
            raise AssertionError("offline 모드에서 네트워크 fetch 호출 금지")

        html, stats = build_market_map(
            weeks_count=4,
            catalog_db=db,
            offline=True,
            as_of=AS_OF,
            fetch_closes=must_not_call,
            fetch_fred=must_not_call,
        )
        assert stats["us_themes"] == 1
        assert stats["kr_themes"] == 0
        assert stats["chips"] == 0
        assert "국장 (KR)" not in html


class TestBuildFallbacks:
    def test_catalog_macro_falls_back_to_yfinance(self, tmp_path):
        # 카탈로그가 없으면 TLT(카탈로그 소스 매크로)를 yfinance 요청 목록에 추가해야 한다
        weeks = build_weeks(AS_OF, 4)
        base = weeks[0] - timedelta(weeks=5)
        long_series = monday_series(
            {(base + timedelta(weeks=i)).isoformat(): 100.0 + 2.0 * i for i in range(10)}
        )
        requested: list[str] = []

        def fake_closes(symbols, start):
            requested.extend(symbols)
            return {s: (long_series if s == "TLT" else []) for s in symbols}

        html, stats = build_market_map(
            weeks_count=4,
            catalog_db=tmp_path / "missing.duckdb",
            as_of=AS_OF,
            now=datetime(2026, 7, 10, 9, 0),
            fetch_closes=fake_closes,
            fetch_fred=lambda series_id, start: [],
        )
        assert "TLT" in requested
        assert "TLT (장기국채) · 4주Δ" in html  # 폴백 시계열로 셀이 채워짐
        assert stats["catalog_symbols"] == 0
        assert stats["catalog_last_bar"] is None


class TestFetchYfinance:
    """스텁 yfinance 모듈로 fetch 경로(배치/재시도/단일 폴백/MultiIndex 언랩) 검증."""

    @pytest.fixture(autouse=True)
    def _no_sleep(self, monkeypatch):
        import engine.market_map.fetch as fetch_mod

        monkeypatch.setattr(fetch_mod.time, "sleep", lambda _s: None)

    def _multiindex_frame(self, symbols: list[str]):
        import pandas as pd

        idx = pd.to_datetime(["2026-07-06", "2026-07-07"])
        cols = pd.MultiIndex.from_product([symbols, ["Open", "Close"]])
        data = [[1.0, 10.0] * len(symbols), [1.0, 11.0] * len(symbols)]
        return pd.DataFrame(data, index=idx, columns=cols)

    def test_batch_multiindex_extraction(self, monkeypatch):
        import sys

        from engine.market_map.fetch import fetch_yfinance_closes

        frame = self._multiindex_frame(["AAA", "BBB"])

        class StubYF:
            def download(self, tickers, **kwargs):
                return frame

        monkeypatch.setitem(sys.modules, "yfinance", StubYF())
        result = fetch_yfinance_closes(["AAA", "BBB"], date(2026, 6, 1))
        assert result["AAA"] == [(date(2026, 7, 6), 10.0), (date(2026, 7, 7), 11.0)]
        assert result["BBB"][0][1] == 10.0

    def test_dead_batch_falls_back_per_symbol_and_unwraps(self, monkeypatch):
        # 배치가 전멸해도 단일심볼 폴백이 MultiIndex 프레임을 벗겨 데이터를 살려야 한다
        import sys

        from engine.market_map.fetch import fetch_yfinance_closes

        calls = {"batch": 0, "single": 0}
        outer = self

        class StubYF:
            def download(self, tickers, **kwargs):
                if isinstance(tickers, list):
                    calls["batch"] += 1
                    raise RuntimeError("rate limited")
                calls["single"] += 1
                return outer._multiindex_frame([tickers])

        monkeypatch.setitem(sys.modules, "yfinance", StubYF())
        result = fetch_yfinance_closes(["AAA", "BBB"], date(2026, 6, 1))
        assert calls["batch"] == 3  # 첫 시도 + 백오프 재시도 2회
        assert calls["single"] == 2
        assert result["AAA"] == [(date(2026, 7, 6), 10.0), (date(2026, 7, 7), 11.0)]
        assert result["BBB"][-1] == (date(2026, 7, 7), 11.0)

    def test_partial_batch_failure_retries_only_empty_symbols(self, monkeypatch):
        # 배치가 일부 심볼만 NaN 으로 돌려줘도 그 심볼만 단일 재시도해야 한다
        import sys

        import pandas as pd

        from engine.market_map.fetch import fetch_yfinance_closes

        nan = float("nan")
        idx = pd.to_datetime(["2026-07-06", "2026-07-07"])
        cols = pd.MultiIndex.from_product([["AAA", "BBB"], ["Open", "Close"]])
        batch_frame = pd.DataFrame(
            [[1.0, 10.0, nan, nan], [1.0, 11.0, nan, nan]], index=idx, columns=cols
        )
        calls = {"batch": 0, "single": []}
        outer = self

        class StubYF:
            def download(self, tickers, **kwargs):
                if isinstance(tickers, list):
                    calls["batch"] += 1
                    return batch_frame
                calls["single"].append(tickers)
                return outer._multiindex_frame([tickers])

        monkeypatch.setitem(sys.modules, "yfinance", StubYF())
        result = fetch_yfinance_closes(["AAA", "BBB"], date(2026, 6, 1))
        assert calls["batch"] == 1
        assert calls["single"] == ["BBB"]
        assert result["AAA"][0] == (date(2026, 7, 6), 10.0)
        assert result["BBB"] == [(date(2026, 7, 6), 10.0), (date(2026, 7, 7), 11.0)]


class TestCliWiring:
    def test_weeks_must_be_positive(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["market-map", "--weeks", "0"])

    def test_market_map_registered(self):
        assert "market-map" in CORE_COMMANDS
        parser = build_parser()
        parsed = parser.parse_args(["market-map", "--weeks", "8", "--offline"])
        assert parsed.command == "market-map"
        assert parsed.weeks == 8
        assert parsed.offline is True
        assert parsed.out.name == "market_map.html"

    def test_main_dispatch_writes_file(self, tmp_path, monkeypatch):
        import engine.market_map as mm
        import trader.cli as cli

        def fake_build(**kwargs):
            stats = {
                "weeks": 1,
                "macro_rows": 0,
                "macro_rows_with_data": 0,
                "us_themes": 0,
                "kr_themes": 0,
                "chips": 0,
                "catalog_symbols": 0,
                "catalog_last_bar": None,
            }
            return "<html>ok</html>", stats

        monkeypatch.setattr(mm, "build_market_map", fake_build)
        out = tmp_path / "map.html"
        rc = cli.main(["market-map", "--offline", "--out", str(out)])
        assert rc == 0
        assert out.read_text(encoding="utf-8") == "<html>ok</html>"
