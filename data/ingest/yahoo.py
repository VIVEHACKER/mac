from __future__ import annotations

import json
from datetime import UTC, date, datetime, time
from urllib.parse import quote
from urllib.request import Request, urlopen

from data.models import PriceBar

YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
YAHOO_ADJUSTED_SOURCE_MARKER = "adjusted=true"
USER_AGENT = "Mozilla/5.0 trader/0.1"


class YahooDataError(RuntimeError):
    pass


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
    source = f"{YAHOO_CHART_URL.format(symbol=quote(source_symbol))}?{YAHOO_ADJUSTED_SOURCE_MARKER}"

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
    return PriceBar(
        symbol=symbol.upper(),
        market=market.lower(),
        source_symbol=source_symbol,
        ts=datetime.fromtimestamp(timestamp, tz=UTC).date(),
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
