from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from math import isfinite
from typing import Any

from valuation.option_vol import OptionQuote


class YahooOptionChainError(RuntimeError):
    pass


@dataclass(frozen=True)
class YahooOptionChainFetch:
    quotes: list[OptionQuote]
    expirations: tuple[date, ...]
    source: str


def fetch_yahoo_option_quotes(
    symbol: str,
    *,
    asof_date: date,
    target_days: int = 30,
    expirations: tuple[date, ...] = (),
    ticker_factory: Any | None = None,
) -> YahooOptionChainFetch:
    ticker = _ticker(symbol, ticker_factory)
    selected_expirations = expirations or _select_yahoo_expirations(
        _available_expirations(ticker),
        asof_date=asof_date,
        target_days=target_days,
    )
    quotes: list[OptionQuote] = []
    for expiration in selected_expirations:
        quotes.extend(_option_chain_quotes(ticker, expiration))
    if not quotes:
        raise YahooOptionChainError(f"{symbol}: Yahoo returned no usable option quotes")
    return YahooOptionChainFetch(
        quotes=quotes,
        expirations=tuple(selected_expirations),
        source=f"yahoo-options:{symbol.upper()}",
    )


def _ticker(symbol: str, ticker_factory: Any | None) -> Any:
    if ticker_factory is not None:
        return ticker_factory(symbol)
    try:
        import yfinance as yf
    except ImportError as exc:
        raise YahooOptionChainError("yfinance is not installed") from exc
    return yf.Ticker(symbol)


def _available_expirations(ticker: Any) -> tuple[date, ...]:
    raw = getattr(ticker, "options", ())
    expirations: list[date] = []
    for item in raw:
        try:
            expirations.append(date.fromisoformat(str(item)))
        except ValueError:
            continue
    if not expirations:
        raise YahooOptionChainError("Yahoo returned no option expirations")
    return tuple(sorted(expirations))


def _select_yahoo_expirations(
    expirations: tuple[date, ...],
    *,
    asof_date: date,
    target_days: int,
) -> tuple[date, ...]:
    future = tuple(expiration for expiration in expirations if expiration > asof_date)
    if not future:
        raise YahooOptionChainError("Yahoo option expirations are all before asof_date")
    day_pairs = [(expiration, (expiration - asof_date).days) for expiration in future]
    below = [pair for pair in day_pairs if pair[1] <= target_days]
    above = [pair for pair in day_pairs if pair[1] >= target_days]
    if below and above:
        return (
            max(below, key=lambda item: item[1])[0],
            min(above, key=lambda item: item[1])[0],
        )
    if len(day_pairs) == 1:
        return (day_pairs[0][0],)
    closest = sorted(day_pairs, key=lambda item: abs(item[1] - target_days))[:2]
    return tuple(sorted(item[0] for item in closest))


def _option_chain_quotes(ticker: Any, expiration: date) -> list[OptionQuote]:
    chain = ticker.option_chain(expiration.isoformat())
    calls = _records_by_strike(getattr(chain, "calls", None))
    puts = _records_by_strike(getattr(chain, "puts", None))
    quotes: list[OptionQuote] = []
    for strike in sorted(set(calls) | set(puts)):
        call = calls.get(strike, {})
        put = puts.get(strike, {})
        quotes.append(
            OptionQuote(
                expiration=expiration,
                strike=strike,
                call_bid=_number(call.get("bid")),
                call_ask=_number(call.get("ask")),
                put_bid=_number(put.get("bid")),
                put_ask=_number(put.get("ask")),
                call_last_trade=_date_value(call.get("lastTradeDate")),
                put_last_trade=_date_value(put.get("lastTradeDate")),
            )
        )
    return quotes


def _records_by_strike(frame: Any) -> dict[float, dict[str, Any]]:
    if frame is None:
        return {}
    records = frame.to_dict("records")
    rows: dict[float, dict[str, Any]] = {}
    for item in records:
        strike = _number(item.get("strike"))
        if strike is not None and strike > 0:
            rows[strike] = item
    return rows


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not isfinite(number):
        return None
    return number


def _date_value(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if hasattr(value, "date"):
        return value.date()
    text = str(value)
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None
