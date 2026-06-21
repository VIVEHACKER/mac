"""펀드 포트폴리오 탭 — 조립된 바벨 FundBook 을 노출.

새 펀드 로직 없음: scripts.fund_book.build_fund_book(검증 조립기)의 결과를 렌더한다.
스냅샷 CSV 는 trader-fund 에서 gitignore 되므로 ../trader/data/snapshots 로 폴백 리졸브.
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

# 모멘텀 기본 ON 에 필요한 스냅샷 (글롭 패턴 — 날짜 변경에 견고)
_FUND_SNAPSHOT = ["fundamentals-*-gp2.csv"]  # core/hunt 횡단 펀더멘털
_FUND_PRICES = ["prices-2*.csv"]  # core/hunt 횡단 가격 (prices-ideal 제외)
_MOM_HISTORY = ["prices-ideal-*.csv"]  # 모멘텀 시계열
_MOM_SNAPSHOT = ["fundamentals-*-gp.csv"]  # 모멘텀 megacap 펀더멘털
_OOS_LEDGER = "fund-book-oos.jsonl"


def resolve_snapshot(patterns: list[str], root: Path) -> Path | None:
    """패턴에 맞는 최신 스냅샷 CSV 를 로컬→../trader 순으로 찾는다(ISO 날짜명=사전순 최신)."""
    for base in (root / "data" / "snapshots", root.parent / "trader" / "data" / "snapshots"):
        hits: list[Path] = []
        for pat in patterns:
            hits.extend(base.glob(pat))
        if hits:
            return max(hits, key=lambda p: p.name)
    return None


def _provenance(contributions: tuple[tuple[str, float], ...]) -> str:
    return "+".join(name for name, _ in contributions) or "—"


@st.cache_data(show_spinner=False)
def fund_book_payload(root: Path, *, momentum_on: bool = True) -> dict:
    """조립된 FundBook 을 plain dict 로 (st.cache_data 피클 안전). 스냅샷 없으면 available=False."""
    from engine.fund_book_oos import load_ledger
    from engine.fund_exposure import compute_exposure
    from scripts.fund_book import build_fund_book  # 무거운 import(yfinance) 지연

    snapshot = resolve_snapshot(_FUND_SNAPSHOT, root)
    prices = resolve_snapshot(_FUND_PRICES, root)
    if snapshot is None or prices is None:
        return {
            "meta": {
                "available": False,
                "message": (
                    "스냅샷 CSV 를 찾지 못했습니다(trader-fund 는 gitignore, "
                    "../trader/data/snapshots 도 없음). "
                    "scripts/snapshot_fundamentals.py / snapshot_prices.py 로 재생성하세요."
                ),
            },
            "positions": [],
            "sectors": [],
            "sleeve_attr": [],
            "oos": {"n_entries": 0, "latest_rebal": None},
        }

    kwargs: dict = {"snapshot": snapshot, "prices": prices}
    mom_hist = resolve_snapshot(_MOM_HISTORY, root) if momentum_on else None
    mom_snap = resolve_snapshot(_MOM_SNAPSHOT, root) if momentum_on else None
    if mom_hist is not None and mom_snap is not None:
        kwargs["price_history"] = mom_hist
        kwargs["momentum_snapshot"] = mom_snap

    book, sectors = build_fund_book(**kwargs)
    exposure = compute_exposure(book, sectors)

    positions = [
        {
            "종목": p.symbol,
            "펀드%": round(p.fund_weight * 100, 2),
            "캡": "★" if p.capped else "",
            "출처슬리브": _provenance(p.contributions),
        }
        for p in book.positions
    ]
    sector_rows = [
        {"섹터": s.sector, "비중%": round(s.weight * 100, 2), "종목수": s.n_names}
        for s in exposure.sector_exposures
    ]
    sleeve_attr = [
        {"슬리브": a.sleeve, "기여%": round(a.weight * 100, 2)} for a in exposure.sleeve_attribution
    ]

    ledger_path = root / "out" / _OOS_LEDGER
    entries = load_ledger(ledger_path) if ledger_path.exists() else []

    return {
        "meta": {
            "available": True,
            "momentum_on": "price_history" in kwargs,
            "invested": round(book.invested, 4),
            "reserve_cash": round(book.reserve_cash, 4),
            "max_name_weight": book.max_name_weight,
            "n_positions": book.n_positions,
            "effective_n": round(exposure.effective_n, 1),
            "top_name": exposure.top_name,
            "top_name_weight": round(exposure.top_name_weight, 4),
            "sleeve_fractions": list(book.sleeve_fractions),
        },
        "positions": positions,
        "sectors": sector_rows,
        "sleeve_attr": sleeve_attr,
        "oos": {
            "n_entries": len(entries),
            "latest_rebal": entries[-1].rebal_date if entries else None,
        },
    }
