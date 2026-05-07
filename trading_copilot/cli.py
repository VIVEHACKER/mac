from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .skill_registry import SkillRegistry
from .storage import TradingStore
from .workflows import TradingWorkflows


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    store = TradingStore(args.db)
    skills = SkillRegistry(args.financial_services_dir)
    workflows = TradingWorkflows(skills, store)

    if args.command == "init":
        store.initialize()
        print(f"Initialized {args.db}")
        return 0

    if args.command == "watchlist":
        if args.watchlist_command == "add":
            store.add_watchlist_item(args.ticker, args.note)
            print(f"Saved watchlist item: {args.ticker.upper()}")
            return 0
        if args.watchlist_command == "list":
            for item in store.list_watchlist():
                suffix = f" - {item.note}" if item.note else ""
                print(f"{item.ticker}{suffix}")
            return 0

    if args.command == "thesis":
        if args.thesis_command == "set":
            store.upsert_thesis(
                args.ticker, args.direction, args.statement, args.invalidation
            )
            print(f"Saved thesis: {args.ticker.upper()}")
            return 0
        if args.thesis_command == "review":
            return emit(workflows.thesis_review(args.ticker), args.output)

    if args.command == "morning":
        return emit(
            workflows.morning_brief(
                args.tickers, include_market_data=args.with_market_data
            ),
            args.output,
        )

    if args.command == "quote":
        return emit(workflows.quote_report(args.ticker), args.output)

    if args.command == "events":
        return emit(workflows.events_report(args.ticker, limit=args.limit), args.output)

    if args.command == "news":
        return emit(workflows.news_report(args.ticker, limit=args.limit), args.output)

    if args.command == "signals":
        return emit(
            workflows.signals_report(
                args.ticker,
                event_limit=args.event_limit,
                news_limit=args.news_limit,
            ),
            args.output,
        )

    if args.command == "macro":
        return emit(workflows.macro_report(), args.output)

    if args.command == "industries":
        report = workflows.industry_leadership_report(
            current_limit=args.current_limit,
            next_limit=args.next_limit,
        )
        if args.csv_output:
            csv_text = workflows.industry_leadership_csv()
            args.csv_output.parent.mkdir(parents=True, exist_ok=True)
            args.csv_output.write_text(csv_text, encoding="utf-8")
            print(f"Wrote {args.csv_output}")
        return emit(report, args.output)

    if args.command == "recommend":
        return emit(
            workflows.recommendation_report(
                ticker=args.ticker,
                target_price=args.target_price,
                stop_price=args.stop_price,
                horizon=args.horizon,
                context=args.context,
                include_sec_events=args.with_sec_events,
                include_news=args.with_news,
                include_signals=args.with_signals,
                event_limit=args.event_limit,
                news_limit=args.news_limit,
            ),
            args.output,
        )

    if args.command == "screen":
        return emit(workflows.screen_prompt(args.criteria, args.direction), args.output)

    if args.command == "screen-all":
        report = workflows.screen_all_report(
            market=args.market,
            max_tickers=args.max_tickers,
            limit=args.limit,
            include_news=args.with_news,
            include_sec_events=args.with_sec_events,
            include_etfs=args.include_etfs,
            include_spacs=args.include_spacs,
        )
        if args.csv_output:
            csv_text = workflows.screen_all_csv(
                market=args.market,
                max_tickers=args.max_tickers,
                include_news=args.with_news,
                include_sec_events=args.with_sec_events,
                include_etfs=args.include_etfs,
                include_spacs=args.include_spacs,
            )
            args.csv_output.parent.mkdir(parents=True, exist_ok=True)
            args.csv_output.write_text(csv_text, encoding="utf-8")
            print(f"Wrote {args.csv_output}")
        return emit(report, args.output)

    if args.command == "portfolio-100":
        single_stock_pool = tuple(
            item.strip().upper()
            for item in args.single_stock_pool.split(",")
            if item.strip()
        )
        report = workflows.aggressive_portfolio_report(
            target_annual_return=args.target_annual_return,
            single_stock_pool=single_stock_pool,
        )
        if args.csv_output:
            csv_text = workflows.aggressive_portfolio_csv(
                target_annual_return=args.target_annual_return,
                single_stock_pool=single_stock_pool,
            )
            args.csv_output.parent.mkdir(parents=True, exist_ok=True)
            args.csv_output.write_text(csv_text, encoding="utf-8")
            print(f"Wrote {args.csv_output}")
        return emit(report, args.output)

    if args.command == "pretrade":
        return emit(
            workflows.pretrade_report(
                ticker=args.ticker,
                side=args.side,
                horizon=args.horizon,
                risk_budget=args.risk_budget,
                user_context=args.context,
            ),
            args.output,
        )

    if args.command == "playbook":
        return emit(
            workflows.playbook_report(
                ticker=args.ticker,
                aum=args.aum,
                target_vol=args.target_vol,
                kelly_multiplier=args.kelly_multiplier,
                max_position_pct=args.max_position,
                max_risk_pct_of_aum=args.max_risk,
                win_probability=args.win_probability,
                upside_pct=args.upside,
                downside_pct=args.downside,
                sector_industry_hint=args.industry,
            ),
            args.output,
        )

    parser.print_help()
    return 2


def build_parser() -> argparse.ArgumentParser:
    cwd = Path.cwd()
    parser = argparse.ArgumentParser(
        prog="trading-copilot",
        description="Generate trading research checklists from financial-services skills.",
    )
    parser.add_argument(
        "--financial-services-dir",
        type=Path,
        default=cwd.parent / "financial-services",
        help="Path to the cloned anthropics/financial-services repository "
        "(default: ../financial-services from current working directory).",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=cwd / "data" / "copilot.db",
        help="SQLite database path (default: ./data/copilot.db).",
    )

    sub = parser.add_subparsers(dest="command")

    sub.add_parser("init", help="Initialize the local SQLite database.")

    watchlist = sub.add_parser("watchlist", help="Manage watchlist.")
    watchlist_sub = watchlist.add_subparsers(dest="watchlist_command")
    watchlist_add = watchlist_sub.add_parser("add")
    watchlist_add.add_argument("ticker")
    watchlist_add.add_argument("--note", default="")
    watchlist_sub.add_parser("list")

    thesis = sub.add_parser("thesis", help="Create or review a stored thesis.")
    thesis_sub = thesis.add_subparsers(dest="thesis_command")
    thesis_set = thesis_sub.add_parser("set")
    thesis_set.add_argument("ticker")
    thesis_set.add_argument("--direction", required=True, choices=["long", "short"])
    thesis_set.add_argument("--statement", required=True)
    thesis_set.add_argument("--invalidation", required=True)
    thesis_review = thesis_sub.add_parser("review")
    thesis_review.add_argument("ticker")
    thesis_review.add_argument("--output", type=Path)

    morning = sub.add_parser("morning", help="Generate a morning brief checklist.")
    morning.add_argument("tickers", nargs="*")
    morning.add_argument(
        "--with-market-data",
        action="store_true",
        help="Fetch Yahoo Finance quote snapshots for the covered tickers.",
    )
    morning.add_argument("--output", type=Path)

    quote = sub.add_parser("quote", help="Fetch a sourced quote snapshot.")
    quote.add_argument("ticker")
    quote.add_argument("--output", type=Path)

    events = sub.add_parser("events", help="Fetch recent SEC EDGAR filing events.")
    events.add_argument("ticker")
    events.add_argument("--limit", type=int, default=5)
    events.add_argument("--output", type=Path)

    news = sub.add_parser("news", help="Fetch recent RSS news headlines.")
    news.add_argument("ticker")
    news.add_argument("--limit", type=int, default=5)
    news.add_argument("--output", type=Path)

    signals = sub.add_parser(
        "signals",
        help="Detect earnings-forecast leading signals from SEC filings and news.",
    )
    signals.add_argument("ticker")
    signals.add_argument("--event-limit", type=int, default=3)
    signals.add_argument("--news-limit", type=int, default=5)
    signals.add_argument("--output", type=Path)

    macro = sub.add_parser(
        "macro",
        help="Generate a FRED-based macro cycle dashboard.",
    )
    macro.add_argument("--output", type=Path)

    industries = sub.add_parser(
        "industries",
        help="Rank current and next industry leadership using ETF proxy rotation.",
    )
    industries.add_argument("--current-limit", type=int, default=10)
    industries.add_argument("--next-limit", type=int, default=10)
    industries.add_argument("--csv-output", type=Path)
    industries.add_argument("--output", type=Path)

    recommend = sub.add_parser(
        "recommend",
        help="Generate a research-only investment view from quote data and stored thesis.",
    )
    recommend.add_argument("ticker")
    recommend.add_argument("--target-price", type=float)
    recommend.add_argument("--stop-price", type=float)
    recommend.add_argument("--horizon", default="swing")
    recommend.add_argument("--context", default="")
    recommend.add_argument(
        "--with-sec-events",
        action="store_true",
        help="Include recent SEC EDGAR filings as recommendation risk inputs.",
    )
    recommend.add_argument(
        "--with-news",
        action="store_true",
        help="Include recent RSS news headlines as recommendation risk inputs.",
    )
    recommend.add_argument(
        "--with-signals",
        action="store_true",
        help="Detect earnings-forecast leading signals from SEC filings and news.",
    )
    recommend.add_argument("--event-limit", type=int, default=3)
    recommend.add_argument("--news-limit", type=int, default=3)
    recommend.add_argument("--output", type=Path)

    screen = sub.add_parser("screen", help="Generate an idea-screen prompt.")
    screen.add_argument("criteria")
    screen.add_argument("--direction", default="both", choices=["long", "short", "both"])
    screen.add_argument("--output", type=Path)

    screen_all = sub.add_parser(
        "screen-all",
        help="Rank a market universe with quote data and optional event signals.",
    )
    screen_all.add_argument("--market", default="us", choices=["us"])
    screen_all.add_argument(
        "--max-tickers",
        type=int,
        default=50,
        help="Maximum tickers to process; use 0 for the full loaded universe.",
    )
    screen_all.add_argument("--limit", type=int, default=25)
    screen_all.add_argument(
        "--with-news",
        action="store_true",
        help="Fetch recent news for each screened ticker.",
    )
    screen_all.add_argument(
        "--with-sec-events",
        action="store_true",
        help="Fetch recent SEC filings for each screened ticker.",
    )
    screen_all.add_argument(
        "--include-etfs",
        action="store_true",
        help="Include ETFs in the universe.",
    )
    screen_all.add_argument(
        "--include-spacs",
        action="store_true",
        help="Include SPAC acquisition companies in the universe.",
    )
    screen_all.add_argument("--csv-output", type=Path)
    screen_all.add_argument("--output", type=Path)

    portfolio = sub.add_parser(
        "portfolio-100",
        help="Draft an aggressive target 100 percent annual return portfolio.",
    )
    portfolio.add_argument("--target-annual-return", type=int, default=100)
    portfolio.add_argument(
        "--single-stock-pool",
        default="NVDA,AVGO,AMD,MSFT,META,AMZN,TSLA,PLTR,CRWD,ARM",
        help="Comma-separated single-stock candidate pool; "
        "top 3 by latest-session relative price change are selected (not multi-week momentum).",
    )
    portfolio.add_argument("--csv-output", type=Path)
    portfolio.add_argument("--output", type=Path)

    pretrade = sub.add_parser("pretrade", help="Generate a guarded pre-trade report.")
    pretrade.add_argument("ticker")
    pretrade.add_argument("--side", required=True, choices=["buy", "sell", "trim", "add"])
    pretrade.add_argument("--horizon", default="swing")
    pretrade.add_argument("--risk-budget", default="Undefined")
    pretrade.add_argument("--context", default="")
    pretrade.add_argument("--output", type=Path)

    playbook = sub.add_parser(
        "playbook",
        help="Single-ticker buy playbook combining macro, sector rotation, technicals, and Kelly sizing.",
    )
    playbook.add_argument("ticker")
    playbook.add_argument(
        "--aum", type=float,
        help="Total AUM for position sizing. Omit to skip the sizing block."
    )
    playbook.add_argument(
        "--target-vol", type=float, default=0.35,
        help="Target portfolio annualized volatility (e.g. 0.35 = 35%%). Default 0.35.",
    )
    playbook.add_argument(
        "--kelly-multiplier", type=float, default=0.5,
        help="Fraction of full Kelly to apply (0.5 = half Kelly, safer). Default 0.5.",
    )
    playbook.add_argument(
        "--max-position", type=float, default=0.25,
        help="Hard cap on single-name weight (e.g. 0.25 = 25%%). Default 0.25.",
    )
    playbook.add_argument(
        "--max-risk", type=float, default=0.02,
        help="Hard cap on portfolio loss if stopped out (e.g. 0.02 = 2%%). Default 0.02.",
    )
    playbook.add_argument(
        "--win-probability", type=float, default=0.55,
        help="Subjective probability of thesis winning. Default 0.55.",
    )
    playbook.add_argument(
        "--upside", type=float, default=0.5,
        help="Expected upside if thesis works (fraction, e.g. 0.5 = +50%%). Default 0.5.",
    )
    playbook.add_argument(
        "--downside", type=float, default=0.2,
        help="Stop-loss fraction (e.g. 0.2 = -20%%). Default 0.2.",
    )
    playbook.add_argument(
        "--industry",
        help="Industry/sector hint (ETF symbol or name). If omitted, the strongest current sector is used.",
    )
    playbook.add_argument("--output", type=Path)

    return parser


def emit(text: str, output: Path | None) -> int:
    if output is None:
        print(text)
        return 0
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text + "\n", encoding="utf-8")
    print(f"Wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
