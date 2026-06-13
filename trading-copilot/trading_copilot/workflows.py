from __future__ import annotations

from datetime import date

from . import forecast_ledger, rate_forecast
from .backtest import (
    DEFAULT_HOLDING_DAYS,
    backtest_to_csv,
    format_backtest_report,
    run_backtest,
)
from .earnings_calendar import (
    EarningsCalendarProvider,
    HybridEarningsCalendarProvider,
    format_earnings_calendar_report,
)
from .economic_calendar import (
    EconomicCalendarProvider,
    collect_economic_events,
    format_economic_calendar_report,
)
from .events import (
    EventProvider,
    NewsRssProvider,
    SecEdgarProvider,
    format_events_report,
    format_news_report,
)
from .fundamentals import (
    FundamentalsProvider,
    HybridFundamentalsProvider,
    format_fundamentals_report,
)
from .industry_rotation import (
    PriceHistoryProvider,
    YahooHistoryProvider,
    analyze_industries,
    format_industry_report,
    industry_scores_to_csv,
)
from .macro import (
    FredCsvProvider,
    MacroDataProvider,
    build_macro_dashboard,
    format_macro_report,
)
from .macro_forecast import (
    KR_SPECS,
    US_SPECS,
    forecast_dashboard,
    format_forecast_report,
)
from .market_data import (
    MarketDataProvider,
    YahooChartProvider,
    format_quote_report,
    format_snapshot_line,
)
from .metrics import build_technical_profile
from .ml_recommendations import (
    build_ml_recommendation,
    format_ml_recommendation_report,
)
from .news_monitor import (
    EventProviderNewsAdapter,
    FastNewsProvider,
    MarketauxNewsProvider,
    build_fast_news_report,
    collect_fast_news,
)
from .pattern_mining import (
    DEFAULT_ASSETS,
    DEFAULT_HORIZONS,
    format_pattern_report,
    mine_default_patterns,
    pattern_results_to_csv,
)
from .playbook import PlaybookBuilder, format_playbook_report
from .portfolio import (
    DEFAULT_SINGLE_STOCK_POOL,
    build_aggressive_portfolio,
    format_portfolio_report,
    portfolio_to_csv,
)
from .quote_summary import QuoteSummaryProvider, YahooQuoteSummaryProvider, map_to_sector_etf
from .recommendations import build_recommendation_report, score_recommendation
from .regime import build_regime_report, format_regime_report
from .screening import candidates_to_csv, format_screen_report, screen_members
from .signals import detect_forecast_signals, format_signals_report
from .skill_registry import SkillRegistry
from .storage import TradingStore, normalize_ticker, tickers_from_items
from .universe import CompositeUniverseProvider, UniverseProvider


class TradingWorkflows:
    def __init__(
        self,
        skills: SkillRegistry,
        store: TradingStore,
        market_data: MarketDataProvider | None = None,
        events: EventProvider | None = None,
        news: EventProvider | None = None,
        universe: UniverseProvider | None = None,
        macro: MacroDataProvider | None = None,
        industry_history: PriceHistoryProvider | None = None,
        fast_news_providers: tuple[FastNewsProvider, ...] | None = None,
        earnings_calendar: EarningsCalendarProvider | None = None,
        fundamentals: FundamentalsProvider | None = None,
        economic_calendar_providers: tuple[EconomicCalendarProvider, ...] | None = None,
        quote_summary: QuoteSummaryProvider | None = None,
    ):
        self.skills = skills
        self.store = store
        self.market_data = market_data or YahooChartProvider()
        self.events = events or SecEdgarProvider()
        self.news = news or NewsRssProvider()
        self.universe = universe or CompositeUniverseProvider()
        self.macro = macro or FredCsvProvider()
        self.industry_history = industry_history or YahooHistoryProvider()
        self.fast_news_providers = fast_news_providers
        self.earnings_calendar = earnings_calendar or HybridEarningsCalendarProvider()
        self.fundamentals = fundamentals or HybridFundamentalsProvider()
        self.economic_calendar_providers = economic_calendar_providers
        self.quote_summary = quote_summary or YahooQuoteSummaryProvider()

    def regime_report(self) -> str:
        report = build_regime_report(
            macro_provider=self.macro,
            history_provider=self.industry_history,
        )
        return format_regime_report(report)

    def backtest_regime_report(
        self,
        start: date,
        end: date,
        *,
        holding_days: int = DEFAULT_HOLDING_DAYS,
        long_history: bool = False,
    ) -> str:
        result = run_backtest(
            start=start,
            end=end,
            history_provider=None if long_history else self.industry_history,
            macro_provider=self.macro,
            holding_days=holding_days,
            long_history=long_history,
        )
        return format_backtest_report(result)

    def backtest_regime_csv(
        self,
        start: date,
        end: date,
        *,
        holding_days: int = DEFAULT_HOLDING_DAYS,
        long_history: bool = False,
    ) -> str:
        result = run_backtest(
            start=start,
            end=end,
            history_provider=None if long_history else self.industry_history,
            macro_provider=self.macro,
            holding_days=holding_days,
            long_history=long_history,
        )
        return backtest_to_csv(result)

    def playbook_report(
        self,
        ticker: str,
        *,
        aum: float | None = None,
        target_vol: float = 0.35,
        kelly_multiplier: float = 0.5,
        max_position_pct: float = 0.25,
        max_risk_pct_of_aum: float = 0.02,
        win_probability: float = 0.55,
        upside_pct: float = 0.5,
        downside_pct: float = 0.2,
        sector_industry_hint: str | None = None,
        include_fundamentals: bool = True,
    ) -> str:
        builder = PlaybookBuilder(
            market_data=self.market_data,
            history_provider=self.industry_history,
            macro_provider=self.macro,
            fundamentals_provider=self.fundamentals if include_fundamentals else None,
            quote_summary_provider=self.quote_summary,
        )
        playbook = builder.build(
            ticker,
            aum=aum,
            target_vol=target_vol,
            kelly_multiplier=kelly_multiplier,
            max_position_pct=max_position_pct,
            max_risk_pct_of_aum=max_risk_pct_of_aum,
            win_probability=win_probability,
            upside_pct=upside_pct,
            downside_pct=downside_pct,
            sector_industry_hint=sector_industry_hint,
        )
        return format_playbook_report(playbook)

    def morning_brief(self, tickers: list[str], include_market_data: bool = False) -> str:
        bundle = self.skills.bundle_for("morning")
        normalized = [normalize_ticker(ticker) for ticker in tickers]
        if not normalized:
            normalized = tickers_from_items(self.store.list_watchlist())

        ticker_lines = "\n".join(f"- {ticker}" for ticker in normalized) or "- None yet"
        sections = [
            f"# Morning Trading Research Brief - {date.today().isoformat()}",
            "",
            "Not investment advice. No trade recommendations. Use this as a review checklist.",
            "",
            "## Coverage",
            ticker_lines,
        ]
        if include_market_data:
            sections.extend(["", self._market_snapshot_section(normalized)])
        sections.extend(
            [
                "",
                "## Overnight Review",
                "- Price action to verify",
                "- News and filings to verify",
                "- Changes versus current thesis",
                "",
                "## Catalysts",
                "- Earnings dates, investor days, macro prints, regulatory events",
                "- Events that could strengthen or weaken each thesis",
                "",
                "## Required Checks",
                "- Mark every number with a source before using it",
                "- Separate facts from assumptions",
                "- Identify disconfirming evidence before considering a trade",
                "",
                "## Source Skills",
                ", ".join(bundle.names),
            ]
        )
        return "\n".join(sections)

    def quote_report(self, ticker: str) -> str:
        return format_quote_report(self.market_data.snapshot(ticker))

    def recommendation_report(
        self,
        ticker: str,
        target_price: float | None,
        stop_price: float | None,
        horizon: str,
        context: str,
        include_sec_events: bool = False,
        include_news: bool = False,
        include_signals: bool = False,
        event_limit: int = 3,
        news_limit: int = 3,
    ) -> str:
        normalized = normalize_ticker(ticker)
        if include_signals:
            include_sec_events = True
            include_news = True
        events = ()
        if include_sec_events:
            events += self._recent_sec_events(normalized, event_limit)
        if include_news:
            events += self._recent_news(normalized, news_limit)
        result = score_recommendation(
            thesis=self.store.get_thesis(normalized),
            snapshot=self.market_data.snapshot(normalized),
            target_price=target_price,
            stop_price=stop_price,
            horizon=horizon,
            context=context,
            events=events,
        )
        return build_recommendation_report(result)

    def ml_recommendation_report(
        self,
        ticker: str,
        target_price: float | None,
        stop_price: float | None,
        horizon: str,
        context: str,
        include_sec_events: bool = False,
        include_news: bool = False,
        include_signals: bool = False,
        event_limit: int = 3,
        news_limit: int = 3,
        pattern_horizon: int = 63,
        min_pattern_samples: int = 3,
        risk_budget_pct: float = 2.0,
        max_position_pct: float = 12.0,
        include_fundamentals: bool = True,
        include_patterns: bool = True,
    ) -> str:
        normalized = normalize_ticker(ticker)
        if include_signals:
            include_sec_events = True
            include_news = True

        events = ()
        if include_sec_events:
            events += self._recent_sec_events(normalized, event_limit)
        if include_news:
            events += self._recent_news(normalized, news_limit)

        data_gaps: list[str] = []
        snapshot = self.market_data.snapshot(normalized)
        history = self.industry_history.history(normalized, range_period="2y", interval="1d")
        benchmark = self.industry_history.history("SPY", range_period="2y", interval="1d")
        technical = build_technical_profile(
            tuple(point.close for point in history),
            benchmark_closes=tuple(point.close for point in benchmark),
        )
        macro_dashboard = build_macro_dashboard(self.macro, price_provider=self.industry_history)
        data_gaps.extend(macro_dashboard.data_gaps)

        fundamentals = None
        if include_fundamentals:
            try:
                fundamentals = self.fundamentals.analysis(normalized)
            except Exception as exc:
                data_gaps.append(f"fundamentals: {exc}")

        sector_score = None
        try:
            sector_symbol = map_to_sector_etf(None, normalized)
            if sector_symbol is None:
                try:
                    summary = self.quote_summary.summary(normalized)
                except Exception as exc:
                    summary = None
                    data_gaps.append(f"quoteSummary: {exc}")
                sector_symbol = map_to_sector_etf(summary, normalized)
            if sector_symbol is not None:
                rotation = analyze_industries(history_provider=self.industry_history)
                sector_score = next(
                    (score for score in rotation.scores if score.symbol == sector_symbol.upper()),
                    None,
                )
                data_gaps.extend(rotation.errors)
                if sector_score is None:
                    data_gaps.append(f"sector fit: {sector_symbol} not found in rotation scores")
            else:
                data_gaps.append("sector fit: no sector/industry proxy mapping")
        except Exception as exc:
            data_gaps.append(f"sector fit: {exc}")

        pattern_results = ()
        if include_patterns:
            try:
                pattern_report = mine_default_patterns(
                    macro_provider=self.macro,
                    history_provider=self.industry_history,
                    assets=(normalized,),
                    horizons=(pattern_horizon,),
                    min_samples=min_pattern_samples,
                )
                pattern_results = pattern_report.results
                data_gaps.extend(pattern_report.errors)
            except Exception as exc:
                data_gaps.append(f"patterns: {exc}")

        result = build_ml_recommendation(
            ticker=normalized,
            snapshot=snapshot,
            technical=technical,
            macro_dashboard=macro_dashboard,
            fundamentals=fundamentals,
            signals=detect_forecast_signals(events),
            pattern_results=pattern_results,
            sector_score=sector_score,
            target_price=target_price,
            stop_price=stop_price,
            horizon=horizon,
            context=context,
            risk_budget_pct=risk_budget_pct,
            max_position_pct=max_position_pct,
            data_gaps=tuple(data_gaps),
        )
        return format_ml_recommendation_report(result)

    def events_report(self, ticker: str, limit: int = 5) -> str:
        normalized = normalize_ticker(ticker)
        return format_events_report(normalized, self._recent_sec_events(normalized, limit))

    def news_report(self, ticker: str, limit: int = 5) -> str:
        normalized = normalize_ticker(ticker)
        return format_news_report(normalized, self._recent_news(normalized, limit))

    def fast_news_report(self, ticker: str, limit: int = 20) -> str:
        normalized = normalize_ticker(ticker)
        providers = self.fast_news_providers or (
            MarketauxNewsProvider(),
            EventProviderNewsAdapter(self.news, "RSS"),
            EventProviderNewsAdapter(self.events, "SEC"),
        )
        items, data_gaps = collect_fast_news(normalized, providers, limit=limit)
        return build_fast_news_report(normalized, items, data_gaps=data_gaps)

    def earnings_calendar_report(self, ticker: str, horizon: str = "3month") -> str:
        normalized = normalize_ticker(ticker)
        try:
            events = self.earnings_calendar.earnings(normalized, horizon=horizon)
            data_gaps = ()
        except Exception as exc:
            events = ()
            data_gaps = (f"{self.earnings_calendar.__class__.__name__}: {exc}",)
        return format_earnings_calendar_report(normalized, events, data_gaps=data_gaps)

    def fundamentals_report(self, ticker: str) -> str:
        return format_fundamentals_report(self.fundamentals.analysis(ticker))

    def signals_report(self, ticker: str, event_limit: int = 3, news_limit: int = 5) -> str:
        normalized = normalize_ticker(ticker)
        events = self._recent_sec_events(normalized, event_limit)
        events += self._recent_news(normalized, news_limit)
        return format_signals_report(normalized, detect_forecast_signals(events))

    def macro_report(self) -> str:
        return format_macro_report(
            build_macro_dashboard(self.macro, price_provider=self.industry_history)
        )

    def _forecast(self, region: str):
        region = region.lower()
        if region == "us":
            return forecast_dashboard(self.macro, US_SPECS, energy_provider=self.macro)
        if region in ("kr", "korea"):
            from .ecos import EcosProvider

            return forecast_dashboard(EcosProvider(), KR_SPECS)
        raise ValueError(f"unknown region {region!r} (use 'us' or 'kr')")

    def forecast_report(self, region: str = "us") -> str:
        title = "US" if region.lower() == "us" else "Korea"
        return format_forecast_report(
            self._forecast(region), f"{title} CPI/PPI Forecast (next release)"
        )

    def forecast_record_report(
        self, region: str, recorded_at: date, path: str = forecast_ledger.DEFAULT_LEDGER
    ) -> str:
        forecasts = self._forecast(region)
        forecast_ledger.record_forecasts(
            forecasts, region=region.lower(), recorded_at=recorded_at, path=path
        )
        return forecast_ledger.ledger_summary(path)

    def forecast_score_report(
        self, scored_at: date, path: str = forecast_ledger.DEFAULT_LEDGER
    ) -> str:
        providers: dict[str, MacroDataProvider] = {"us": self.macro}
        try:
            from .ecos import EcosProvider

            providers["kr"] = EcosProvider()
        except Exception:  # noqa: BLE001
            pass
        scored = forecast_ledger.score_pending(providers, scored_at=scored_at, path=path)
        header = f"Scored {len(scored)} newly-released forecast(s).\n\n"
        return header + forecast_ledger.ledger_summary(path)

    def _rate_providers(self, today: date | None = None) -> dict[str, MacroDataProvider]:
        providers: dict[str, MacroDataProvider] = {"us": self.macro}
        try:
            from .ecos import EcosProvider

            providers["kr"] = EcosProvider()
            if today is not None:
                # Daily provider for the KORIBOR market signal: the sample key
                # caps daily queries at 10 rows, so keep the window short.
                from datetime import timedelta

                providers["kr_market"] = EcosProvider(
                    cycle="D",
                    start=(today - timedelta(days=12)).strftime("%Y%m%d"),
                    end=today.strftime("%Y%m%d"),
                )
        except Exception:  # noqa: BLE001 - KR provider is optional
            pass
        return providers

    def rate_forecast_report(self, region: str, today: date) -> str:
        signals = rate_forecast.collect_signals(region, self._rate_providers(today), today)
        return rate_forecast.format_rate_forecast_report(signals)

    def rate_record_report(
        self,
        region: str,
        recorded_at: date,
        path: str = rate_forecast.DEFAULT_RATE_LEDGER,
        horizon_days: int = 21,
        force: bool = False,
    ) -> str:
        meeting = rate_forecast.next_meeting(region.lower(), recorded_at)
        days_out = (meeting - recorded_at).days
        if days_out > horizon_days:
            header = (
                f"Next {region} meeting {meeting.isoformat()} is {days_out}d away "
                f"(> {horizon_days}d horizon) — not recorded.\n\n"
            )
            return header + rate_forecast.rate_ledger_summary(path)
        if days_out == 0:
            # Date-granularity ledger cannot prove a meeting-day entry predates
            # the announcement — forward-OOS integrity needs strictly-prior rows.
            header = (
                f"{region} meeting {meeting.isoformat()} is today — recording refused "
                "(forward-OOS requires a strictly pre-meeting date).\n\n"
            )
            return header + rate_forecast.rate_ledger_summary(path)
        if not force and rate_forecast.already_recorded(region, meeting, path):
            # Idempotent rerun: skip before touching any provider so a daily
            # cron stays green even when FRED/ECOS are down.
            header = f"{region} {meeting.isoformat()} already recorded — skipped.\n\n"
            return header + rate_forecast.rate_ledger_summary(path)
        signals = rate_forecast.collect_signals(
            region, self._rate_providers(recorded_at), recorded_at
        )
        row = rate_forecast.record_rate_forecast(
            signals, recorded_at=recorded_at, path=path, force=force
        )
        header = (
            f"Recorded {region} {signals.meeting.isoformat()} forecast.\n\n"
            if row
            else f"{region} {signals.meeting.isoformat()} already recorded or scored — "
            "skipped (--force supersedes a pending forecast, never a scored one).\n\n"
        )
        return (
            header
            + rate_forecast.format_rate_forecast_report(signals)
            + "\n"
            + rate_forecast.rate_ledger_summary(path)
        )

    def rate_score_report(
        self, scored_at: date, path: str = rate_forecast.DEFAULT_RATE_LEDGER
    ) -> str:
        scored = rate_forecast.score_rate_pending(
            self._rate_providers(), scored_at=scored_at, path=path
        )
        header = f"Scored {len(scored)} announced decision(s).\n\n"
        return header + rate_forecast.rate_ledger_summary(path)

    def economic_calendar_report(
        self,
        days: int = 60,
        start: date | None = None,
    ) -> str:
        result = collect_economic_events(
            providers=self.economic_calendar_providers,
            start=start,
            days=days,
        )
        return format_economic_calendar_report(result)

    def industry_leadership_report(
        self,
        current_limit: int = 10,
        next_limit: int = 10,
    ) -> str:
        result = analyze_industries(
            history_provider=self.industry_history,
            current_limit=current_limit,
            next_limit=next_limit,
        )
        return format_industry_report(result)

    def industry_leadership_csv(self) -> str:
        result = analyze_industries(history_provider=self.industry_history)
        return industry_scores_to_csv(result.scores)

    def patterns_report(
        self,
        assets: tuple[str, ...] = DEFAULT_ASSETS,
        horizons: tuple[int, ...] = DEFAULT_HORIZONS,
        min_samples: int = 5,
        limit: int = 25,
    ) -> str:
        result = mine_default_patterns(
            macro_provider=self.macro,
            history_provider=self.industry_history,
            assets=assets,
            horizons=horizons,
            min_samples=min_samples,
        )
        return format_pattern_report(result, limit=limit)

    def patterns_csv(
        self,
        assets: tuple[str, ...] = DEFAULT_ASSETS,
        horizons: tuple[int, ...] = DEFAULT_HORIZONS,
        min_samples: int = 5,
    ) -> str:
        result = mine_default_patterns(
            macro_provider=self.macro,
            history_provider=self.industry_history,
            assets=assets,
            horizons=horizons,
            min_samples=min_samples,
        )
        return pattern_results_to_csv(result)

    def screen_prompt(self, criteria: str, direction: str = "both") -> str:
        bundle = self.skills.bundle_for("screen")
        return "\n".join(
            [
                "# Idea Screen",
                "",
                "Not investment advice. Screens surface candidates, not conclusions.",
                "",
                f"Direction: {direction.strip().lower()}",
                f"Criteria: {criteria.strip() or 'User did not provide criteria.'}",
                "",
                "## Output Required",
                "- 5 to 10 candidate names",
                "- Why each candidate appeared in the screen",
                "- Key risks and disconfirming evidence",
                "- Suggested diligence steps before any trade decision",
                "",
                "## Source Skills",
                ", ".join(bundle.names),
            ]
        )

    def screen_all_report(
        self,
        market: str,
        max_tickers: int,
        limit: int,
        include_news: bool,
        include_sec_events: bool,
        include_etfs: bool,
        include_spacs: bool = False,
    ) -> str:
        members = self._universe_members(market, include_etfs, include_spacs)
        lookup = WorkflowScreeningEventLookup(
            sec_provider=self.events,
            news_provider=self.news,
            include_sec_events=include_sec_events,
            include_news=include_news,
        )
        candidates = screen_members(
            members=members,
            market_data=self.market_data,
            event_lookup=lookup,
            max_tickers=max_tickers,
        )
        processed = len(members) if max_tickers == 0 else min(len(members), max_tickers)
        return format_screen_report(
            title=f"{market.upper()} Market Screen",
            candidates=candidates,
            total_universe=len(members),
            processed=processed,
            capped=max_tickers != 0 and len(members) > max_tickers,
            limit=limit,
        )

    def screen_all_csv(
        self,
        market: str,
        max_tickers: int,
        include_news: bool,
        include_sec_events: bool,
        include_etfs: bool,
        include_spacs: bool = False,
    ) -> str:
        members = self._universe_members(market, include_etfs, include_spacs)
        lookup = WorkflowScreeningEventLookup(
            sec_provider=self.events,
            news_provider=self.news,
            include_sec_events=include_sec_events,
            include_news=include_news,
        )
        candidates = screen_members(
            members=members,
            market_data=self.market_data,
            event_lookup=lookup,
            max_tickers=max_tickers,
        )
        return candidates_to_csv(candidates)

    def aggressive_portfolio_report(
        self,
        target_annual_return: int = 100,
        single_stock_pool: tuple[str, ...] = DEFAULT_SINGLE_STOCK_POOL,
    ) -> str:
        draft = build_aggressive_portfolio(
            target_annual_return=target_annual_return,
            market_data=self.market_data,
            single_stock_pool=single_stock_pool,
        )
        return format_portfolio_report(draft)

    def aggressive_portfolio_csv(
        self,
        target_annual_return: int = 100,
        single_stock_pool: tuple[str, ...] = DEFAULT_SINGLE_STOCK_POOL,
    ) -> str:
        draft = build_aggressive_portfolio(
            target_annual_return=target_annual_return,
            market_data=self.market_data,
            single_stock_pool=single_stock_pool,
        )
        return portfolio_to_csv(draft)

    def _market_snapshot_section(self, tickers: list[str]) -> str:
        if not tickers:
            return "## Market Snapshot\n- No tickers available."

        lines = ["## Market Snapshot"]
        for ticker in tickers:
            try:
                lines.append(format_snapshot_line(self.market_data.snapshot(ticker)))
            except Exception as exc:
                lines.append(f"- {ticker}: unavailable | Error: {exc}")
        return "\n".join(lines)

    def _recent_sec_events(self, ticker: str, limit: int) -> tuple:
        return self.events.recent_events(ticker, limit=limit)

    def _recent_news(self, ticker: str, limit: int) -> tuple:
        return self.news.recent_events(ticker, limit=limit)

    def _universe_members(self, market: str, include_etfs: bool, include_spacs: bool) -> tuple:
        return self.universe.members(market, include_etfs=include_etfs, include_spacs=include_spacs)

    def pretrade_report(
        self,
        ticker: str,
        side: str,
        horizon: str,
        risk_budget: str,
        user_context: str,
    ) -> str:
        bundle = self.skills.bundle_for("pretrade")
        normalized = normalize_ticker(ticker)
        thesis = self.store.get_thesis(normalized)
        self.store.record_pretrade(normalized, side, horizon, risk_budget, user_context)

        thesis_block = (
            "\n".join(
                [
                    f"Direction: {thesis.direction}",
                    f"Statement: {thesis.statement}",
                    f"Invalidation: {thesis.invalidation}",
                ]
            )
            if thesis
            else "No stored thesis. Create one before treating this as actionable."
        )

        return "\n".join(
            [
                f"# Pre-Trade Research Check - {normalized}",
                "",
                "Not investment advice. No order should be placed by this tool.",
                "",
                "## Proposed Action Under Review",
                f"- Side: {side.strip().lower()}",
                f"- Horizon: {horizon.strip()}",
                f"- Risk Budget: {risk_budget.strip()}",
                f"- User Context: {user_context.strip() or 'None provided'}",
                "",
                "## Stored Thesis",
                thesis_block,
                "",
                "## Disconfirming Evidence",
                "- What new fact would make this trade wrong today?",
                "- Is the setup already priced in?",
                "- Is there an upcoming catalyst that changes risk/reward?",
                "",
                "## Risk Plan",
                "- Define invalidation before entry",
                "- Confirm position size against portfolio-level risk",
                "- Check earnings dates, liquidity, gap risk, and correlated exposure",
                "",
                "## Human Approval Gate",
                "- User must approve or reject after reviewing sourced facts",
                "- No order routing, broker API call, or trade execution is available here",
                "",
                "## Source Skills",
                ", ".join(bundle.names),
            ]
        )

    def thesis_review(self, ticker: str) -> str:
        bundle = self.skills.bundle_for("morning")
        normalized = normalize_ticker(ticker)
        thesis = self.store.get_thesis(normalized)
        if thesis is None:
            return (
                f"# Thesis Review - {normalized}\n\n"
                "No thesis stored. Add a falsifiable thesis before review.\n\n"
                f"## Source Skills\n{', '.join(bundle.names)}"
            )
        return "\n".join(
            [
                f"# Thesis Review - {normalized}",
                "",
                "Not investment advice. Review whether the thesis remains intact.",
                "",
                f"Direction: {thesis.direction}",
                f"Statement: {thesis.statement}",
                f"Invalidation: {thesis.invalidation}",
                "",
                "## Review Questions",
                "- What changed since the thesis was written?",
                "- Which pillar strengthened or weakened?",
                "- What data would force a trim, exit, or no-action decision?",
                "",
                "## Source Skills",
                ", ".join(bundle.names),
            ]
        )


class WorkflowScreeningEventLookup:
    def __init__(
        self,
        sec_provider: EventProvider,
        news_provider: EventProvider,
        include_sec_events: bool,
        include_news: bool,
    ):
        self.sec_provider = sec_provider
        self.news_provider = news_provider
        self.include_sec_events = include_sec_events
        self.include_news = include_news

    def events_for(self, ticker: str) -> tuple:
        events = ()
        if self.include_sec_events:
            events += safe_recent_events(self.sec_provider, ticker, limit=2)
        if self.include_news:
            events += safe_recent_events(self.news_provider, ticker, limit=3)
        return events


def safe_recent_events(provider: EventProvider, ticker: str, limit: int) -> tuple:
    try:
        return provider.recent_events(ticker, limit=limit)
    except Exception:
        return ()
