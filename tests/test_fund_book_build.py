from pathlib import Path

from engine.fund_book import FundBook
from scripts.fund_book import build_fund_book

TRADER_SNAP = Path("/Users/jjuni/재무관리 모델/trader/data/snapshots")


def test_build_fund_book_core_hunt_only():
    book, sectors = build_fund_book(
        snapshot=TRADER_SNAP / "fundamentals-2026-06-01-gp2.csv",
        prices=TRADER_SNAP / "prices-2026-06-01.csv",
    )
    assert isinstance(book, FundBook)
    assert isinstance(sectors, dict)
    # core 슬리브는 항상 존재, fraction 0.35
    fracs = dict(book.sleeve_fractions)
    assert fracs.get("core") == 0.35
    # momentum off → invested 는 core(+hunt) 만; core 가 35% 채우면 ~0.35
    assert 0.25 <= book.invested <= 0.36
    assert all(0.0 <= p.fund_weight <= book.max_name_weight + 1e-9 for p in book.positions)


def test_build_fund_book_with_momentum():
    book, _ = build_fund_book(
        snapshot=TRADER_SNAP / "fundamentals-2026-06-01-gp2.csv",
        prices=TRADER_SNAP / "prices-2026-06-01.csv",
        price_history=TRADER_SNAP / "prices-ideal-2026-06-01.csv",
        momentum_snapshot=TRADER_SNAP / "fundamentals-2026-06-01-gp.csv",
    )
    fracs = dict(book.sleeve_fractions)
    assert fracs.get("momentum") == 0.25
    # core 35 + momentum 25 = 60% (hunt 비어도)
    assert 0.55 <= book.invested <= 0.61
