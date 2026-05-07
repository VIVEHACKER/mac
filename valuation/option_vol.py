from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from math import exp, sqrt
from pathlib import Path


@dataclass(frozen=True)
class OptionQuote:
    expiration: date
    strike: float
    call_bid: float | None = None
    call_ask: float | None = None
    put_bid: float | None = None
    put_ask: float | None = None
    call_last_trade: date | None = None
    put_last_trade: date | None = None

    @property
    def call_mid(self) -> float | None:
        return _mid(self.call_bid, self.call_ask)

    @property
    def put_mid(self) -> float | None:
        return _mid(self.put_bid, self.put_ask)


@dataclass(frozen=True)
class VarianceSwapResult:
    expiration: date
    days_to_expiration: int
    forward: float
    reference_strike: float
    variance: float
    volatility: float


@dataclass(frozen=True)
class VixCalculationResult:
    asof_date: date
    target_days: int
    volatility: float
    risk_free_rate: float
    near: VarianceSwapResult
    next: VarianceSwapResult
    warnings: tuple[str, ...] = ()
    source: str = "option-chain:vix-formula"


@dataclass(frozen=True)
class OptionChainQuality:
    quote_count: int
    expiration_count: int
    warnings: tuple[str, ...]
    errors: tuple[str, ...]


def load_option_quotes_csv(path: Path) -> list[OptionQuote]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = [_normalize_row(row) for row in reader]
    quotes: list[OptionQuote] = []
    for row in rows:
        expiration = row.get("expiration") or row.get("expiry")
        strike = row.get("strike")
        if not expiration or not strike:
            raise ValueError("CSV must include expiration and strike columns")
        quotes.append(
            OptionQuote(
                expiration=date.fromisoformat(expiration),
                strike=_required_float(strike, "strike"),
                call_bid=_optional_float(row.get("call_bid")),
                call_ask=_optional_float(row.get("call_ask")),
                put_bid=_optional_float(row.get("put_bid")),
                put_ask=_optional_float(row.get("put_ask")),
                call_last_trade=_optional_date(row.get("call_last_trade") or row.get("last_trade_date")),
                put_last_trade=_optional_date(row.get("put_last_trade") or row.get("last_trade_date")),
            )
        )
    return quotes


def calculate_vix_like_index(
    quotes: list[OptionQuote],
    *,
    asof_date: date,
    target_days: int = 30,
    risk_free_rate: float = 0.0,
    max_quote_age_days: int | None = None,
    require_last_trade: bool = False,
    max_bid_ask_spread_pct: float | None = None,
) -> VixCalculationResult:
    if target_days < 1:
        raise ValueError("target_days must be >= 1")
    quality = validate_option_chain(
        quotes,
        asof_date=asof_date,
        target_days=target_days,
        max_quote_age_days=max_quote_age_days,
        require_last_trade=require_last_trade,
        max_bid_ask_spread_pct=max_bid_ask_spread_pct,
    )
    if quality.errors:
        raise ValueError("; ".join(quality.errors))
    by_expiration: dict[date, list[OptionQuote]] = defaultdict(list)
    for quote in quotes:
        if quote.expiration > asof_date and quote.strike > 0:
            by_expiration[quote.expiration].append(quote)
    if not by_expiration:
        raise ValueError("no option quotes expire after asof_date")

    expirations = sorted(by_expiration)
    near_expiration, next_expiration = _select_expirations(
        expirations,
        asof_date=asof_date,
        target_days=target_days,
    )
    near = _variance_for_expiration(
        by_expiration[near_expiration],
        asof_date=asof_date,
        expiration=near_expiration,
        risk_free_rate=risk_free_rate,
    )
    if near_expiration == next_expiration:
        return VixCalculationResult(
            asof_date=asof_date,
            target_days=target_days,
            volatility=near.volatility,
            risk_free_rate=risk_free_rate,
            near=near,
            next=near,
            warnings=quality.warnings,
        )

    next_result = _variance_for_expiration(
        by_expiration[next_expiration],
        asof_date=asof_date,
        expiration=next_expiration,
        risk_free_rate=risk_free_rate,
    )
    weight = (target_days - near.days_to_expiration) / (
        next_result.days_to_expiration - near.days_to_expiration
    )
    weight = min(1.0, max(0.0, weight))
    variance = near.variance * (1.0 - weight) + next_result.variance * weight
    return VixCalculationResult(
        asof_date=asof_date,
        target_days=target_days,
        volatility=sqrt(variance),
        risk_free_rate=risk_free_rate,
        near=near,
        next=next_result,
        warnings=quality.warnings,
    )


def validate_option_chain(
    quotes: list[OptionQuote],
    *,
    asof_date: date,
    target_days: int = 30,
    min_strikes: int = 5,
    max_quote_age_days: int | None = None,
    require_last_trade: bool = False,
    max_bid_ask_spread_pct: float | None = None,
) -> OptionChainQuality:
    if max_quote_age_days is not None and max_quote_age_days < 0:
        raise ValueError("max_quote_age_days must be >= 0")
    if max_bid_ask_spread_pct is not None and max_bid_ask_spread_pct < 0:
        raise ValueError("max_bid_ask_spread_pct must be >= 0")
    usable_quotes = [quote for quote in quotes if quote.expiration > asof_date and quote.strike > 0]
    errors: list[str] = []
    warnings: list[str] = []
    if not usable_quotes:
        errors.append("no option quotes expire after asof_date")
        return OptionChainQuality(0, 0, tuple(warnings), tuple(errors))

    by_expiration: dict[date, list[OptionQuote]] = defaultdict(list)
    for quote in usable_quotes:
        by_expiration[quote.expiration].append(quote)

    expirations = sorted(by_expiration)
    below = [expiration for expiration in expirations if (expiration - asof_date).days <= target_days]
    above = [expiration for expiration in expirations if (expiration - asof_date).days >= target_days]
    if len(expirations) == 1:
        warnings.append("only one expiration is available; no term-structure interpolation")
    elif not below or not above:
        warnings.append("target days are not bracketed by expirations; using nearest expirations")

    selected = set(
        _select_expirations(
            expirations,
            asof_date=asof_date,
            target_days=target_days,
        )
    )
    for expiration in sorted(selected):
        rows = by_expiration[expiration]
        strikes = {quote.strike for quote in rows}
        if len(strikes) < 3:
            errors.append(f"{expiration}: at least three strikes are required")
        elif len(strikes) < min_strikes:
            warnings.append(f"{expiration}: only {len(strikes)} strikes available")
        inverted = sum(1 for quote in rows if _has_inverted_market(quote))
        if inverted:
            warnings.append(f"{expiration}: {inverted} inverted bid/ask row(s) ignored")
        missing_sides = sum(1 for quote in rows if quote.call_mid is None or quote.put_mid is None)
        if missing_sides:
            warnings.append(f"{expiration}: {missing_sides} row(s) missing call or put mid")
        stale = _stale_quote_count(rows, asof_date, max_quote_age_days)
        if stale:
            warnings.append(f"{expiration}: {stale} stale option side(s)")
        missing_trade_dates = _missing_last_trade_count(rows) if require_last_trade else 0
        if missing_trade_dates:
            warnings.append(f"{expiration}: {missing_trade_dates} option side(s) missing last trade date")
        wide_spreads = _wide_spread_count(rows, max_bid_ask_spread_pct)
        if wide_spreads:
            warnings.append(f"{expiration}: {wide_spreads} option side(s) exceed bid/ask spread limit")

    return OptionChainQuality(
        quote_count=len(usable_quotes),
        expiration_count=len(expirations),
        warnings=tuple(warnings),
        errors=tuple(errors),
    )


def _select_expirations(
    expirations: list[date],
    *,
    asof_date: date,
    target_days: int,
) -> tuple[date, date]:
    day_pairs = [(expiration, (expiration - asof_date).days) for expiration in expirations]
    below = [pair for pair in day_pairs if pair[1] <= target_days]
    above = [pair for pair in day_pairs if pair[1] >= target_days]
    if below and above:
        return max(below, key=lambda item: item[1])[0], min(above, key=lambda item: item[1])[0]
    if len(day_pairs) == 1:
        return day_pairs[0][0], day_pairs[0][0]
    closest = sorted(day_pairs, key=lambda item: abs(item[1] - target_days))[:2]
    selected = sorted(item[0] for item in closest)
    return selected[0], selected[1]


def _variance_for_expiration(
    quotes: list[OptionQuote],
    *,
    asof_date: date,
    expiration: date,
    risk_free_rate: float,
) -> VarianceSwapResult:
    days_to_expiration = (expiration - asof_date).days
    if days_to_expiration <= 0:
        raise ValueError("expiration must be after asof_date")
    term_years = days_to_expiration / 365.0
    strike_quotes = {quote.strike: quote for quote in quotes if quote.strike > 0}
    strikes = sorted(strike_quotes)
    if len(strikes) < 3:
        raise ValueError(f"{expiration}: at least three strikes are required")

    forward_quote = _forward_reference_quote(strike_quotes, strikes, risk_free_rate, term_years)
    forward = forward_quote.strike + exp(risk_free_rate * term_years) * (
        (forward_quote.call_mid or 0.0) - (forward_quote.put_mid or 0.0)
    )
    reference_candidates = [strike for strike in strikes if strike <= forward]
    reference_strike = max(reference_candidates) if reference_candidates else strikes[0]

    total = 0.0
    contributions = 0
    for index, strike in enumerate(strikes):
        quote = strike_quotes[strike]
        option_price = _otm_mid(quote, reference_strike)
        if option_price is None or option_price <= 0:
            continue
        delta_k = _strike_width(strikes, index)
        total += delta_k / (strike * strike) * exp(risk_free_rate * term_years) * option_price
        contributions += 1
    if contributions < 3:
        raise ValueError(f"{expiration}: at least three usable OTM option prices are required")

    variance = (2.0 / term_years) * total - (1.0 / term_years) * (
        (forward / reference_strike) - 1.0
    ) ** 2
    if variance <= 0:
        raise ValueError(f"{expiration}: option chain produced non-positive variance")
    return VarianceSwapResult(
        expiration=expiration,
        days_to_expiration=days_to_expiration,
        forward=forward,
        reference_strike=reference_strike,
        variance=variance,
        volatility=sqrt(variance),
    )


def _forward_reference_quote(
    strike_quotes: dict[float, OptionQuote],
    strikes: list[float],
    risk_free_rate: float,
    term_years: float,
) -> OptionQuote:
    candidates = [
        strike_quotes[strike]
        for strike in strikes
        if strike_quotes[strike].call_mid is not None and strike_quotes[strike].put_mid is not None
    ]
    if not candidates:
        raise ValueError("at least one strike needs both call and put prices")
    return min(
        candidates,
        key=lambda quote: abs((quote.call_mid or 0.0) - (quote.put_mid or 0.0))
        / exp(risk_free_rate * term_years),
    )


def _otm_mid(quote: OptionQuote, reference_strike: float) -> float | None:
    if quote.strike < reference_strike:
        return quote.put_mid
    if quote.strike > reference_strike:
        return quote.call_mid
    if quote.call_mid is None or quote.put_mid is None:
        return None
    return (quote.call_mid + quote.put_mid) / 2.0


def _strike_width(strikes: list[float], index: int) -> float:
    if index == 0:
        return strikes[1] - strikes[0]
    if index == len(strikes) - 1:
        return strikes[-1] - strikes[-2]
    return (strikes[index + 1] - strikes[index - 1]) / 2.0


def _mid(bid: float | None, ask: float | None) -> float | None:
    values = [value for value in (bid, ask) if value is not None and value >= 0]
    if len(values) == 2 and ask is not None and bid is not None and ask < bid:
        return None
    if not values:
        return None
    return sum(values) / len(values)


def _has_inverted_market(quote: OptionQuote) -> bool:
    return (
        quote.call_bid is not None
        and quote.call_ask is not None
        and quote.call_ask < quote.call_bid
    ) or (
        quote.put_bid is not None and quote.put_ask is not None and quote.put_ask < quote.put_bid
    )


def _stale_quote_count(
    rows: list[OptionQuote],
    asof_date: date,
    max_quote_age_days: int | None,
) -> int:
    if max_quote_age_days is None:
        return 0
    count = 0
    for quote in rows:
        for last_trade in (quote.call_last_trade, quote.put_last_trade):
            if last_trade is not None and (asof_date - last_trade).days > max_quote_age_days:
                count += 1
    return count


def _missing_last_trade_count(rows: list[OptionQuote]) -> int:
    return sum(
        1
        for quote in rows
        for last_trade in (quote.call_last_trade, quote.put_last_trade)
        if last_trade is None
    )


def _wide_spread_count(
    rows: list[OptionQuote],
    max_bid_ask_spread_pct: float | None,
) -> int:
    if max_bid_ask_spread_pct is None:
        return 0
    count = 0
    for quote in rows:
        if _spread_pct(quote.call_bid, quote.call_ask) > max_bid_ask_spread_pct:
            count += 1
        if _spread_pct(quote.put_bid, quote.put_ask) > max_bid_ask_spread_pct:
            count += 1
    return count


def _spread_pct(bid: float | None, ask: float | None) -> float:
    mid = _mid(bid, ask)
    if bid is None or ask is None or mid is None or mid <= 0:
        return 0.0
    return (ask - bid) / mid


def _normalize_row(row: dict[str, str]) -> dict[str, str]:
    return {key.strip().lower(): value.strip() for key, value in row.items() if key is not None}


def _optional_float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    return float(value.replace(",", ""))


def _required_float(value: str, field: str) -> float:
    try:
        return _optional_float(value) or 0.0
    except ValueError as exc:
        raise ValueError(f"{field} must be numeric") from exc


def _optional_date(value: str | None) -> date | None:
    if value is None or value == "":
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        return date.fromisoformat(normalized[:10])
    except ValueError:
        return datetime.fromisoformat(normalized).date()
