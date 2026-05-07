# Trading Copilot

Local trading research copilot that reuses the cloned `anthropics/financial-services`
agent and skill markdown files.

This tool does not provide investment advice, route orders, or execute trades. It
generates review artifacts for a human decision.

## Quick Start

```powershell
cd <workspace>\trading-copilot
python -m trading_copilot init
python -m trading_copilot watchlist add MSFT --note "Cloud earnings revision watch"
python -m trading_copilot thesis set MSFT --direction long --statement "Azure growth and operating leverage can support revisions." --invalidation "Cloud growth decelerates below peer median."
python -m trading_copilot quote MSFT --output out\quote-MSFT.md
python -m trading_copilot events MSFT --limit 5 --output out\events-MSFT.md
python -m trading_copilot news MSFT --limit 5 --output out\news-MSFT.md
python -m trading_copilot news-fast MSFT --limit 20 --output out\fast-news-MSFT.md
python -m trading_copilot calendar MSFT --horizon 3month --output out\calendar-MSFT.md
python -m trading_copilot fundamentals MSFT --output out\fundamentals-MSFT.md
python -m trading_copilot signals MSFT --event-limit 3 --news-limit 5 --output out\signals-MSFT.md
python -m trading_copilot macro --output out\macro-cycle.md
python -m trading_copilot industries --current-limit 10 --next-limit 10 --output out\industry-leadership.md --csv-output out\industry-leadership.csv
python -m trading_copilot screen-all --market us --max-tickers 50 --limit 25 --with-news --output out\screen-us.md --csv-output out\screen-us.csv
python -m trading_copilot portfolio-100 --single-stock-pool NVDA,AVGO,AMD,MSFT,META,AMZN,TSLA,PLTR,CRWD,ARM --output out\portfolio-100.md --csv-output out\portfolio-100.csv
python -m trading_copilot morning MSFT --with-market-data --output out\morning-with-market.md
python -m trading_copilot recommend MSFT --target-price 520 --stop-price 390 --horizon swing --with-signals --context "Only after earnings reaction stabilizes" --output out\recommend-MSFT.md
python -m trading_copilot pretrade MSFT --side buy --risk-budget "1% portfolio risk" --context "Only after earnings reaction stabilizes" --output out\pretrade-MSFT.md
```

## Workflows

- `morning`: daily watchlist checklist
- `quote`: sourced quote snapshot from Yahoo Finance chart data
- `events`: recent SEC EDGAR filing events by ticker
- `news`: recent RSS news headlines by ticker
- `news-fast`: faster headline monitor combining optional Marketaux ticker news, RSS, and SEC filing events
- `calendar`: upcoming earnings calendar events through Alpha Vantage when `ALPHAVANTAGE_API_KEY` is set
- `fundamentals`: basic SEC companyfacts financial statement analysis: revenue growth, margin, balance sheet leverage, and free cash flow
- `signals`: earnings-forecast leading signals from contracts, guidance, demand, margin, regulatory, and filing events
- `macro`: FRED-based macro cycle dashboard covering CPI, core CPI, unemployment, Fed funds, the 10Y-2Y yield curve, industrial production, and retail sales
- `industries`: ETF-proxy rotation radar that separates current leading industries from next-leader candidates using 1M/3M/6M relative strength and acceleration
- `screen-all`: ranked market-universe screen with quote data and optional news/SEC signal checks
- `portfolio-100`: aggressive target-100%-annual-return portfolio draft with leverage, commodities, bonds, and exactly 3 single stocks
- `recommend`: research-only investment view using quote data, stored thesis, target price, stop price, optional SEC/news events, and optional forecast signals
- `screen`: idea screen prompt
- `thesis set/review`: falsifiable thesis storage and review
- `pretrade`: guarded pre-trade checklist
- `playbook`: single-ticker buy playbook combining macro regime, sector rotation, technical (12-1m momentum, vol, drawdown, distance from 52w high), and fractional Kelly position sizing. Designed for concentrated high-conviction long positions where the edge is "good cycle + leading sector + good company + entry at reasonable price"

### Playbook example

```powershell
python -m trading_copilot playbook NVDA `
  --aum 100000 `
  --target-vol 0.4 `
  --kelly-multiplier 0.5 `
  --max-position 0.3 `
  --max-risk 0.02 `
  --win-probability 0.6 `
  --upside 0.8 `
  --downside 0.25 `
  --output out\playbook-NVDA.md
```

The sizing is the minimum of three constraints:
1. Half-Kelly fraction from your subjective win probability + upside/downside
2. Volatility-targeted weight (target vol / asset's 60-day realized vol)
3. Hard risk cap (max loss as % of AUM if stop is hit)

Plus a hard concentration cap (`--max-position`).

When `fundamentals_provider` is configured (default uses SEC EDGAR XBRL
`companyfacts`), the composite score becomes a 5-factor weighted average:
20% Macro Cycle + 20% Sector + 20% Technical + 20% Quality + 20% Value.

- **Quality (0-100)**: ROE (25), operating or net margin (25), revenue growth
  YoY (20), ROA (15), Liabilities/Equity (15).
- **Value (0-100)**: Trailing P/E (50) + PEG (50). PEG defaults to 25/50 when
  earnings are negative or growth is non-positive.

Pass `--skip-fundamentals` to disable the SEC fetch (faster; reverts to the
3-factor 30/30/40 composite). Set `TRADING_COPILOT_CONTACT="Your Name email@host"`
to identify yourself to SEC EDGAR per their fair-access policy.

### Optional API keys

```powershell
$env:MARKETAUX_API_KEY="your-key"
$env:ALPHAVANTAGE_API_KEY="your-key"
```

Without `MARKETAUX_API_KEY`, `news-fast` still uses RSS and SEC events but reports
the missing wire/news API as a data gap. Without `ALPHAVANTAGE_API_KEY`,
`calendar` emits a data-gap section instead of failing the run.

`macro` tries the FRED CSV endpoint first. If the local network times out or
blocks FRED, it falls back to GovSpending JSON exports that mirror FRED series
and lists the actual source URLs in the generated report.
