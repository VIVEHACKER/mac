from __future__ import annotations

from datetime import date, datetime

from data.models import FundamentalRecord
from engine.compounder import rank_compounders
from engine.compounder_dossier import Dossier, build_dossier, format_dossier_markdown


def _series(symbol, rev, ni, fcf, eq, debt, sh, eps):
    out = []
    for i, year in enumerate((2020, 2021, 2022, 2023)):
        out.append(
            FundamentalRecord(
                symbol=symbol,
                market="us",
                period_end=date(year, 12, 31),
                asof_ts=datetime(year + 1, 3, 1),
                revenue=rev[i],
                net_income=ni[i],
                free_cash_flow=fcf[i],
                total_equity=eq,
                total_debt=debt,
                shares_out=sh,
                eps=eps,
            )
        )
    return out


def test_build_dossier_carries_archetype_and_alt_signals_hook():
    q = _series(
        "QLT", [100, 110, 121, 133], [20, 24, 30, 40], [18, 22, 28, 38], 100.0, 10.0, 50.0, 5.0
    )
    ranked = rank_compounders({"QLT": (q, 60.0)}, top_n=1)
    d = build_dossier(ranked[0])
    assert isinstance(d, Dossier)
    assert d.symbol == "QLT"
    assert d.archetype in ("profitable_compounder", "hypergrowth_disruptor", "value_turnaround")
    assert d.alt_signals == {}  # P1 leaves empty; P3 fills
    assert "roic" in d.metrics


def test_format_dossier_markdown_contains_key_fields():
    q = _series(
        "QLT", [100, 110, 121, 133], [20, 24, 30, 40], [18, 22, 28, 38], 100.0, 10.0, 50.0, 5.0
    )
    d = build_dossier(rank_compounders({"QLT": (q, 60.0)}, top_n=1)[0])
    md = format_dossier_markdown(d)
    assert "QLT" in md
    assert "ROIC" in md or "roic" in md
    assert d.rationale in md
