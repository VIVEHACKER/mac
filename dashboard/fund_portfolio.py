"""펀드 포트폴리오 탭 — 조립된 바벨 FundBook 을 노출.

새 펀드 로직 없음: scripts.fund_book.build_fund_book(검증 조립기)의 결과를 렌더한다.
스냅샷 CSV 는 trader-fund 에서 gitignore 되므로 ../trader/data/snapshots 로 폴백 리졸브.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
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


def _resolve_db(root: Path) -> Path:
    """trader 카탈로그 DuckDB 를 root 기준으로 해석(워크스테이션 절대경로 폴백 회피)."""
    local = root / "data" / "store" / "trader.duckdb"
    if local.exists():
        return local
    return root.parent / "trader" / "data" / "store" / "trader.duckdb"


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

    kwargs: dict = {"snapshot": snapshot, "prices": prices, "db": _resolve_db(root)}
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


_HONESTY = (
    "정직성: 알파 주장 없음 · core/hunt 검증 엣지 없음 · momentum 만 검증"
    "(+8.15%/yr walk-forward, US 한정) · 리스크 모델 아님(서술적 진단)."
)


def render_fund_portfolio(root: Path) -> None:
    st.subheader("펀드 포트폴리오 — 조립된 50/50 바벨")
    c1, c2 = st.columns([1, 4])
    momentum_on = c1.checkbox("모멘텀 슬리브 포함", value=True, key="fund_mom")
    if c2.button("↻ 새로고침", key="fund_refresh"):
        fund_book_payload.clear()

    try:
        payload = fund_book_payload(root, momentum_on=momentum_on)
    except Exception as e:  # 조립 실패 — 탭만 죽고 앱은 유지
        st.error(f"펀드 북 조립 실패: {e}")
        return

    meta = payload["meta"]
    if not meta["available"]:
        st.warning(meta["message"])
        return

    st.caption(_HONESTY)

    # 상단 — 슬리브 배분 (정책 타겟 vs 실현)
    fracs = dict(meta["sleeve_fractions"])
    reserve_policy = round(1.0 - sum(fracs.values()), 4)
    bar = {**fracs, "reserve": reserve_policy}
    st.markdown("**정책 타겟 (슬리브 비중)**")
    st.bar_chart(pd.DataFrame({"비중": bar}))
    m = st.columns(5)
    m[0].metric("invested(실현)", f"{meta['invested'] * 100:.1f}%")
    m[1].metric("reserve(실현)", f"{meta['reserve_cash'] * 100:.1f}%")
    m[2].metric("종목수", meta["n_positions"])
    m[3].metric("유효종목수", meta["effective_n"])
    top = meta["top_name"] or "—"
    m[4].metric("top", f"{top} {meta['top_name_weight'] * 100:.1f}%")
    if not meta["momentum_on"]:
        st.info("모멘텀 슬리브 제외(토글 OFF 또는 시계열 스냅샷 없음) — core+hunt 만 표시.")

    # 중단 — 종목 / 섹터
    cols = st.columns([3, 2])
    with cols[0]:
        st.markdown("**종목 (펀드 비중 · 출처 슬리브)**")
        st.dataframe(pd.DataFrame(payload["positions"]), use_container_width=True, hide_index=True)
    with cols[1]:
        st.markdown("**섹터 익스포저**")
        st.dataframe(pd.DataFrame(payload["sectors"]), use_container_width=True, hide_index=True)
        st.markdown("**슬리브 기여**")
        st.dataframe(
            pd.DataFrame(payload["sleeve_attr"]), use_container_width=True, hide_index=True
        )

    # 하단 — OOS 성과
    st.markdown("**포워드 OOS 성과**")
    oos = payload["oos"]
    if oos["n_entries"] == 0:
        st.info(
            "포워드 OOS 원장 미가동(표본 0). PIT 기록을 시작하려면 "
            "`scripts/fund_book_oos.py` 로 리밸 시점 펀드 북을 등록하세요."
        )
    else:
        st.caption(
            f"원장 {oos['n_entries']}건 · 최근 리밸 {oos['latest_rebal']} "
            "(누적/연환산 초과·적중률·excess Sharpe 채점은 mark price history 필요 — 후속)"
        )
