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


def test_parse_handles_leading_whitespace_header():
    # Prefix every line with a single space to simulate leading-whitespace CSV
    whitespace_sample = "\n".join(" " + line for line in SAMPLE.splitlines())
    tickers = parse_ishares_holdings(whitespace_sample)
    assert tickers == ["AAA", "BBB", "CCC"]


from datetime import date  # noqa: E402

from scripts.fetch_index_constituents import (  # noqa: E402
    parse_wikipedia_constituents,
    write_universe_csv,
)

# ---------------------------------------------------------------------------
# Wikipedia constituent parser tests
# ---------------------------------------------------------------------------

_WIKI_FIXTURE = """\
<html><body>
<h2>Constituents</h2>
<table class="wikitable sortable jquery-tablesorter">
<tbody>
<tr><th>Symbol</th><th>Security</th><th>Sector</th></tr>
<tr><td><a href="/wiki/AAA">AAA</a></td><td>Alpha Corp</td><td>Tech</td></tr>
<tr><td><a href="/wiki/BBB">BBB</a></td><td>Beta Inc</td><td>Finance</td></tr>
<tr><td><a href="/wiki/CCC">CCC</a></td><td>Gamma Ltd</td><td>Health</td></tr>
</tbody>
</table>
<h2>Unrelated section</h2>
<table>
<tr><td><a href="/wiki/DDD">DDD</a></td><td>Delta Co</td></tr>
</table>
</body></html>
"""


def test_parse_wikipedia_constituents_only_reads_symbol_table():
    tickers = parse_wikipedia_constituents(_WIKI_FIXTURE)
    assert tickers == ["AAA", "BBB", "CCC"]
    assert "DDD" not in tickers


def test_parse_wikipedia_constituents_no_symbol_table_returns_empty():
    html = '<table class="wikitable sortable"><tr><th>Name</th><th>Sector</th></tr><tr><td>X</td></tr></table>'
    assert parse_wikipedia_constituents(html) == []


def test_parse_wikipedia_constituents_dedup_preserves_order():
    html = """\
<table class="wikitable sortable">
<tr><th>Symbol</th><th>Security</th></tr>
<tr><td>AAA</td><td>Alpha</td></tr>
<tr><td>BBB</td><td>Beta</td></tr>
<tr><td>AAA</td><td>Alpha Dup</td></tr>
</table>
"""
    tickers = parse_wikipedia_constituents(html)
    assert tickers == ["AAA", "BBB"]


def test_write_universe_csv_schema(tmp_path):
    out = tmp_path / "u.csv"
    # write_universe_csv writes a {ticker: subclass} mapping; dedup is main()'s job.
    mapping = {"AAA": "us-mid-cap", "BBB": "us-small-cap"}
    n = write_universe_csv(mapping, out, run_date=date(2026, 5, 31), source="ishares")
    assert n == 2
    text = out.read_text(encoding="utf-8")
    header = text.splitlines()[0]
    assert header == (
        "universe,symbol,market,start_date,end_date,source,confidence,"
        "asset_class,asset_subclass,role"
    )
    assert "SP400_600_CURRENT,AAA,us,2026-05-31,,ishares,medium,equity,us-mid-cap,risk" in text
    assert "SP400_600_CURRENT,BBB,us,2026-05-31,,ishares,medium,equity,us-small-cap,risk" in text
