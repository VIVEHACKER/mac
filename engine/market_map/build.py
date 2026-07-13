"""마켓 히트맵 조립 — 카탈로그/yfinance/FRED 데이터를 모아 페이지 HTML 생성.

fetch 계층은 전부 주입 가능(fetch_closes/fetch_fred)해서 테스트는 네트워크 없이
합성 시계열로 전체 파이프라인을 돌린다.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path

from engine.market_map import compute
from engine.market_map.compute import MacroRow, ThemeRow
from engine.market_map.render import render_page
from engine.market_map.themes import (
    KR_THEME_ETFS,
    MACRO_SPECS,
    SECTOR_CYCLICALS,
    SECTOR_DEFENSIVES,
    TICKER_CHIPS,
    US_THEMES,
    yfinance_symbols_needed,
)

logger = logging.getLogger(__name__)

DailySeries = list[tuple[date, float]]
FetchCloses = Callable[[list[str], date], dict[str, DailySeries]]
FetchFred = Callable[[str, date], DailySeries]

# 4주 lookback + 결측 버퍼 — 첫 표시 주에도 비교 기준이 있도록 여유를 둔다
_HISTORY_BUFFER_WEEKS = 7


def _catalog_closes(
    catalog_db: Path | str, symbols: list[str], start: date
) -> dict[str, DailySeries]:
    """카탈로그 일별 종가 벌크 조회 — read-only 단일 커넥션/단일 쿼리.

    심볼당 connect(get_bars×2 connect)를 반복하면 다른 프로세스(대시보드/ingest)가
    락을 잡고 있을 때 심볼×재시도(~2초)만큼 스톨한다. 한 번 열고 실패면 즉시 비운다.
    """
    import duckdb

    out: dict[str, DailySeries] = {}
    if not symbols or not Path(catalog_db).exists():
        logger.warning("catalog db not found: %s — US 테마는 빈 칸", catalog_db)
        return out
    try:
        con = duckdb.connect(str(catalog_db), read_only=True)
    except Exception as exc:
        logger.warning("catalog open failed (락 경합?) — US 테마는 빈 칸: %s", exc)
        return out
    try:
        placeholders = ",".join("?" for _ in symbols)
        rows = con.execute(
            "SELECT symbol, ts, close FROM bars "
            f"WHERE market = 'us' AND freq = '1d' AND ts >= ? AND symbol IN ({placeholders}) "
            "ORDER BY symbol, ts",
            [start, *[s.upper() for s in symbols]],
        ).fetchall()
    except Exception as exc:
        logger.warning("catalog query failed — US 테마는 빈 칸: %s", exc)
        return out
    finally:
        con.close()
    for symbol, ts, close in rows:
        if close and close > 0:
            out.setdefault(symbol, []).append((ts, close))
    return out


def build_market_map(
    *,
    weeks_count: int = 28,
    catalog_db: Path | str = "data/store/trader.duckdb",
    offline: bool = False,
    as_of: date | None = None,
    dashboard_url: str = "http://localhost:8501",
    fetch_closes: FetchCloses | None = None,
    fetch_fred: FetchFred | None = None,
    now: datetime | None = None,
) -> tuple[str, dict[str, object]]:
    """마켓 히트맵 페이지 HTML 과 요약 통계를 만든다."""
    as_of = as_of or date.today()
    now = now or datetime.now()
    weeks = compute.build_weeks(as_of, weeks_count)
    start = weeks[0] - timedelta(weeks=_HISTORY_BUFFER_WEEKS)

    # 1) 로컬 카탈로그 — US 테마 종목 + 카탈로그 소스 매크로 심볼
    theme_symbols = sorted({s for theme in US_THEMES for s in theme.symbols})
    catalog_macro = [spec.symbol for spec in MACRO_SPECS if spec.source == "catalog"]
    catalog_series = _catalog_closes(catalog_db, theme_symbols + catalog_macro, start)

    # 2) yfinance — 매크로/칩/섹터ETF/KR 테마 ETF (+ 카탈로그에 없던 매크로 폴백)
    yf_series: dict[str, DailySeries] = {}
    if not offline:
        closes_fn: FetchCloses
        if fetch_closes is None:
            from engine.market_map.fetch import fetch_yfinance_closes

            closes_fn = fetch_yfinance_closes
        else:
            closes_fn = fetch_closes
        needed = yfinance_symbols_needed()
        for symbol in catalog_macro:
            if symbol not in catalog_series and symbol not in needed:
                needed.append(symbol)
        yf_series = closes_fn(needed, start)

    # 3) FRED — 수익률곡선/HY 스프레드
    fred_series: dict[str, DailySeries] = {}
    if not offline:
        fred_fn: FetchFred
        if fetch_fred is None:
            from engine.market_map.fetch import fetch_fred_closes

            fred_fn = fetch_fred_closes
        else:
            fred_fn = fetch_fred
        for spec in MACRO_SPECS:
            if spec.source == "fred":
                fred_series[spec.symbol] = fred_fn(spec.symbol, start)

    def _series_for(spec_symbol: str, source: str) -> DailySeries:
        if source == "catalog":
            return catalog_series.get(spec_symbol) or yf_series.get(spec_symbol) or []
        if source == "fred":
            return fred_series.get(spec_symbol) or []
        return yf_series.get(spec_symbol) or []

    # 매크로 히트맵 행
    macro_rows: list[MacroRow] = []
    for spec in MACRO_SPECS:
        series = _series_for(spec.symbol, spec.source)
        cells = compute.macro_change_cells(
            series, weeks, spec.direction, min_base=spec.min_base, as_of=as_of
        )
        macro_rows.append(MacroRow(name=spec.name, cells=cells))
    rotation = compute.sector_rotation_cells(
        {s: yf_series.get(s) or [] for s in SECTOR_CYCLICALS},
        {s: yf_series.get(s) or [] for s in SECTOR_DEFENSIVES},
        weeks,
        as_of=as_of,
    )
    macro_rows.append(MacroRow(name="섹터 로테이션", cells=rotation))

    # 테마 히트맵 행 (데이터가 잡힌 테마만)
    us_rows: list[ThemeRow] = []
    for theme in US_THEMES:
        row = compute.theme_row(
            theme.name,
            {s: catalog_series[s] for s in theme.symbols if s in catalog_series},
            weeks,
            as_of=as_of,
        )
        if row.n > 0:
            us_rows.append(row)
    kr_rows: list[ThemeRow] = []
    for theme in KR_THEME_ETFS:
        row = compute.theme_row(
            theme.name,
            {s: yf_series[s] for s in theme.symbols if yf_series.get(s)},
            weeks,
            as_of=as_of,
        )
        if row.n > 0:
            kr_rows.append(row)

    # 상단 티커 칩 — 마지막 종가 vs 직전 종가
    chips: list[tuple[str, float, float]] = []
    for label, symbol in TICKER_CHIPS:
        series = [p for p in (yf_series.get(symbol) or []) if p[0] <= as_of]
        if len(series) >= 2:
            chips.append((label, series[-1][1], series[-2][1]))

    # 마지막 주가 진행 중이면 WTD 로 표기. 금요일도 부분 취급(보수적) — KST 금요일 저녁엔
    # US 금요일 세션이 아직 안 끝났다. 주말 생성만 완료된 주로 본다.
    partial_last = bool(weeks) and compute.week_monday(as_of) == weeks[-1] and as_of.weekday() < 5
    # US 카탈로그 신선도 — US 테마 심볼의 as_of 이하 관측치만 (TLT 등 매크로 심볼 제외)
    theme_symbol_set = {s for theme in US_THEMES for s in theme.symbols}
    catalog_as_of = max(
        (
            ts
            for symbol, series in catalog_series.items()
            if symbol in theme_symbol_set
            for ts, _close in series
            if ts <= as_of
        ),
        default=None,
    )

    html = render_page(
        as_of=as_of,
        generated_at=now,
        chips=chips,
        weeks=weeks,
        macro_rows=macro_rows,
        us_rows=us_rows,
        kr_rows=kr_rows,
        dashboard_url=dashboard_url,
        partial_last=partial_last,
        catalog_as_of=catalog_as_of,
    )
    stats: dict[str, object] = {
        "weeks": len(weeks),
        "macro_rows": len(macro_rows),
        "macro_rows_with_data": sum(1 for r in macro_rows if any(c is not None for c in r.cells)),
        "us_themes": len(us_rows),
        "kr_themes": len(kr_rows),
        "chips": len(chips),
        "catalog_symbols": len(catalog_series),
        "catalog_last_bar": catalog_as_of.isoformat() if catalog_as_of else None,
    }
    return html, stats
