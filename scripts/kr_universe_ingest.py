"""KR 테마 유니버스 검증 + 카탈로그 적재.

왜 필요한가: pykrx 의 테마/섹터/전체티커 열거 API 는 이 환경에서 KRX 서버가 빈 응답을
주어 전부 깨져 있다 (per-symbol OHLCV 만 정상). 그래서 KR 테마 구성종목은
``data/kr_theme_universe.csv`` 에 수작업 큐레이션하고, 이 스크립트가:

1. 각 코드의 실제 종목명을 ``pykrx.stock.get_market_ticker_name`` 으로 조회해
   CSV 의 이름과 대조한다 — **불일치/조회실패 코드는 제외** (잘못 적은 6자리 코드가
   엉뚱한 회사로 히트맵에 새는 것 방지, per-symbol 이름 조회는 열거와 달리 동작함).
2. 검증 통과 코드의 일봉을 ``fetch_pykrx_bars`` 로 받아 카탈로그에 적재
   (심볼 단위 재시도/백오프 — ingest CLI 는 재시도가 없음).
3. 검증 통과분을 ``data/kr_theme_universe.validated.csv`` 로 출력 — build 가 이걸 읽는다.
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import re
import sys
import time
from collections.abc import Callable, Sequence
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data.catalog import DEFAULT_CATALOG_PATH, MarketDataCatalog  # noqa: E402

logger = logging.getLogger(__name__)

DEFAULT_CSV = ROOT / "data" / "kr_theme_universe.csv"
DEFAULT_VALIDATED = ROOT / "data" / "kr_theme_universe.validated.csv"
FULL_START = date(2020, 1, 1)  # 28주 히트맵 창엔 충분, pykrx 과거 페치는 느려 6년으로 제한
RETRY_DELAYS_S: tuple[float, ...] = (3.0, 10.0)
PUT_RETRY_DELAYS_S: tuple[float, ...] = (5.0, 15.0)  # 다른 cron 과의 DuckDB 쓰기 락 경합
THROTTLE_S = 0.3
MIN_VALIDATED_RATIO = 0.5  # 이 비율 미만이면 대량 실패로 보고 기존 파일 보존

# 하드 실패(진짜 큐레이션 오류) vs 소프트 실패(일시 장애) 구분 — 소프트는 이전 유효분 보존.
_HARD_REASON_PREFIXES = ("name mismatch", "unknown code")


# 같은 보통주를 가리키는 '표기 차이'만 제거한다. 우선주 마커(우/우B 등)는 다른 종목이므로
# 제거하지 않는다 — 그래야 잘못 적은 코드가 우선주로 매핑돼도 이름 대조에서 걸린다.
_COSMETIC_SUFFIXES = ("보통주",)


def _norm_name(name: str) -> str:
    """이름 대조용 정규화 — 한글/영숫자만 남기고 소문자화 (점·공백·（주） 차이 흡수)."""
    return re.sub(r"[^0-9a-z가-힣]", "", name.lower())


def _strip_cosmetic(norm: str) -> str:
    for suf in _COSMETIC_SUFFIXES:
        s = _norm_name(suf)
        if s and norm.endswith(s) and len(norm) > len(s):
            return norm[: -len(s)]
    return norm


def names_match(curated: str, official: str) -> bool:
    """큐레이션 이름과 pykrx 공식명이 같은 종목인가 — 표기(보통주/구두점) 차이만 흡수 후 완전일치.

    접두 포함은 허용하지 않는다: '삼성전자'(보통주) vs '삼성전자우'(우선주)는 다른 종목이므로,
    코드를 잘못 적어 우선주로 매핑되면 반드시 불일치로 걸러져야 한다.
    """
    a, b = _strip_cosmetic(_norm_name(curated)), _strip_cosmetic(_norm_name(official))
    return bool(a) and a == b


CuratedRow = tuple[str, str, str, str]  # (theme_key, code, name, market)


def load_curated(csv_path: Path | str) -> list[CuratedRow]:
    rows: list[CuratedRow] = []
    with open(csv_path, encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            code = str(r["code"]).strip().zfill(6)
            rows.append(
                (
                    str(r["theme_key"]).strip(),
                    code,
                    str(r["name"]).strip(),
                    str(r["market"]).strip(),
                )
            )
    return rows


def verify_and_ingest(
    curated: Sequence[CuratedRow],
    *,
    catalog: MarketDataCatalog,
    name_of: Callable[[str], str],
    fetch_bars: Callable[[str, str, date, date], list],
    end: date,
    sleep: Callable[[float], None] = time.sleep,
    retry_delays: Sequence[float] = RETRY_DELAYS_S,
    throttle_s: float = THROTTLE_S,
) -> tuple[list[CuratedRow], list[tuple[CuratedRow, str]]]:
    """이름 대조 + OHLCV 적재. 반환 = (검증통과 행[공식명으로 교체], 실패 [(행, 사유)])."""
    validated: list[CuratedRow] = []
    failed: list[tuple[CuratedRow, str]] = []
    for i, row in enumerate(curated):
        theme_key, code, curated_name, market = row
        if i and throttle_s > 0:
            sleep(throttle_s)
        try:
            official = name_of(code)
        except Exception as exc:
            failed.append((row, f"name lookup failed: {exc}"))
            continue
        if not official:
            failed.append((row, "unknown code (empty name)"))
            continue
        if not names_match(curated_name, official):
            failed.append((row, f"name mismatch: curated={curated_name!r} official={official!r}"))
            continue

        bars: list | None = None
        for attempt, delay in enumerate((0.0, *retry_delays)):
            if delay:
                sleep(delay)
            try:
                bars = fetch_bars(code, market, FULL_START, end)
                break
            except Exception as exc:
                logger.warning("ohlcv fetch failed for %s (attempt %d): %s", code, attempt + 1, exc)
        if not bars:
            failed.append((row, "no ohlcv bars"))
            continue
        stored: int | None = None
        for attempt, delay in enumerate((0.0, *PUT_RETRY_DELAYS_S)):
            if delay:
                sleep(delay)
            try:
                stored = catalog.put_bars(bars)
                break
            except Exception as exc:  # 다른 cron 과 쓰기 락 경합 — 잠시 후 재시도
                logger.warning("put_bars failed for %s (attempt %d): %s", code, attempt + 1, exc)
        if stored is None:
            failed.append((row, "put_bars failed after retries"))
            continue
        # 공식명으로 교체해 저장 — 표시 이름의 단일 진실은 pykrx
        validated.append((theme_key, code, official, market))
        print(f"OK {code} {official} ({market}, {stored} bars)", flush=True)
    return validated, failed


def is_hard_failure(reason: str) -> bool:
    """진짜 큐레이션 오류(이름 불일치/미지코드) = 하드. 네트워크/락 = 소프트."""
    return any(reason.startswith(p) for p in _HARD_REASON_PREFIXES)


def load_previous_validated(path: Path | str) -> dict[str, CuratedRow]:
    """이전 validated CSV → code 별 행. 일시 실패 시 기존 유효분 보존용."""
    p = Path(path)
    if not p.exists():
        return {}
    out: dict[str, CuratedRow] = {}
    try:
        with open(p, encoding="utf-8", newline="") as fh:
            for r in csv.DictReader(fh):
                code = str(r["code"]).strip().zfill(6)
                out[code] = (
                    str(r["theme_key"]).strip(),
                    code,
                    str(r["name"]).strip(),
                    str(r["market"]).strip(),
                )
    except (OSError, ValueError, KeyError):
        return {}
    return out


def write_validated(rows: Sequence[CuratedRow], out_path: Path | str) -> None:
    """원자적 쓰기 — temp 에 쓰고 rename. 중단돼도 기존 파일이 반쪽으로 깨지지 않는다."""
    out = Path(out_path)
    tmp = out.with_suffix(out.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["theme_key", "code", "name", "market"])
        writer.writerows(rows)
    os.replace(tmp, out)


def reconcile_validated(
    validated: Sequence[CuratedRow],
    failed: Sequence[tuple[CuratedRow, str]],
    previous: dict[str, CuratedRow],
) -> tuple[list[CuratedRow], list[str]]:
    """소프트 실패(일시 장애) 종목은 이전 유효분을 보존해 유니버스가 쪼그라들지 않게 한다.

    하드 실패(이름 불일치/미지코드)는 진짜 오류이므로 이전 유효분이 있어도 버린다.
    반환 = (최종 행[검증순 + 보존분], 보존한 코드 목록).
    """
    result = list(validated)
    validated_codes = {r[1] for r in validated}
    retained: list[str] = []
    for row, reason in failed:
        code = row[1]
        if is_hard_failure(reason) or code in validated_codes:
            continue
        prev = previous.get(code)
        if prev is not None:
            result.append(prev)
            retained.append(code)
    return result, retained


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--out", type=Path, default=DEFAULT_VALIDATED)
    parser.add_argument("--catalog-db", type=Path, default=ROOT / DEFAULT_CATALOG_PATH)
    parser.add_argument("--end", default=None, help="종료일 ISO (기본 today)")
    args = parser.parse_args(argv)

    from pykrx import stock

    from data.ingest.pykrx_kr import fetch_pykrx_bars

    curated = load_curated(args.csv)
    catalog = MarketDataCatalog(args.catalog_db)
    end = date.fromisoformat(args.end) if args.end else date.today()
    print(f"verifying {len(curated)} curated KR symbols", flush=True)
    validated, failed = verify_and_ingest(
        curated,
        catalog=catalog,
        name_of=stock.get_market_ticker_name,
        fetch_bars=fetch_pykrx_bars,
        end=end,
    )
    previous = load_previous_validated(args.out)
    final, retained = reconcile_validated(validated, failed, previous)

    # 대량 실패 가드: 유효분이 큐레이션의 절반 미만이면 pykrx/KRX 전면 장애로 보고
    # 기존 validated 파일을 덮어쓰지 않는다 (프로덕션 유니버스 truncation 방지).
    if curated and len(final) < len(curated) * MIN_VALIDATED_RATIO:
        print(
            f"\nABORT: validated={len(final)} < {MIN_VALIDATED_RATIO:.0%} of {len(curated)} "
            f"— 대량 실패로 판단, 기존 {args.out} 보존 (덮어쓰지 않음)",
            flush=True,
        )
        return 2

    write_validated(final, args.out)
    print(
        f"\ndone: validated={len(validated)} retained={len(retained)} "
        f"final={len(final)} failed={len(failed)} → {args.out}",
        flush=True,
    )
    for row, reason in failed:
        kind = "HARD" if is_hard_failure(reason) else "soft"
        print(f"  DROP[{kind}] {row[1]} {row[2]} [{row[0]}]: {reason}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
