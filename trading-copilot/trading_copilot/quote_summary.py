from __future__ import annotations

from dataclasses import dataclass
import http.cookiejar
import json
from typing import Any, Callable, Protocol
from urllib.parse import quote
from urllib.request import HTTPCookieProcessor, Request, build_opener


@dataclass(frozen=True)
class TickerSummary:
    ticker: str
    sector: str | None
    industry: str | None
    trailing_pe: float | None
    forward_pe: float | None
    market_cap: float | None
    source: str


class QuoteSummaryProvider(Protocol):
    def summary(self, ticker: str) -> TickerSummary:
        pass


class QuoteSummaryError(RuntimeError):
    pass


YAHOO_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) trading-copilot/0.1"

SECTOR_NAME_TO_ETF: dict[str, str] = {
    "Technology": "XLK",
    "Information Technology": "XLK",
    "Communication Services": "XLC",
    "Communications": "XLC",
    "Consumer Cyclical": "XLY",
    "Consumer Discretionary": "XLY",
    "Consumer Defensive": "XLP",
    "Consumer Staples": "XLP",
    "Financial Services": "XLF",
    "Financials": "XLF",
    "Healthcare": "XLV",
    "Health Care": "XLV",
    "Industrials": "XLI",
    "Energy": "XLE",
    "Basic Materials": "XLB",
    "Materials": "XLB",
    "Real Estate": "XLRE",
    "Utilities": "XLU",
}

INDUSTRY_KEYWORD_TO_ETF: dict[str, str] = {
    "semiconductor": "SMH",
    "software": "IGV",
    "internet content": "XLC",
    "internet retail": "XLY",
    "biotech": "IBB",
    "biotechnology": "IBB",
    "regional bank": "KRE",
    "insurance": "KIE",
    "homebuilding": "XHB",
    "residential construction": "XHB",
    "transportation": "IYT",
    "trucking": "IYT",
    "railroads": "IYT",
    "airlines": "IYT",
    "aerospace": "ITA",
    "defense": "ITA",
    "oil & gas e&p": "XOP",
    "oil & gas equipment": "OIH",
    "oil & gas drilling": "OIH",
    "metals & mining": "XME",
    "copper": "COPX",
    "gold": "GDX",
    "silver": "SIL",
    "uranium": "URA",
    "solar": "TAN",
    "wind": "FAN",
    "infrastructure": "PAVE",
    "engineering & construction": "PAVE",
    "specialty retail": "XRT",
    "apparel retail": "XRT",
    "discount stores": "XRT",
    "rare earth": "REMX",
}

KNOWN_TICKER_TO_ETF: dict[str, str] = {
    "NVDA": "SMH", "AMD": "SMH", "TSM": "SMH", "AVGO": "SMH", "ASML": "SMH",
    "MU": "SMH", "QCOM": "SMH", "INTC": "SMH", "ARM": "SMH", "MRVL": "SMH",
    "MSFT": "IGV", "ORCL": "IGV", "CRM": "IGV", "ADBE": "IGV", "NOW": "IGV",
    "WDAY": "IGV", "PLTR": "IGV", "DDOG": "IGV", "SNOW": "IGV", "MDB": "IGV",
    "CRWD": "CIBR", "ZS": "CIBR", "PANW": "CIBR", "S": "CIBR", "OKTA": "CIBR",
    "AAPL": "XLK",
    "META": "XLC", "GOOGL": "XLC", "GOOG": "XLC", "NFLX": "XLC", "DIS": "XLC",
    "AMZN": "XLY", "TSLA": "XLY", "HD": "XLY", "MCD": "XLY", "NKE": "XLY",
    "SBUX": "XLY", "LOW": "XLY", "BKNG": "XLY",
    "WMT": "XLP", "PG": "XLP", "KO": "XLP", "PEP": "XLP", "COST": "XLP",
    "JPM": "XLF", "BAC": "XLF", "GS": "XLF", "WFC": "XLF", "MS": "XLF",
    "V": "XLF", "MA": "XLF", "AXP": "XLF", "BRK-B": "XLF", "BRK.B": "XLF",
    "JNJ": "XLV", "UNH": "XLV", "PFE": "XLV", "LLY": "XLV", "MRK": "XLV",
    "ABBV": "XLV", "TMO": "XLV", "DHR": "XLV",
    "XOM": "XLE", "CVX": "XLE", "COP": "XLE", "OXY": "XLE", "EOG": "XLE", "SLB": "OIH",
    "BA": "ITA", "LMT": "ITA", "RTX": "ITA", "NOC": "ITA", "GD": "ITA",
    "CAT": "XLI", "DE": "XLI", "GE": "XLI", "HON": "XLI", "UPS": "IYT", "FDX": "IYT",
    "NEM": "GDX", "GOLD": "GDX",
    "FCX": "XME", "CLF": "XME",
}


class YahooQuoteSummaryProvider:
    BASE_URL = "https://query1.finance.yahoo.com/v10/finance/quoteSummary"
    CRUMB_URL = "https://query1.finance.yahoo.com/v1/test/getcrumb"
    COOKIE_URL = "https://fc.yahoo.com/"

    def __init__(self):
        self._opener = self._build_opener()
        self._crumb: str | None = None

    def _build_opener(self):
        cookie_jar = http.cookiejar.CookieJar()
        opener = build_opener(HTTPCookieProcessor(cookie_jar))
        opener.addheaders = [("User-Agent", YAHOO_USER_AGENT)]
        return opener

    def _ensure_crumb(self) -> str:
        if self._crumb is not None:
            return self._crumb
        try:
            response = self._opener.open(self.COOKIE_URL, timeout=10)
            response.read()
        except Exception:
            pass
        try:
            response = self._opener.open(self.CRUMB_URL, timeout=10)
            crumb = response.read().decode("utf-8").strip()
        except Exception as exc:
            raise QuoteSummaryError(f"failed to obtain Yahoo crumb: {exc}") from exc
        if not crumb:
            raise QuoteSummaryError("Yahoo returned empty crumb")
        self._crumb = crumb
        return crumb

    def summary(self, ticker: str) -> TickerSummary:
        normalized = ticker.strip().upper()
        crumb = self._ensure_crumb()
        url = (
            f"{self.BASE_URL}/{quote(normalized)}"
            f"?modules=assetProfile,defaultKeyStatistics,summaryDetail,price"
            f"&crumb={quote(crumb)}"
        )
        try:
            response = self._opener.open(url, timeout=15)
            payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            raise QuoteSummaryError(f"{normalized}: quoteSummary fetch failed: {exc}") from exc
        return parse_quote_summary(normalized, payload, source=url)


def parse_quote_summary(
    ticker: str,
    payload: dict[str, Any],
    *,
    source: str,
) -> TickerSummary:
    qs = payload.get("quoteSummary") if isinstance(payload, dict) else None
    if not isinstance(qs, dict):
        raise QuoteSummaryError(f"{ticker}: invalid quoteSummary payload")
    if qs.get("error"):
        raise QuoteSummaryError(f"{ticker}: Yahoo error: {qs['error']}")
    results = qs.get("result")
    if not isinstance(results, list) or not results:
        raise QuoteSummaryError(f"{ticker}: empty quoteSummary result")
    first = results[0]
    if not isinstance(first, dict):
        raise QuoteSummaryError(f"{ticker}: malformed quoteSummary result")

    sector = None
    industry = None
    trailing_pe = None
    forward_pe = None
    market_cap = None

    asset_profile = first.get("assetProfile")
    if isinstance(asset_profile, dict):
        sector = asset_profile.get("sector") or None
        industry = asset_profile.get("industry") or None

    stats = first.get("defaultKeyStatistics")
    if isinstance(stats, dict):
        trailing_pe = _raw_value(stats.get("trailingPE"))
        forward_pe = _raw_value(stats.get("forwardPE"))

    summary_detail = first.get("summaryDetail")
    if isinstance(summary_detail, dict):
        if trailing_pe is None:
            trailing_pe = _raw_value(summary_detail.get("trailingPE"))
        if forward_pe is None:
            forward_pe = _raw_value(summary_detail.get("forwardPE"))

    price = first.get("price")
    if isinstance(price, dict):
        market_cap = _raw_value(price.get("marketCap"))

    return TickerSummary(
        ticker=ticker,
        sector=sector,
        industry=industry,
        trailing_pe=trailing_pe,
        forward_pe=forward_pe,
        market_cap=market_cap,
        source=source,
    )


def map_to_sector_etf(summary: TickerSummary | None, ticker: str) -> str | None:
    """Resolve a ticker to its best-matching sector/industry ETF symbol.

    Order of resolution:
      1. KNOWN_TICKER_TO_ETF curated overrides (fast path for well-known names)
      2. Yahoo industry name -> INDUSTRY_KEYWORD_TO_ETF (granular: SMH, IGV, ...)
      3. Yahoo sector name -> SECTOR_NAME_TO_ETF (broad: XLK, XLF, ...)
      4. None (caller should fall back to the strongest current sector)
    """
    normalized = ticker.strip().upper()
    if normalized in KNOWN_TICKER_TO_ETF:
        return KNOWN_TICKER_TO_ETF[normalized]
    if summary is None:
        return None
    if summary.industry:
        ind_lower = summary.industry.lower()
        for keyword, etf in INDUSTRY_KEYWORD_TO_ETF.items():
            if keyword in ind_lower:
                return etf
    if summary.sector:
        return SECTOR_NAME_TO_ETF.get(summary.sector)
    return None


def _raw_value(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, dict):
        raw = value.get("raw")
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            return float(raw)
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


__all__ = [
    "TickerSummary",
    "QuoteSummaryProvider",
    "QuoteSummaryError",
    "YahooQuoteSummaryProvider",
    "parse_quote_summary",
    "map_to_sector_etf",
    "KNOWN_TICKER_TO_ETF",
    "SECTOR_NAME_TO_ETF",
    "INDUSTRY_KEYWORD_TO_ETF",
]
