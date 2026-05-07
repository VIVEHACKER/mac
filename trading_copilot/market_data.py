from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
from typing import Any, Callable, Protocol
from urllib.parse import quote
from urllib.request import Request, urlopen

from .storage import normalize_ticker


class MarketDataProvider(Protocol):
    def snapshot(self, ticker: str) -> "MarketSnapshot":
        pass


@dataclass(frozen=True)
class MarketSnapshot:
    ticker: str
    price: float
    previous_close: float
    change: float
    change_percent: float
    currency: str
    as_of: datetime
    source: str


class MarketDataError(RuntimeError):
    pass


class YahooChartProvider:
    BASE_URL = "https://query1.finance.yahoo.com/v8/finance/chart"

    def __init__(self, fetch_json: Callable[[str], dict[str, Any]] | None = None):
        self.fetch_json = fetch_json or default_fetch_json

    def snapshot(self, ticker: str) -> MarketSnapshot:
        normalized = normalize_ticker(ticker)
        errors: list[str] = []
        snapshots: list[MarketSnapshot] = []
        for yahoo_symbol in yahoo_symbol_candidates(normalized):
            source = f"{self.BASE_URL}/{quote(yahoo_symbol)}"
            try:
                payload = self.fetch_json(source)
                meta = self._extract_meta(payload, yahoo_symbol)
                snapshots.append(self._snapshot_from_meta(yahoo_symbol, meta, source))
            except Exception as exc:
                errors.append(f"{yahoo_symbol}: {exc}")
        if snapshots:
            return max(snapshots, key=lambda snapshot: snapshot.as_of)
        raise MarketDataError(f"{normalized}: Yahoo chart lookup failed ({'; '.join(errors)})")

    @staticmethod
    def _snapshot_from_meta(ticker: str, meta: dict[str, Any], source: str) -> MarketSnapshot:
        resolved = str(meta.get("symbol") or ticker).upper()

        price = required_number(meta, "regularMarketPrice", resolved)
        previous_close = optional_number(meta, "previousClose")
        if previous_close is None:
            previous_close = optional_number(meta, "chartPreviousClose")
        if previous_close is None:
            raise MarketDataError(f"{resolved}: missing previous close in Yahoo chart response")

        change = price - previous_close
        change_percent = (change / previous_close * 100) if previous_close else 0.0
        timestamp = optional_number(meta, "regularMarketTime")
        as_of = (
            datetime.fromtimestamp(timestamp, tz=timezone.utc)
            if timestamp is not None
            else datetime.now(timezone.utc)
        )

        return MarketSnapshot(
            ticker=resolved,
            price=price,
            previous_close=previous_close,
            change=change,
            change_percent=change_percent,
            currency=str(meta.get("currency") or "USD"),
            as_of=as_of,
            source=source,
        )

    @staticmethod
    def _extract_meta(payload: dict[str, Any], ticker: str) -> dict[str, Any]:
        chart = payload.get("chart")
        if not isinstance(chart, dict):
            raise MarketDataError(f"{ticker}: invalid Yahoo chart response")
        if chart.get("error"):
            raise MarketDataError(f"{ticker}: Yahoo chart error: {chart['error']}")
        results = chart.get("result")
        if not isinstance(results, list) or not results:
            raise MarketDataError(f"{ticker}: Yahoo chart response has no result")
        first = results[0]
        if not isinstance(first, dict):
            raise MarketDataError(f"{ticker}: Yahoo chart result is invalid")
        meta = first.get("meta")
        if not isinstance(meta, dict):
            raise MarketDataError(f"{ticker}: Yahoo chart result has no meta")
        return meta


def default_fetch_json(url: str) -> dict[str, Any]:
    request = Request(url, headers={"User-Agent": "trading-copilot/0.1"})
    with urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


KOREA_INDEX_ALIASES = {
    "KOSPI": "^KS11",
    "KOSDAQ": "^KQ11",
}


def yahoo_symbol_candidates(ticker: str) -> tuple[str, ...]:
    normalized = normalize_ticker(ticker)
    if normalized in KOREA_INDEX_ALIASES:
        return (KOREA_INDEX_ALIASES[normalized],)
    if re.fullmatch(r"\d{6}", normalized):
        return (f"{normalized}.KS", f"{normalized}.KQ")
    return (normalized,)


def required_number(payload: dict[str, Any], key: str, ticker: str) -> float:
    value = optional_number(payload, key)
    if value is None:
        raise MarketDataError(f"{ticker}: missing numeric field {key}")
    return value


def optional_number(payload: dict[str, Any], key: str) -> float | None:
    value = payload.get(key)
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def format_quote_report(snapshot: MarketSnapshot) -> str:
    return "\n".join(
        [
            f"# Quote Snapshot - {snapshot.ticker}",
            "",
            "No trade recommendations. This is sourced market data for human review.",
            "",
            f"Price: {snapshot.price:.2f} {snapshot.currency}",
            f"Previous Close: {snapshot.previous_close:.2f} {snapshot.currency}",
            f"Change: {format_signed(snapshot.change)} ({format_signed(snapshot.change_percent)}%)",
            f"As Of: {snapshot.as_of.isoformat()}",
            f"Source: {snapshot.source}",
        ]
    )


def format_snapshot_line(snapshot: MarketSnapshot) -> str:
    return (
        f"- {snapshot.ticker}: {snapshot.price:.2f} {snapshot.currency} "
        f"({format_signed(snapshot.change)}, {format_signed(snapshot.change_percent)}%) "
        f"as of {snapshot.as_of.isoformat()} | Source: {snapshot.source}"
    )


def format_signed(value: float) -> str:
    return f"{value:+.2f}"
