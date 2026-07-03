"""Sector-cap ACTIVATION wiring (follow-up to the pretrade sector gate, audit P1). The gate
existed but nothing set a live cap or fed the symbol->sector map into the submission path:

  * ``live_risk_policy`` now carries ``max_sector_weight`` (default 0.35, ``LIVE_MAX_SECTOR_WEIGHT``
    env override, clamped: non-finite/<=0 -> default, >1 -> 1.0 i.e. off).
  * ``live-submit`` auto-loads the newest ``data/sectors/*-sectors.csv`` (or ``--sectors-csv``)
    and passes it to ``process_order_intents``. A missing map is a WARNING in shadow mode but a
    BLOCKING issue for a real ``--submit`` — real money never trades sector-blind silently.
"""

from __future__ import annotations

from engine.live import live_risk_policy
from trader import cli
from trader.execution.adapters.fake import FakeBrokerAdapter
from trader.execution.broker import AccountSnapshot
from trader.execution.runner import ExecutionResult

_GATE_ENV = [
    "LIVE_MAX_SECTOR_WEIGHT",
    "LIVE_BROKER",
    "LIVE_MAX_LIMIT_DEVIATION",
    "LIVE_ALLOW_MARKET_ORDERS",
]


def _clean_env(monkeypatch) -> None:
    for var in _GATE_ENV:
        monkeypatch.delenv(var, raising=False)


# ---------------------------------------------------------------- live_risk_policy


def test_live_risk_policy_defaults_sector_cap(monkeypatch) -> None:
    _clean_env(monkeypatch)
    assert live_risk_policy().max_sector_weight == 0.35


def test_live_risk_policy_env_override(monkeypatch) -> None:
    _clean_env(monkeypatch)
    monkeypatch.setenv("LIVE_MAX_SECTOR_WEIGHT", "0.25")
    assert live_risk_policy().max_sector_weight == 0.25


def test_live_risk_policy_clamps_nonsense_values(monkeypatch) -> None:
    _clean_env(monkeypatch)
    for bad, expected in (("nan", 0.35), ("-0.2", 0.35), ("0", 0.35), ("2.5", 1.0)):
        monkeypatch.setenv("LIVE_MAX_SECTOR_WEIGHT", bad)
        assert live_risk_policy().max_sector_weight == expected, bad


# ---------------------------------------------------------------- live-submit wiring


def _submit_args(tmp_path, extra: list[str]) -> list[str]:
    return [
        "live-submit",
        "QQQ",
        "--side",
        "buy",
        "--qty",
        "1",
        "--price",
        "100",
        "--broker",
        "fake",
        "--order-log",
        str(tmp_path / "orders.jsonl"),
        "--halt-state",
        str(tmp_path / "halt.json"),
        "--equity-state",
        str(tmp_path / "equity.json"),
        "--catalog-db",
        str(tmp_path / "cat.duckdb"),
        *extra,
    ]


def test_live_submit_passes_sector_map_to_runner(tmp_path, monkeypatch) -> None:
    _clean_env(monkeypatch)
    sectors_csv = tmp_path / "u-sectors.csv"
    sectors_csv.write_text("symbol,sic,sector\nQQQ,,etf\nAAPL,3571,tech\n", encoding="utf-8")

    captured: dict = {}

    def _capture(intents, **kwargs):
        captured.update(kwargs)
        return [ExecutionResult(intents[0].client_order_id, "submit", "accepted")]

    monkeypatch.setattr(cli, "_live_readiness_issues", lambda **k: [])
    monkeypatch.setattr(
        cli,
        "_live_broker_adapter",
        lambda *a, **k: FakeBrokerAdapter(account=AccountSnapshot("f", 1e6, 1e6, 1e6)),
    )
    monkeypatch.setattr(cli, "process_order_intents", _capture)

    code = cli.main(_submit_args(tmp_path, ["--sectors-csv", str(sectors_csv)]))

    assert code == 0
    assert captured.get("sectors") == {"QQQ": "etf", "AAPL": "tech"}
    assert captured["policy"].max_sector_weight == 0.35  # live cap active by default


def test_live_submit_shadow_warns_but_proceeds_without_sector_map(
    tmp_path, monkeypatch, capsys
) -> None:
    _clean_env(monkeypatch)
    monkeypatch.setattr(cli, "_live_readiness_issues", lambda **k: [])
    monkeypatch.setattr(
        cli,
        "_live_broker_adapter",
        lambda *a, **k: FakeBrokerAdapter(account=AccountSnapshot("f", 1e6, 1e6, 1e6)),
    )
    monkeypatch.setattr(cli, "_latest_sector_map", lambda: None)
    monkeypatch.setattr(
        cli,
        "process_order_intents",
        lambda intents, **k: [ExecutionResult(intents[0].client_order_id, "dry_run", "accepted")],
    )

    code = cli.main(_submit_args(tmp_path, []))
    out = capsys.readouterr().out

    assert code == 0  # shadow rehearsal proceeds
    assert "sector" in out.lower() and "missing" in out.lower()  # but never silently


def test_live_submit_real_submission_blocks_without_sector_map(
    tmp_path, monkeypatch, capsys
) -> None:
    # Real money never trades sector-blind: --submit with no discoverable map is a blocking
    # issue (exit 2), listed in the readiness output like any other gate failure.
    _clean_env(monkeypatch)
    monkeypatch.setattr(cli, "_live_readiness_issues", lambda **k: [])
    monkeypatch.setattr(cli, "_latest_sector_map", lambda: None)

    code = cli.main(_submit_args(tmp_path, ["--submit", "--ack-live-order"]))
    out = capsys.readouterr().out

    assert code == 2
    assert "sector" in out.lower()


def test_live_submit_real_submission_blocks_when_symbol_not_in_map(
    tmp_path, monkeypatch, capsys
) -> None:
    # codex P1: a loaded map that does not classify the ORDER symbol (wrong universe / empty
    # file) let a real submission slip past the sector cap sector-blind. --submit must fail
    # closed unless the submitted symbol itself is mapped.
    _clean_env(monkeypatch)
    sectors_csv = tmp_path / "u-sectors.csv"
    sectors_csv.write_text("symbol,sic,sector\nAAPL,3571,tech\n", encoding="utf-8")  # no QQQ
    monkeypatch.setattr(cli, "_live_readiness_issues", lambda **k: [])

    code = cli.main(
        _submit_args(tmp_path, ["--submit", "--ack-live-order", "--sectors-csv", str(sectors_csv)])
    )
    out = capsys.readouterr().out

    assert code == 2
    assert "sector" in out.lower() and "qqq" in out.lower()


def test_live_submit_explicit_missing_sectors_csv_is_config_error(
    tmp_path, monkeypatch, capsys
) -> None:
    _clean_env(monkeypatch)
    monkeypatch.setattr(cli, "_live_readiness_issues", lambda **k: [])

    code = cli.main(_submit_args(tmp_path, ["--sectors-csv", str(tmp_path / "nope.csv")]))
    out = capsys.readouterr().out

    assert code == 2  # an explicitly named map that does not exist must fail loudly
    assert "sector" in out.lower()
