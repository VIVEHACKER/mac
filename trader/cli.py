from __future__ import annotations

import argparse
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

from data.catalog import DEFAULT_CATALOG_PATH, MarketDataCatalog
from data.delistings import load_delisting_returns_csv
from data.fundamentals_csv import load_fundamentals_csv
from data.ingest.ccxt_crypto import fetch_ccxt_bars, normalize_crypto_symbol
from data.ingest.fred_macro import fetch_fred_series
from data.ingest.krx_flow_csv import KrxFlowCsvError, parse_krx_flow_csv
from data.ingest.krx_flows import KrxFlowError, fetch_krx_flows, fetch_naver_investor_flows
from data.ingest.option_sentiment import vix_from_macro
from data.ingest.pykrx_kr import fetch_pykrx_bars, normalize_kr_symbol
from data.ingest.yahoo import YAHOO_ADJUSTED_SOURCE_MARKER, fetch_yahoo_bars
from data.ingest.yahoo_options import YahooOptionChainError, fetch_yahoo_option_quotes
from data.ingest.yfinance_fundamentals import fetch_yfinance_fundamentals
from data.models import (
    DelistingReturn,
    FlowRecord,
    FundamentalRecord,
    MacroObservation,
    OptionSentimentRecord,
    UniverseMember,
    ValuationRecord,
)
from data.quality import evaluate_catalog_quality, format_quality_report
from data.universe import load_universe_members_csv
from data.universe_audit import format_universe_audit_report, run_universe_audit
from engine.backtest import format_backtest_report, run_momentum_backtest
from engine.factor_portfolio import format_factor_portfolio_report, run_factor_rotation_backtest
from engine.paper import PaperBroker
from engine.portfolio import (
    format_portfolio_report,
    format_screen_report,
    run_momentum_rotation_backtest,
    screen_momentum,
)
from engine.robustness import format_robustness_report, run_momentum_robustness_grid
from engine.walkforward import (
    SELECTION_METRICS,
    format_walk_forward_report,
    run_factor_walk_forward,
)
from risk.kill_switch import check_kill_switch
from risk.shortability import (
    ShortabilityCheck,
    check_shortability,
    load_short_availability_csv,
)
from strategies.factor_aqr import rank_aqr_factors
from strategies.statarb_pairs import (
    PairAnalysis,
    PairBacktestResult,
    analyze_pair,
    backtest_pair_mean_reversion,
)
from valuation.composite import composite_fair_value, confidence_from_dispersion
from valuation.dcf import discounted_cash_flow
from valuation.entry import average_true_range_pct, make_entry_plan
from valuation.option_vol import (
    VixCalculationResult,
    calculate_vix_like_index,
    load_option_quotes_csv,
)
from valuation.score import discount_pct, rating_from_discount

ROOT = Path(__file__).resolve().parents[1]
COPILOT_DIR = ROOT / "trading-copilot"
DEFAULT_FINANCIAL_SERVICES_DIR = ROOT / "financial-services"
DEFAULT_DB = COPILOT_DIR / "data" / "copilot.db"
DEFAULT_CATALOG_DB = ROOT / DEFAULT_CATALOG_PATH
CORE_COMMANDS = {
    "init",
    "ingest",
    "bars",
    "status",
    "screen",
    "backtest",
    "portfolio",
    "factor-portfolio",
    "walk-forward",
    "universe-audit",
    "pair",
    "vix-calc",
    "macro",
    "fundamentals",
    "flows",
    "factor",
    "valuate",
    "entry",
    "paper",
    "risk-check",
    "robustness",
    "quality",
    "dashboard",
    "copilot",
}


def main(argv: list[str] | None = None) -> int:
    """Run the root trading system, with copilot commands kept as fallback."""
    args = list(sys.argv[1:] if argv is None else argv)
    load_dotenv(ROOT / ".env")

    if not args or args[0] in {"-h", "--help"}:
        build_parser().print_help()
        return 0
    if args[0] not in CORE_COMMANDS:
        return _run_copilot(args)

    parser = build_parser()
    parsed = parser.parse_args(args)

    if parsed.command == "copilot":
        return _run_copilot(parsed.args)
    if parsed.command == "init":
        return _run_init(parsed.catalog_db)
    if parsed.command == "ingest":
        return _run_ingest(parsed)
    if parsed.command == "bars":
        return _run_bars(parsed)
    if parsed.command == "status":
        return _run_status(parsed.catalog_db)
    if parsed.command == "screen":
        return _run_screen(parsed)
    if parsed.command == "backtest":
        return _run_backtest(parsed)
    if parsed.command == "portfolio":
        return _run_portfolio(parsed)
    if parsed.command == "factor-portfolio":
        return _run_factor_portfolio(parsed)
    if parsed.command == "walk-forward":
        return _run_walk_forward(parsed)
    if parsed.command == "universe-audit":
        return _run_universe_audit(parsed)
    if parsed.command == "pair":
        return _run_pair(parsed)
    if parsed.command == "vix-calc":
        return _run_vix_calc(parsed)
    if parsed.command == "macro":
        return _run_macro(parsed)
    if parsed.command == "fundamentals":
        return _run_fundamentals(parsed)
    if parsed.command == "flows":
        return _run_flows(parsed)
    if parsed.command == "factor":
        return _run_factor(parsed)
    if parsed.command == "valuate":
        return _run_valuate(parsed)
    if parsed.command == "entry":
        return _run_entry(parsed)
    if parsed.command == "paper":
        return _run_paper(parsed)
    if parsed.command == "risk-check":
        return _run_risk_check(parsed)
    if parsed.command == "robustness":
        return _run_robustness(parsed)
    if parsed.command == "quality":
        return _run_quality(parsed)
    if parsed.command == "dashboard":
        return _run_dashboard(parsed)

    parser.print_help()
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="trader",
        description=(
            "Runnable trading MVP: ingest price data, query DuckDB, and backtest a "
            "time-series momentum strategy. Unknown commands are forwarded to trading-copilot."
        ),
    )
    sub = parser.add_subparsers(dest="command")

    init = sub.add_parser("init", help="Initialize the root DuckDB catalog and copilot DB.")
    init.add_argument("--catalog-db", type=Path, default=DEFAULT_CATALOG_DB)

    ingest = sub.add_parser("ingest", help="Fetch daily bars and store them in DuckDB.")
    _add_market_symbols_args(ingest)
    _add_date_args(ingest)
    _add_provider_arg(ingest)
    ingest.add_argument("--catalog-db", type=Path, default=DEFAULT_CATALOG_DB)

    bars = sub.add_parser("bars", help="Print stored bars from the DuckDB catalog.")
    _add_market_symbol_args(bars)
    _add_date_args(bars, required=False)
    bars.add_argument("--limit", type=int, default=10)
    bars.add_argument("--catalog-db", type=Path, default=DEFAULT_CATALOG_DB)

    status = sub.add_parser("status", help="Show stored catalog coverage.")
    status.add_argument("--catalog-db", type=Path, default=DEFAULT_CATALOG_DB)

    screen = sub.add_parser("screen", help="Rank a stored/fetched universe by lookback momentum.")
    _add_market_symbols_args(screen)
    _add_date_args(screen)
    screen.add_argument("--lookback", type=int, default=126)
    screen.add_argument("--no-fetch", action="store_true", help="Use stored bars only.")
    _add_provider_arg(screen)
    screen.add_argument("--output", type=Path)
    screen.add_argument("--catalog-db", type=Path, default=DEFAULT_CATALOG_DB)

    backtest = sub.add_parser(
        "backtest",
        help="Run a no-key Yahoo-backed momentum backtest, auto-ingesting missing bars.",
    )
    _add_market_symbol_args(backtest)
    _add_date_args(backtest)
    backtest.add_argument("--lookback", type=int, default=126)
    backtest.add_argument("--initial-cash", type=float, default=10_000.0)
    backtest.add_argument("--fee-bps", type=float, default=2.0)
    backtest.add_argument("--max-position", type=float, default=1.0)
    backtest.add_argument("--no-fetch", action="store_true", help="Use stored bars only.")
    _add_provider_arg(backtest)
    _add_benchmark_args(backtest)
    backtest.add_argument("--output", type=Path)
    backtest.add_argument("--catalog-db", type=Path, default=DEFAULT_CATALOG_DB)

    portfolio = sub.add_parser(
        "portfolio",
        help="Backtest a top-N cross-sectional momentum rotation portfolio.",
    )
    _add_market_symbols_args(portfolio)
    _add_date_args(portfolio)
    portfolio.add_argument("--lookback", type=int, default=126)
    portfolio.add_argument("--top-n", type=int, default=3)
    portfolio.add_argument("--rebalance-days", type=int, default=21)
    portfolio.add_argument("--initial-cash", type=float, default=10_000.0)
    portfolio.add_argument("--fee-bps", type=float, default=2.0)
    portfolio.add_argument("--no-fetch", action="store_true", help="Use stored bars only.")
    _add_provider_arg(portfolio)
    _add_benchmark_args(portfolio)
    _add_pit_universe_args(portfolio)
    _add_delisting_return_args(portfolio)
    _add_preflight_audit_args(portfolio)
    portfolio.add_argument("--output", type=Path)
    portfolio.add_argument("--catalog-db", type=Path, default=DEFAULT_CATALOG_DB)

    factor_portfolio = sub.add_parser(
        "factor-portfolio",
        help="Backtest multi-factor rotation with reversal, low-vol and risk filter controls.",
    )
    _add_market_symbols_args(factor_portfolio)
    _add_date_args(factor_portfolio)
    _add_factor_portfolio_args(factor_portfolio)
    factor_portfolio.add_argument("--no-fetch", action="store_true", help="Use stored bars only.")
    _add_provider_arg(factor_portfolio)
    _add_benchmark_args(factor_portfolio)
    _add_pit_universe_args(factor_portfolio)
    _add_delisting_return_args(factor_portfolio)
    _add_preflight_audit_args(factor_portfolio)
    factor_portfolio.add_argument("--output", type=Path)
    factor_portfolio.add_argument("--catalog-db", type=Path, default=DEFAULT_CATALOG_DB)

    walk_forward = sub.add_parser(
        "walk-forward",
        help="Run factor walk-forward parameter selection and out-of-sample validation.",
    )
    _add_market_symbols_args(walk_forward)
    _add_date_args(walk_forward)
    _add_factor_portfolio_args(walk_forward)
    walk_forward.add_argument("--train-years", type=int, default=5)
    walk_forward.add_argument(
        "--validation-years",
        type=int,
        default=0,
        help="Use the last N train years as an inner validation window for parameter selection.",
    )
    walk_forward.add_argument("--test-years", type=int, default=3)
    walk_forward.add_argument("--step-years", type=int, default=1)
    walk_forward.add_argument("--momentum-lookbacks", default="126,252")
    walk_forward.add_argument("--top-ns", default="2,3")
    walk_forward.add_argument("--reversal-lookbacks", help="Comma-separated reversal lookbacks.")
    walk_forward.add_argument("--volatility-lookbacks", help="Comma-separated volatility lookbacks.")
    walk_forward.add_argument("--rebalance-days-values", help="Comma-separated rebalance intervals.")
    walk_forward.add_argument(
        "--defensive-symbols",
        help="Comma-separated defensive assets. Use CASH to hold cash when risk-off.",
    )
    walk_forward.add_argument(
        "--max-risk-weights",
        help="Comma-separated max risky-asset weights for search, e.g. 0.7,1.0.",
    )
    walk_forward.add_argument(
        "--drawdown-guards",
        help="Comma-separated strategy drawdown guard thresholds for search, e.g. 0,0.15.",
    )
    walk_forward.add_argument(
        "--risk-filter-lookbacks",
        help="Comma-separated risk-filter lookbacks for search. 0 disables the filter.",
    )
    walk_forward.add_argument(
        "--weighting-modes",
        help="Comma-separated weighting modes for search: inverse-vol,equal.",
    )
    walk_forward.add_argument(
        "--selection-metric",
        default="annualized-excess",
        choices=sorted(SELECTION_METRICS),
        help="Train-window objective used to choose the next test-window parameter set.",
    )
    walk_forward.add_argument("--no-fetch", action="store_true", help="Use stored bars only.")
    _add_provider_arg(walk_forward)
    _add_benchmark_args(walk_forward)
    _add_pit_universe_args(walk_forward)
    _add_delisting_return_args(walk_forward)
    _add_preflight_audit_args(walk_forward)
    walk_forward.add_argument("--output", type=Path)
    walk_forward.add_argument("--catalog-db", type=Path, default=DEFAULT_CATALOG_DB)

    universe_audit = sub.add_parser(
        "universe-audit",
        help="Check PIT universe readiness: bars, delistings, and optional fundamentals.",
    )
    universe_audit.add_argument("symbols", nargs="?", default="ALL")
    universe_audit.add_argument("--market", default="us", choices=["us", "kospi", "kosdaq", "crypto"])
    _add_date_args(universe_audit)
    _add_pit_universe_args(universe_audit)
    _add_delisting_return_args(universe_audit)
    universe_audit.add_argument("--require-fundamentals", action="store_true")
    universe_audit.add_argument("--no-require-delistings", action="store_true")
    universe_audit.add_argument("--rebalance-days", type=int, default=21)
    universe_audit.add_argument("--strict", action="store_true")
    universe_audit.add_argument("--output", type=Path)
    universe_audit.add_argument("--catalog-db", type=Path, default=DEFAULT_CATALOG_DB)

    pair = sub.add_parser(
        "pair",
        help="Analyze a two-asset statistical-arbitrage spread from catalog bars.",
    )
    pair.add_argument("first")
    pair.add_argument("second")
    pair.add_argument("--market", default="us", choices=["us", "kospi", "kosdaq", "crypto"])
    _add_date_args(pair)
    pair.add_argument("--lookback", type=int, default=252)
    pair.add_argument("--entry-z", type=float, default=2.0)
    pair.add_argument("--exit-z", type=float, default=0.5)
    pair.add_argument("--min-observations", type=int, default=60)
    pair.add_argument("--validate", action="store_true", help="Run rolling cost-adjusted validation.")
    pair.add_argument("--fee-bps", type=float, default=2.0)
    pair.add_argument("--slippage-bps", type=float, default=2.0)
    pair.add_argument("--min-trades", type=int, default=3)
    pair.add_argument("--min-sharpe", type=float, default=0.0)
    pair.add_argument("--max-drawdown", type=float, default=0.2)
    pair.add_argument("--shortability-csv", type=Path)
    pair.add_argument("--require-shortability", action="store_true")
    pair.add_argument("--max-borrow-fee-bps", type=float, default=500.0)
    pair.add_argument("--shortability-max-age-days", type=int, default=2)
    pair.add_argument(
        "--min-shortability-confidence",
        default="medium",
        choices=["low", "medium", "high"],
    )
    pair.add_argument("--no-fetch", action="store_true", help="Use stored bars only.")
    _add_provider_arg(pair)
    pair.add_argument("--output", type=Path)
    pair.add_argument("--catalog-db", type=Path, default=DEFAULT_CATALOG_DB)

    vix_calc = sub.add_parser(
        "vix-calc",
        help="Calculate a VIX-like 30-day volatility index from an option-chain CSV.",
    )
    vix_calc.add_argument("--source", choices=["csv", "yahoo"], default="csv")
    vix_calc.add_argument("--file", type=Path)
    vix_calc.add_argument("--underlying", help="Yahoo option-chain underlying, e.g. SPY.")
    vix_calc.add_argument("--expirations", help="Comma-separated YYYY-MM-DD expirations for Yahoo.")
    vix_calc.add_argument("--as-of", default=date.today().isoformat())
    vix_calc.add_argument("--target-days", type=int, default=30)
    vix_calc.add_argument(
        "--risk-free-rate",
        type=float,
        default=None,
        help="Annualized decimal rate, e.g. 0.045 for 4.5%.",
    )
    vix_calc.add_argument(
        "--risk-free-series",
        default="DGS10",
        help="Catalog macro series used when --risk-free-rate is omitted. Use empty string to disable.",
    )
    vix_calc.add_argument("--risk-free-max-age-days", type=int, default=14)
    vix_calc.add_argument("--max-option-quote-age-days", type=int, default=7)
    vix_calc.add_argument("--max-bid-ask-spread-pct", type=float, default=0.5)
    vix_calc.add_argument("--require-last-trade", action="store_true")
    vix_calc.add_argument("--strict-quality", action="store_true")
    vix_calc.add_argument("--store", action="store_true", help="Store result in option_sentiment.")
    vix_calc.add_argument("--market", default="US")
    vix_calc.add_argument("--output", type=Path)
    vix_calc.add_argument("--catalog-db", type=Path, default=DEFAULT_CATALOG_DB)

    robustness = sub.add_parser(
        "robustness",
        help="Run train/test parameter-grid checks for a momentum rotation portfolio.",
    )
    _add_market_symbols_args(robustness)
    _add_date_args(robustness)
    robustness.add_argument("--split", required=True, help="Train/test split date, e.g. 2016-01-01.")
    robustness.add_argument("--lookbacks", default="63,126,252")
    robustness.add_argument("--top-ns", default="1,2,3")
    robustness.add_argument("--rebalance-days", default="21,63")
    robustness.add_argument("--initial-cash", type=float, default=10_000.0)
    robustness.add_argument("--fee-bps", type=float, default=2.0)
    robustness.add_argument("--no-fetch", action="store_true", help="Use stored bars only.")
    _add_provider_arg(robustness)
    _add_benchmark_args(robustness)
    _add_pit_universe_args(robustness)
    _add_delisting_return_args(robustness)
    _add_preflight_audit_args(robustness)
    robustness.add_argument("--output", type=Path)
    robustness.add_argument("--catalog-db", type=Path, default=DEFAULT_CATALOG_DB)

    macro = sub.add_parser("macro", help="Fetch/store public FRED macro series.")
    macro.add_argument("series", help="FRED series id or comma-separated ids, e.g. DGS10,VIXCLS")
    _add_date_args(macro)
    macro.add_argument("--provider", default="fred", choices=["fred", "manual"])
    macro.add_argument("--date")
    macro.add_argument("--value", type=float)
    macro.add_argument("--country", default="US")
    macro.add_argument("--category", default="macro")
    macro.add_argument("--catalog-db", type=Path, default=DEFAULT_CATALOG_DB)

    fundamentals = sub.add_parser(
        "fundamentals",
        help="Store PIT fundamentals from yfinance, CSV, or explicit numeric inputs.",
    )
    _add_market_symbol_args(fundamentals)
    fundamentals.add_argument("--provider", default="yfinance", choices=["yfinance", "csv", "manual"])
    fundamentals.add_argument("--file", type=Path, help="CSV fundamentals import.")
    fundamentals.add_argument("--period-end", default=date.today().isoformat())
    fundamentals.add_argument("--revenue", type=float)
    fundamentals.add_argument("--net-income", type=float)
    fundamentals.add_argument("--free-cash-flow", type=float)
    fundamentals.add_argument("--equity", type=float)
    fundamentals.add_argument("--debt", type=float)
    fundamentals.add_argument("--shares-out", type=float)
    fundamentals.add_argument("--eps", type=float)
    fundamentals.add_argument("--catalog-db", type=Path, default=DEFAULT_CATALOG_DB)

    flows = sub.add_parser("flows", help="Fetch/store KRX investor flow data.")
    _add_market_symbol_args(flows)
    _add_date_args(flows)
    flows.add_argument(
        "--provider",
        default="pykrx",
        choices=["pykrx", "csv", "naver-estimate", "manual"],
        help=(
            "pykrx and csv store reported KRX rows only. naver-estimate stores "
            "close x net-volume estimates in quarantine."
        ),
    )
    flows.add_argument(
        "--allow-estimated",
        action="store_true",
        help="Allow pykrx to fall back to quarantined Naver estimated rows.",
    )
    flows.add_argument("--date")
    flows.add_argument("--investor", default="foreign")
    flows.add_argument("--net-value", type=float)
    flows.add_argument("--buy-value", type=float, default=0.0)
    flows.add_argument("--sell-value", type=float, default=0.0)
    flows.add_argument("--net-volume", type=float)
    flows.add_argument("--value-kind", default="reported_value")
    flows.add_argument("--confidence", default="high", choices=["high", "medium", "low"])
    flows.add_argument("--file", type=Path, help="KRX reported investor-flow CSV export.")
    flows.add_argument("--catalog-db", type=Path, default=DEFAULT_CATALOG_DB)

    factor = sub.add_parser("factor", help="Rank a universe by AQR value/momentum/quality.")
    _add_market_symbols_args(factor)
    _add_date_args(factor)
    factor.add_argument("--lookback", type=int, default=126)
    factor.add_argument("--output", type=Path)
    factor.add_argument("--catalog-db", type=Path, default=DEFAULT_CATALOG_DB)

    valuate = sub.add_parser("valuate", help="Compute/store a valuation score and fair value.")
    _add_market_symbol_args(valuate)
    valuate.add_argument("--fair-value", type=float)
    valuate.add_argument("--fair-dcf", type=float)
    valuate.add_argument("--fair-multiple", type=float)
    valuate.add_argument("--fair-rim", type=float)
    valuate.add_argument("--wacc", type=float, default=0.09)
    valuate.add_argument("--growth", type=float, default=0.05)
    valuate.add_argument("--terminal-growth", type=float, default=0.025)
    valuate.add_argument("--catalog-db", type=Path, default=DEFAULT_CATALOG_DB)

    entry = sub.add_parser("entry", help="Create/store a margin-of-safety ATR entry ladder.")
    _add_market_symbol_args(entry)
    entry.add_argument("--margin-of-safety", type=float, default=0.25)
    entry.add_argument("--catalog-db", type=Path, default=DEFAULT_CATALOG_DB)

    paper = sub.add_parser("paper", help="Run a local paper order simulation.")
    _add_market_symbol_args(paper)
    paper.add_argument("--side", choices=["buy", "sell"], default="buy")
    paper.add_argument("--qty", type=float, required=True)
    paper.add_argument("--price", type=float, required=True)
    paper.add_argument("--cash", type=float, default=10_000.0)
    paper.add_argument("--strategy", default="manual")

    risk_check = sub.add_parser("risk-check", help="Evaluate kill-switch limits.")
    risk_check.add_argument("--start-equity", type=float, required=True)
    risk_check.add_argument("--current-equity", type=float, required=True)
    risk_check.add_argument("--gross-exposure", type=float, required=True)
    risk_check.add_argument("--max-daily-drawdown", type=float, default=0.02)
    risk_check.add_argument("--max-gross-exposure", type=float, default=1.0)

    quality = sub.add_parser("quality", help="Run catalog data-quality checks.")
    quality.add_argument("--as-of", default=date.today().isoformat())
    quality.add_argument(
        "--strict",
        action="store_true",
        help="Return non-zero for warnings; quarantined estimate info is non-blocking.",
    )
    quality.add_argument("--require-flow", help="Comma-separated symbols requiring reported flow.")
    quality.add_argument(
        "--flow-market",
        default="kospi",
        choices=["kospi", "kosdaq"],
        help="Market for --require-flow symbols.",
    )
    quality.add_argument("--catalog-db", type=Path, default=DEFAULT_CATALOG_DB)

    dashboard = sub.add_parser("dashboard", help="Print the Streamlit command for the dashboard.")
    dashboard.add_argument("--port", type=int, default=8501)

    copilot = sub.add_parser("copilot", help="Forward arguments to the integrated copilot CLI.")
    copilot.add_argument("args", nargs=argparse.REMAINDER)
    return parser


def _run_init(catalog_db: Path) -> int:
    MarketDataCatalog(catalog_db).initialize()
    copilot_result = _run_copilot(["init"])
    print(f"Initialized market catalog: {catalog_db}")
    return copilot_result


def _run_ingest(args: argparse.Namespace) -> int:
    start = _parse_date(args.start)
    end = _parse_date(args.end)
    catalog = MarketDataCatalog(args.catalog_db)
    for symbol in _parse_symbols(args.symbols):
        bars = _fetch_bars(symbol, args.market, start, end, provider=args.provider)
        stored = catalog.put_bars(bars)
        first = bars[0]
        last = bars[-1]
        print(
            f"Stored {stored} {first.freq} bars for {first.symbol} "
            f"({first.market}, {first.source_symbol}) from {first.ts} to {last.ts}"
        )
    return 0


def _run_bars(args: argparse.Namespace) -> int:
    catalog = MarketDataCatalog(args.catalog_db)
    symbol = _catalog_symbol(args.symbol, args.market)
    bars = catalog.get_bars(
        symbol,
        market=args.market,
        start=_parse_optional_date(args.start),
        end=_parse_optional_date(args.end),
    )
    if not bars:
        print("No bars found. Run `uv run trader ingest SYMBOL` first.")
        return 1
    selected = bars[-args.limit :] if args.limit > 0 else bars
    print("| Date | Symbol | Open | High | Low | Close | Volume |")
    print("|---|---|---:|---:|---:|---:|---:|")
    for bar in selected:
        print(
            f"| {bar.ts} | {bar.symbol} | {bar.open:.2f} | {bar.high:.2f} | "
            f"{bar.low:.2f} | {bar.close:.2f} | {bar.volume:,.0f} |"
        )
    return 0


def _run_status(catalog_db: Path) -> int:
    coverage = MarketDataCatalog(catalog_db).coverage()
    if not coverage:
        print("No catalog data yet. Run `uv run trader ingest MSFT`.")
        return 0
    print("| Symbol | Market | Freq | Start | End | Rows |")
    print("|---|---|---|---|---|---:|")
    for item in coverage:
        print(
            f"| {item.symbol} | {item.market} | {item.freq} | "
            f"{item.start} | {item.end} | {item.rows} |"
        )
    return 0


def _run_screen(args: argparse.Namespace) -> int:
    start = _parse_date(args.start)
    end = _parse_date(args.end)
    catalog = MarketDataCatalog(args.catalog_db)
    bars_by_symbol = _load_or_fetch_universe(
        catalog=catalog,
        symbols=_parse_symbols(args.symbols),
        market=args.market,
        start=start,
        end=end,
        fetch_missing=not args.no_fetch,
        provider=args.provider,
    )
    rows = screen_momentum(bars_by_symbol, lookback=args.lookback)
    if not rows:
        print("No screen rows available. Use a shorter --lookback or fetch more history.")
        return 1
    report = format_screen_report(rows)
    return _emit(report, args.output)


def _run_backtest(args: argparse.Namespace) -> int:
    start = _parse_date(args.start)
    end = _parse_date(args.end)
    catalog = MarketDataCatalog(args.catalog_db)
    catalog_symbol = _catalog_symbol(args.symbol, args.market)
    bars = catalog.get_bars(catalog_symbol, market=args.market, start=start, end=end)
    if _bars_need_fetch(bars, args.market, args.provider, start, end) and not args.no_fetch:
        fetched = _fetch_bars(args.symbol, args.market, start, end, provider=args.provider)
        catalog.put_bars(fetched)
        bars = catalog.get_bars(catalog_symbol, market=args.market, start=start, end=end)
    if not bars:
        print("No bars available for backtest.")
        return 1
    benchmark_bars = _load_or_fetch_benchmark(
        catalog=catalog,
        symbol=args.benchmark,
        market=args.benchmark_market or args.market,
        start=start,
        end=end,
        fetch_missing=not args.no_fetch,
        provider=_benchmark_provider(args.provider, args.market, args.benchmark_market),
    )

    result = run_momentum_backtest(
        bars,
        lookback=args.lookback,
        initial_cash=args.initial_cash,
        fee_bps=args.fee_bps,
        max_position=args.max_position,
        benchmark_bars=benchmark_bars,
    )
    report = format_backtest_report(result)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report + "\n", encoding="utf-8")
        print(f"Wrote {args.output}")
    else:
        print(report)
    return 0


def _run_portfolio(args: argparse.Namespace) -> int:
    start = _parse_date(args.start)
    end = _parse_date(args.end)
    catalog = MarketDataCatalog(args.catalog_db)
    pit_members = _load_pit_universe(catalog, args, market=args.market)
    symbols = _symbols_for_request(args.symbols, pit_members)
    pit_members = _filter_pit_members(pit_members, symbols)
    delisting_returns = _load_delisting_returns(catalog, args, symbols, args.market, start, end)
    bars_by_symbol = _load_or_fetch_universe(
        catalog=catalog,
        symbols=symbols,
        market=args.market,
        start=start,
        end=end,
        fetch_missing=not args.no_fetch,
        provider=args.provider,
    )
    preflight = _preflight_universe_audit(
        catalog,
        args,
        pit_members=pit_members,
        symbols=symbols,
        market=args.market,
        start=start,
        end=end,
    )
    if preflight != 0:
        return preflight
    benchmark_bars = _load_or_fetch_benchmark(
        catalog=catalog,
        symbol=args.benchmark,
        market=args.benchmark_market or args.market,
        start=start,
        end=end,
        fetch_missing=not args.no_fetch,
        provider=_benchmark_provider(args.provider, args.market, args.benchmark_market),
    )
    result = run_momentum_rotation_backtest(
        bars_by_symbol,
        lookback=args.lookback,
        top_n=args.top_n,
        initial_cash=args.initial_cash,
        rebalance_days=args.rebalance_days,
        fee_bps=args.fee_bps,
        benchmark_bars=benchmark_bars,
        universe_members=pit_members,
        delisting_returns=delisting_returns,
    )
    report = format_portfolio_report(result)
    return _emit(report, args.output)


def _run_robustness(args: argparse.Namespace) -> int:
    start = _parse_date(args.start)
    end = _parse_date(args.end)
    split = _parse_date(args.split)
    if split <= start or split >= end:
        raise ValueError("--split must be between --start and --end")
    if args.benchmark is None:
        raise ValueError("--benchmark is required for robustness checks")
    catalog = MarketDataCatalog(args.catalog_db)
    pit_members = _load_pit_universe(catalog, args, market=args.market)
    symbols = _symbols_for_request(args.symbols, pit_members)
    pit_members = _filter_pit_members(pit_members, symbols)
    delisting_returns = _load_delisting_returns(catalog, args, symbols, args.market, start, end)
    bars_by_symbol = _load_or_fetch_universe(
        catalog=catalog,
        symbols=symbols,
        market=args.market,
        start=start,
        end=end,
        fetch_missing=not args.no_fetch,
        provider=args.provider,
    )
    preflight = _preflight_universe_audit(
        catalog,
        args,
        pit_members=pit_members,
        symbols=symbols,
        market=args.market,
        start=start,
        end=end,
    )
    if preflight != 0:
        return preflight
    benchmark_market = args.benchmark_market or args.market
    benchmark_bars = _load_or_fetch_benchmark(
        catalog=catalog,
        symbol=args.benchmark,
        market=benchmark_market,
        start=start,
        end=end,
        fetch_missing=not args.no_fetch,
        provider=_benchmark_provider(args.provider, args.market, args.benchmark_market),
    )
    if benchmark_bars is None:
        raise ValueError("--benchmark is required for robustness checks")
    report = run_momentum_robustness_grid(
        bars_by_symbol,
        benchmark_bars=benchmark_bars,
        split_date=split,
        lookbacks=tuple(_parse_ints(args.lookbacks)),
        top_ns=tuple(_parse_ints(args.top_ns)),
        rebalance_days_values=tuple(_parse_ints(args.rebalance_days)),
        initial_cash=args.initial_cash,
        fee_bps=args.fee_bps,
        universe_members=pit_members,
        delisting_returns=delisting_returns,
    )
    if not report.rows:
        print("No robustness rows available. Use a longer window or smaller lookbacks.")
        return 1
    return _emit(format_robustness_report(report), args.output)


def _run_factor_portfolio(args: argparse.Namespace) -> int:
    start = _parse_date(args.start)
    end = _parse_date(args.end)
    data_start = _factor_warmup_start(
        start,
        args.momentum_lookback,
        args.reversal_lookback,
        args.volatility_lookback,
        args.risk_filter_lookback,
    )
    catalog = MarketDataCatalog(args.catalog_db)
    pit_members = _load_pit_universe(catalog, args, market=args.market)
    symbols = _symbols_for_request(args.symbols, pit_members)
    pit_members = _filter_pit_members(pit_members, symbols)
    delisting_returns = _load_delisting_returns(catalog, args, symbols, args.market, data_start, end)
    bars_by_symbol = _load_or_fetch_universe(
        catalog=catalog,
        symbols=symbols,
        market=args.market,
        start=data_start,
        end=end,
        fetch_missing=not args.no_fetch,
        provider=args.provider,
    )
    preflight = _preflight_universe_audit(
        catalog,
        args,
        pit_members=pit_members,
        symbols=symbols,
        market=args.market,
        start=start,
        end=end,
    )
    if preflight != 0:
        return preflight
    benchmark_bars = _load_or_fetch_benchmark(
        catalog=catalog,
        symbol=args.benchmark,
        market=args.benchmark_market or args.market,
        start=data_start,
        end=end,
        fetch_missing=not args.no_fetch,
        provider=_benchmark_provider(args.provider, args.market, args.benchmark_market),
    )
    result = run_factor_rotation_backtest(
        bars_by_symbol,
        benchmark_bars=benchmark_bars,
        fundamentals_by_symbol=_fundamentals_history(catalog, symbols, args.market),
        universe_members=pit_members,
        delisting_returns=delisting_returns,
        momentum_lookback=args.momentum_lookback,
        reversal_lookback=args.reversal_lookback,
        volatility_lookback=args.volatility_lookback,
        risk_filter_lookback=args.risk_filter_lookback,
        top_n=args.top_n,
        initial_cash=args.initial_cash,
        rebalance_days=args.rebalance_days,
        fee_bps=args.fee_bps,
        defensive_symbol=args.defensive_symbol,
        weighting=args.weighting,
        max_risk_weight=args.max_risk_weight,
        drawdown_guard=args.drawdown_guard,
        defensive_only=args.defensive_only,
        trade_start=start,
        trade_end=end,
    )
    return _emit(format_factor_portfolio_report(result), args.output)


def _run_walk_forward(args: argparse.Namespace) -> int:
    start = _parse_date(args.start)
    end = _parse_date(args.end)
    if args.benchmark is None:
        raise ValueError("--benchmark is required for walk-forward checks")
    momentum_lookbacks = tuple(_parse_ints(args.momentum_lookbacks))
    top_ns = tuple(_parse_ints(args.top_ns))
    reversal_lookbacks = (
        tuple(_parse_ints(args.reversal_lookbacks))
        if args.reversal_lookbacks
        else (args.reversal_lookback,)
    )
    volatility_lookbacks = (
        tuple(_parse_ints(args.volatility_lookbacks))
        if args.volatility_lookbacks
        else (args.volatility_lookback,)
    )
    rebalance_days_values = (
        tuple(_parse_ints(args.rebalance_days_values))
        if args.rebalance_days_values
        else (args.rebalance_days,)
    )
    defensive_symbols = (
        tuple(_parse_defensive_symbols(args.defensive_symbols))
        if args.defensive_symbols
        else (args.defensive_symbol,)
    )
    max_risk_weights = (
        tuple(_parse_floats(args.max_risk_weights, min_value=0.0, max_value=1.0, min_exclusive=True))
        if args.max_risk_weights
        else (args.max_risk_weight,)
    )
    drawdown_guards = (
        tuple(_parse_floats(args.drawdown_guards, min_value=0.0, max_value=1.0, max_exclusive=True))
        if args.drawdown_guards
        else (args.drawdown_guard,)
    )
    risk_filter_lookbacks = (
        tuple(_parse_nonnegative_ints(args.risk_filter_lookbacks))
        if args.risk_filter_lookbacks
        else (args.risk_filter_lookback,)
    )
    weighting_modes = (
        tuple(_parse_weighting_modes(args.weighting_modes))
        if args.weighting_modes
        else (args.weighting,)
    )
    data_start = _factor_warmup_start(
        start,
        *momentum_lookbacks,
        *reversal_lookbacks,
        *volatility_lookbacks,
        *risk_filter_lookbacks,
    )
    catalog = MarketDataCatalog(args.catalog_db)
    pit_members = _load_pit_universe(catalog, args, market=args.market)
    symbols = _symbols_for_request(args.symbols, pit_members)
    pit_members = _filter_pit_members(pit_members, symbols)
    delisting_returns = _load_delisting_returns(catalog, args, symbols, args.market, data_start, end)
    bars_by_symbol = _load_or_fetch_universe(
        catalog=catalog,
        symbols=symbols,
        market=args.market,
        start=data_start,
        end=end,
        fetch_missing=not args.no_fetch,
        provider=args.provider,
    )
    preflight = _preflight_universe_audit(
        catalog,
        args,
        pit_members=pit_members,
        symbols=symbols,
        market=args.market,
        start=start,
        end=end,
    )
    if preflight != 0:
        return preflight
    benchmark_bars = _load_or_fetch_benchmark(
        catalog=catalog,
        symbol=args.benchmark,
        market=args.benchmark_market or args.market,
        start=data_start,
        end=end,
        fetch_missing=not args.no_fetch,
        provider=_benchmark_provider(args.provider, args.market, args.benchmark_market),
    )
    if benchmark_bars is None:
        raise ValueError("--benchmark is required for walk-forward checks")
    report = run_factor_walk_forward(
        bars_by_symbol,
        benchmark_bars=benchmark_bars,
        fundamentals_by_symbol=_fundamentals_history(catalog, symbols, args.market),
        universe_members=pit_members,
        delisting_returns=delisting_returns,
        start=start,
        end=end,
        train_years=args.train_years,
        validation_years=args.validation_years,
        test_years=args.test_years,
        step_years=args.step_years,
        momentum_lookbacks=momentum_lookbacks,
        reversal_lookbacks=reversal_lookbacks,
        volatility_lookbacks=volatility_lookbacks,
        top_ns=top_ns,
        risk_filter_lookback=args.risk_filter_lookback,
        risk_filter_lookbacks=risk_filter_lookbacks,
        weighting_modes=weighting_modes,
        selection_metric=args.selection_metric,
        rebalance_days=args.rebalance_days,
        rebalance_days_values=rebalance_days_values,
        fee_bps=args.fee_bps,
        defensive_symbol=args.defensive_symbol,
        defensive_symbols=defensive_symbols,
        max_risk_weight=args.max_risk_weight,
        max_risk_weights=max_risk_weights,
        drawdown_guard=args.drawdown_guard,
        drawdown_guards=drawdown_guards,
        defensive_only=args.defensive_only,
    )
    if not report.rows:
        print("No walk-forward rows available. Use a longer window or smaller lookbacks.")
        return 1
    return _emit(format_walk_forward_report(report), args.output)


def _run_universe_audit(args: argparse.Namespace) -> int:
    start = _parse_date(args.start)
    end = _parse_date(args.end)
    catalog = MarketDataCatalog(args.catalog_db)
    pit_members = _load_pit_universe(catalog, args, market=args.market)
    symbols = [] if _parse_symbols(args.symbols) == ["ALL"] else _parse_symbols(args.symbols)
    _load_delisting_returns(catalog, args, symbols or _symbols_for_request("ALL", pit_members), args.market, start, end)
    if not getattr(args, "pit_universe", None):
        raise ValueError("--pit-universe or --universe-csv is required for universe-audit")
    report = run_universe_audit(
        catalog,
        universe=args.pit_universe,
        market=args.market,
        start=start,
        end=end,
        symbols=symbols or None,
        require_fundamentals=args.require_fundamentals,
        require_delistings=not args.no_require_delistings,
        rebalance_days=args.rebalance_days,
    )
    result = _emit(format_universe_audit_report(report), args.output)
    if result != 0:
        return result
    if args.strict and not report.ready:
        return 2
    return 0


def _run_pair(args: argparse.Namespace) -> int:
    start = _parse_date(args.start)
    end = _parse_date(args.end)
    catalog = MarketDataCatalog(args.catalog_db)
    bars_by_symbol = _load_or_fetch_universe(
        catalog=catalog,
        symbols=[args.first, args.second],
        market=args.market,
        start=start,
        end=end,
        fetch_missing=not args.no_fetch,
        provider=args.provider,
    )
    first_symbol = _catalog_symbol(args.first, args.market)
    second_symbol = _catalog_symbol(args.second, args.market)
    first_bars = bars_by_symbol.get(first_symbol)
    second_bars = bars_by_symbol.get(second_symbol)
    if not first_bars or not second_bars:
        print("No enough bars available for pair analysis.")
        return 1

    analysis = analyze_pair(
        first_bars,
        second_bars,
        lookback=args.lookback,
        entry_z=args.entry_z,
        exit_z=args.exit_z,
        min_observations=min(args.min_observations, args.lookback),
    )
    if analysis is None:
        print("No usable pair spread. Check overlap, positive prices, and observation count.")
        return 1
    validation = None
    if args.validate:
        validation = backtest_pair_mean_reversion(
            first_bars,
            second_bars,
            lookback=args.lookback,
            entry_z=args.entry_z,
            exit_z=args.exit_z,
            min_observations=max(args.min_observations, args.lookback + 2),
            fee_bps=args.fee_bps,
            slippage_bps=args.slippage_bps,
            min_trades=args.min_trades,
            min_sharpe=args.min_sharpe,
            max_drawdown_limit=args.max_drawdown,
        )
    short_gate = _pair_shortability_gate(analysis, args, end, args.market)
    report = _format_pair_analysis(analysis, validation, short_gate)
    emit_result = _emit(report, args.output)
    if args.require_shortability and short_gate is not None and not short_gate.passed:
        return 1
    return emit_result


def _run_vix_calc(args: argparse.Namespace) -> int:
    asof_date = _parse_date(args.as_of)
    catalog = MarketDataCatalog(args.catalog_db)
    risk_free_rate, risk_free_source, risk_free_warnings = _resolve_vix_risk_free_rate(
        catalog,
        args,
        asof_date,
    )
    try:
        quotes, quote_source = _load_vix_quotes(args, asof_date)
        result = calculate_vix_like_index(
            quotes,
            asof_date=asof_date,
            target_days=args.target_days,
            risk_free_rate=risk_free_rate,
            max_quote_age_days=args.max_option_quote_age_days,
            require_last_trade=args.require_last_trade or args.source == "yahoo",
            max_bid_ask_spread_pct=args.max_bid_ask_spread_pct,
        )
    except (ValueError, YahooOptionChainError) as exc:
        print(f"VIX calculation failed: {exc}")
        return 1
    warnings = (*result.warnings, *risk_free_warnings)
    stored_market = None
    if args.strict_quality and warnings:
        print(
            _format_vix_calculation(
                result,
                quote_source=quote_source,
                risk_free_source=risk_free_source,
                extra_warnings=risk_free_warnings,
            )
        )
        print("VIX calculation blocked by --strict-quality warnings.")
        return 1
    if args.store:
        catalog.put_option_sentiment(
            [
                OptionSentimentRecord(
                    date=asof_date,
                    market=args.market,
                    vix=result.volatility * 100,
                    source=f"{result.source};quotes={quote_source};rf={risk_free_source}",
                )
            ]
        )
        stored_market = args.market.upper()
    return _emit(
        _format_vix_calculation(
            result,
            quote_source=quote_source,
            risk_free_source=risk_free_source,
            extra_warnings=risk_free_warnings,
            stored_market=stored_market,
        ),
        args.output,
    )


def _run_macro(args: argparse.Namespace) -> int:
    start = _parse_date(args.start)
    end = _parse_date(args.end)
    catalog = MarketDataCatalog(args.catalog_db)
    for series_id in _parse_symbols(args.series):
        if args.provider == "manual":
            if args.value is None:
                raise ValueError("--value is required for manual macro observations")
            observation_date = _parse_date(args.date or args.end)
            rows = [
                MacroObservation(
                    series_id=series_id,
                    country=args.country,
                    category=args.category,
                    asof_date=observation_date,
                    release_ts=datetime.combine(observation_date, datetime.min.time()).replace(hour=18),
                    value=args.value,
                    source="manual",
                )
            ]
        else:
            rows = fetch_fred_series(
                series_id,
                start,
                end,
                country=args.country,
                category=args.category,
            )
            catalog.delete_macro_range(series_id, start, end)
            if series_id.upper() == "VIXCLS":
                catalog.delete_option_sentiment_range("US", start, end)
        stored = catalog.put_macro(rows)
        if series_id.upper() == "VIXCLS":
            catalog.put_option_sentiment(vix_from_macro(rows))
        print(
            f"Stored {stored} macro observations for {series_id.upper()} "
            f"from {rows[0].asof_date} to {rows[-1].asof_date}; "
            f"sources={_source_summary(rows)}"
        )
    return 0


def _run_fundamentals(args: argparse.Namespace) -> int:
    catalog = MarketDataCatalog(args.catalog_db)
    symbol = _catalog_symbol(args.symbol, args.market)
    now = datetime.now(tz=UTC).replace(tzinfo=None)
    if args.provider == "csv":
        if args.file is None:
            raise ValueError("--file is required for --provider csv")
        records = load_fundamentals_csv(args.file)
    elif args.provider == "manual":
        record = FundamentalRecord(
            symbol=symbol,
            market=args.market,
            period_end=_parse_date(args.period_end),
            asof_ts=now,
            revenue=args.revenue,
            net_income=args.net_income,
            free_cash_flow=args.free_cash_flow,
            total_equity=args.equity,
            total_debt=args.debt,
            shares_out=args.shares_out,
            eps=args.eps,
            source="manual",
        )
        records = [record]
    else:
        records = fetch_yfinance_fundamentals(symbol, args.market, asof_ts=now)
    stored = catalog.put_fundamentals(records)
    label = symbol if args.provider != "csv" else f"{len({record.symbol for record in records})} symbol(s)"
    print(f"Stored {stored} fundamentals record(s) for {label}")
    return 0


def _run_flows(args: argparse.Namespace) -> int:
    catalog = MarketDataCatalog(args.catalog_db)
    if args.provider == "manual":
        if args.net_value is None:
            raise ValueError("--net-value is required for manual flows")
        flow_date = _parse_date(args.date or args.end)
        rows = [
            FlowRecord(
                symbol=_catalog_symbol(args.symbol, args.market),
                market=args.market,
                ts=flow_date,
                investor=args.investor,
                net_value=args.net_value,
                buy_value=args.buy_value,
                sell_value=args.sell_value,
                net_volume=args.net_volume,
                release_ts=datetime.combine(flow_date, datetime.min.time()).replace(hour=18),
                value_kind=args.value_kind,
                confidence=args.confidence,
                source="manual",
            )
        ]
    elif args.provider == "csv":
        if args.file is None:
            raise ValueError("--file is required for --provider csv")
        try:
            rows = parse_krx_flow_csv(args.file, args.symbol, args.market)
        except KrxFlowCsvError as exc:
            print(f"Flow CSV import failed: {exc}")
            return 1
    elif args.provider == "naver-estimate":
        start = _parse_date(args.start)
        end = _parse_date(args.end)
        rows = fetch_naver_investor_flows(
            _catalog_symbol(args.symbol, args.market),
            args.market,
            start,
            end,
        )
    else:
        start = _parse_date(args.start)
        end = _parse_date(args.end)
        try:
            rows = fetch_krx_flows(
                args.symbol,
                args.market,
                start,
                end,
                allow_estimated=args.allow_estimated,
            )
        except KrxFlowError as exc:
            print(f"Flow ingest failed: {exc}")
            return 1
    untrusted = [row for row in rows if row.value_kind != "reported_value" or row.confidence != "high"]
    trusted = [row for row in rows if row not in untrusted]
    if trusted:
        catalog.put_flows(trusted)
    if untrusted:
        catalog.put_flow_estimates(untrusted)
    stored = len(rows)
    storage_note = (
        f"{len(trusted)} reported rows, {len(untrusted)} quarantined estimate rows"
        if untrusted
        else f"{len(trusted)} reported rows"
    )
    print(
        f"Stored {stored} KRX flow rows for {_catalog_symbol(args.symbol, args.market)}; "
        f"{storage_note}; sources={_source_summary(rows)}"
    )
    if untrusted:
        print("Warning: estimated flow rows were quarantined and are excluded from default signals.")
    return 0


def _run_factor(args: argparse.Namespace) -> int:
    start = _parse_date(args.start)
    end = _parse_date(args.end)
    catalog = MarketDataCatalog(args.catalog_db)
    symbols = [_catalog_symbol(symbol, args.market) for symbol in _parse_symbols(args.symbols)]
    bars_by_symbol = {
        symbol: catalog.get_bars(symbol, market=args.market, start=start, end=end) for symbol in symbols
    }
    fundamentals = {
        symbol: fundamental_rows[0]
        for symbol in symbols
        if (
            fundamental_rows := catalog.get_fundamentals(
                symbol,
                market=args.market,
                as_of=datetime.now(tz=UTC).replace(tzinfo=None),
            )
        )
    }
    factor_rows = rank_aqr_factors(bars_by_symbol, fundamentals, lookback=args.lookback)
    if not factor_rows:
        print("No factor rows available. Store fundamentals and enough price history first.")
        return 1
    lines = [
        "# AQR Factor Rank",
        "",
        "| Rank | Symbol | Value | Momentum | Quality | Composite |",
        "|---:|---|---:|---:|---:|---:|",
    ]
    for rank, row in enumerate(factor_rows, 1):
        lines.append(
            f"| {rank} | {row.symbol} | {row.value:.4f} | {row.momentum * 100:+.2f}% | "
            f"{row.quality:.4f} | {row.composite:.2f} |"
        )
    return _emit("\n".join(lines), args.output)


def _run_valuate(args: argparse.Namespace) -> int:
    catalog = MarketDataCatalog(args.catalog_db)
    symbol = _catalog_symbol(args.symbol, args.market)
    latest_price = _latest_close(catalog, symbol, args.market)
    fair_dcf = args.fair_dcf
    fair_multiple = args.fair_multiple
    fair_rim = args.fair_rim
    if args.fair_value is not None:
        fair = args.fair_value
        dispersion = 0.0
    else:
        if fair_dcf is None:
            fundamentals = catalog.get_fundamentals(symbol, market=args.market)
            if fundamentals:
                item = fundamentals[0]
                if item.free_cash_flow and item.shares_out:
                    fair_dcf = discounted_cash_flow(
                        free_cash_flow=item.free_cash_flow,
                        shares_out=item.shares_out,
                        net_debt=item.total_debt or 0.0,
                        growth=args.growth,
                        wacc=args.wacc,
                        terminal_growth=args.terminal_growth,
                    ).fair_value
        fair, dispersion = composite_fair_value(
            {"dcf": fair_dcf, "multiple": fair_multiple, "rim": fair_rim}
        )
    disc = discount_pct(latest_price, fair)
    record = ValuationRecord(
        symbol=symbol,
        market=args.market,
        asof_date=date.today(),
        current_price=latest_price,
        fair_value=fair,
        fair_dcf=fair_dcf,
        fair_multiple=fair_multiple,
        fair_rim=fair_rim,
        dispersion_pct=dispersion,
        discount_pct=disc,
        rating=rating_from_discount(disc),
        confidence=confidence_from_dispersion(dispersion),
        source="trader:valuation",
    )
    catalog.put_valuations([record])
    print(_format_valuation(record))
    return 0


def _run_entry(args: argparse.Namespace) -> int:
    catalog = MarketDataCatalog(args.catalog_db)
    symbol = _catalog_symbol(args.symbol, args.market)
    valuations = catalog.get_valuations(symbol=symbol, market=args.market, limit=1)
    if not valuations:
        print("No valuation found. Run `uv run trader valuate SYMBOL --fair-value ...` first.")
        return 1
    bars = catalog.get_bars(symbol, market=args.market)
    if len(bars) < 2:
        print("No enough bars for ATR entry plan.")
        return 1
    highs = [bar.high for bar in bars]
    lows = [bar.low for bar in bars]
    closes = [bar.close for bar in bars]
    plan = make_entry_plan(
        symbol=symbol,
        market=args.market,
        current_price=valuations[0].current_price,
        fair_value=valuations[0].fair_value,
        atr_pct=average_true_range_pct(highs, lows, closes),
        asof_ts=datetime.now(tz=UTC).replace(tzinfo=None),
        margin_of_safety=args.margin_of_safety,
    )
    catalog.put_entry_plans([plan])
    print(f"Entry plan for {symbol}: target_entry={plan.target_entry:,.2f}, stop={plan.stop_loss:,.2f}, target={plan.target_exit:,.2f}, R/R={plan.risk_reward:.2f}")
    print(plan.ladder_json)
    return 0


def _run_paper(args: argparse.Namespace) -> int:
    broker = PaperBroker(initial_cash=args.cash)
    order = broker.submit_market_order(
        strategy=args.strategy,
        symbol=_catalog_symbol(args.symbol, args.market),
        market=args.market,
        side=args.side,
        qty=args.qty,
        price=args.price,
        ts=datetime.now(tz=UTC).replace(tzinfo=None),
    )
    marks = {order.symbol: order.price}
    print(f"Filled {order.side} {order.qty:g} {order.symbol} @ {order.price:,.2f}")
    print(f"Cash: {broker.cash:,.2f}")
    print(f"Equity: {broker.equity(marks):,.2f}")
    print(f"Gross exposure: {broker.gross_exposure(marks):.2f}")
    return 0


def _run_risk_check(args: argparse.Namespace) -> int:
    result = check_kill_switch(
        start_equity=args.start_equity,
        current_equity=args.current_equity,
        gross_exposure=args.gross_exposure,
        max_daily_drawdown=args.max_daily_drawdown,
        max_gross_exposure=args.max_gross_exposure,
    )
    if result.halted:
        print("HALT")
        for reason in result.reasons:
            print(f"- {reason}")
        return 2
    print("OK")
    return 0


def _run_quality(args: argparse.Namespace) -> int:
    catalog = MarketDataCatalog(args.catalog_db)
    required_flows: tuple[tuple[str, str], ...] = ()
    if args.require_flow:
        required_flows = tuple(
            (_catalog_symbol(symbol, args.flow_market), args.flow_market)
            for symbol in _parse_symbols(args.require_flow)
        )
    issues = evaluate_catalog_quality(
        catalog,
        as_of=_parse_date(args.as_of),
        required_flows=required_flows,
    )
    print(format_quality_report(issues))
    if any(issue.severity == "error" for issue in issues):
        return 1
    if args.strict and any(issue.severity == "warn" for issue in issues):
        return 2
    return 0


def _run_dashboard(args: argparse.Namespace) -> int:
    command = f"uv run streamlit run dashboard/app.py --server.port {args.port}"
    print(command)
    print(f"Dashboard URL: http://localhost:{args.port}")
    return 0


def _load_or_fetch_universe(
    *,
    catalog: MarketDataCatalog,
    symbols: list[str],
    market: str,
    start: date,
    end: date,
    fetch_missing: bool,
    provider: str,
) -> dict[str, list]:
    catalog_symbols = {symbol: _catalog_symbol(symbol, market) for symbol in symbols}
    bars_by_symbol = {
        catalog_symbol: catalog.get_bars(catalog_symbol, market=market, start=start, end=end)
        for catalog_symbol in catalog_symbols.values()
    }
    if fetch_missing:
        for requested_symbol, catalog_symbol in catalog_symbols.items():
            bars = bars_by_symbol[catalog_symbol]
            if not _bars_need_fetch(bars, market, provider, start, end):
                continue
            fetched = _fetch_bars(requested_symbol, market, start, end, provider=provider)
            catalog.put_bars(fetched)
            bars_by_symbol[catalog_symbol] = catalog.get_bars(
                catalog_symbol, market=market, start=start, end=end
            )
    return {symbol: bars for symbol, bars in bars_by_symbol.items() if bars}


def _load_pit_universe(
    catalog: MarketDataCatalog,
    args: argparse.Namespace,
    *,
    market: str,
) -> list:
    if getattr(args, "universe_csv", None):
        records = load_universe_members_csv(args.universe_csv)
        catalog.put_universe_members(records)
        if not getattr(args, "pit_universe", None):
            universes = sorted({record.universe for record in records})
            if len(universes) == 1:
                args.pit_universe = universes[0]
            else:
                raise ValueError("--pit-universe is required when CSV contains multiple universes")
    if not getattr(args, "pit_universe", None):
        return []
    members = catalog.get_universe_members(args.pit_universe, market=market)
    if not members:
        raise ValueError(f"No PIT universe members found for {args.pit_universe} ({market}).")
    return members


def _symbols_for_request(symbols_arg: str, pit_members: list) -> list[str]:
    requested = _parse_symbols(symbols_arg)
    if pit_members and requested == ["ALL"]:
        return sorted({member.symbol for member in pit_members})
    if pit_members:
        allowed = set(requested)
        return sorted({member.symbol for member in pit_members if member.symbol in allowed})
    return requested


def _filter_pit_members(
    pit_members: list[UniverseMember],
    symbols: list[str],
) -> list[UniverseMember]:
    if not pit_members:
        return []
    allowed = {symbol.upper() for symbol in symbols}
    filtered = [member for member in pit_members if member.symbol.upper() in allowed]
    if not filtered:
        raise ValueError("requested symbols are not present in the PIT universe")
    return filtered


def _load_delisting_returns(
    catalog: MarketDataCatalog,
    args: argparse.Namespace,
    symbols: list[str],
    market: str,
    start: date,
    end: date,
) -> list[DelistingReturn]:
    if getattr(args, "delisting_returns_csv", None):
        records = load_delisting_returns_csv(args.delisting_returns_csv)
        catalog.put_delisting_returns(records)
    return catalog.get_delisting_returns(symbols=symbols, market=market, start=start, end=end)


def _preflight_universe_audit(
    catalog: MarketDataCatalog,
    args: argparse.Namespace,
    *,
    pit_members: list[UniverseMember],
    symbols: list[str],
    market: str,
    start: date,
    end: date,
) -> int:
    if getattr(args, "skip_universe_audit", False) or not pit_members:
        return 0
    universe = getattr(args, "pit_universe", None)
    if not universe:
        return 0
    report = run_universe_audit(
        catalog,
        universe=universe,
        market=market,
        start=start,
        end=end,
        symbols=symbols,
        require_fundamentals=getattr(args, "audit_require_fundamentals", False),
        require_delistings=not getattr(args, "audit_no_require_delistings", False),
        rebalance_days=getattr(args, "rebalance_days", 21),
    )
    if report.ready:
        print(
            f"Universe audit: OK ({report.active_symbols} symbols, "
            f"{report.error_count} errors, {report.warn_count} warnings)"
        )
        return 0
    print(format_universe_audit_report(report))
    print("Backtest blocked by universe audit errors.")
    return 2


def _fundamentals_history(
    catalog: MarketDataCatalog,
    symbols: list[str],
    market: str,
) -> dict[str, FundamentalRecord | list[FundamentalRecord]]:
    rows: dict[str, FundamentalRecord | list[FundamentalRecord]] = {}
    for symbol in symbols:
        records = catalog.get_fundamentals(symbol, market=market, limit=0)
        if records:
            rows[symbol.upper()] = records
    return rows


def _load_or_fetch_benchmark(
    *,
    catalog: MarketDataCatalog,
    symbol: str | None,
    market: str,
    start: date,
    end: date,
    fetch_missing: bool,
    provider: str,
) -> list | None:
    if symbol is None:
        return None
    catalog_symbol = _catalog_symbol(symbol, market)
    bars = catalog.get_bars(catalog_symbol, market=market, start=start, end=end)
    if _bars_need_fetch(bars, market, provider, start, end) and fetch_missing:
        fetched = _fetch_bars(symbol, market, start, end, provider=provider)
        catalog.put_bars(fetched)
        bars = catalog.get_bars(catalog_symbol, market=market, start=start, end=end)
    if not bars:
        raise ValueError(f"No benchmark bars available for {catalog_symbol} ({market}).")
    return bars


def _bars_need_fetch(
    bars: list,
    market: str,
    provider: str,
    start: date,
    end: date,
) -> bool:
    if not bars:
        return True
    return bars[0].ts > start or bars[-1].ts < end or _bars_need_refresh(bars, market, provider)


def _bars_need_refresh(bars: list, market: str, provider: str) -> bool:
    if not bars:
        return False
    if market.lower() != "us" or provider.lower() not in {"auto", "yahoo"}:
        return False
    return any(YAHOO_ADJUSTED_SOURCE_MARKER not in getattr(bar, "source", "") for bar in bars)


def _benchmark_provider(provider: str, market: str, benchmark_market: str | None) -> str:
    if provider == "auto" or benchmark_market is None or benchmark_market == market:
        return provider
    return "auto"


def _latest_close(catalog: MarketDataCatalog, symbol: str, market: str) -> float:
    bars = catalog.get_bars(symbol, market=market)
    if not bars:
        raise ValueError(f"{symbol}: no bars found. Ingest price data first.")
    return bars[-1].close


def _format_valuation(record: ValuationRecord) -> str:
    return "\n".join(
        [
            f"# Valuation - {record.symbol}",
            "",
            "| Metric | Value |",
            "|---|---:|",
            f"| Market | {record.market} |",
            f"| Current Price | {record.current_price:,.2f} |",
            f"| Fair Value | {record.fair_value:,.2f} |",
            f"| Discount | {record.discount_pct * 100:+.2f}% |",
            f"| Rating | {record.rating:+d} |",
            f"| Confidence | {record.confidence} |",
        ]
    )


def _format_pair_analysis(
    analysis: PairAnalysis,
    validation: PairBacktestResult | None = None,
    short_gate: ShortabilityCheck | None = None,
) -> str:
    half_life = (
        "n/a" if analysis.half_life_days is None else f"{analysis.half_life_days:.1f} days"
    )
    if analysis.signal is None:
        signal = "No entry signal"
    else:
        signal = f"Long {analysis.signal.long_symbol} / Short {analysis.signal.short_symbol}"
    lines = [
        f"# Pair Analysis - {analysis.first_symbol}/{analysis.second_symbol}",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Observations | {analysis.observations} |",
        f"| Hedge Ratio | {analysis.hedge_ratio:.4f} |",
        f"| Intercept | {analysis.intercept:.4f} |",
        f"| Spread Z-score | {analysis.z_score:+.2f} |",
        f"| Correlation | {analysis.correlation:.3f} |",
        f"| Half-life | {half_life} |",
        f"| State | {analysis.state} |",
        f"| Signal | {signal} |",
    ]
    if validation is not None:
        verdict = "PASS" if validation.passed else "FAIL"
        reasons = "OK" if validation.passed else "; ".join(validation.reasons)
        lines.extend(
            [
                "",
                "## Cost-adjusted Rolling Validation",
                "",
                "| Metric | Value |",
                "|---|---:|",
                f"| Verdict | {verdict} |",
                f"| Reasons | {reasons} |",
                f"| OOS Observations | {validation.observations} |",
                f"| Trades | {validation.trades} |",
                f"| Gross Return | {validation.gross_return * 100:+.2f}% |",
                f"| Net Return | {validation.net_return * 100:+.2f}% |",
                f"| Annualized Return | {validation.annualized_return * 100:+.2f}% |",
                f"| Sharpe | {validation.sharpe:.2f} |",
                f"| Max Drawdown | {validation.max_drawdown * 100:.2f}% |",
                f"| Hit Rate | {validation.hit_rate * 100:.2f}% |",
                f"| Fee + Slippage | {validation.fee_bps:.2f} + {validation.slippage_bps:.2f} bps |",
            ]
        )
    if short_gate is not None:
        verdict = "PASS" if short_gate.passed else "FAIL"
        reasons = "OK" if short_gate.passed else "; ".join(short_gate.reasons)
        warnings = "OK" if not short_gate.warnings else "; ".join(short_gate.warnings)
        borrow_fee = (
            "n/a" if short_gate.borrow_fee_bps is None else f"{short_gate.borrow_fee_bps:.2f} bps"
        )
        lines.extend(
            [
                "",
                "## Shortability Gate",
                "",
                "| Metric | Value |",
                "|---|---:|",
                f"| Verdict | {verdict} |",
                f"| Short Symbol | {short_gate.symbol} |",
                f"| Market | {short_gate.market} |",
                f"| As Of | {short_gate.asof_date} |",
                f"| Borrow Fee | {borrow_fee} |",
                f"| Source | {short_gate.source} |",
                f"| Confidence | {short_gate.confidence} |",
                f"| Reasons | {reasons} |",
                f"| Warnings | {warnings} |",
            ]
        )
    return "\n".join(lines)


def _format_vix_calculation(
    result: VixCalculationResult,
    *,
    quote_source: str,
    risk_free_source: str,
    extra_warnings: tuple[str, ...] = (),
    stored_market: str | None = None,
) -> str:
    warnings = (*result.warnings, *extra_warnings)
    lines = [
        "# VIX-like Option Volatility",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| As of | {result.asof_date} |",
        f"| Target Days | {result.target_days} |",
        f"| Volatility | {result.volatility * 100:.2f}% |",
        f"| VIX Points | {result.volatility * 100:.2f} |",
        f"| Risk-free Rate | {result.risk_free_rate * 100:.3f}% |",
        f"| Risk-free Source | {risk_free_source} |",
        f"| Quote Source | {quote_source} |",
        f"| Near Expiration | {result.near.expiration} ({result.near.days_to_expiration}d) |",
        f"| Near Forward | {result.near.forward:.2f} |",
        f"| Near K0 | {result.near.reference_strike:.2f} |",
        f"| Next Expiration | {result.next.expiration} ({result.next.days_to_expiration}d) |",
        f"| Next Forward | {result.next.forward:.2f} |",
        f"| Next K0 | {result.next.reference_strike:.2f} |",
        f"| Source | {result.source} |",
    ]
    if stored_market is not None:
        lines.append(f"| Stored | option_sentiment/{stored_market} |")
    if warnings:
        lines.extend(["", "## Quality Warnings", ""])
        lines.extend(f"- {warning}" for warning in warnings)
    return "\n".join(lines)


def _pair_shortability_gate(
    analysis: PairAnalysis,
    args: argparse.Namespace,
    asof_date: date,
    market: str,
) -> ShortabilityCheck | None:
    if analysis.signal is None:
        return None
    if not args.shortability_csv and not args.require_shortability:
        return None
    rows = load_short_availability_csv(args.shortability_csv) if args.shortability_csv else []
    return check_shortability(
        analysis.signal.short_symbol,
        market,
        rows,
        asof_date=asof_date,
        max_borrow_fee_bps=args.max_borrow_fee_bps,
        require_row=args.require_shortability,
        max_age_days=args.shortability_max_age_days,
        min_confidence=args.min_shortability_confidence,
    )


def _load_vix_quotes(args: argparse.Namespace, asof_date: date) -> tuple[list, str]:
    if args.source == "csv":
        if args.file is None:
            raise ValueError("--file is required when --source csv")
        return load_option_quotes_csv(args.file), f"csv:{args.file.name}"
    if args.underlying is None:
        raise ValueError("--underlying is required when --source yahoo")
    expirations = tuple(_parse_date(item.strip()) for item in args.expirations.split(",")) if args.expirations else ()
    fetched = fetch_yahoo_option_quotes(
        args.underlying,
        asof_date=asof_date,
        target_days=args.target_days,
        expirations=expirations,
    )
    return fetched.quotes, fetched.source


def _resolve_vix_risk_free_rate(
    catalog: MarketDataCatalog,
    args: argparse.Namespace,
    asof_date: date,
) -> tuple[float, str, tuple[str, ...]]:
    if args.risk_free_rate is not None:
        return args.risk_free_rate, "manual:--risk-free-rate", ()
    series = (args.risk_free_series or "").strip().upper()
    if not series:
        return 0.0, "zero", ("risk-free series disabled; using 0.0",)

    asof_ts = datetime.combine(asof_date, datetime.max.time())
    rows = catalog.get_macro(series, as_of=asof_ts, limit=1)
    if not rows:
        return 0.0, "zero", (f"{series} not found in catalog; using 0.0 risk-free rate",)

    latest = rows[0]
    warnings: list[str] = []
    age_days = (asof_date - latest.asof_date).days
    if age_days > args.risk_free_max_age_days:
        warnings.append(
            f"{series} is {age_days} days old; max age is {args.risk_free_max_age_days}"
        )
    raw_rate = latest.value
    annualized_rate = raw_rate / 100.0 if abs(raw_rate) > 1 else raw_rate
    return annualized_rate, f"catalog:{series}@{latest.asof_date}", tuple(warnings)


def _source_summary(records: list) -> str:
    sources = sorted({getattr(record, "source", "") or "unknown" for record in records})
    return ",".join(sources)


def _emit(text: str, output: Path | None) -> int:
    if output is None:
        print(text)
        return 0
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text + "\n", encoding="utf-8")
    print(f"Wrote {output}")
    return 0


def _fetch_bars(symbol: str, market: str, start: date, end: date, provider: str) -> list:
    market_key = market.lower()
    provider_key = provider.lower()
    if provider_key == "auto":
        if market_key == "crypto":
            provider_key = "ccxt"
        elif market_key in {"kospi", "kosdaq"}:
            provider_key = "pykrx"
        else:
            provider_key = "yahoo"
    if provider_key == "ccxt":
        if market_key != "crypto":
            raise ValueError("ccxt provider is only supported for --market crypto")
        return fetch_ccxt_bars(symbol, start, end)
    if provider_key == "pykrx":
        return fetch_pykrx_bars(symbol, market, start, end)
    if provider_key == "yahoo":
        return fetch_yahoo_bars(symbol, market, start, end)
    raise ValueError(f"unknown provider: {provider}")


def _catalog_symbol(symbol: str, market: str) -> str:
    market_key = market.lower()
    if market_key in {"kospi", "kosdaq"}:
        return normalize_kr_symbol(symbol)
    if market_key == "crypto":
        return normalize_crypto_symbol(symbol)
    return symbol.upper()


def _run_copilot(argv: list[str]) -> int:
    forwarded = _with_workspace_defaults(argv)
    if str(COPILOT_DIR) not in sys.path:
        sys.path.insert(0, str(COPILOT_DIR))

    from trading_copilot.cli import main as copilot_main

    return copilot_main(forwarded)


def _with_workspace_defaults(argv: list[str]) -> list[str]:
    defaults: list[str] = []
    if "--financial-services-dir" not in argv:
        defaults.extend(["--financial-services-dir", str(DEFAULT_FINANCIAL_SERVICES_DIR)])
    if "--db" not in argv:
        defaults.extend(["--db", str(DEFAULT_DB)])
    return [*defaults, *argv]


def _add_market_symbol_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("symbol")
    parser.add_argument("--market", default="us", choices=["us", "kospi", "kosdaq", "crypto"])


def _add_market_symbols_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("symbols", help="Ticker or comma-separated tickers, e.g. MSFT,AAPL,NVDA")
    parser.add_argument("--market", default="us", choices=["us", "kospi", "kosdaq", "crypto"])


def _add_provider_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--provider",
        default="auto",
        choices=["auto", "yahoo", "ccxt", "pykrx"],
        help="Data provider. auto uses CCXT for crypto, pykrx for Korean equities, and Yahoo otherwise.",
    )


def _add_benchmark_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--benchmark",
        help="Optional benchmark symbol for excess-return comparison, e.g. SPY.",
    )
    parser.add_argument(
        "--benchmark-market",
        choices=["us", "kospi", "kosdaq", "crypto"],
        help="Benchmark market. Defaults to the strategy market.",
    )


def _add_pit_universe_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--pit-universe",
        help="Point-in-time universe name stored in the catalog.",
    )
    parser.add_argument(
        "--universe-csv",
        type=Path,
        help="CSV with universe,symbol,market,start_date,end_date,source,confidence columns.",
    )


def _add_delisting_return_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--delisting-returns-csv",
        type=Path,
        help="CSV with symbol,market,ts,return_pct,source,confidence columns.",
    )


def _add_preflight_audit_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--skip-universe-audit",
        action="store_true",
        help="Skip the automatic PIT universe audit preflight.",
    )
    parser.add_argument(
        "--audit-require-fundamentals",
        action="store_true",
        help="Require PIT fundamentals during the automatic universe audit.",
    )
    parser.add_argument(
        "--audit-no-require-delistings",
        action="store_true",
        help="Do not require explicit delisting returns for ended PIT members.",
    )


def _add_factor_portfolio_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--momentum-lookback", type=int, default=252)
    parser.add_argument("--reversal-lookback", type=int, default=21)
    parser.add_argument("--volatility-lookback", type=int, default=63)
    parser.add_argument("--risk-filter-lookback", type=int, default=200)
    parser.add_argument("--top-n", type=int, default=3)
    parser.add_argument("--rebalance-days", type=int, default=21)
    parser.add_argument("--initial-cash", type=float, default=10_000.0)
    parser.add_argument("--fee-bps", type=float, default=2.0)
    parser.add_argument(
        "--defensive-symbol",
        default="TLT",
        help="Symbol to hold when risk filter is off. Set to empty string for cash.",
    )
    parser.add_argument(
        "--weighting",
        default="inverse-vol",
        choices=["inverse-vol", "equal"],
        help="Position sizing for selected assets.",
    )
    parser.add_argument(
        "--max-risk-weight",
        type=float,
        default=1.0,
        help="Cap each non-defensive asset weight and put overflow into the defensive asset or cash.",
    )
    parser.add_argument(
        "--drawdown-guard",
        type=float,
        default=0.0,
        help="Switch to the defensive asset when strategy drawdown reaches this decimal threshold.",
    )
    parser.add_argument(
        "--defensive-only",
        action="store_true",
        help="Exclude the defensive symbol from normal factor ranking and use it only for risk-off/capped overflow.",
    )


def _add_date_args(parser: argparse.ArgumentParser, required: bool = False) -> None:
    default_start = (date.today() - timedelta(days=365 * 3)).isoformat()
    parser.add_argument("--start", default=None if required else default_start, required=required)
    parser.add_argument("--end", default=date.today().isoformat())


def _factor_warmup_start(start: date, *lookbacks: int) -> date:
    max_lookback = max(lookbacks)
    calendar_days = max_lookback * 365 // 252 + 45
    return start - timedelta(days=calendar_days)


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def _parse_optional_date(value: str | None) -> date | None:
    if value is None:
        return None
    return _parse_date(value)


def _parse_symbols(value: str) -> list[str]:
    symbols = [item.strip().upper() for item in value.split(",") if item.strip()]
    if not symbols:
        raise ValueError("at least one symbol is required")
    return symbols


def _parse_ints(value: str) -> list[int]:
    items = [int(item.strip()) for item in value.split(",") if item.strip()]
    if not items:
        raise ValueError("at least one integer is required")
    if any(item < 1 for item in items):
        raise ValueError("integer values must be >= 1")
    return items


def _parse_nonnegative_ints(value: str) -> list[int]:
    items = [int(item.strip()) for item in value.split(",") if item.strip()]
    if not items:
        raise ValueError("at least one integer is required")
    if any(item < 0 for item in items):
        raise ValueError("integer values must be >= 0")
    return items


def _parse_floats(
    value: str,
    *,
    min_value: float | None = None,
    max_value: float | None = None,
    min_exclusive: bool = False,
    max_exclusive: bool = False,
) -> list[float]:
    items = [float(item.strip()) for item in value.split(",") if item.strip()]
    if not items:
        raise ValueError("at least one float is required")
    for item in items:
        if min_value is not None:
            below_min = item <= min_value if min_exclusive else item < min_value
            if below_min:
                raise ValueError(f"float values must be {'>' if min_exclusive else '>='} {min_value}")
        if max_value is not None:
            above_max = item >= max_value if max_exclusive else item > max_value
            if above_max:
                raise ValueError(f"float values must be {'<' if max_exclusive else '<='} {max_value}")
    return items


def _parse_weighting_modes(value: str) -> list[str]:
    modes = [item.strip().lower() for item in value.split(",") if item.strip()]
    if not modes:
        raise ValueError("at least one weighting mode is required")
    invalid = [item for item in modes if item not in {"inverse-vol", "equal"}]
    if invalid:
        raise ValueError(f"unknown weighting mode(s): {', '.join(invalid)}")
    return modes


def _parse_defensive_symbols(value: str) -> list[str | None]:
    symbols = [item.strip().upper() for item in value.split(",") if item.strip()]
    if not symbols:
        raise ValueError("at least one defensive symbol is required")
    return [None if symbol in {"CASH", "NONE"} else symbol for symbol in symbols]
