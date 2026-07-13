"""마켓 히트맵 외부 데이터 수집 — yfinance 배치 + FRED(기존 ingest 모듈 재사용).

실패 허용 설계: 심볼/시리즈 단위로 조용히 비우고(로그만 남김) 페이지는
가용한 데이터로 렌더된다. 배치 실패 시 심볼 단위 폴백 + 백오프 재시도.
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime

logger = logging.getLogger(__name__)

DailySeries = list[tuple[date, float]]

_RETRY_DELAYS_S = (2.0, 5.0)
_PER_SYMBOL_THROTTLE_S = 0.4


def _to_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _series_from_frame(frame: object) -> DailySeries:
    """yfinance DataFrame(단일 심볼 뷰)에서 (date, close) 목록 추출."""
    out: DailySeries = []
    try:
        closes = frame["Close"]  # type: ignore[index]
        items = closes.dropna().items()  # type: ignore[union-attr]
    except Exception:
        return out
    for ts, close in items:
        d = _to_date(ts)
        if d is None:
            continue
        try:
            c = float(close)
        except (TypeError, ValueError):
            continue
        if c == c and c > 0:  # NaN/음수 가드
            out.append((d, c))
    out.sort(key=lambda pair: pair[0])
    return out


def _frame_for_symbol(frame: object, symbol: str) -> object:
    """group_by="ticker" MultiIndex 프레임에서 심볼 레벨을 벗긴다 (단일 심볼 포함)."""
    try:
        return frame[symbol]  # type: ignore[index]
    except Exception:
        return frame


def fetch_yfinance_closes(
    symbols: list[str], start: date, end: date | None = None
) -> dict[str, DailySeries]:
    """일별 종가 배치 수집. 배치 실패 → 백오프 재시도 → 심볼 단위 폴백."""
    if not symbols:
        return {}
    import yfinance as yf  # 무거운 의존 — market-map 경로에서만 로드

    kwargs = {
        "start": start.isoformat(),
        "end": end.isoformat() if end else None,
        "interval": "1d",
        "auto_adjust": True,
        "progress": False,
        "threads": False,
        "group_by": "ticker",
    }

    frame = None
    for attempt, delay in enumerate((0.0, *_RETRY_DELAYS_S)):
        if delay:
            time.sleep(delay)
        try:
            frame = yf.download(symbols, **kwargs)
            break
        except Exception as exc:  # 네트워크/레이트리밋 — 재시도 대상
            logger.warning("yfinance batch download failed (attempt %d): %s", attempt + 1, exc)

    result: dict[str, DailySeries] = {}
    if frame is not None and len(getattr(frame, "columns", [])) > 0:
        for symbol in symbols:
            result[symbol] = _series_from_frame(_frame_for_symbol(frame, symbol))

    # 배치 전멸뿐 아니라 부분 실패(일부 심볼만 NaN/누락)도 심볼 단위로 천천히 재시도
    for symbol in [s for s in symbols if not result.get(s)]:
        time.sleep(_PER_SYMBOL_THROTTLE_S)
        try:
            sub = yf.download(symbol, **kwargs)
            result[symbol] = _series_from_frame(_frame_for_symbol(sub, symbol))
        except Exception as exc:
            logger.warning("yfinance single download failed for %s: %s", symbol, exc)
            result[symbol] = []

    empty = [s for s, v in result.items() if not v]
    if empty:
        logger.warning("yfinance returned no data for: %s", ", ".join(empty))
    return result


def fetch_fred_closes(series_id: str, start: date, end: date | None = None) -> DailySeries:
    """FRED 시계열 (기존 data.ingest.fred_macro 재사용 — keyless CSV + API 폴백). 실패 시 []."""
    from data.ingest.fred_macro import fetch_fred_series

    try:
        observations = fetch_fred_series(series_id, start, end or date.today())
    except Exception as exc:
        logger.warning("FRED fetch failed for %s: %s", series_id, exc)
        return []
    out = [(o.asof_date, o.value) for o in observations if o.value is not None]
    out.sort(key=lambda pair: pair[0])
    return out
