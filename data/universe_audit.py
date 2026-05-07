from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time

from data.catalog import MarketDataCatalog
from data.models import DelistingReturn, UniverseMember

MAX_EDGE_GAP_DAYS = 7


@dataclass(frozen=True)
class UniverseAuditIssue:
    severity: str
    area: str
    symbol: str
    message: str


@dataclass(frozen=True)
class UniverseAuditReport:
    universe: str
    market: str
    start: date
    end: date
    member_rows: int
    active_symbols: int
    price_symbols_checked: int
    rebalance_dates_checked: int
    require_fundamentals: bool
    issues: list[UniverseAuditIssue]

    @property
    def error_count(self) -> int:
        return sum(issue.severity == "error" for issue in self.issues)

    @property
    def warn_count(self) -> int:
        return sum(issue.severity == "warn" for issue in self.issues)

    @property
    def ready(self) -> bool:
        return self.error_count == 0


def run_universe_audit(
    catalog: MarketDataCatalog,
    *,
    universe: str,
    market: str,
    start: date,
    end: date,
    symbols: list[str] | None = None,
    require_fundamentals: bool = False,
    require_delistings: bool = True,
    rebalance_days: int = 21,
    max_issues: int = 200,
) -> UniverseAuditReport:
    if end < start:
        raise ValueError("end must be >= start")
    if rebalance_days < 1:
        raise ValueError("rebalance_days must be >= 1")
    catalog.initialize()
    requested = {symbol.upper() for symbol in symbols or []}
    members = [
        member
        for member in catalog.get_universe_members(universe, market=market)
        if not requested or member.symbol.upper() in requested
    ]
    active_members = [member for member in members if _overlaps(member, start, end)]
    active_symbols = sorted({member.symbol.upper() for member in active_members})
    delistings = catalog.get_delisting_returns(
        symbols=active_symbols,
        market=market,
        start=start,
        end=end,
    )
    delistings_by_symbol = _delistings_by_symbol(delistings)
    issues: list[UniverseAuditIssue] = []
    if not members:
        issues.append(UniverseAuditIssue("error", "universe", universe, "universe has no members"))
    elif not active_members:
        issues.append(
            UniverseAuditIssue("error", "universe", universe, "universe has no active members in window")
        )

    rebalance_dates = _rebalance_dates(start, end, rebalance_days)
    for symbol in active_symbols:
        intervals = [member for member in active_members if member.symbol.upper() == symbol]
        required_start = min(max(member.start_date, start) for member in intervals)
        required_end = max(min(member.end_date or end, end) for member in intervals)
        bars = catalog.get_bars(symbol, market=market, start=required_start, end=required_end)
        if not bars:
            _append_issue(
                issues,
                max_issues,
                UniverseAuditIssue(
                    "error",
                    "price",
                    symbol,
                    f"no bars for active interval {required_start} to {required_end}",
                ),
            )
        else:
            first_bar = bars[0]
            last_bar = bars[-1]
            if (first_bar.ts - required_start).days > MAX_EDGE_GAP_DAYS:
                _append_issue(
                    issues,
                    max_issues,
                    UniverseAuditIssue(
                        "error",
                        "price",
                        symbol,
                        f"first bar {first_bar.ts} is after required start {required_start}",
                    ),
                )
            if (required_end - last_bar.ts).days > MAX_EDGE_GAP_DAYS:
                ended = any(
                    member.end_date is not None and member.end_date <= end
                    for member in intervals
                )
                has_delisting = _has_delisting_after(
                    delistings_by_symbol,
                    symbol,
                    last_bar.ts,
                    required_end,
                )
                if ended and has_delisting:
                    _append_issue(
                        issues,
                        max_issues,
                        UniverseAuditIssue(
                            "warn",
                            "price",
                            symbol,
                            f"price ends at {last_bar.ts}; explicit delisting return covers exit",
                        ),
                    )
                else:
                    reason = "ended member missing delisting return" if ended else "ongoing member price ends early"
                    _append_issue(
                        issues,
                        max_issues,
                        UniverseAuditIssue(
                            "error",
                            "price",
                            symbol,
                            f"{reason}: last bar {last_bar.ts}, required through {required_end}",
                        ),
                    )
        if require_delistings:
            for member in intervals:
                if member.end_date is None or member.end_date > end:
                    continue
                if not _has_delisting_after(
                    delistings_by_symbol,
                    symbol,
                    start,
                    min(member.end_date, end),
                ):
                    _append_issue(
                        issues,
                        max_issues,
                        UniverseAuditIssue(
                            "error",
                            "delisting",
                            symbol,
                            f"member ends {member.end_date} but has no explicit delisting return",
                        ),
                    )
        if require_fundamentals:
            missing_asof = _first_missing_fundamental_date(
                catalog,
                symbol=symbol,
                market=market,
                dates=rebalance_dates,
                members=intervals,
            )
            if missing_asof is not None:
                _append_issue(
                    issues,
                    max_issues,
                    UniverseAuditIssue(
                        "error",
                        "fundamentals",
                        symbol,
                        f"no PIT fundamental record available as of rebalance {missing_asof}",
                    ),
                )

    return UniverseAuditReport(
        universe=universe,
        market=market,
        start=start,
        end=end,
        member_rows=len(members),
        active_symbols=len(active_symbols),
        price_symbols_checked=len(active_symbols),
        rebalance_dates_checked=len(rebalance_dates) if require_fundamentals else 0,
        require_fundamentals=require_fundamentals,
        issues=sorted(issues, key=lambda item: (_severity_rank(item.severity), item.area, item.symbol)),
    )


def format_universe_audit_report(report: UniverseAuditReport) -> str:
    lines = [
        "# Universe Data Audit",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Universe | {report.universe} |",
        f"| Market | {report.market} |",
        f"| Window | {report.start} to {report.end} |",
        f"| Member Rows | {report.member_rows} |",
        f"| Active Symbols | {report.active_symbols} |",
        f"| Price Symbols Checked | {report.price_symbols_checked} |",
        f"| Require Fundamentals | {'yes' if report.require_fundamentals else 'no'} |",
        f"| Rebalance Dates Checked | {report.rebalance_dates_checked} |",
        f"| Errors | {report.error_count} |",
        f"| Warnings | {report.warn_count} |",
        f"| Ready | {'yes' if report.ready else 'no'} |",
        "",
    ]
    if not report.issues:
        lines.append("Data audit: OK")
        return "\n".join(lines)
    lines.extend(
        [
            "## Issues",
            "",
            "| Severity | Area | Symbol | Message |",
            "|---|---|---|---|",
        ]
    )
    for issue in report.issues:
        lines.append(f"| {issue.severity} | {issue.area} | {issue.symbol} | {issue.message} |")
    return "\n".join(lines)


def _overlaps(member: UniverseMember, start: date, end: date) -> bool:
    member_end = member.end_date or end
    return member.start_date <= end and member_end >= start


def _rebalance_dates(start: date, end: date, rebalance_days: int) -> list[date]:
    dates: list[date] = []
    current = start
    while current <= end:
        dates.append(current)
        current = date.fromordinal(current.toordinal() + rebalance_days)
    return dates


def _active_member(member: UniverseMember, as_of: date) -> bool:
    return member.start_date <= as_of and (member.end_date is None or as_of <= member.end_date)


def _first_missing_fundamental_date(
    catalog: MarketDataCatalog,
    *,
    symbol: str,
    market: str,
    dates: list[date],
    members: list[UniverseMember],
) -> date | None:
    for as_of in dates:
        if not any(_active_member(member, as_of) for member in members):
            continue
        records = catalog.get_fundamentals(
            symbol,
            market=market,
            as_of=datetime.combine(as_of, time.max),
        )
        if not records:
            return as_of
    return None


def _delistings_by_symbol(
    delistings: list[DelistingReturn],
) -> dict[str, tuple[DelistingReturn, ...]]:
    rows: dict[str, list[DelistingReturn]] = {}
    for item in delistings:
        rows.setdefault(item.symbol.upper(), []).append(item)
    return {
        symbol: tuple(sorted(items, key=lambda item: item.ts))
        for symbol, items in rows.items()
    }


def _has_delisting_after(
    delistings_by_symbol: dict[str, tuple[DelistingReturn, ...]],
    symbol: str,
    after: date,
    until: date,
) -> bool:
    return any(after <= item.ts <= until for item in delistings_by_symbol.get(symbol.upper(), ()))


def _append_issue(
    issues: list[UniverseAuditIssue],
    max_issues: int,
    issue: UniverseAuditIssue,
) -> None:
    if len(issues) < max_issues:
        issues.append(issue)


def _severity_rank(severity: str) -> int:
    return {"error": 0, "warn": 1, "info": 2}.get(severity, 99)
