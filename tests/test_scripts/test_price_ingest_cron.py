"""Operational price-ingest runner (audit P1: stale yahoo marks + manual ingest). The cron
runner keeps the live catalog fresh without keys and upgrades itself when keys appear:

  * symbols = union of every paper-drill book's positions + the benchmark — the marks the
    freshness gate actually needs;
  * source  = alpaca (IEX) when ALPACA_API_KEY/SECRET are set, else the keyless yahoo EOD
    fallback — setting the keys in .env is the single activation switch, no code change;
  * it invokes the SAME `trader live-price-ingest` CLI the operator uses (no second ingest
    implementation).
"""

from __future__ import annotations

import json

from scripts.price_ingest_cron import collect_symbols, decide_source, run


def _write_state(out_dir, name: str, positions: dict[str, float]) -> None:
    (out_dir / f"paper-drill-state-{name}.json").write_text(
        json.dumps({"nav": 10_000.0, "peak": 10_000.0, "positions": positions}),
        encoding="utf-8",
    )


def test_collect_symbols_unions_books_and_benchmark(tmp_path) -> None:
    _write_state(tmp_path, "a", {"AAPL": 3, "MSFT": 2})
    _write_state(tmp_path, "b", {"XOM": 1, "AAPL": 5})
    symbols = collect_symbols(out_dir=tmp_path, benchmark="SPY")
    assert symbols == ["AAPL", "MSFT", "SPY", "XOM"]  # sorted union, benchmark included


def test_collect_symbols_without_states_still_covers_benchmark(tmp_path) -> None:
    assert collect_symbols(out_dir=tmp_path, benchmark="SPY") == ["SPY"]


def test_decide_source_keyless_falls_back_to_yahoo(monkeypatch) -> None:
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
    assert decide_source() == "yahoo"
    monkeypatch.setenv("ALPACA_API_KEY", "  ")  # blank = unset
    monkeypatch.setenv("ALPACA_SECRET_KEY", "")
    assert decide_source() == "yahoo"


def test_decide_source_uses_alpaca_when_keys_present(monkeypatch) -> None:
    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "s")
    assert decide_source() == "alpaca"


def test_run_invokes_cli_ingest_with_collected_symbols(tmp_path, monkeypatch) -> None:
    _write_state(tmp_path, "a", {"AAPL": 3})
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)

    captured: dict = {}

    def fake_cli_main(argv):
        captured["argv"] = list(argv)
        return 0

    code = run(out_dir=tmp_path, benchmark="SPY", cli_main=fake_cli_main)

    assert code == 0
    argv = captured["argv"]
    assert argv[0] == "live-price-ingest"
    assert argv[1] == "AAPL,SPY"
    assert argv[argv.index("--source") + 1] == "yahoo"
