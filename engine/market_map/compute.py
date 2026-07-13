"""마켓 히트맵 순수 계산 — 주별 버킷, 4주 변화율, risk-on/off 부호, 테마 주별 수익.

레퍼런스(surgedesk.co.kr) 규약을 그대로 따른다:
- 주 = 월요일 시작. 셀 값은 그 주의 마지막 종가 기준.
- 매크로 셀 = 4주 전 주말 종가 대비 % 변화, 지표 방향성에 따라 risk_on/off 해석.
- |pct| < deadband(0.5%p) 는 neutral(무채색).
- 테마 셀 = 그 주 매핑 종목들의 평균 주간(5거래일) 수익률 %.
모든 함수는 입력 시계열의 미래 데이터를 참조하지 않는다 (no-lookahead).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

DailySeries = Sequence[tuple[date, float]]

# pct_pair 색 램프 — 레퍼런스 pctPair(js) 포팅. (배경, 글자색)
PAPER = ("#FBF7EE", "#1A1612")
MACRO_SCALE = 12.0  # 매크로 4주Δ 정규화 폭
THEME_SCALE = 4.0  # 테마 주간수익 정규화 폭
NEUTRAL_DEADBAND = 0.5  # |pct| 이 미만이면 방향 판정 없이 neutral


def pct_pair(pct: float | None, scale: float) -> tuple[str, str]:
    """부호/크기에 따른 (배경색, 글자색). 양수=따뜻한 테라코타, 음수=차가운 잉크."""
    if pct is None:
        return PAPER
    n = max(-1.0, min(1.0, pct / scale))
    if n >= 0:
        if n < 0.05:
            return PAPER
        if n < 0.15:
            return ("#F4D5C7", "#3D342C")
        if n < 0.35:
            return ("#D88A65", "#FFFFFF")
        if n < 0.65:
            return ("#C96442", "#FFFFFF")
        return ("#A44E30", "#FFFFFF")
    a = abs(n)
    if a < 0.05:
        return PAPER
    if a < 0.15:
        return ("#EFE6D6", "#3D342C")
    if a < 0.35:
        return ("#BDB5A3", "#1A1612")
    if a < 0.65:
        return ("#6B5F52", "#FBF7EE")
    return ("#3D342C", "#FBF7EE")


@dataclass(frozen=True)
class MacroCell:
    pct: float  # 표시 값 (% 또는 %p)
    interp: str  # "risk_on" | "risk_off" | "neutral"

    @property
    def signed(self) -> float:
        """색 계산용 부호 값 — risk_on=+|pct|, risk_off=-|pct|, neutral=0."""
        if self.interp == "risk_on":
            return abs(self.pct)
        if self.interp == "risk_off":
            return -abs(self.pct)
        return 0.0


@dataclass(frozen=True)
class MacroRow:
    name: str
    cells: list[MacroCell | None]

    @property
    def avg_pct(self) -> float | None:
        vals = [c.pct for c in self.cells if c is not None]
        return sum(vals) / len(vals) if vals else None

    @property
    def avg_signed(self) -> float | None:
        vals = [c.signed for c in self.cells if c is not None]
        return sum(vals) / len(vals) if vals else None


@dataclass(frozen=True)
class ThemeRow:
    name: str
    n: int  # 데이터가 잡힌 매핑 종목 수
    tickers: str  # 호버 툴팁용 종목 나열
    series: list[float | None]  # 주별 평균 수익률 %

    @property
    def avg(self) -> float | None:
        vals = [v for v in self.series if v is not None]
        return sum(vals) / len(vals) if vals else None


def week_monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def build_weeks(as_of: date, count: int) -> list[date]:
    """as_of 가 속한 주를 마지막으로 하는 최근 count 개 주(월요일) 목록."""
    if count < 1:
        raise ValueError("count must be >= 1")
    last = week_monday(as_of)
    return [last - timedelta(weeks=i) for i in range(count - 1, -1, -1)]


def weekly_last_closes(
    series: DailySeries, weeks: Sequence[date], as_of: date | None = None
) -> list[float | None]:
    """각 주(월~일)의 마지막 종가. 해당 주에 데이터가 없으면 None.

    as_of 이후 데이터는 무시한다 (no-lookahead 방어선).
    """
    index = {monday: i for i, monday in enumerate(weeks)}
    out: list[float | None] = [None] * len(weeks)
    for ts, close in series:
        if close is None:
            continue
        if as_of is not None and ts > as_of:
            continue
        i = index.get(week_monday(ts))
        if i is not None:
            out[i] = close  # series 는 날짜 오름차순 — 뒤 값이 그 주 마지막 종가
    return out


def _interp(pct: float, direction: int, deadband: float) -> str:
    if abs(pct) < deadband:
        return "neutral"
    return "risk_on" if pct * direction > 0 else "risk_off"


def macro_change_cells(
    series: DailySeries,
    weeks: Sequence[date],
    direction: int,
    *,
    lookback_weeks: int = 4,
    deadband: float = NEUTRAL_DEADBAND,
    min_base: float = 1e-9,
    as_of: date | None = None,
) -> list[MacroCell | None]:
    """주별 '4주 전 대비 % 변화' 셀. direction: +1=상승이 risk_on, -1=상승이 risk_off.

    변화율은 (cur - base) / |base| 로 계산한다 — 분모가 음수인 구간(수익률곡선 역전 등)에서도
    '값이 오르면 양수'라는 부호 의미가 유지된다 (naive cur/base-1 은 음수 분모에서 부호 반전).
    min_base: |base| 하한 — 0 근처 분모의 % 폭주가 행 평균까지 오염시키는 것을 방지.
    """
    ext_weeks = [weeks[0] - timedelta(weeks=lookback_weeks - i) for i in range(lookback_weeks)]
    ext_weeks.extend(weeks)
    closes = weekly_last_closes(series, ext_weeks, as_of=as_of)
    cells: list[MacroCell | None] = []
    for i in range(lookback_weeks, len(ext_weeks)):
        cur, base = closes[i], closes[i - lookback_weeks]
        if cur is None or base is None or abs(base) <= min_base:
            cells.append(None)
            continue
        pct = (cur - base) / abs(base) * 100.0
        cells.append(MacroCell(pct=round(pct, 1), interp=_interp(pct, direction, deadband)))
    return cells


def weekly_returns_pct(
    series: DailySeries, weeks: Sequence[date], as_of: date | None = None
) -> list[float | None]:
    """주별 수익률 % — 그 주 마지막 종가 / 전주 마지막 종가 - 1."""
    ext_weeks = [weeks[0] - timedelta(weeks=1), *weeks]
    closes = weekly_last_closes(series, ext_weeks, as_of=as_of)
    out: list[float | None] = []
    for i in range(1, len(ext_weeks)):
        cur, prev = closes[i], closes[i - 1]
        if cur is None or prev is None or prev <= 0:
            out.append(None)
        else:
            out.append((cur / prev - 1.0) * 100.0)
    return out


def _mean_weekly_cells(
    symbol_series: dict[str, DailySeries],
    weeks: Sequence[date],
    as_of: date | None = None,
) -> tuple[dict[str, list[float | None]], list[float | None]]:
    """심볼별 주간 수익률과 그 주별 평균(미반올림). 데이터 없는 심볼은 제외."""
    per_symbol: dict[str, list[float | None]] = {}
    for symbol, series in symbol_series.items():
        returns = weekly_returns_pct(series, weeks, as_of=as_of)
        if any(v is not None for v in returns):
            per_symbol[symbol] = returns

    cells: list[float | None] = []
    for i in range(len(weeks)):
        vals = [v for r in per_symbol.values() if (v := r[i]) is not None]
        cells.append(sum(vals) / len(vals) if vals else None)
    return per_symbol, cells


def theme_row(
    name: str,
    symbol_series: dict[str, DailySeries],
    weeks: Sequence[date],
    as_of: date | None = None,
) -> ThemeRow:
    """테마 행 — 매핑 종목들의 주별 수익률 평균. 데이터 없는 종목은 n 에서 제외.

    반올림은 표시 직전(여기)에서만 — 파생 계산은 _mean_weekly_cells 원시값을 쓸 것.
    """
    per_symbol, raw_cells = _mean_weekly_cells(symbol_series, weeks, as_of=as_of)
    return ThemeRow(
        name=name,
        n=len(per_symbol),
        tickers=", ".join(sorted(per_symbol)),
        series=[round(v, 1) if v is not None else None for v in raw_cells],
    )


def sector_rotation_cells(
    cyclicals: dict[str, DailySeries],
    defensives: dict[str, DailySeries],
    weeks: Sequence[date],
    *,
    deadband: float = NEUTRAL_DEADBAND,
    as_of: date | None = None,
) -> list[MacroCell | None]:
    """섹터 로테이션 = Cyclicals 주간 수익률 평균 - Defensives 평균 (%p). 양수 = risk-on.

    diff 와 risk 판정은 반올림 전 원시 평균으로 계산한다 — 표시용 반올림값을 재사용하면
    deadband(0.5) 경계에서 분류가 뒤집힐 수 있다.
    """
    _, cyc = _mean_weekly_cells(cyclicals, weeks, as_of=as_of)
    _, dfs = _mean_weekly_cells(defensives, weeks, as_of=as_of)
    cells: list[MacroCell | None] = []
    for c, d in zip(cyc, dfs, strict=True):
        if c is None or d is None:
            cells.append(None)
            continue
        diff = c - d
        cells.append(MacroCell(pct=round(diff, 1), interp=_interp(diff, 1, deadband)))
    return cells
