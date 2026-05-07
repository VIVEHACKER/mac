from __future__ import annotations

import csv
from datetime import date, datetime, time
from pathlib import Path

from data.ingest.krx_flows import INVESTOR_COLUMNS
from data.ingest.pykrx_kr import normalize_kr_symbol
from data.models import FlowRecord


class KrxFlowCsvError(RuntimeError):
    pass


DATE_COLUMNS = ("날짜", "TRD_DD", "date", "ts")
ROW_INVESTOR_COLUMNS = ("투자자구분", "INVST_TP_NM", "investor")
ROW_NET_VALUE_COLUMNS = ("NETBID_TRDVAL", "순매수금액", "net_value")
ROW_BUY_VALUE_COLUMNS = ("BID_TRDVAL", "매수금액", "buy_value")
ROW_SELL_VALUE_COLUMNS = ("ASK_TRDVAL", "매도금액", "sell_value")
ROW_NET_VOLUME_COLUMNS = ("NETBID_TRDVOL", "순매수량", "net_volume")


def parse_krx_flow_csv(path: Path | str, symbol: str, market: str) -> list[FlowRecord]:
    source_path = Path(path)
    rows = _read_csv(source_path)
    if not rows:
        raise KrxFlowCsvError(f"{source_path}: empty CSV")
    records = _parse_row_oriented(rows, symbol=symbol, market=market, source_path=source_path)
    if not records:
        records = _parse_date_wide(rows, symbol=symbol, market=market, source_path=source_path)
    if not records:
        raise KrxFlowCsvError(
            f"{source_path}: no reported flow rows found. Expected KRX daily investor "
            "columns such as 날짜/외국인합계/기관합계 or row columns such as "
            "날짜/투자자구분/순매수금액."
        )
    return records


def _parse_row_oriented(
    rows: list[dict[str, str]],
    *,
    symbol: str,
    market: str,
    source_path: Path,
) -> list[FlowRecord]:
    records: list[FlowRecord] = []
    for row in rows:
        row_date = _find_date(row)
        investor_raw = _first_value(row, ROW_INVESTOR_COLUMNS)
        net_value = _parse_number(_first_value(row, ROW_NET_VALUE_COLUMNS))
        if row_date is None or not investor_raw or net_value is None:
            continue
        investor = INVESTOR_COLUMNS.get(investor_raw.strip(), investor_raw.strip())
        records.append(
            _record(
                symbol=symbol,
                market=market,
                ts=row_date,
                investor=investor,
                net_value=net_value,
                buy_value=_parse_number(_first_value(row, ROW_BUY_VALUE_COLUMNS)) or 0.0,
                sell_value=_parse_number(_first_value(row, ROW_SELL_VALUE_COLUMNS)) or 0.0,
                net_volume=_parse_number(_first_value(row, ROW_NET_VOLUME_COLUMNS)),
                source_path=source_path,
            )
        )
    return records


def _parse_date_wide(
    rows: list[dict[str, str]],
    *,
    symbol: str,
    market: str,
    source_path: Path,
) -> list[FlowRecord]:
    records: list[FlowRecord] = []
    investor_columns = {
        column: INVESTOR_COLUMNS[column]
        for row in rows
        for column in row
        if column in INVESTOR_COLUMNS
    }
    for row in rows:
        row_date = _find_date(row)
        if row_date is None:
            continue
        for column, investor in investor_columns.items():
            net_value = _parse_number(row.get(column))
            if net_value is None:
                continue
            records.append(
                _record(
                    symbol=symbol,
                    market=market,
                    ts=row_date,
                    investor=investor,
                    net_value=net_value,
                    source_path=source_path,
                )
            )
    return records


def _record(
    *,
    symbol: str,
    market: str,
    ts: date,
    investor: str,
    net_value: float,
    source_path: Path,
    buy_value: float = 0.0,
    sell_value: float = 0.0,
    net_volume: float | None = None,
) -> FlowRecord:
    return FlowRecord(
        symbol=normalize_kr_symbol(symbol),
        market=market.lower(),
        ts=ts,
        investor=investor,
        net_value=net_value,
        buy_value=buy_value,
        sell_value=sell_value,
        net_volume=net_volume,
        release_ts=datetime.combine(ts, time(18, 0)),
        value_kind="reported_value",
        confidence="high",
        source=f"krx-csv:{source_path.name}",
    )


def _read_csv(path: Path) -> list[dict[str, str]]:
    last_error: UnicodeDecodeError | None = None
    for encoding in ("utf-8-sig", "cp949", "euc-kr"):
        try:
            with path.open(newline="", encoding=encoding) as handle:
                return [
                    {str(key).strip(): str(value).strip() for key, value in row.items()}
                    for row in csv.DictReader(handle)
                ]
        except UnicodeDecodeError as exc:
            last_error = exc
    raise KrxFlowCsvError(f"{path}: cannot decode CSV") from last_error


def _find_date(row: dict[str, str]) -> date | None:
    value = _first_value(row, DATE_COLUMNS)
    if value is None:
        return None
    normalized = value.replace("/", "-").replace(".", "-").strip()
    try:
        return date.fromisoformat(normalized)
    except ValueError:
        try:
            return datetime.strptime(value.strip(), "%Y%m%d").date()
        except ValueError:
            return None


def _first_value(row: dict[str, str], columns: tuple[str, ...]) -> str | None:
    lowered = {key.lower(): value for key, value in row.items()}
    for column in columns:
        value = row.get(column)
        if value is None:
            value = lowered.get(column.lower())
        if value not in {None, ""}:
            return value
    return None


def _parse_number(value: str | None) -> float | None:
    if value is None:
        return None
    normalized = value.replace(",", "").replace("−", "-").strip()
    if normalized in {"", "-"}:
        return None
    try:
        return float(normalized)
    except ValueError:
        return None
