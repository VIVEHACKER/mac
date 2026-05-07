import unittest

from trading_copilot.universe import (
    CompositeUniverseProvider,
    KindKoreaUniverseProvider,
    NasdaqTraderUniverseProvider,
    UniverseMember,
    parse_kind_corp_list,
)


class UniverseTests(unittest.TestCase):
    def test_parses_nasdaq_trader_symbol_files_and_filters_test_issues(self):
        provider = NasdaqTraderUniverseProvider(fetch_text=FakeUniverseResponses())

        members = provider.members("us", include_etfs=False)

        self.assertEqual(
            members,
            (
                UniverseMember(symbol="AAPL", name="Apple Inc. Common Stock", market="NASDAQ", source="nasdaqtrader"),
                UniverseMember(symbol="MSFT", name="Microsoft Corporation Common Stock", market="NASDAQ", source="nasdaqtrader"),
                UniverseMember(symbol="IBM", name="International Business Machines Corporation", market="NYSE", source="nasdaqtrader"),
            ),
        )

    def test_parses_kind_kospi_corp_list_with_yahoo_suffix(self):
        members = parse_kind_corp_list(KIND_SAMPLE_HTML, market="KOSPI", yahoo_suffix="KS")

        self.assertEqual(
            members,
            [
                UniverseMember(symbol="005930.KS", name="삼성전자", market="KOSPI", source="kind.krx.co.kr corpList"),
                UniverseMember(symbol="000660.KS", name="SK하이닉스", market="KOSPI", source="kind.krx.co.kr corpList"),
            ],
        )

    def test_kind_provider_supports_kospi_kosdaq_and_combined_korea_universe(self):
        provider = KindKoreaUniverseProvider(fetch_text=FakeKindResponses())

        kospi = provider.members("kospi")
        kosdaq = provider.members("kosdaq")
        korea = provider.members("kr")

        self.assertEqual(kospi[0].symbol, "005930.KS")
        self.assertEqual(kosdaq[0].symbol, "196170.KQ")
        self.assertEqual([member.market for member in korea], ["KOSPI", "KOSPI", "KOSDAQ"])

    def test_composite_universe_routes_korean_markets(self):
        provider = CompositeUniverseProvider(
            us_provider=NasdaqTraderUniverseProvider(fetch_text=FakeUniverseResponses()),
            korea_provider=KindKoreaUniverseProvider(fetch_text=FakeKindResponses()),
        )

        self.assertEqual(provider.members("kosdaq")[0].symbol, "196170.KQ")


class FakeUniverseResponses:
    def __call__(self, url: str) -> str:
        if url.endswith("nasdaqlisted.txt"):
            return "\n".join(
                [
                    "Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares",
                    "AAPL|Apple Inc. Common Stock|Q|N|N|100|N|N",
                    "MSFT|Microsoft Corporation Common Stock|Q|N|N|100|N|N",
                    "AACO|Abony Acquisition Corp. I - Class A Ordinary Share|G|N|N|100|N|N",
                    "AACB|Artius II Acquisition Inc. - Class A Ordinary Shares|G|N|N|100|N|N",
                    "AACBU|Artius II Acquisition Inc. - Units|G|N|N|100|N|N",
                    "AACBR|Artius II Acquisition Inc. - Rights|G|N|N|100|N|N",
                    "XYZW|Example Corp Warrant|G|N|N|100|N|N",
                    "TEST|Test Company|Q|Y|N|100|N|N",
                    "QQQ|Invesco QQQ Trust|G|N|N|100|Y|N",
                    "File Creation Time: 0507202600:00",
                ]
            )
        if url.endswith("otherlisted.txt"):
            return "\n".join(
                [
                    "ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol",
                    "IBM|International Business Machines Corporation|N|IBM|N|100|N|IBM",
                    "ABC PR A|ABC Preferred Stock|N|ABCpA|N|100|N|ABC PR A",
                    "SPY|SPDR S&P 500 ETF Trust|P|SPY|Y|100|N|SPY",
                    "ZZZ|Test Other|A|ZZZ|N|100|Y|ZZZ",
                    "File Creation Time: 0507202600:00",
                ]
            )
        raise AssertionError(f"Unexpected URL: {url}")


class FakeKindResponses:
    def __call__(self, url: str) -> str:
        if "stockMkt" in url:
            return KIND_SAMPLE_HTML
        if "kosdaqMkt" in url:
            return KIND_KOSDAQ_SAMPLE_HTML
        raise AssertionError(f"Unexpected URL: {url}")


KIND_SAMPLE_HTML = """
<table>
  <tr>
    <th>회사명</th><th>시장구분</th><th>종목코드</th><th>업종</th>
  </tr>
  <tr>
    <td>삼성전자</td><td>유가</td><td>005930</td><td>통신 및 방송 장비 제조업</td>
  </tr>
  <tr>
    <td>SK하이닉스</td><td>유가</td><td>000660</td><td>반도체 제조업</td>
  </tr>
  <tr>
    <td>삼성에피스홀딩스</td><td>유가</td><td>0126Z0</td><td>기타 금융업</td>
  </tr>
</table>
"""


KIND_KOSDAQ_SAMPLE_HTML = """
<table>
  <tr>
    <th>회사명</th><th>시장구분</th><th>종목코드</th><th>업종</th>
  </tr>
  <tr>
    <td>알테오젠</td><td>코스닥</td><td>196170</td><td>의약품 제조업</td>
  </tr>
</table>
"""


if __name__ == "__main__":
    unittest.main()
