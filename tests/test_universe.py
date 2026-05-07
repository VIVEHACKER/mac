import unittest

from trading_copilot.universe import NasdaqTraderUniverseProvider, UniverseMember


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


if __name__ == "__main__":
    unittest.main()
