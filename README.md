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
python -m trading_copilot signals MSFT --event-limit 3 --news-limit 5 --output out\signals-MSFT.md
python -m trading_copilot macro --output out\macro-cycle.md
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
- `signals`: earnings-forecast leading signals from contracts, guidance, demand, margin, regulatory, and filing events
- `macro`: FRED-based macro cycle dashboard covering CPI, core CPI, unemployment, Fed funds, the 10Y-2Y yield curve, industrial production, and retail sales
- `screen-all`: ranked market-universe screen with quote data and optional news/SEC signal checks
- `portfolio-100`: aggressive target-100%-annual-return portfolio draft with leverage, commodities, bonds, and exactly 3 single stocks
- `recommend`: research-only investment view using quote data, stored thesis, target price, stop price, optional SEC/news events, and optional forecast signals
- `screen`: idea screen prompt
- `thesis set/review`: falsifiable thesis storage and review
- `pretrade`: guarded pre-trade checklist
