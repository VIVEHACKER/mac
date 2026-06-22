from datetime import date

from engine.fund_book_oos import FundBookOOSEntry
from scripts.fund_marks_live import build_live_marks, ledger_symbols, open_book_mtm


def _entry(rebal="2026-05-29", weights=None, prices=None, bench_price=700.0):
    weights = weights or {"MU": 0.5, "AAL": 0.5}
    prices = prices or {"MU": 100.0, "AAL": 50.0}
    return FundBookOOSEntry(
        rebal_date=rebal,
        weights=weights,
        entry_prices=prices,
        benchmark_symbol="SPY",
        benchmark_price=bench_price,
        sleeve_fractions={"core": 0.35},
        reserve_cash=0.0,
        invested=1.0,
    )


def test_ledger_symbols_includes_benchmark_and_holdings():
    syms = ledger_symbols([_entry()])
    assert syms == ["AAL", "MU", "SPY"]  # 정렬 + 벤치 포함


def test_build_live_marks_uses_injected_fetch_no_network():
    captured = {}

    def fake_fetch(symbols, start, end):
        captured["symbols"] = symbols
        captured["start"] = start
        return {
            "2026-05-29": dict.fromkeys(symbols, 100.0),
            "2026-06-10": dict.fromkeys(symbols, 110.0),
        }

    dates, table = build_live_marks([_entry()], end=date(2026, 6, 11), fetch=fake_fetch)
    assert dates == ["2026-05-29", "2026-06-10"]
    assert set(captured["symbols"]) == {"AAL", "MU", "SPY"}
    assert captured["start"] == "2026-05-29"  # earliest rebal
    assert table["2026-06-10"]["SPY"] == 110.0


def test_open_book_mtm_unrealized_excess_vs_spy():
    # port +10% (MU 100→110, AAL 50→55), bench +5% (SPY 700→735) → 미실현 초과 +5%
    e = _entry()
    marks_today = {"MU": 110.0, "AAL": 55.0, "SPY": 735.0}
    mtm = open_book_mtm(e, marks_today)
    assert abs(mtm["port_return"] - 0.10) < 1e-9
    assert abs(mtm["benchmark_return"] - 0.05) < 1e-9
    assert abs(mtm["unrealized_excess"] - 0.05) < 1e-9
    assert mtm["marked"] == 2  # MU, AAL 둘 다 마크됨


def test_open_book_mtm_none_when_no_marks():
    e = _entry()
    assert open_book_mtm(e, {"SPY": 735.0}) is None  # 보유종목 마크 0 → None
