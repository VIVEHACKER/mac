from __future__ import annotations

import argparse

from scripts.sec_edgar_ingest import resolve_tickers


def _args(**kw) -> argparse.Namespace:
    base = {"universe_csv": None, "tickers": None}
    base.update(kw)
    return argparse.Namespace(**base)


def test_resolve_from_tickers_flag():
    assert resolve_tickers(_args(tickers="aaa, bbb,CCC")) == ["AAA", "BBB", "CCC"]


def test_resolve_default_is_megacaps():
    from scripts.sec_edgar_ingest import TICKERS

    assert resolve_tickers(_args()) == TICKERS


def test_resolve_from_universe_csv(tmp_path):
    csv_path = tmp_path / "u.csv"
    csv_path.write_text(
        "universe,symbol,market,start_date,end_date,source,confidence,"
        "asset_class,asset_subclass,role\n"
        "SP400_600_CURRENT,AAA,us,2026-05-31,,ishares,medium,equity,us-mid-cap,risk\n"
        "SP400_600_CURRENT,BBB,us,2026-05-31,,ishares,medium,equity,us-small-cap,risk\n",
        encoding="utf-8",
    )
    assert resolve_tickers(_args(universe_csv=csv_path)) == ["AAA", "BBB"]
