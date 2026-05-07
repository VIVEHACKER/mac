from __future__ import annotations

from datetime import date, datetime

import pandas as pd

from data.ingest.fred_macro import (
    _parse_cboe_vix_csv,
    _parse_fred_api_json,
    _parse_treasury_yield_xml,
    fetch_fred_series,
)
from data.ingest.krx_flow_csv import parse_krx_flow_csv
from data.ingest.krx_flows import fetch_krx_flows, fetch_naver_investor_flows
from data.ingest.yfinance_fundamentals import fetch_yfinance_fundamentals


def test_fetch_fred_series_parses_injected_csv() -> None:
    csv_text = "observation_date,DGS10\n2025-01-01,4.1\n2025-01-02,4.2\n"

    def fetch_text(url: str) -> str:
        assert "cosd=2025-01-02" in url
        assert "coed=2025-01-02" in url
        return csv_text

    rows = fetch_fred_series(
        "DGS10",
        date(2025, 1, 2),
        date(2025, 1, 2),
        fetch_text=fetch_text,
    )

    assert len(rows) == 1
    assert rows[0].series_id == "DGS10"
    assert rows[0].value == 4.2


def test_parse_fred_api_json_sets_official_source() -> None:
    rows = _parse_fred_api_json(
        {
            "observations": [
                {"date": "2025-01-02", "realtime_start": "2025-01-03", "value": "4.5"}
            ]
        },
        series_id="DGS10",
        country="US",
        category="rates",
        series_name="DGS10",
        freq="D",
        unit="percent",
    )

    assert rows[0].source == "fred-api:DGS10"
    assert rows[0].release_ts.date() == date(2025, 1, 3)


def test_parse_treasury_yield_xml_official_dgs10() -> None:
    xml_text = """
    <feed xmlns:d="http://schemas.microsoft.com/ado/2007/08/dataservices"
          xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata"
          xmlns="http://www.w3.org/2005/Atom">
      <entry><content><m:properties>
        <d:NEW_DATE>2026-05-07T00:00:00</d:NEW_DATE>
        <d:BC_10YEAR>4.30</d:BC_10YEAR>
      </m:properties></content></entry>
    </feed>
    """

    rows = _parse_treasury_yield_xml(
        xml_text,
        start=date(2026, 5, 1),
        end=date(2026, 5, 8),
        country="US",
        category="rates",
        series_name="DGS10",
        freq="D",
        unit="percent",
    )

    assert len(rows) == 1
    assert rows[0].value == 4.30
    assert rows[0].source == "treasury:daily_treasury_yield_curve:BC_10YEAR"


def test_parse_cboe_vix_csv_official_vixcls() -> None:
    rows = _parse_cboe_vix_csv(
        "DATE,OPEN,HIGH,LOW,CLOSE\n05/07/2026,18,19,17,18.5\n",
        start=date(2026, 5, 1),
        end=date(2026, 5, 8),
        country="US",
        category="volatility",
        series_name="VIXCLS",
        freq="D",
        unit="index",
    )

    assert len(rows) == 1
    assert rows[0].series_id == "VIXCLS"
    assert rows[0].source == "cboe:VIX_History"


def test_fetch_krx_flows_uses_injected_frame() -> None:
    frame = pd.DataFrame({"외국인": [1000], "개인": [-500]}, index=pd.to_datetime(["2025-01-02"]))

    rows = fetch_krx_flows(
        "5930",
        "kospi",
        date(2025, 1, 2),
        date(2025, 1, 2),
        fetch_frame=lambda _start, _end, _ticker: frame,
    )

    assert {row.investor for row in rows} == {"foreign", "retail"}
    assert rows[0].symbol == "005930"


def test_parse_krx_flow_csv_daily_wide_export(tmp_path) -> None:
    csv_path = tmp_path / "krx-flow.csv"
    csv_path.write_text(
        "날짜,기관합계,기타법인,개인,외국인합계,전체\n"
        "2026/05/07,1000,0,-3000,2000,0\n",
        encoding="utf-8",
    )

    rows = parse_krx_flow_csv(csv_path, "5930", "kospi")

    assert {row.investor for row in rows} >= {"institution", "foreign"}
    assert rows[0].symbol == "005930"
    assert rows[0].value_kind == "reported_value"
    assert rows[0].confidence == "high"
    assert rows[0].source == "krx-csv:krx-flow.csv"


def test_parse_krx_flow_csv_row_oriented_export(tmp_path) -> None:
    csv_path = tmp_path / "krx-flow-row.csv"
    csv_path.write_text(
        "날짜,투자자구분,매도금액,매수금액,순매수금액,순매수량\n"
        "2026-05-07,외국인,100,300,200,10\n",
        encoding="utf-8",
    )

    rows = parse_krx_flow_csv(csv_path, "005930", "kospi")

    assert len(rows) == 1
    assert rows[0].investor == "foreign"
    assert rows[0].net_value == 200
    assert rows[0].net_volume == 10


def test_fetch_naver_investor_flows_parses_fallback_table() -> None:
    html = """
    <table>
      <tr>
        <th rowspan="2">날짜</th><th rowspan="2">종가</th>
        <th class="bg01">기관</th><th colspan="3" class="bg01 last">외국인</th>
      </tr>
      <tr><th>순매매량</th><th>순매매량</th><th>보유주수</th><th>보유율</th></tr>
      <tr><td>2026.05.07</td><td>271500</td><td>100</td><td>-200</td><td>1</td><td>49.0%</td></tr>
    </table>
    """

    rows = fetch_naver_investor_flows(
        "005930",
        "kospi",
        date(2026, 5, 7),
        date(2026, 5, 7),
        fetch_html=lambda _url: html,
    )

    assert len(rows) == 2
    assert rows[0].investor == "foreign"
    assert rows[0].net_value == -54_300_000
    assert rows[0].net_volume == -200
    assert rows[0].value_kind == "estimated_close_x_volume"
    assert rows[0].confidence == "medium"


def test_yfinance_fundamentals_uses_injected_info() -> None:
    rows = fetch_yfinance_fundamentals(
        "MSFT",
        "us",
        fetch_info=lambda _symbol: {
            "sharesOutstanding": 10,
            "totalRevenue": 100,
            "netIncomeToCommon": 20,
            "freeCashflow": 15,
            "bookValue": 3,
            "trailingEps": 2,
        },
        asof_ts=datetime(2025, 1, 2),
    )

    assert rows[0].shares_out == 10
    assert rows[0].total_equity == 30
