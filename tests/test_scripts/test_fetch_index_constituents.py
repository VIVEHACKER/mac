from __future__ import annotations

from scripts.fetch_index_constituents import parse_ishares_holdings

SAMPLE = """\
"iShares Core S&P Small-Cap ETF"
"Fund Holdings as of","May 30, 2026"
\x20
"Ticker","Name","Sector","Asset Class","Market Value"
"AAA","Alpha Industries","Industrials","Equity","123456.00"
"BBB","Beta Health","Health Care","Equity","98765.00"
"CCC","Gamma Tech","Information Technology","Equity","55555.00"
"-","CASH COLLATERAL","Cash and/or Derivatives","Cash","1000.00"
"USD","USD CASH","Cash and/or Derivatives","Cash","500.00"
"""


def test_parse_returns_only_equity_tickers():
    tickers = parse_ishares_holdings(SAMPLE)
    assert tickers == ["AAA", "BBB", "CCC"]


def test_parse_skips_preamble_and_non_equity():
    # cash/derivative rows and the metadata preamble must be excluded
    tickers = parse_ishares_holdings(SAMPLE)
    assert "USD" not in tickers
    assert "-" not in tickers


def test_parse_empty_or_headerless_returns_empty():
    assert parse_ishares_holdings("garbage\nno header here\n") == []
