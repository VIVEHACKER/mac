from __future__ import annotations

import csv
import json
import os
import subprocess
import xml.etree.ElementTree as ET
from collections.abc import Callable
from datetime import date, datetime, time
from io import StringIO
from urllib.error import URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from data.models import MacroObservation

FRED_CSV_URL = (
    "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}&cosd={start}&coed={end}"
)
FRED_API_URL = "https://api.stlouisfed.org/fred/series/observations"
CBOE_VIX_HISTORY_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"
TREASURY_YIELD_XML_URL = (
    "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml"
    "?data=daily_treasury_yield_curve&field_tdr_date_value={year}"
)
USER_AGENT = "Mozilla/5.0 trader/0.1"
FRED_YAHOO_FALLBACKS = {
    "DGS10": "^TNX",
    "VIXCLS": "^VIX",
}


class FredDataError(RuntimeError):
    pass


def fetch_fred_series(
    series_id: str,
    start: date,
    end: date,
    *,
    country: str = "US",
    category: str = "macro",
    series_name: str = "",
    freq: str = "",
    unit: str = "",
    fetch_text: Callable[[str], str] | None = None,
) -> list[MacroObservation]:
    normalized = series_id.strip().upper()
    if fetch_text is None:
        rows = _fetch_fred_api_if_configured(
            normalized,
            start=start,
            end=end,
            country=country,
            category=category,
            series_name=series_name or normalized,
            freq=freq,
            unit=unit,
        )
        if rows:
            return rows
    fetcher = fetch_text or _fetch_text
    try:
        text = fetcher(
            FRED_CSV_URL.format(
                series_id=quote(normalized),
                start=start.isoformat(),
                end=end.isoformat(),
            )
        )
        rows = _parse_fred_csv(
            text,
            series_id=normalized,
            start=start,
            end=end,
            country=country,
            category=category,
            series_name=series_name or normalized,
            freq=freq,
            unit=unit,
        )
    except Exception:
        rows = _fetch_official_fallback(
            normalized,
            start=start,
            end=end,
            country=country,
            category=category,
            series_name=series_name or normalized,
            freq=freq,
            unit=unit,
        )
    if not rows:
        rows = _fetch_official_fallback(
            normalized,
            start=start,
            end=end,
            country=country,
            category=category,
            series_name=series_name or normalized,
            freq=freq,
            unit=unit,
        )
    if not rows:
        rows = _fetch_yahoo_fallback(
            normalized,
            start=start,
            end=end,
            country=country,
            category=category,
            series_name=series_name or normalized,
            freq=freq,
            unit=unit,
        )
    if not rows:
        raise FredDataError(f"{normalized}: no FRED rows returned for requested window")
    return rows


def _parse_fred_csv(
    text: str,
    *,
    series_id: str,
    start: date,
    end: date,
    country: str,
    category: str,
    series_name: str,
    freq: str,
    unit: str,
) -> list[MacroObservation]:
    reader = csv.DictReader(StringIO(text))
    rows: list[MacroObservation] = []
    for item in reader:
        raw_date = item.get("observation_date") or item.get("DATE") or item.get("date")
        raw_value = item.get(series_id) or item.get("value")
        if not raw_date or raw_value is None:
            continue
        raw_value_text = str(raw_value)
        if raw_value_text in {"", "."}:
            continue
        asof_date = date.fromisoformat(raw_date)
        if asof_date < start or asof_date > end:
            continue
        rows.append(
            MacroObservation(
                series_id=series_id,
                country=country,
                category=category,
                asof_date=asof_date,
                release_ts=datetime.combine(asof_date, time(18, 0)),
                value=float(raw_value_text),
                revision_n=0,
                series_name=series_name,
                freq=freq,
                unit=unit,
                source=f"fred:{series_id}",
            )
        )
    return rows


def _fetch_fred_api_if_configured(
    series_id: str,
    *,
    start: date,
    end: date,
    country: str,
    category: str,
    series_name: str,
    freq: str,
    unit: str,
) -> list[MacroObservation]:
    api_key = os.getenv("FRED_API_KEY")
    if not api_key:
        return []
    params = urlencode(
        {
            "series_id": series_id,
            "observation_start": start.isoformat(),
            "observation_end": end.isoformat(),
            "file_type": "json",
            "api_key": api_key,
        }
    )
    try:
        payload = json.loads(_fetch_text(f"{FRED_API_URL}?{params}"))
    except Exception:
        return []
    return _parse_fred_api_json(
        payload,
        series_id=series_id,
        country=country,
        category=category,
        series_name=series_name,
        freq=freq,
        unit=unit,
    )


def _parse_fred_api_json(
    payload: dict,
    *,
    series_id: str,
    country: str,
    category: str,
    series_name: str,
    freq: str,
    unit: str,
) -> list[MacroObservation]:
    rows: list[MacroObservation] = []
    for item in payload.get("observations", []):
        raw_value = item.get("value")
        if raw_value in {None, "", "."}:
            continue
        asof_date = date.fromisoformat(item["date"])
        realtime_start = item.get("realtime_start") or item["date"]
        release_date = date.fromisoformat(realtime_start)
        rows.append(
            MacroObservation(
                series_id=series_id,
                country=country,
                category=category,
                asof_date=asof_date,
                release_ts=datetime.combine(release_date, time(18, 0)),
                value=float(raw_value),
                revision_n=0,
                series_name=series_name,
                freq=freq,
                unit=unit,
                source=f"fred-api:{series_id}",
            )
        )
    return rows


def _fetch_official_fallback(
    series_id: str,
    *,
    start: date,
    end: date,
    country: str,
    category: str,
    series_name: str,
    freq: str,
    unit: str,
) -> list[MacroObservation]:
    if series_id == "DGS10":
        return _fetch_treasury_10y(
            start=start,
            end=end,
            country=country,
            category=category,
            series_name=series_name,
            freq=freq,
            unit=unit,
        )
    if series_id == "VIXCLS":
        return _fetch_cboe_vix(
            start=start,
            end=end,
            country=country,
            category=category,
            series_name=series_name,
            freq=freq,
            unit=unit,
        )
    return []


def _fetch_treasury_10y(
    *,
    start: date,
    end: date,
    country: str,
    category: str,
    series_name: str,
    freq: str,
    unit: str,
) -> list[MacroObservation]:
    rows: list[MacroObservation] = []
    for year in range(start.year, end.year + 1):
        try:
            text = _fetch_text(TREASURY_YIELD_XML_URL.format(year=year))
        except Exception:
            continue
        rows.extend(
            _parse_treasury_yield_xml(
                text,
                start=start,
                end=end,
                country=country,
                category=category,
                series_name=series_name or "10-Year Treasury Yield",
                freq=freq,
                unit=unit,
            )
        )
    deduped = {row.asof_date: row for row in rows}
    return [deduped[item] for item in sorted(deduped)]


def _parse_treasury_yield_xml(
    text: str,
    *,
    start: date,
    end: date,
    country: str,
    category: str,
    series_name: str,
    freq: str,
    unit: str,
) -> list[MacroObservation]:
    root = ET.fromstring(text)
    ns = {
        "m": "http://schemas.microsoft.com/ado/2007/08/dataservices/metadata",
        "d": "http://schemas.microsoft.com/ado/2007/08/dataservices",
    }
    rows: list[MacroObservation] = []
    for properties in root.findall(".//m:properties", ns):
        raw_date = _xml_text(properties, "NEW_DATE", ns)
        raw_value = _xml_text(properties, "BC_10YEAR", ns)
        if not raw_date or not raw_value:
            continue
        asof_date = date.fromisoformat(raw_date[:10])
        if asof_date < start or asof_date > end:
            continue
        rows.append(
            MacroObservation(
                series_id="DGS10",
                country=country,
                category=category,
                asof_date=asof_date,
                release_ts=datetime.combine(asof_date, time(18, 0)),
                value=float(raw_value),
                revision_n=0,
                series_name=series_name,
                freq=freq or "D",
                unit=unit or "percent",
                source="treasury:daily_treasury_yield_curve:BC_10YEAR",
            )
        )
    return rows


def _fetch_cboe_vix(
    *,
    start: date,
    end: date,
    country: str,
    category: str,
    series_name: str,
    freq: str,
    unit: str,
) -> list[MacroObservation]:
    try:
        text = _fetch_text(CBOE_VIX_HISTORY_URL)
    except Exception:
        return []
    return _parse_cboe_vix_csv(
        text,
        start=start,
        end=end,
        country=country,
        category=category,
        series_name=series_name or "CBOE Volatility Index",
        freq=freq,
        unit=unit,
    )


def _parse_cboe_vix_csv(
    text: str,
    *,
    start: date,
    end: date,
    country: str,
    category: str,
    series_name: str,
    freq: str,
    unit: str,
) -> list[MacroObservation]:
    reader = csv.DictReader(StringIO(text))
    rows: list[MacroObservation] = []
    for item in reader:
        raw_date = _csv_get(item, "DATE", "date")
        raw_value = _csv_get(item, "CLOSE", "close")
        if not raw_date or raw_value is None:
            continue
        raw_value_text = str(raw_value)
        if raw_value_text in {"", "."}:
            continue
        asof_date = _parse_loose_date(raw_date)
        if asof_date < start or asof_date > end:
            continue
        rows.append(
            MacroObservation(
                series_id="VIXCLS",
                country=country,
                category=category,
                asof_date=asof_date,
                release_ts=datetime.combine(asof_date, time(18, 0)),
                value=float(raw_value_text),
                revision_n=0,
                series_name=series_name,
                freq=freq or "D",
                unit=unit or "index",
                source="cboe:VIX_History",
            )
        )
    return rows


def _xml_text(element: ET.Element, name: str, ns: dict[str, str]) -> str | None:
    child = element.find(f"d:{name}", ns)
    if child is None or child.text is None:
        return None
    return child.text.strip()


def _csv_get(item: dict[str, str], *names: str) -> str | None:
    lowered = {key.lower(): value for key, value in item.items()}
    for name in names:
        value = lowered.get(name.lower())
        if value is not None:
            return value.strip()
    return None


def _parse_loose_date(value: str) -> date:
    value = value.strip()
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return datetime.strptime(value, "%m/%d/%Y").date()


def _fetch_text(url: str) -> str:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=10) as response:
            return response.read().decode("utf-8")
    except (TimeoutError, URLError):
        # 시크릿(api_key) 포함 URL은 curl argv로 넘기지 않는다 — ps 목록에 노출됨.
        # 호출부(fetch_fred_series)가 키 없는 CSV 경로로 폴백하므로 fail-open 유지.
        if "api_key=" in url:
            raise
        return _fetch_text_with_curl(url)


def _fetch_text_with_curl(url: str) -> str:
    result = subprocess.run(
        ["curl", "-L", "--fail", "--silent", "--max-time", "20", "-A", USER_AGENT, url],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def _fetch_yahoo_fallback(
    series_id: str,
    *,
    start: date,
    end: date,
    country: str,
    category: str,
    series_name: str,
    freq: str,
    unit: str,
) -> list[MacroObservation]:
    yahoo_symbol = FRED_YAHOO_FALLBACKS.get(series_id)
    if yahoo_symbol is None:
        return []
    from data.ingest.yahoo import fetch_yahoo_bars

    bars = fetch_yahoo_bars(yahoo_symbol, "us", start, end)
    return [
        MacroObservation(
            series_id=series_id,
            country=country,
            category=category,
            asof_date=bar.ts,
            release_ts=datetime.combine(bar.ts, time(18, 0)),
            value=bar.close,
            revision_n=0,
            series_name=series_name,
            freq=freq or "D",
            unit=unit,
            source=f"yahoo-fallback:{yahoo_symbol}",
        )
        for bar in bars
    ]
