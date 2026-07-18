"""market-map 확장 패널 데이터 — 검증 선정(top-N), forward-OOS 원장, 거시 예측 위젯.

전부 fail-open: 소스가 없거나 로드가 실패하면 None/빈 값을 돌려주고 페이지는 해당
섹션 없이 렌더된다 (기존 히트맵 lane 과 동일한 설계). 무거운 의존(pandas/valuation)은
전부 지연 import — 히트맵만 쓰는 경로에 비용을 전가하지 않는다.
"""

from __future__ import annotations

import csv
import json
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from data.models import FlowRecord

logger = logging.getLogger(__name__)

DailySeries = list[tuple[date, float]]
FlowFetcher = Callable[[str, str, date, date], "list[FlowRecord]"]

DEFAULT_OOS_LEDGER_GLOB = "out/paper-oos-ledger-*.jsonl"
DEFAULT_COPILOT_OUT = Path("trading-copilot/out")

RATE_REGION_LABELS = {"us": "FOMC", "kr": "금통위"}
MARKET_LABELS = {"us": "🇺🇸", "kr": "🇰🇷"}


# ── 검증 선정 (scan_universe) ────────────────────────────────────────────────


@dataclass(frozen=True)
class SelectionRow:
    rank: int | None
    ticker: str
    action: str
    band: str  # confidence band: high/medium/low
    score: float  # confidence score 0-100
    price: float | None
    target_entry: float | None
    stop_loss: float | None
    target_exit: float | None
    in_top_n: bool


@dataclass(frozen=True)
class SelectionPanel:
    strategy_id: str
    top_n: int
    universe_size: int
    asof: datetime
    rows: list[SelectionRow] = field(default_factory=list)
    pbo: float | None = None  # 전략 검증의 과최적화 확률 — 정직 표기용


def load_selection_panel(
    max_rows: int = 10, *, strategies_root: Path | str = "."
) -> SelectionPanel | None:
    """핀 스냅샷 기반 scan_universe top-N. 네트워크 없음(~2-3s). 실패 시 None."""
    try:
        from scripts.evaluate_ticker import DEFAULT_FUNDAMENTALS, DEFAULT_PRICES, load_universe
        from valuation.recommendation import load_validated_strategy, scan_universe

        bars, funds, asof = load_universe(DEFAULT_PRICES, DEFAULT_FUNDAMENTALS)
        strategy = load_validated_strategy()
        results = scan_universe(
            bars_by_symbol=bars,
            fundamentals_by_symbol=funds,
            strategy=strategy,
            asof_ts=asof,
        )
    except Exception as exc:
        logger.warning("selection panel unavailable: %s", exc)
        return None
    ranked = sorted((r for r in results if r.rank is not None), key=lambda r: r.rank or 10**9)[
        :max_rows
    ]
    rows = [
        SelectionRow(
            rank=r.rank,
            ticker=r.ticker,
            action=r.action,
            band=getattr(r.confidence, "band", ""),
            score=float(getattr(r.confidence, "score", 0.0)),
            price=r.current_price,
            target_entry=getattr(r.entry_plan, "target_entry", None) if r.entry_plan else None,
            stop_loss=getattr(r.entry_plan, "stop_loss", None) if r.entry_plan else None,
            target_exit=getattr(r.entry_plan, "target_exit", None) if r.entry_plan else None,
            in_top_n=bool(r.in_top_n),
        )
        for r in ranked
    ]
    if not rows:
        return None
    # ValidatedStrategy 는 pbo 를 노출하지 않아 config 에서 직접 읽는다 (없으면 폴백).
    pbo = getattr(strategy, "pbo", None)
    if pbo is None:
        pbo = strategy_pbo(strategy.strategy_id, strategies_root)
    return SelectionPanel(
        strategy_id=strategy.strategy_id,
        top_n=int(strategy.top_n),
        universe_size=int(ranked[0].universe_size) if ranked else 0,
        asof=asof,
        rows=rows,
        pbo=float(pbo) if isinstance(pbo, (int, float)) else None,
    )


# ── forward-OOS 페이퍼 원장 ──────────────────────────────────────────────────


@dataclass(frozen=True)
class OOSRow:
    rebal_date: str  # ISO
    mark_date: date | None
    n_names: int
    port_pct: float | None  # 기간 수익률 %
    bench_pct: float | None
    excess_pct: float | None
    closed: bool  # 다음 리밸로 마감된 기간인가 (아니면 인터임 MTM)


@dataclass(frozen=True)
class OOSPanel:
    strategy_id: str
    benchmark: str
    rows: list[OOSRow]
    n_entries: int
    n_closed: int
    cum_port_pct: float | None  # 폐쇄 기간 체인 누적 (n_closed=0 이면 None)
    cum_bench_pct: float | None
    cum_excess_pct: float | None
    backtest_excess_ann: float | None = None  # 전략별 기대 초과(연, 수수료 반영) — 없으면 표기 생략


DEFAULT_STRATEGIES_CONFIG = Path("config/validated_strategies.json")


def _default_strategy_id(root: Path) -> str | None:
    """config/validated_strategies.json 의 default 전략 id — 원장 선택 앵커."""
    try:
        payload = json.loads((root / DEFAULT_STRATEGIES_CONFIG).read_text(encoding="utf-8"))
        default = payload.get("default")
        return str(default) if default else None
    except Exception:
        return None


def discover_oos_ledger(root: Path | str = ".") -> Path | None:
    """배포후보 원장 발견 — 글롭 정렬순이 아니라 config default 전략에 앵커한다.

    원장이 여럿일 때(예: aqr vs combined) 알파벳 첫 매치를 잡으면 배포 전략이
    조용히 바뀔 수 있다. 규칙: default 전략 id 로 시작하는 원장이 정확히 하나면
    그것, 아니면 전체가 하나일 때만 그것, 그 외에는 None + 경고 (명시 지정 요구).
    """
    root = Path(root)
    matches = sorted(root.glob(DEFAULT_OOS_LEDGER_GLOB))
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]
    default = _default_strategy_id(root)
    if default:
        preferred = [p for p in matches if p.name.startswith(f"paper-oos-ledger-{default}")]
        if len(preferred) == 1:
            return preferred[0]
    logger.warning(
        "여러 OOS 원장 발견 — 자동 선택 불가, --oos-ledger 로 명시하라: %s",
        ", ".join(p.name for p in matches),
    )
    return None


def _strategy_field(strategy_id: str, field: str, root: Path | str) -> float | None:
    """validated_strategies.json 에서 전략 지표 하나를 읽는다.

    원장 전략 id 는 config 키의 변형(예: ``…_pit110``)일 수 있어 최장 접두 매치.
    """
    try:
        payload = json.loads((Path(root) / DEFAULT_STRATEGIES_CONFIG).read_text(encoding="utf-8"))
        strategies = payload.get("strategies") or {}
    except Exception:
        return None
    best_key = ""
    for key in strategies:
        if strategy_id.startswith(key) and len(key) > len(best_key):
            best_key = key
    if not best_key:
        return None
    value = strategies[best_key].get(field)
    return float(value) if isinstance(value, (int, float)) else None


def strategy_backtest_excess(strategy_id: str, root: Path | str = ".") -> float | None:
    """전략의 백테스트 기대 초과(연, 수수료 반영)."""
    return _strategy_field(strategy_id, "avg_excess_after_cost", root)


def strategy_pbo(strategy_id: str, root: Path | str = ".") -> float | None:
    """전략의 과최적화 확률(PBO) — '크기 fragile' 의 정직한 근거."""
    return _strategy_field(strategy_id, "pbo", root)


def oos_start_date(ledger_path: Path | str | None) -> date | None:
    """원장 최초 리밸일 — OOS 마킹용 종가는 여기까지 거슬러 필요하다 (히트맵 창과 무관)."""
    if not ledger_path or not Path(ledger_path).exists():
        return None
    try:
        from engine.paper_oos import load_ledger

        entries = load_ledger(Path(ledger_path))
    except Exception as exc:
        logger.warning("oos ledger read failed: %s", exc)
        return None
    if not entries:
        return None
    return min(date.fromisoformat(e.rebal_date) for e in entries)


def oos_symbols(ledger_path: Path | str | None) -> list[str]:
    """원장 마킹에 필요한 심볼 (포지션 ∪ 벤치마크) — closes 수집 대상."""
    if not ledger_path or not Path(ledger_path).exists():
        return []
    try:
        from engine.paper_oos import load_ledger

        entries = load_ledger(Path(ledger_path))
    except Exception as exc:
        logger.warning("oos ledger read failed: %s", exc)
        return []
    symbols = {s for e in entries for s in e.weights}
    symbols.update(e.benchmark_symbol for e in entries)
    return sorted(symbols)


# 마크 신선도 한계 — 오래된 종가를 뒤 리밸일까지 끌고 가 폐쇄 기간을 얼려붙은 가격으로
# 채점하는 것 방지 (engine.paper_oos.mark_prices_at_dates 의 max_staleness_days 와 동일 취지).
# 연휴 낀 주말(최대 4일 갭)을 통과시키되 거래정지/수집실패는 걸러낸다.
OOS_MARK_MAX_AGE_DAYS = 5


def _mark_at(
    series: DailySeries | None, on_or_before: date, max_age_days: int | None = None
) -> float | None:
    """날짜 이하의 마지막 종가 (series 는 날짜 오름차순). 너무 오래된 관측은 버린다."""
    if not series:
        return None
    mark: float | None = None
    observed: date | None = None
    for ts, close in series:
        if ts > on_or_before:
            break
        mark, observed = close, ts
    if (
        mark is not None
        and observed is not None
        and max_age_days is not None
        and (on_or_before - observed).days > max_age_days
    ):
        return None
    return mark


def load_oos_panel(
    ledger_path: Path | str | None,
    closes: dict[str, DailySeries],
    as_of: date,
    backtest_excess_ann: float | None = None,
) -> OOSPanel | None:
    """원장 + 종가 시계열 → 리밸 회차별 vs 벤치 성과. 폐쇄 기간은 다음 리밸일에 마킹.

    산정기준(패널에 그대로 표기): 마크 = 조정종가, 포트 수익 = 마크된 심볼만
    가중 재정규화(engine.paper_oos._period_return 과 동일 규약), 마지막 회차는
    인터임 MTM(폐쇄 아님).
    """
    if not ledger_path or not Path(ledger_path).exists():
        return None
    try:
        from engine.paper_oos import _period_return, load_ledger

        entries = load_ledger(Path(ledger_path))
    except Exception as exc:
        logger.warning("oos ledger load failed: %s", exc)
        return None
    # no-lookahead: as_of 이후 리밸은 존재하지 않는 것으로 — 과거 시점 재생성 시
    # 미래 리밸이 직전 기간을 미래 가격으로 '폐쇄'해 버리면 안 된다.
    entries = [e for e in entries if e.rebal_date <= as_of.isoformat()]
    if not entries:
        return None
    entries = sorted(entries, key=lambda e: e.rebal_date)

    rows: list[OOSRow] = []
    closed_pairs: list[tuple[float, float]] = []  # (port, bench) 소수 수익률
    for i, entry in enumerate(entries):
        nxt = entries[i + 1] if i + 1 < len(entries) else None
        mark_date = date.fromisoformat(nxt.rebal_date) if nxt else as_of
        marks = {
            symbol: mark
            for symbol in entry.weights
            if (mark := _mark_at(closes.get(symbol), mark_date, OOS_MARK_MAX_AGE_DAYS)) is not None
        }
        port = _period_return(entry, marks)
        bench_mark = _mark_at(closes.get(entry.benchmark_symbol), mark_date, OOS_MARK_MAX_AGE_DAYS)
        bench = (
            bench_mark / entry.benchmark_price - 1.0
            if bench_mark is not None and entry.benchmark_price > 0
            else None
        )
        closed = nxt is not None
        if closed and port is not None and bench is not None:
            closed_pairs.append((port, bench))
        rows.append(
            OOSRow(
                rebal_date=entry.rebal_date,
                mark_date=mark_date,
                n_names=len(entry.weights),
                port_pct=port * 100.0 if port is not None else None,
                bench_pct=bench * 100.0 if bench is not None else None,
                excess_pct=(port - bench) * 100.0
                if port is not None and bench is not None
                else None,
                closed=closed,
            )
        )

    cum_port = cum_bench = None
    if closed_pairs:
        p = b = 1.0
        for port, bench in closed_pairs:
            p *= 1.0 + port
            b *= 1.0 + bench
        cum_port, cum_bench = (p - 1.0) * 100.0, (b - 1.0) * 100.0
    return OOSPanel(
        strategy_id=entries[0].strategy_id,
        benchmark=entries[0].benchmark_symbol,
        rows=rows,
        n_entries=len(entries),
        n_closed=len(closed_pairs),
        cum_port_pct=cum_port,
        cum_bench_pct=cum_bench,
        cum_excess_pct=(cum_port - cum_bench)
        if cum_port is not None and cum_bench is not None
        else None,
        backtest_excess_ann=backtest_excess_ann,
    )


# ── 거시 예측 위젯 (trading-copilot 원장 — 순수 파일 읽기, 네트워크 0) ─────────


@dataclass(frozen=True)
class RateCard:
    region: str  # us | kr
    meeting: str  # ISO date
    current_rate: float | None
    probs: dict[str, float]  # {cut, hold, hike}
    modal: str
    recorded_at: str
    pending: bool
    n_scored: int
    hit_rate: float | None  # modal 적중률 0-1
    mean_brier: float | None


@dataclass(frozen=True)
class MacroCard:
    region: str
    label: str  # CPI/PPI 라벨
    target: str  # YYYY-MM
    forecast_mom: float | None
    forecast_yoy: float | None
    pi80: tuple[float, float] | None
    skill_pct: float | None
    recorded_at: str
    pending: bool
    n_scored: int
    mae: float | None  # 평균 |MoM 오차| (%p)
    pi80_coverage: float | None  # 0-1


@dataclass(frozen=True)
class ForecastPanel:
    rates: list[RateCard]
    macros: list[MacroCard]


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def parse_rate_cards(lines: list[dict], as_of: date) -> list[RateCard]:
    """지역별 최신 rate_forecast (미채점·미래 회의 우선) + 사후채점 트랙레코드.

    원장은 append-only 라서 forecast 행의 ``status`` 는 채점 후에도 "pending" 으로
    남는다 — 실제 상태는 같은 (region, meeting) 의 rate_score 행 존재로 판정한다.
    ``superseded=True`` 는 (프로듀서 기준) force 교체본에 찍히는 표식이므로 걸러내면
    안 되고, 같은 회의의 '나중 줄'이 항상 권위본이다.
    """
    cards: list[RateCard] = []
    for region in ("us", "kr"):
        # 같은 (region, meeting) 은 마지막 줄이 권위본 (force 교체 포함)
        by_meeting: dict[str, dict] = {}
        for r in lines:
            if (
                r.get("kind") == "rate_forecast"
                and r.get("region") == region
                and isinstance(r.get("probs"), dict)
            ):
                by_meeting[str(r.get("meeting", ""))] = r
        if not by_meeting:
            continue
        scored_meetings = {
            str(r.get("meeting", ""))
            for r in lines
            if r.get("kind") == "rate_score" and r.get("region") == region
        }
        unscored = [m for m in by_meeting if m not in scored_meetings]
        upcoming = [m for m in unscored if m >= as_of.isoformat()]
        if upcoming:
            meeting = min(upcoming)  # 다가오는 회의 중 가장 가까운 것
        elif unscored:
            meeting = max(unscored)  # 결정은 지났지만 아직 채점 전 (결과 대기)
        else:
            meeting = max(by_meeting)  # 전부 채점됨 → 마지막 회의를 '지난 기록'으로
        best = by_meeting[meeting]
        scores = [r for r in lines if r.get("kind") == "rate_score" and r.get("region") == region]
        hits = [bool(r.get("modal_hit")) for r in scores if r.get("modal_hit") is not None]
        briers = [float(r["brier"]) for r in scores if isinstance(r.get("brier"), (int, float))]
        cards.append(
            RateCard(
                region=region,
                meeting=meeting,
                current_rate=best.get("current_rate"),
                probs={k: float(v) for k, v in best["probs"].items()},
                modal=str(best.get("modal", "")),
                recorded_at=str(best.get("recorded_at", "")),
                pending=meeting not in scored_meetings,
                n_scored=len(scores),
                hit_rate=sum(hits) / len(hits) if hits else None,
                mean_brier=sum(briers) / len(briers) if briers else None,
            )
        )
    return cards


def parse_macro_cards(lines: list[dict], as_of: date) -> list[MacroCard]:
    """(region, series) 별 최신 nowcast (pending 우선) + MAE/PI80 커버리지."""
    keys: list[tuple[str, str]] = []
    by_key: dict[tuple[str, str], list[dict]] = {}
    for r in lines:
        if r.get("kind") != "forecast" or not r.get("series_id"):
            continue
        key = (str(r.get("region", "")), str(r["series_id"]))
        if key not in by_key:
            keys.append(key)
            by_key[key] = []
        by_key[key].append(r)

    # append-only 원장: forecast 행 status 는 채점 후에도 "pending" — 실제 pending 은
    # 같은 (region, series, target) 의 score 행 부재로 판정한다.
    scored_targets = {
        (str(r.get("region", "")), str(r.get("series_id", "")), str(r.get("target", "")))
        for r in lines
        if r.get("kind") == "score"
    }

    cards: list[MacroCard] = []
    for key in keys:
        rows = by_key[key]
        region, series_id = key
        pending = [
            r for r in rows if (region, series_id, str(r.get("target", ""))) not in scored_targets
        ]
        pool = pending or rows
        best = max(pool, key=lambda r: (str(r.get("target", "")), str(r.get("recorded_at", ""))))
        scores = [
            r
            for r in lines
            if r.get("kind") == "score"
            and r.get("region") == region
            and r.get("series_id") == series_id
        ]
        abs_errors = [
            float(r["abs_error_mom"])
            for r in scores
            if isinstance(r.get("abs_error_mom"), (int, float))
        ]
        covered = [bool(r.get("in_pi80")) for r in scores if r.get("in_pi80") is not None]
        pi80 = best.get("pi80")
        cards.append(
            MacroCard(
                region=region,
                label=str(best.get("label", series_id)),
                target=str(best.get("target", "")),
                forecast_mom=best.get("forecast_mom"),
                forecast_yoy=best.get("forecast_yoy"),
                pi80=(float(pi80[0]), float(pi80[1]))
                if isinstance(pi80, (list, tuple)) and len(pi80) == 2
                else None,
                skill_pct=best.get("skill_pct"),
                recorded_at=str(best.get("recorded_at", "")),
                pending=(region, series_id, str(best.get("target", ""))) not in scored_targets,
                n_scored=len(scores),
                mae=sum(abs_errors) / len(abs_errors) if abs_errors else None,
                pi80_coverage=sum(covered) / len(covered) if covered else None,
            )
        )
    cards.sort(key=lambda c: (c.region, c.label))
    return cards


def _filter_asof(lines: list[dict], as_of: date) -> list[dict]:
    """as_of 이후에 기록/채점된 행 제거 — 과거 시점 재생성에 미래 원장이 새면 안 된다.

    이벤트 시각: forecast/rate_forecast → recorded_at, score/rate_score → scored_at.
    타임스탬프가 없는 행은 (구버전 호환) 통과시킨다.
    """
    iso = as_of.isoformat()
    out: list[dict] = []
    for r in lines:
        ts = str(r.get("recorded_at") or r.get("scored_at") or "")[:10]
        if ts and ts > iso:
            continue
        out.append(r)
    return out


# ── KR 수급 (외국인/기관 순매수, naver 추정) ─────────────────────────────────


@dataclass(frozen=True)
class FlowRow:
    code: str
    name: str
    foreign_net: float  # 기간 외국인 순매수 합 (원)
    institution_net: float  # 기간 기관 순매수 합 (원)
    combined_net: float


@dataclass(frozen=True)
class FlowPanel:
    rows: list[FlowRow]  # combined_net 내림차순
    lookback_days: int
    confidence: str  # naver 추정 = "medium"
    asof: date


def load_flow_panel(
    bellwethers: Sequence[tuple[str, str, str]],  # (code, name, market)
    fetch_flows: FlowFetcher,
    as_of: date,
    *,
    lookback_days: int = 10,
    time_budget_s: float = 25.0,
    monotonic: Callable[[], float] | None = None,
) -> FlowPanel | None:
    """대표 KR 종목들의 외국인/기관 기간 순매수. naver 추정치(medium)라 방향성 참고용.

    빌드 시점 fetch (매크로/칩과 동일 패턴), 심볼 단위 fail-open. 전부 실패면 None.
    ``time_budget_s``: 총 시간 예산 — naver 장애 시 12심볼 × 20초 timeout(≈4분) 스톨을
    막는다. 예산 초과 시 남은 심볼은 조용히 버리지 않고 로그로 알린다.
    """
    import time
    from datetime import timedelta

    clock = monotonic or time.monotonic
    started = clock()

    # 버퍼는 fetch 용(주말/휴일 때문에 달력일 > 거래일). 합산은 실제 거래일 최근 N개로 제한 —
    # 안 그러면 버퍼 전체(~17-18 거래일)를 더해 '최근 10거래일' 라벨과 어긋난다.
    start = as_of - timedelta(days=lookback_days * 2 + 5)
    rows: list[FlowRow] = []
    for i, (code, name, market) in enumerate(bellwethers):
        if i > 0 and clock() - started > time_budget_s:
            logger.warning(
                "flow panel time budget %.0fs 초과 — 남은 %d 심볼 스킵",
                time_budget_s,
                len(bellwethers) - i,
            )
            break
        try:
            recs = fetch_flows(code, market, start, as_of)
        except Exception as exc:
            logger.warning("flow fetch failed for %s: %s", code, exc)
            continue
        in_range = [r for r in recs if start <= r.ts <= as_of]
        if not in_range:
            continue
        window = set(sorted({r.ts for r in in_range}, reverse=True)[:lookback_days])
        recent = [r for r in in_range if r.ts in window]
        fnet = sum(r.net_value for r in recent if r.investor == "foreign")
        inet = sum(r.net_value for r in recent if r.investor == "institution")
        rows.append(FlowRow(code, name, fnet, inet, fnet + inet))
    if not rows:
        return None
    rows.sort(key=lambda r: r.combined_net, reverse=True)
    return FlowPanel(rows=rows, lookback_days=lookback_days, confidence="medium", asof=as_of)


def default_kr_bellwethers(csv_path: Path | str, per_theme: int = 1) -> list[tuple[str, str, str]]:
    """검증된 KR 유니버스에서 테마별 상위 per_theme 종목(대장주) — 큐레이션 순서=시총순."""
    path = Path(csv_path)
    if not path.exists():
        return []
    seen: dict[str, int] = {}
    out: list[tuple[str, str, str]] = []
    try:
        with open(path, encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                key = str(row.get("theme_key", "")).strip()
                if seen.get(key, 0) >= per_theme:
                    continue
                seen[key] = seen.get(key, 0) + 1
                out.append(
                    (
                        str(row.get("code", "")).strip().zfill(6),
                        str(row.get("name", "")).strip(),
                        str(row.get("market", "kospi")).strip(),
                    )
                )
    except (OSError, ValueError) as exc:
        logger.warning("KR bellwether CSV read failed: %s", exc)
        return []
    return out


def load_forecast_panel(
    copilot_out: Path | str = DEFAULT_COPILOT_OUT, as_of: date | None = None
) -> ForecastPanel | None:
    """trading-copilot 원장 2종을 파일로만 읽는다. 둘 다 비어 있으면 None."""
    as_of = as_of or date.today()
    out_dir = Path(copilot_out)
    try:
        rates = parse_rate_cards(
            _filter_asof(_read_jsonl(out_dir / "rate_ledger.jsonl"), as_of), as_of
        )
        macros = parse_macro_cards(
            _filter_asof(_read_jsonl(out_dir / "forecast_ledger.jsonl"), as_of), as_of
        )
    except Exception as exc:
        logger.warning("forecast panel unavailable: %s", exc)
        return None
    if not rates and not macros:
        return None
    return ForecastPanel(rates=rates, macros=macros)
