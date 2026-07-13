from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import Any
from urllib.parse import quote
from urllib.request import Request, urlopen

from data.models import PriceBar

YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
YAHOO_ADJUSTED_SOURCE_MARKER = "adjusted=true"
USER_AGENT = "Mozilla/5.0 trader/0.1"
YAHOO_INTRADAY_INTERVALS = {"1m", "2m", "5m", "15m", "30m", "60m", "90m", "1h"}
MAX_QUOTE_SYMBOLS = 250
QUOTE_SYMBOL_PATTERN = re.compile(r"^[A-Z0-9^=][A-Z0-9.^=_-]{0,23}$")


class YahooDataError(RuntimeError):
    pass


@dataclass(frozen=True)
class YahooQuote:
    symbol: str
    source_symbol: str
    price: float
    day_open: float | None
    timestamp: datetime
    currency: str
    source: str = "yfinance:1m"


def resolve_yahoo_symbol(symbol: str, market: str) -> str:
    normalized = symbol.strip().upper()
    market_key = market.lower()
    if market_key == "kospi" and normalized.isdigit():
        return f"{normalized}.KS"
    if market_key == "kosdaq" and normalized.isdigit():
        return f"{normalized}.KQ"
    if market_key == "crypto":
        if "-" in normalized:
            return normalized
        if "/" in normalized:
            base, quote_currency = normalized.split("/", 1)
            return f"{base}-{quote_currency}"
        return f"{normalized}-USD"
    return normalized


def fetch_yahoo_bars(
    symbol: str,
    market: str,
    start: date,
    end: date,
    interval: str = "1d",
) -> list[PriceBar]:
    source_symbol = resolve_yahoo_symbol(symbol, market)
    payload = _fetch_chart_payload(source_symbol, start, end, interval)
    result = payload.get("chart", {}).get("result") or []
    if not result:
        raise YahooDataError(f"{source_symbol}: Yahoo chart response has no result")

    chart = result[0]
    timestamps = chart.get("timestamp") or []
    quote_data = (chart.get("indicators", {}).get("quote") or [{}])[0]
    adjusted_data = (chart.get("indicators", {}).get("adjclose") or [{}])[0]
    meta = chart.get("meta", {})
    currency = str(meta.get("currency") or "")
    source = (
        f"{YAHOO_CHART_URL.format(symbol=quote(source_symbol))}?"
        f"{YAHOO_ADJUSTED_SOURCE_MARKER}&interval={quote(interval)}"
    )

    bars: list[PriceBar] = []
    for index, timestamp in enumerate(timestamps):
        parsed = _bar_from_quote(
            symbol=symbol,
            market=market,
            source_symbol=source_symbol,
            timestamp=timestamp,
            quote_data=quote_data,
            adjusted_data=adjusted_data,
            index=index,
            interval=interval,
            currency=currency,
            source=source,
        )
        if parsed is not None:
            bars.append(parsed)

    if not bars:
        raise YahooDataError(f"{source_symbol}: Yahoo chart response had no complete bars")
    return bars


def _fetch_chart_payload(symbol: str, start: date, end: date, interval: str) -> dict:
    period1 = _to_epoch(start)
    period2 = _to_epoch(end) + 24 * 60 * 60
    url = (
        YAHOO_CHART_URL.format(symbol=quote(symbol))
        + f"?period1={period1}&period2={period2}&interval={quote(interval)}"
        + "&events=history&includeAdjustedClose=true"
    )
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))
    error = payload.get("chart", {}).get("error")
    if error:
        description = error.get("description") or error
        raise YahooDataError(f"{symbol}: {description}")
    return payload


def _bar_from_quote(
    *,
    symbol: str,
    market: str,
    source_symbol: str,
    timestamp: int,
    quote_data: dict,
    adjusted_data: dict,
    index: int,
    interval: str,
    currency: str,
    source: str,
) -> PriceBar | None:
    try:
        open_value = quote_data["open"][index]
        high_value = quote_data["high"][index]
        low_value = quote_data["low"][index]
        close_value = quote_data["close"][index]
        volume_value = quote_data["volume"][index]
    except (KeyError, IndexError):
        return None
    values = (open_value, high_value, low_value, close_value, volume_value)
    if any(value is None for value in values):
        return None
    adjusted_close = _value_at(adjusted_data, "adjclose", index)
    if adjusted_close is not None and close_value:
        adjustment_ratio = adjusted_close / close_value
        open_value *= adjustment_ratio
        high_value *= adjustment_ratio
        low_value *= adjustment_ratio
        close_value = adjusted_close
    moment = datetime.fromtimestamp(timestamp, tz=UTC)
    bar_timestamp: date = (
        moment.replace(tzinfo=None) if interval in YAHOO_INTRADAY_INTERVALS else moment.date()
    )
    return PriceBar(
        symbol=symbol.upper(),
        market=market.lower(),
        source_symbol=source_symbol,
        ts=bar_timestamp,
        open=float(open_value),
        high=float(high_value),
        low=float(low_value),
        close=float(close_value),
        volume=float(volume_value),
        freq=interval,
        currency=currency,
        source=source,
    )


def _value_at(data: dict, key: str, index: int) -> float | None:
    try:
        value = data[key][index]
    except (KeyError, IndexError, TypeError):
        return None
    if value is None:
        return None
    return float(value)


def _to_epoch(value: date) -> int:
    return int(datetime.combine(value, time.min, tzinfo=UTC).timestamp())


def aggregate_intraday_bars(
    bars: Sequence[PriceBar], *, bars_per_bucket: int, frequency: str
) -> list[PriceBar]:
    """Aggregate consecutive intraday bars without crossing a UTC trading date."""

    if bars_per_bucket < 1:
        raise ValueError("bars_per_bucket must be >= 1")
    ordered = sorted(bars, key=lambda bar: bar.ts)
    result: list[PriceBar] = []
    bucket: list[PriceBar] = []
    bucket_date: date | None = None

    def flush() -> None:
        if not bucket:
            return
        first, last = bucket[0], bucket[-1]
        result.append(
            PriceBar(
                symbol=first.symbol,
                market=first.market,
                source_symbol=first.source_symbol,
                ts=first.ts,
                open=first.open,
                high=max(bar.high for bar in bucket),
                low=min(bar.low for bar in bucket),
                close=last.close,
                volume=sum(bar.volume for bar in bucket),
                freq=frequency,
                currency=first.currency,
                source=f"{first.source}|aggregate={frequency}",
            )
        )
        bucket.clear()

    for bar in ordered:
        if not isinstance(bar.ts, datetime):
            raise ValueError("aggregate_intraday_bars requires datetime timestamps")
        current_date = bar.ts.date()
        if bucket and (current_date != bucket_date or len(bucket) >= bars_per_bucket):
            flush()
        if not bucket:
            bucket_date = current_date
        bucket.append(bar)
    flush()
    return result


def fetch_yahoo_quotes(
    symbols: Sequence[str],
    market: str,
    *,
    download: Callable[..., Any] | None = None,
) -> dict[str, YahooQuote]:
    """Fetch the latest one-minute indicative quote for up to 250 Yahoo symbols."""

    normalized = list(dict.fromkeys(symbol.strip().upper() for symbol in symbols if symbol.strip()))
    if not normalized:
        return {}
    if len(normalized) > MAX_QUOTE_SYMBOLS:
        raise YahooDataError(
            f"Yahoo quote batch is limited to {MAX_QUOTE_SYMBOLS} symbols, got {len(normalized)}"
        )
    invalid = [symbol for symbol in normalized if not QUOTE_SYMBOL_PATTERN.fullmatch(symbol)]
    if invalid:
        raise YahooDataError(f"invalid Yahoo quote symbols: {', '.join(invalid[:5])}")

    source_by_symbol = {symbol: resolve_yahoo_symbol(symbol, market) for symbol in normalized}
    try:
        if download is None:
            import yfinance as yf

            download = yf.download
        frame = download(
            list(source_by_symbol.values()),
            period="1d",
            interval="1m",
            auto_adjust=False,
            progress=False,
            threads=True,
            timeout=10,
            multi_level_index=True,
        )
    except Exception as exc:
        raise YahooDataError(f"Yahoo quote batch failed: {exc}") from exc
    if frame is None or getattr(frame, "empty", True):
        raise YahooDataError("Yahoo quote batch returned no rows")

    try:
        closes = frame["Close"]
        opens = frame["Open"]
    except (KeyError, TypeError) as exc:
        raise YahooDataError("Yahoo quote batch did not contain OHLC columns") from exc

    currency = "KRW" if market.lower() in {"kospi", "kosdaq"} else "USD"
    quotes: dict[str, YahooQuote] = {}
    for symbol, source_symbol in source_by_symbol.items():
        close_series = _frame_series(closes, source_symbol)
        if close_series is None:
            continue
        close_series = close_series.dropna()
        if close_series.empty:
            continue
        open_series = _frame_series(opens, source_symbol)
        open_value = None
        if open_series is not None:
            open_series = open_series.dropna()
            if not open_series.empty:
                open_value = float(open_series.iloc[0])
        timestamp = _utc_datetime(close_series.index[-1])
        quotes[symbol] = YahooQuote(
            symbol=symbol,
            source_symbol=source_symbol,
            price=float(close_series.iloc[-1]),
            day_open=open_value,
            timestamp=timestamp,
            currency=currency,
        )
    return quotes


def _frame_series(frame: Any, source_symbol: str) -> Any | None:
    columns = getattr(frame, "columns", None)
    if columns is None:
        return frame
    if source_symbol in columns:
        return frame[source_symbol]
    if len(columns) == 1:
        return frame.iloc[:, 0]
    return None


def _utc_datetime(value: Any) -> datetime:
    moment = value.to_pydatetime() if hasattr(value, "to_pydatetime") else value
    if not isinstance(moment, datetime):
        raise YahooDataError(f"Yahoo quote timestamp is not a datetime: {value!r}")
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)
