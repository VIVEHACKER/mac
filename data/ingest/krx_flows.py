from __future__ import annotations

from collections.abc import Callable
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, datetime, time
from importlib import import_module
from io import StringIO
from typing import Any

import pandas as pd
import requests

from data.ingest.pykrx_kr import normalize_kr_symbol
from data.models import FlowRecord


class KrxFlowError(RuntimeError):
    pass


NAVER_FRGN_URL = "https://finance.naver.com/item/frgn.naver"
USER_AGENT = "Mozilla/5.0 trader/0.1"

INVESTOR_COLUMNS = {
    "금융투자": "financial_investment",
    "보험": "insurance",
    "투신": "investment_trust",
    "사모": "private_fund",
    "은행": "bank",
    "기타금융": "other_financial",
    "연기금": "pension",
    "기관합계": "institution",
    "기타법인": "other_corporate",
    "개인": "retail",
    "외국인": "foreign",
    "외국인합계": "foreign",
    "기타외국인": "other_foreign",
}


def fetch_krx_flows(
    symbol: str,
    market: str,
    start: date,
    end: date,
    fetch_frame: Callable[[str, str, str], Any] | None = None,
    allow_estimated: bool = False,
) -> list[FlowRecord]:
    if market.lower() not in {"kospi", "kosdaq"}:
        raise KrxFlowError("KRX flows are only supported for --market kospi or kosdaq")
    market_key = market.lower()
    source_symbol = normalize_kr_symbol(symbol)
    if fetch_frame is None:
        try:
            frame = _default_fetch_frame(start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), source_symbol)
        except Exception:
            frame = None
    else:
        frame = fetch_frame(start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), source_symbol)
    records = _frame_to_flows(frame, source_symbol=source_symbol, market=market_key)
    if not records and fetch_frame is None and allow_estimated:
        records = fetch_naver_investor_flows(source_symbol, market_key, start, end)
    if not records:
        raise KrxFlowError(
            f"{source_symbol}: no reported KRX flow rows returned. "
            "Configure KRX_ID/KRX_PW for pykrx access or explicitly use "
            "--provider naver-estimate for quarantined estimated rows."
        )
    return records


def fetch_naver_investor_flows(
    symbol: str,
    market: str,
    start: date,
    end: date,
    *,
    max_pages: int = 20,
    fetch_html: Callable[[str], str] | None = None,
) -> list[FlowRecord]:
    source_symbol = normalize_kr_symbol(symbol)
    fetcher = fetch_html or _fetch_naver_html
    records: list[FlowRecord] = []
    for page in range(1, max_pages + 1):
        html = fetcher(f"{NAVER_FRGN_URL}?code={source_symbol}&page={page}")
        page_records = _naver_html_to_flows(
            html,
            source_symbol=source_symbol,
            market=market,
            start=start,
            end=end,
        )
        records.extend(page_records)
        page_dates = [record.ts for record in page_records]
        if page_dates and min(page_dates) <= start:
            break
    deduped = {
        (record.symbol, record.market, record.ts, record.investor): record for record in records
    }
    return sorted(deduped.values(), key=lambda item: (item.ts, item.investor))


def _frame_to_flows(frame: Any, *, source_symbol: str, market: str) -> list[FlowRecord]:
    if frame is None or getattr(frame, "empty", False):
        return []
    records: list[FlowRecord] = []
    for timestamp, row in frame.iterrows():
        row_date = _to_date(timestamp)
        release_ts = datetime.combine(row_date, time(18, 0))
        for raw_name, investor in INVESTOR_COLUMNS.items():
            if raw_name not in row:
                continue
            value = row[raw_name]
            if value is None:
                continue
            records.append(
                FlowRecord(
                    symbol=source_symbol,
                    market=market.lower(),
                    ts=row_date,
                    investor=investor,
                    net_value=float(value),
                    release_ts=release_ts,
                    value_kind="reported_value",
                    confidence="high",
                    source=f"pykrx:trading_value:{source_symbol}",
                )
            )
    return records


def _default_fetch_frame(from_date: str, to_date: str, ticker: str) -> Any:
    with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
        stock_module = import_module("pykrx.stock")
        return stock_module.get_market_trading_value_by_date(from_date, to_date, ticker)


def _naver_html_to_flows(
    html: str,
    *,
    source_symbol: str,
    market: str,
    start: date,
    end: date,
) -> list[FlowRecord]:
    tables = pd.read_html(StringIO(html))
    flow_table = _find_naver_flow_table(tables)
    records: list[FlowRecord] = []
    for _, row in flow_table.dropna(how="all").iterrows():
        row_date = _parse_naver_date(row.get("날짜"))
        if row_date is None or row_date < start or row_date > end:
            continue
        close = _to_float(row.get("종가"))
        institution_volume = _to_float(row.get("기관_순매매량"))
        foreign_volume = _to_float(row.get("외국인_순매매량"))
        release_ts = datetime.combine(row_date, time(18, 0))
        for investor, volume in (("institution", institution_volume), ("foreign", foreign_volume)):
            if close is None or volume is None:
                continue
            records.append(
                FlowRecord(
                    symbol=source_symbol,
                    market=market.lower(),
                    ts=row_date,
                    investor=investor,
                    net_value=volume * close,
                    net_volume=volume,
                    release_ts=release_ts,
                    value_kind="estimated_close_x_volume",
                    confidence="medium",
                    source=f"naver:frgn:{source_symbol}",
                )
            )
    return records


def _find_naver_flow_table(tables: list[pd.DataFrame]) -> pd.DataFrame:
    for table in tables:
        if isinstance(table.columns, pd.MultiIndex):
            table = table.copy()
            table.columns = [
                str(top) if top == bottom else f"{top}_{bottom}" for top, bottom in table.columns
            ]
        if {"날짜", "종가", "기관_순매매량", "외국인_순매매량"}.issubset(
            set(map(str, table.columns))
        ):
            return table
    raise KrxFlowError("Naver investor flow table was not found")


def _fetch_naver_html(url: str) -> str:
    response = requests.get(
        url,
        headers={"User-Agent": USER_AGENT, "Referer": "https://finance.naver.com/"},
        timeout=20,
    )
    response.raise_for_status()
    return response.content.decode("euc-kr", errors="replace")


def _parse_naver_date(value: Any) -> date | None:
    if value is None or pd.isna(value):
        return None
    try:
        return datetime.strptime(str(value), "%Y.%m.%d").date()
    except ValueError:
        return None


def _to_float(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    try:
        return float(str(value).replace(",", "").replace("%", "").strip())
    except ValueError:
        return None


def _to_date(value: Any) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    return value.to_pydatetime().date()
