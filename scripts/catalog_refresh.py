"""카탈로그 리프레시 드라이버 — US 일봉을 심볼 단위 재시도/스로틀로 재수집.

왜 `trader ingest` 를 그대로 안 쓰나: ingest 루프에는 재시도·스로틀이 없어 100+ 심볼을
한 번에 돌리면 yahoo 레이트리밋에 걸리고, 중간 예외가 run 전체를 중단시킨다. 여기서는
심볼 단위로 (지수 백오프 재시도 + 심볼 간 스로틀) 하고 실패는 집계만 한다 (fail-open).

왜 full 히스토리인가: yahoo 조정종가는 배당/분할 때 과거 전체가 재조정된다. 기존
시계열에 최신 꼬리만 이어붙이면 이음새가 생긴다. ``--mode incremental`` 은 평일용
저비용 탑업(겹침 재수집으로 근처 이음새만 봉합)이고, 주 1회 full 로 전체를 리셋한다.

대상 심볼 = 카탈로그의 US 일봉 심볼 ∪ market-map US 테마/카탈로그-소스 매크로 심볼.
"""

from __future__ import annotations

import argparse
import logging
import random
import sys
import time
from collections.abc import Callable, Sequence
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data.catalog import DEFAULT_CATALOG_PATH, MarketDataCatalog  # noqa: E402
from engine.market_map.themes import MACRO_SPECS, US_THEMES  # noqa: E402

logger = logging.getLogger(__name__)

FULL_START = date(1990, 1, 1)
INCREMENTAL_OVERLAP_DAYS = 14  # 탑업 겹침 — 직전 봉 주변 조정/결측을 같이 다시 쓴다
RETRY_DELAYS_S: tuple[float, ...] = (8.0, 30.0)
THROTTLE_S = 1.0
THROTTLE_JITTER_S = 0.5
# 광역 유니버스에는 상폐/재활용 심볼이 섞여 있어 부분 실패가 정상이다 (fail-open).
# 절반 넘게 실패하면 시스템 문제(레이트리밋 차단 등)로 보고 비정상 종료.
FAIL_EXIT_RATIO = 0.5

FetchBars = Callable[[str, str, date, date], list]


def market_map_symbols() -> list[str]:
    """market-map 이 카탈로그에서 읽는 US 심볼 전부 (테마 + catalog 소스 매크로)."""
    symbols = {s.upper() for theme in US_THEMES for s in theme.symbols}
    symbols.update(spec.symbol.upper() for spec in MACRO_SPECS if spec.source == "catalog")
    return sorted(symbols)


def map_scope_symbols(root: Path | str = ROOT) -> list[str]:
    """평일 탑업 대상 — market-map 심볼 + forward-OOS 원장 마킹 심볼(벤치 포함)."""
    from engine.market_map.panels import discover_oos_ledger, oos_symbols

    symbols = set(market_map_symbols())
    symbols.update(oos_symbols(discover_oos_ledger(root)))
    return sorted(symbols)


def collect_symbols(catalog_db: Path | str) -> list[str]:
    """카탈로그의 기존 US 일봉 심볼 ∪ market-map 필요 심볼 (정렬)."""
    import duckdb

    symbols = set(market_map_symbols())
    if Path(catalog_db).exists():
        try:
            con = duckdb.connect(str(catalog_db), read_only=True)
            try:
                rows = con.execute(
                    "SELECT DISTINCT symbol FROM bars WHERE market = 'us' AND freq = '1d'"
                ).fetchall()
            finally:
                con.close()
            symbols.update(str(r[0]).upper() for r in rows)
        except Exception as exc:  # 락 경합/스키마 부재 — map 심볼만으로 진행
            logger.warning("catalog symbol scan failed (%s) — market-map 심볼만 갱신", exc)
    return sorted(symbols)


def last_bar_dates(catalog_db: Path | str) -> dict[str, date]:
    """심볼별 마지막 봉 날짜 — incremental 시작점 계산용. 실패 시 빈 dict (→ full)."""
    import duckdb

    if not Path(catalog_db).exists():
        return {}
    try:
        con = duckdb.connect(str(catalog_db), read_only=True)
        try:
            rows = con.execute(
                "SELECT symbol, max(ts) FROM bars "
                "WHERE market = 'us' AND freq = '1d' GROUP BY symbol"
            ).fetchall()
        finally:
            con.close()
    except Exception as exc:
        logger.warning("last-bar scan failed (%s) — full 시작점으로 폴백", exc)
        return {}
    return {str(sym).upper(): ts for sym, ts in rows if ts is not None}


def refresh_symbols(
    symbols: Sequence[str],
    *,
    catalog: MarketDataCatalog,
    fetch_bars: FetchBars,
    mode: str = "full",
    starts: dict[str, date] | None = None,
    end: date | None = None,
    sleep: Callable[[float], None] = time.sleep,
    retry_delays: Sequence[float] = RETRY_DELAYS_S,
    throttle_s: float = THROTTLE_S,
) -> tuple[list[str], list[str]]:
    """심볼 단위 재시도/스로틀 수집. 반환 = (성공 심볼, 실패 심볼)."""
    end = end or date.today()
    starts = starts or {}
    ok: list[str] = []
    failed: list[str] = []
    for i, symbol in enumerate(symbols):
        if i and throttle_s > 0:
            sleep(throttle_s + random.uniform(0.0, THROTTLE_JITTER_S))
        if mode == "incremental" and symbol in starts:
            start = starts[symbol] - timedelta(days=INCREMENTAL_OVERLAP_DAYS)
        else:
            start = FULL_START
        bars: list | None = None
        for attempt, delay in enumerate((0.0, *retry_delays)):
            if delay:
                sleep(delay)
            try:
                bars = fetch_bars(symbol, "us", start, end)
                break
            except Exception as exc:
                logger.warning(
                    "fetch failed for %s (attempt %d/%d): %s",
                    symbol,
                    attempt + 1,
                    1 + len(retry_delays),
                    exc,
                )
        if not bars:
            failed.append(symbol)
            continue
        stored: int | None = None
        for attempt, delay in enumerate((0.0, 5.0, 15.0)):
            if delay:
                sleep(delay)
            try:
                stored = catalog.put_bars(bars)
                break
            except Exception as exc:  # 다른 cron(가격 ingest 등)과의 쓰기 락 경합 — 잠시 후 재시도
                logger.warning(
                    "put_bars failed for %s (attempt %d/3): %s", symbol, attempt + 1, exc
                )
        if stored is None:
            failed.append(symbol)
            continue
        ok.append(symbol)
        print(f"{symbol}: {stored} bars ({bars[0].ts} → {bars[-1].ts})", flush=True)
    return ok, failed


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog-db", type=Path, default=ROOT / DEFAULT_CATALOG_PATH)
    parser.add_argument("--mode", choices=("full", "incremental"), default="full")
    parser.add_argument(
        "--scope",
        choices=("all", "map"),
        default="all",
        help="all=카탈로그∪market-map 전체(주간 풀 리프레시용), map=market-map+OOS 심볼만(평일 탑업용).",
    )
    parser.add_argument("--symbols", help="쉼표 구분 심볼 목록 (지정 시 --scope 무시)")
    args = parser.parse_args(argv)

    from data.ingest.yahoo import fetch_yahoo_bars

    if args.symbols:
        symbols = sorted({s.strip().upper() for s in args.symbols.split(",") if s.strip()})
    elif args.scope == "map":
        symbols = map_scope_symbols()
    else:
        symbols = collect_symbols(args.catalog_db)
    starts = last_bar_dates(args.catalog_db) if args.mode == "incremental" else {}
    catalog = MarketDataCatalog(args.catalog_db)
    print(f"refreshing {len(symbols)} us symbols (mode={args.mode})", flush=True)
    ok, failed = refresh_symbols(
        symbols,
        catalog=catalog,
        fetch_bars=fetch_yahoo_bars,
        mode=args.mode,
        starts=starts,
    )
    print(f"done: ok={len(ok)} failed={len(failed)}", flush=True)
    if failed:
        print("failed symbols: " + ", ".join(failed), flush=True)
    if symbols and len(failed) > len(symbols) * FAIL_EXIT_RATIO:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
