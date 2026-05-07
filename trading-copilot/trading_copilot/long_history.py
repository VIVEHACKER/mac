from __future__ import annotations

from datetime import date, datetime, timezone
import json
from typing import Any, Callable
from urllib.parse import quote
from urllib.request import Request, urlopen

from .industry_rotation import (
    IndustryDataError,
    PricePoint,
    extract_chart_result,
)


class YahooDailyHistoryProvider:
    """Daily-resolution Yahoo chart provider for arbitrary historical windows.

    Yahoo's ``range=max`` endpoint downsamples to monthly when the window is wide,
    so multi-decade backtests would lose daily resolution. This provider passes
    explicit ``period1`` / ``period2`` Unix timestamps which keep daily granularity
    for any window the API supports.

    Symbol convention matches industry_rotation.YahooHistoryProvider:
      - US equities/ETFs: ``SPY``, ``QQQ``, ``GLD``
      - Indices: ``^VIX``, ``^GSPC``
      - Korean (Yahoo suffix): ``005930.KS``
    """

    BASE_URL = "https://query1.finance.yahoo.com/v8/finance/chart"

    def __init__(
        self,
        start: date,
        end: date,
        fetch_json: Callable[[str], dict[str, Any]] | None = None,
    ):
        if start >= end:
            raise ValueError("start must precede end")
        self.start = start
        self.end = end
        self.fetch_json = fetch_json or default_fetch_json

    def history(
        self,
        symbol: str,
        range_period: str = "6mo",  # ignored; preserved for Protocol compatibility
        interval: str = "1d",
    ) -> tuple[PricePoint, ...]:
        normalized = symbol.strip().upper()
        period1 = int(datetime(self.start.year, self.start.month, self.start.day, tzinfo=timezone.utc).timestamp())
        period2 = int(datetime(self.end.year, self.end.month, self.end.day, tzinfo=timezone.utc).timestamp())
        source = (
            f"{self.BASE_URL}/{quote(normalized)}"
            f"?period1={period1}&period2={period2}&interval={quote(interval)}"
        )
        payload = self.fetch_json(source)
        result = extract_chart_result(payload, normalized)
        timestamps = result.get("timestamp")
        indicators = result.get("indicators", {})
        quotes = indicators.get("quote") if isinstance(indicators, dict) else None
        if not isinstance(timestamps, list) or not isinstance(quotes, list) or not quotes:
            raise IndustryDataError(f"{normalized}: Yahoo chart response has no history")
        closes = quotes[0].get("close") if isinstance(quotes[0], dict) else None
        if not isinstance(closes, list):
            raise IndustryDataError(f"{normalized}: Yahoo chart response has no close prices")

        points: list[PricePoint] = []
        for timestamp, close in zip(timestamps, closes):
            if isinstance(close, bool) or not isinstance(close, (int, float)):
                continue
            if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
                continue
            observed_at = datetime.fromtimestamp(timestamp, tz=timezone.utc).date()
            points.append(PricePoint(normalized, observed_at, float(close)))
        if len(points) < 2:
            raise IndustryDataError(f"{normalized}: insufficient price history in window")
        return tuple(points)


def default_fetch_json(url: str) -> dict[str, Any]:
    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 trading-copilot/0.1",
        },
    )
    with urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


__all__ = ["YahooDailyHistoryProvider"]
