from __future__ import annotations

from collections.abc import Callable
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, datetime
from importlib import import_module
from io import StringIO
from typing import Any

from data.models import PriceBar

KR_MARKETS = {"kospi", "kosdaq"}


class PykrxDataError(RuntimeError):
    pass


def normalize_kr_symbol(symbol: str) -> str:
    normalized = symbol.strip()
    if not normalized.isdigit():
        raise PykrxDataError(f"{symbol}: Korean equity symbols must be numeric tickers")
    if len(normalized) > 6:
        raise PykrxDataError(f"{symbol}: Korean equity symbols must be at most 6 digits")
    return normalized.zfill(6)


def fetch_pykrx_bars(
    symbol: str,
    market: str,
    start: date,
    end: date,
    fetch_ohlcv: Callable[[str, str, str], Any] | None = None,
) -> list[PriceBar]:
    market_key = market.lower()
    if market_key not in KR_MARKETS:
        raise PykrxDataError("pykrx provider is only supported for --market kospi or kosdaq")

    source_symbol = normalize_kr_symbol(symbol)
    fetcher = fetch_ohlcv or _default_fetch_ohlcv
    frame = fetcher(_to_yyyymmdd(start), _to_yyyymmdd(end), source_symbol)
    bars = _frame_to_bars(frame, source_symbol=source_symbol, market=market_key)
    if not bars:
        raise PykrxDataError(f"{source_symbol}: no OHLCV bars returned from pykrx")
    return bars


def _frame_to_bars(frame: Any, *, source_symbol: str, market: str) -> list[PriceBar]:
    bars: list[PriceBar] = []
    for timestamp, row in frame.iterrows():
        open_value = _row_value(row, "시가", "open")
        high_value = _row_value(row, "고가", "high")
        low_value = _row_value(row, "저가", "low")
        close_value = _row_value(row, "종가", "close")
        volume_value = _row_value(row, "거래량", "volume")
        if any(value is None for value in (open_value, high_value, low_value, close_value, volume_value)):
            continue

        open_float = float(open_value)
        high_float = float(high_value)
        low_float = float(low_value)
        close_float = float(close_value)
        if min(open_float, high_float, low_float, close_float) <= 0:
            continue

        bars.append(
            PriceBar(
                symbol=source_symbol,
                market=market.lower(),
                source_symbol=source_symbol,
                ts=_to_date(timestamp),
                open=open_float,
                high=high_float,
                low=low_float,
                close=close_float,
                volume=float(volume_value),
                freq="1d",
                currency="KRW",
                source=f"pykrx:{source_symbol}",
            )
        )
    return sorted(bars, key=lambda bar: bar.ts)


def _default_fetch_ohlcv(from_date: str, to_date: str, ticker: str) -> Any:
    with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
        stock_module = import_module("pykrx.stock")
    return stock_module.get_market_ohlcv_by_date(from_date, to_date, ticker)


def _row_value(row: Any, korean_name: str, english_name: str) -> Any:
    for key in (korean_name, english_name, english_name.capitalize()):
        if key in row:
            return row[key]
    return None


def _to_date(value: Any) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    return value.to_pydatetime().date()


def _to_yyyymmdd(value: date) -> str:
    return value.strftime("%Y%m%d")
