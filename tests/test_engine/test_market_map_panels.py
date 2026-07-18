"""market-map 확장 패널 — 예측 원장 파싱, OOS 마킹, 렌더/딥링크, 하위산업 드릴다운."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from engine.market_map import build_market_map
from engine.market_map.compute import ThemeRow
from engine.market_map.panels import (
    MacroCard,
    RateCard,
    SelectionPanel,
    SelectionRow,
    _mark_at,
    discover_oos_ledger,
    load_forecast_panel,
    load_oos_panel,
    oos_symbols,
    parse_macro_cards,
    parse_rate_cards,
)
from engine.market_map.render import (
    dashboard_deeplink,
    render_forecast_panel,
    render_page,
    render_selection_panel,
    render_theme_table,
)
from engine.market_map.themes import KR_THEMES, US_SUBTHEMES, US_THEMES

AS_OF = date(2026, 7, 17)


# ── 예측 원장 파싱 ───────────────────────────────────────────────────────────


def _rate_line(**kw) -> dict:
    base = {
        "kind": "rate_forecast",
        "recorded_at": "2026-07-08",
        "region": "us",
        "meeting": "2026-07-29",
        "current_rate": 3.75,
        "probs": {"cut": 0.09, "hold": 0.70, "hike": 0.21},
        "modal": "hold",
        "superseded": False,
        "status": "pending",
    }
    base.update(kw)
    return base


class TestRateCards:
    def test_picks_latest_pending_future_meeting(self):
        lines = [
            _rate_line(meeting="2026-06-17", status="scored", recorded_at="2026-06-12"),
            _rate_line(),
            {
                "kind": "rate_score",
                "region": "us",
                "meeting": "2026-06-17",
                "modal_hit": True,
                "brier": 0.2537,
            },
        ]
        cards = parse_rate_cards(lines, AS_OF)
        assert len(cards) == 1
        card = cards[0]
        assert card.meeting == "2026-07-29" and card.pending
        assert card.n_scored == 1 and card.hit_rate == 1.0
        assert abs((card.mean_brier or 0) - 0.2537) < 1e-9

    def test_forced_revision_wins_even_if_marked_superseded(self):
        # 프로듀서는 force 교체본에 superseded=True 를 찍는다 — 나중 줄이 권위본.
        lines = [
            _rate_line(probs={"cut": 0.5, "hold": 0.4, "hike": 0.1}, recorded_at="2026-07-01"),
            _rate_line(
                probs={"cut": 0.1, "hold": 0.8, "hike": 0.1},
                recorded_at="2026-07-08",
                superseded=True,
            ),
        ]
        cards = parse_rate_cards(lines, AS_OF)
        assert cards[0].probs["hold"] == 0.8  # 교체본 채택
        assert cards[0].recorded_at == "2026-07-08"

    def test_scored_meeting_not_pending_despite_status_field(self):
        # append-only: forecast 행 status 는 영원히 "pending" — score 행 존재로 판정
        lines = [
            _rate_line(meeting="2026-06-17"),
            {"kind": "rate_score", "region": "us", "meeting": "2026-06-17", "modal_hit": True},
        ]
        cards = parse_rate_cards(lines, AS_OF)
        assert cards[0].meeting == "2026-06-17"
        assert not cards[0].pending  # '지난 회의 기록' 라벨 대상

    def test_decided_but_unscored_meeting_stays_pending(self):
        # 회의는 지났지만 아직 채점 전 (예: 어제 금통위) → 결과 대기로 표시
        lines = [_rate_line(meeting="2026-07-16", region="kr")]
        cards = parse_rate_cards(lines, AS_OF)
        assert cards[0].meeting == "2026-07-16" and cards[0].pending

    def test_regions_sorted_us_kr(self):
        lines = [_rate_line(), _rate_line(region="kr", meeting="2026-08-27")]
        cards = parse_rate_cards(lines, AS_OF)
        assert [c.region for c in cards] == ["us", "kr"]


class TestMacroCards:
    def test_pending_preferred_and_track_record(self):
        lines = [
            {
                "kind": "forecast",
                "region": "kr",
                "series_id": "901Y009/0",
                "label": "소비자물가지수 (CPI)",
                "target": "2026-06",
                "forecast_mom": 0.29,
                "status": "scored",
                "recorded_at": "2026-06-07",
            },
            {
                "kind": "forecast",
                "region": "kr",
                "series_id": "901Y009/0",
                "label": "소비자물가지수 (CPI)",
                "target": "2026-07",
                "forecast_mom": 0.1756,
                "forecast_yoy": 3.1588,
                "pi80": [-0.13, 0.479],
                "skill_pct": 22.02,
                "status": "pending",
                "recorded_at": "2026-07-07",
            },
            {
                "kind": "score",
                "region": "kr",
                "series_id": "901Y009/0",
                "target": "2026-06",
                "abs_error_mom": 0.2349,
                "in_pi80": True,
            },
        ]
        cards = parse_macro_cards(lines, AS_OF)
        assert len(cards) == 1
        card = cards[0]
        assert card.target == "2026-07" and card.pending
        assert card.pi80 == (-0.13, 0.479)
        assert card.n_scored == 1 and card.pi80_coverage == 1.0
        assert abs((card.mae or 0) - 0.2349) < 1e-9

    def test_all_scored_targets_fall_back_to_past_record(self):
        # 5월 US CPI 케이스: 전부 채점됨 → 최신 것을 '지난 발표 기록'으로 (pending 아님)
        lines = [
            {
                "kind": "forecast",
                "region": "us",
                "series_id": "CPIAUCSL",
                "label": "Headline CPI",
                "target": "2026-05",
                "forecast_mom": 0.4172,
                "status": "pending",  # append-only 라 필드는 그대로지만
                "recorded_at": "2026-06-07",
            },
            {
                "kind": "score",
                "region": "us",
                "series_id": "CPIAUCSL",
                "target": "2026-05",
                "abs_error_mom": 0.1,
                "in_pi80": True,
            },
        ]
        cards = parse_macro_cards(lines, AS_OF)
        assert len(cards) == 1
        assert not cards[0].pending  # score 행이 있으므로 pending 아님

    def test_load_forecast_panel_missing_dir_is_none(self, tmp_path):
        assert load_forecast_panel(tmp_path / "nope", AS_OF) is None

    def test_load_forecast_panel_reads_jsonl(self, tmp_path):
        (tmp_path / "rate_ledger.jsonl").write_text(
            json.dumps(_rate_line()) + "\n", encoding="utf-8"
        )
        (tmp_path / "forecast_ledger.jsonl").write_text("", encoding="utf-8")
        panel = load_forecast_panel(tmp_path, AS_OF)
        assert panel is not None and len(panel.rates) == 1 and panel.macros == []


# ── forward-OOS 원장 ─────────────────────────────────────────────────────────


def _write_ledger(tmp_path) -> Path:
    path = tmp_path / "out" / "paper-oos-ledger-test_strategy.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    entries = [
        {
            "rebal_date": "2026-06-05",
            "strategy_id": "test_strategy",
            "weights": {"AAA": 0.5, "BBB": 0.5},
            "entry_prices": {"AAA": 100.0, "BBB": 200.0},
            "benchmark_symbol": "SPY",
            "benchmark_price": 700.0,
        },
        {
            "rebal_date": "2026-07-02",
            "strategy_id": "test_strategy",
            "weights": {"AAA": 1.0},
            "entry_prices": {"AAA": 110.0},
            "benchmark_symbol": "SPY",
            "benchmark_price": 707.0,
        },
    ]
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
    return path


CLOSES = {
    "AAA": [(date(2026, 7, 1), 109.0), (date(2026, 7, 2), 110.0), (date(2026, 7, 16), 121.0)],
    "BBB": [(date(2026, 7, 2), 210.0), (date(2026, 7, 16), 220.0)],
    "SPY": [(date(2026, 7, 2), 707.0), (date(2026, 7, 16), 721.14)],
}


class TestOOSPanel:
    def test_mark_at_uses_last_close_on_or_before(self):
        assert _mark_at(CLOSES["AAA"], date(2026, 7, 2)) == 110.0
        assert _mark_at(CLOSES["AAA"], date(2026, 7, 1)) == 109.0
        assert _mark_at(CLOSES["AAA"], date(2026, 6, 30)) is None
        assert _mark_at(None, AS_OF) is None

    def test_mark_at_rejects_stale_observation(self):
        # 얼려붙은 가격으로 뒤 리밸일을 채점하면 안 됨 — 신선도 한계 초과 시 None
        series = [(date(2026, 6, 1), 100.0)]
        assert _mark_at(series, date(2026, 6, 5), max_age_days=5) == 100.0
        assert _mark_at(series, date(2026, 7, 2), max_age_days=5) is None
        assert _mark_at(series, date(2026, 7, 2)) == 100.0  # 한계 미지정이면 종전 동작

    def test_oos_start_date_is_first_rebal(self, tmp_path):
        from engine.market_map.panels import oos_start_date

        path = _write_ledger(tmp_path)
        assert oos_start_date(path) == date(2026, 6, 5)
        assert oos_start_date(tmp_path / "no.jsonl") is None

    def test_discover_and_symbols(self, tmp_path):
        path = _write_ledger(tmp_path)
        assert discover_oos_ledger(tmp_path) == path
        assert oos_symbols(path) == ["AAA", "BBB", "SPY"]
        assert oos_symbols(tmp_path / "missing.jsonl") == []

    def test_closed_period_marks_at_next_rebal(self, tmp_path):
        path = _write_ledger(tmp_path)
        panel = load_oos_panel(path, CLOSES, AS_OF)
        assert panel is not None
        assert panel.n_entries == 2 and panel.n_closed == 1
        first, second = panel.rows
        # 폐쇄 기간: AAA +10%, BBB +5% → 포트 +7.5%, SPY +1% → 초과 +6.5%p
        assert first.closed and first.mark_date == date(2026, 7, 2)
        assert abs(first.port_pct - 7.5) < 1e-9
        assert abs(first.bench_pct - 1.0) < 1e-9
        assert abs(first.excess_pct - 6.5) < 1e-9
        # 진행 기간: as_of 마킹 (AAA 121/110 = +10%, SPY 721.14/707 = +2%)
        assert not second.closed and second.mark_date == AS_OF
        assert abs(second.port_pct - 10.0) < 1e-9
        assert abs(second.bench_pct - 2.0) < 1e-6
        # 누적은 폐쇄 기간만 (n=1)
        assert abs(panel.cum_port_pct - 7.5) < 1e-9
        assert abs(panel.cum_excess_pct - 6.5) < 1e-9

    def test_missing_ledger_is_none(self, tmp_path):
        assert load_oos_panel(tmp_path / "no.jsonl", {}, AS_OF) is None
        assert load_oos_panel(None, {}, AS_OF) is None


# ── 렌더 / 딥링크 / 드릴다운 ─────────────────────────────────────────────────


def test_dashboard_deeplink_urlencodes():
    url = dashboard_deeplink("http://localhost:8501/", ticker="BRK-B", market="us")
    assert url == "http://localhost:8501/?tab=recommender&ticker=BRK-B&market=us"


def test_us_subthemes_are_subsets_of_parents():
    parents = {t.name: set(t.symbols) for t in US_THEMES}
    for parent_name, subs in US_SUBTHEMES.items():
        assert parent_name in parents
        for sub in subs:
            missing = set(sub.symbols) - parents[parent_name]
            assert not missing, f"{parent_name}/{sub.name}: {missing}"


def test_kr_themes_have_multiple_symbols():
    multi = [t for t in KR_THEMES if len(t.symbols) >= 2]
    assert len(multi) >= 10  # ETF 프록시 단독에서 개별종목 확장으로


def test_render_theme_table_subrows_hidden_with_toggle():
    weeks = [date(2026, 7, 6), date(2026, 7, 13)]
    parent = ThemeRow(name="🤖 테마", n=2, tickers="AAA, BBB", series=[1.0, None])
    sub = ThemeRow(name="하위", n=1, tickers="AAA", series=[2.0, None])
    html = render_theme_table([parent], weeks, sub_rows={"🤖 테마": [sub]}, group_prefix="us")
    assert 'class="has-subs"' in html and "toggleSubs('us0')" in html
    assert 'class="subrow sub-us0"' in html and "└ 하위" in html
    # sub_rows 없는 행은 토글 없음
    html2 = render_theme_table([parent], weeks)
    assert "has-subs" not in html2


def _selection_panel() -> SelectionPanel:
    return SelectionPanel(
        strategy_id="aqr_top7_cap20_trail10",
        top_n=7,
        universe_size=106,
        asof=datetime(2026, 6, 27),
        rows=[
            SelectionRow(
                rank=1,
                ticker="NVDA",
                action="BUY",
                band="high",
                score=78.0,
                price=190.0,
                target_entry=185.0,
                stop_loss=170.0,
                target_exit=230.0,
                in_top_n=True,
            ),
            SelectionRow(
                rank=8,
                ticker="BRK-B",
                action="HOLD",
                band="medium",
                score=55.0,
                price=500.0,
                target_entry=None,
                stop_loss=None,
                target_exit=None,
                in_top_n=False,
            ),
        ],
    )


def test_render_selection_panel_links_and_topn():
    html = render_selection_panel(_selection_panel(), "http://localhost:8501")
    assert "?tab=recommender&amp;ticker=NVDA&amp;market=us" in html or (
        "?tab=recommender&ticker=NVDA&market=us" in html
    )
    assert 'class="topn"' in html and 'class="badge buy"' in html
    assert "185.00" in html and "—" in html  # entry 없는 행은 대시


def test_render_forecast_panel_bars_and_track():
    panel_rates = [
        RateCard(
            region="us",
            meeting="2026-07-29",
            current_rate=3.75,
            probs={"cut": 0.09, "hold": 0.70, "hike": 0.21},
            modal="hold",
            recorded_at="2026-07-08",
            pending=True,
            n_scored=1,
            hit_rate=1.0,
            mean_brier=0.2537,
        )
    ]
    panel_macros = [
        MacroCard(
            region="kr",
            label="소비자물가지수 (CPI)",
            target="2026-07",
            forecast_mom=0.1756,
            forecast_yoy=3.1588,
            pi80=(-0.13, 0.479),
            skill_pct=22.02,
            recorded_at="2026-07-07",
            pending=True,
            n_scored=4,
            mae=0.18,
            pi80_coverage=1.0,
        )
    ]
    from engine.market_map.panels import ForecastPanel

    html = render_forecast_panel(ForecastPanel(rates=panel_rates, macros=panel_macros))
    assert "FOMC" in html and "width:70%" in html and "Brier 0.254" in html
    assert "PI80" in html and "MAE 0.18%p" in html


def test_render_page_panel_sections_and_nav(tmp_path):
    ledger = _write_ledger(tmp_path)
    oos = load_oos_panel(ledger, CLOSES, AS_OF)
    weeks = [date(2026, 7, 6), date(2026, 7, 13)]
    html = render_page(
        as_of=AS_OF,
        generated_at=datetime(2026, 7, 17, 9, 0, 0),
        chips=[],
        weeks=weeks,
        macro_rows=[],
        us_rows=[],
        kr_rows=[],
        selection=_selection_panel(),
        oos=oos,
        forecasts=None,
    )
    assert 'id="selection"' in html and 'id="oos"' in html
    assert 'href="#selection"' in html and 'href="#oos"' in html
    assert 'id="forecast"' not in html  # 없는 패널 섹션/네비는 생략
    assert "진행 (MTM)" in html and "폐쇄" in html


def test_build_market_map_wires_panels(tmp_path):
    ledger = _write_ledger(tmp_path)
    copilot_out = tmp_path / "copilot"
    copilot_out.mkdir()
    (copilot_out / "rate_ledger.jsonl").write_text(
        json.dumps(_rate_line()) + "\n", encoding="utf-8"
    )

    def fake_closes(symbols, start, end=None):
        return {s: CLOSES.get(s, []) for s in symbols}

    html, stats = build_market_map(
        weeks_count=2,
        catalog_db=tmp_path / "missing.duckdb",
        as_of=AS_OF,
        fetch_closes=fake_closes,
        fetch_fred=lambda series, start: [],
        now=datetime(2026, 7, 17, 9, 0, 0),
        oos_ledger=ledger,
        copilot_out=copilot_out,
        with_selection=False,
    )
    assert stats["oos_entries"] == 2 and stats["oos_closed"] == 1
    assert stats["forecast_cards"] == 1
    assert stats["selection_rows"] == 0
    assert 'id="oos"' in html and 'id="forecast"' in html


def test_rate_card_prefers_nearest_upcoming_meeting():
    lines = [
        _rate_line(meeting="2026-09-16", recorded_at="2026-07-10"),
        _rate_line(meeting="2026-07-29", recorded_at="2026-07-01"),
        _rate_line(meeting="2026-07-29", recorded_at="2026-07-08"),
    ]
    cards = parse_rate_cards(lines, AS_OF)
    assert cards[0].meeting == "2026-07-29"  # 먼 회의(09-16)가 아니라 가장 가까운 회의
    assert cards[0].recorded_at == "2026-07-08"  # 그 회의의 최신 기록


def test_build_fetches_oos_marks_beyond_heatmap_window(tmp_path):
    """원장이 히트맵 창보다 오래돼도 폐쇄 기간이 잘리면 안 된다 — OOS 심볼은 T0 창으로 수집."""
    ledger = tmp_path / "out" / "paper-oos-ledger-old.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    entries = [
        {
            "rebal_date": "2026-01-09",
            "strategy_id": "old",
            "weights": {"AAA": 1.0},
            "entry_prices": {"AAA": 100.0},
            "benchmark_symbol": "SPY",
            "benchmark_price": 700.0,
        },
        {
            "rebal_date": "2026-02-06",
            "strategy_id": "old",
            "weights": {"AAA": 1.0},
            "entry_prices": {"AAA": 105.0},
            "benchmark_symbol": "SPY",
            "benchmark_price": 703.0,
        },
    ]
    ledger.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
    old_closes = {
        "AAA": [(date(2026, 2, 6), 105.0), (date(2026, 7, 16), 120.0)],
        "SPY": [(date(2026, 2, 6), 703.0), (date(2026, 7, 16), 721.14)],
    }
    calls: list[tuple[tuple[str, ...], date]] = []

    def fake_closes(symbols, start, end=None):
        calls.append((tuple(sorted(symbols)), start))
        return {s: old_closes.get(s, []) for s in symbols}

    _html, stats = build_market_map(
        weeks_count=2,
        catalog_db=tmp_path / "missing.duckdb",
        as_of=AS_OF,
        fetch_closes=fake_closes,
        fetch_fred=lambda series, start: [],
        now=datetime(2026, 7, 18, 9, 0, 0),
        oos_ledger=ledger,
        copilot_out=tmp_path / "no-copilot",
        with_selection=False,
    )
    # 폐쇄 기간(01-09→02-06)이 살아 있어야 한다 — 창 밖이라고 사라지면 회귀
    assert stats["oos_entries"] == 2 and stats["oos_closed"] == 1
    # OOS 심볼 수집은 원장 T0(-버퍼) 이전까지 거슬러 요청됐어야 한다
    oos_calls = [start for symbols, start in calls if "SPY" in symbols]
    assert oos_calls and min(oos_calls) <= date(2026, 1, 2)


# ── codex 2차 리뷰 회귀 ──────────────────────────────────────────────────────


def test_discover_ledger_anchors_on_config_default(tmp_path):
    """원장이 여럿이면 글롭 순서가 아니라 config default 전략으로 앵커."""
    out = tmp_path / "out"
    out.mkdir()
    entry = {
        "rebal_date": "2026-06-05",
        "strategy_id": "x",
        "weights": {"AAA": 1.0},
        "entry_prices": {"AAA": 1.0},
        "benchmark_symbol": "SPY",
        "benchmark_price": 1.0,
    }
    # 알파벳상 'aqr...' 보다 앞서는 미끼 원장
    (out / "paper-oos-ledger-aaa_decoy.jsonl").write_text(
        json.dumps(entry) + "\n", encoding="utf-8"
    )
    (out / "paper-oos-ledger-aqr_top7_cap20_trail10_pit110.jsonl").write_text(
        json.dumps(entry) + "\n", encoding="utf-8"
    )
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "validated_strategies.json").write_text(
        json.dumps({"default": "aqr_top7_cap20_trail10", "strategies": {}}),
        encoding="utf-8",
    )
    picked = discover_oos_ledger(tmp_path)
    assert picked is not None
    assert picked.name == "paper-oos-ledger-aqr_top7_cap20_trail10_pit110.jsonl"


def test_discover_ledger_ambiguous_without_config_returns_none(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    for name in ("a", "b"):
        (out / f"paper-oos-ledger-{name}.jsonl").write_text("{}", encoding="utf-8")
    assert discover_oos_ledger(tmp_path) is None  # 명시 지정 요구


def test_strategy_backtest_excess_prefix_match(tmp_path):
    from engine.market_map.panels import strategy_backtest_excess

    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "validated_strategies.json").write_text(
        json.dumps(
            {
                "default": "aqr_top7_cap20_trail10",
                "strategies": {
                    "aqr_top7_cap20_trail10": {"avg_excess_after_cost": 0.074},
                },
            }
        ),
        encoding="utf-8",
    )
    assert strategy_backtest_excess("aqr_top7_cap20_trail10_pit110", tmp_path) == 0.074
    assert strategy_backtest_excess("unknown_strategy", tmp_path) is None


def test_strategy_pbo_reads_config(tmp_path):
    from engine.market_map.panels import strategy_pbo

    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "validated_strategies.json").write_text(
        json.dumps({"strategies": {"aqr_top7_cap20_trail10": {"pbo": 0.39}}}),
        encoding="utf-8",
    )
    assert strategy_pbo("aqr_top7_cap20_trail10_pit110", tmp_path) == 0.39
    assert strategy_pbo("nope", tmp_path) is None


def test_oos_panel_no_lookahead_past_asof(tmp_path):
    """과거 as_of 재생성 시 미래 리밸이 기간을 폐쇄하면 안 된다 (no-lookahead)."""
    path = _write_ledger(tmp_path)  # 리밸 06-05, 07-02
    asof_past = date(2026, 6, 20)
    closes = {
        "AAA": [(date(2026, 6, 19), 104.0), (date(2026, 7, 2), 110.0)],
        "BBB": [(date(2026, 6, 19), 206.0), (date(2026, 7, 2), 210.0)],
        "SPY": [(date(2026, 6, 19), 705.0), (date(2026, 7, 2), 707.0)],
    }
    panel = load_oos_panel(path, closes, asof_past)
    assert panel is not None
    assert panel.n_entries == 1 and panel.n_closed == 0  # 07-02 리밸은 미래 → 제외
    row = panel.rows[0]
    assert not row.closed and row.mark_date == asof_past
    assert abs(row.port_pct - (0.5 * 4.0 + 0.5 * 3.0)) < 1e-9  # 06-19 마크 기준 MTM


def test_forecast_panel_filters_future_records(tmp_path):
    """과거 as_of 재생성 시 그 이후 기록/채점 행은 보이면 안 된다."""
    lines = [
        _rate_line(meeting="2026-06-17", recorded_at="2026-06-12"),
        _rate_line(meeting="2026-07-29", recorded_at="2026-07-08"),
        {
            "kind": "rate_score",
            "region": "us",
            "meeting": "2026-06-17",
            "modal_hit": True,
            "brier": 0.25,
            "scored_at": "2026-07-08",
        },
    ]
    (tmp_path / "rate_ledger.jsonl").write_text(
        "\n".join(json.dumps(r) for r in lines) + "\n", encoding="utf-8"
    )
    panel = load_forecast_panel(tmp_path, date(2026, 6, 15))
    assert panel is not None and len(panel.rates) == 1
    card = panel.rates[0]
    assert card.meeting == "2026-06-17" and card.pending  # 미래 기록/채점 모두 배제
    assert card.n_scored == 0
