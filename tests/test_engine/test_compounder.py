from __future__ import annotations

from datetime import date, datetime

from data.models import FundamentalRecord
from engine.compounder import (
    ArchetypeScore,
    compute_metrics,
    score_archetypes,
)


def _series(symbol, rev, ni, fcf, eq, debt, sh, eps):
    """4 annual records 2020-2023 with constant per-field values except revenue ramp."""
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


def test_compute_metrics_returns_expected_keys():
    recs = _series(
        "AAA", [100, 120, 150, 190], [10, 14, 20, 30], [8, 12, 18, 28], 100.0, 20.0, 50.0, 3.0
    )
    m = compute_metrics(recs, price=60.0)
    for key in ("revenue_cagr", "margin_trend", "roic", "fcf_margin", "pfcf", "share_growth"):
        assert key in m


def test_profitable_compounder_scores_highest_for_quality_name():
    # quality: high roic/fcf/rising margin ; junk: low everything
    quality = _series(
        "QLT", [100, 110, 121, 133], [20, 24, 30, 40], [18, 22, 28, 38], 100.0, 10.0, 50.0, 5.0
    )
    junk = _series("JNK", [100, 101, 102, 103], [1, 1, 1, 1], [0, 0, 0, 0], 100.0, 200.0, 60.0, 0.1)
    universe = {"QLT": (quality, 60.0), "JNK": (junk, 5.0)}
    scores = score_archetypes(universe)
    assert (
        scores["QLT"]["profitable_compounder"].score > scores["JNK"]["profitable_compounder"].score
    )
    assert isinstance(scores["QLT"]["profitable_compounder"], ArchetypeScore)


def test_hypergrowth_scores_highest_for_fast_grower_even_if_unprofitable():
    grower = _series(
        "GRW", [100, 160, 256, 410], [-5, -3, 0, 5], [-4, -2, 1, 6], 50.0, 0.0, 40.0, 0.5
    )
    slow = _series(
        "SLO", [100, 103, 106, 109], [20, 20, 20, 20], [18, 18, 18, 18], 100.0, 0.0, 40.0, 4.0
    )
    universe = {"GRW": (grower, 30.0), "SLO": (slow, 50.0)}
    scores = score_archetypes(universe)
    assert (
        scores["GRW"]["hypergrowth_disruptor"].score > scores["SLO"]["hypergrowth_disruptor"].score
    )


def test_value_scores_highest_for_cheap_recovering_name():
    cheap = _series(
        "CHP", [100, 100, 105, 115], [2, 4, 8, 14], [3, 6, 10, 16], 200.0, 20.0, 100.0, 1.4
    )
    pricey = _series(
        "PRC", [100, 110, 121, 133], [30, 33, 36, 40], [28, 31, 34, 38], 50.0, 0.0, 50.0, 8.0
    )
    universe = {"CHP": (cheap, 8.0), "PRC": (pricey, 300.0)}
    scores = score_archetypes(universe)
    assert scores["CHP"]["value_turnaround"].score > scores["PRC"]["value_turnaround"].score


from engine.compounder import CandidateScore, rank_compounders  # noqa: E402


def test_rank_assigns_best_archetype_and_orders_by_score():
    quality = _series(
        "QLT", [100, 110, 121, 133], [20, 24, 30, 40], [18, 22, 28, 38], 100.0, 10.0, 50.0, 5.0
    )
    grower = _series(
        "GRW", [100, 160, 256, 410], [-5, -3, 0, 5], [-4, -2, 1, 6], 50.0, 0.0, 40.0, 0.5
    )
    junk = _series("JNK", [100, 101, 102, 103], [1, 1, 1, 1], [0, 0, 0, 0], 100.0, 250.0, 70.0, 0.1)
    universe = {"QLT": (quality, 60.0), "GRW": (grower, 30.0), "JNK": (junk, 5.0)}

    ranked = rank_compounders(universe, top_n=2)
    assert all(isinstance(c, CandidateScore) for c in ranked)
    assert len(ranked) == 2
    # descending by best_score
    assert ranked[0].best_score >= ranked[1].best_score
    # junk should not be in the top 2
    assert "JNK" not in [c.symbol for c in ranked]
    # best_archetype is the max-scoring archetype for that name
    top = ranked[0]
    assert top.best_archetype == max(top.scores, key=lambda a: top.scores[a].score)


def test_rank_excludes_names_without_metrics():
    empty = []  # no records -> compute_metrics returns {}
    good = _series(
        "OK", [100, 110, 121, 133], [20, 24, 30, 40], [18, 22, 28, 38], 100.0, 10.0, 50.0, 5.0
    )
    universe = {"OK": (good, 60.0), "BAD": (empty, 10.0)}
    ranked = rank_compounders(universe, top_n=5)
    assert [c.symbol for c in ranked] == ["OK"]


def test_profitable_compounder_penalizes_dilution():
    from datetime import date, datetime

    rev = [100, 110, 121, 133]
    ni = [20, 24, 30, 40]
    fcf = [18, 22, 28, 38]
    eps = 5.0
    eq = 100.0
    debt = 10.0

    flat_records = _series("FLAT", rev, ni, fcf, eq, debt, 50.0, eps)

    dil_shares = [50.0, 52.0, 55.0, 58.0]
    dil_records = []
    for i, year in enumerate((2020, 2021, 2022, 2023)):
        dil_records.append(
            FundamentalRecord(
                symbol="DIL",
                market="us",
                period_end=date(year, 12, 31),
                asof_ts=datetime(year + 1, 3, 1),
                revenue=rev[i],
                net_income=ni[i],
                free_cash_flow=fcf[i],
                total_equity=eq,
                total_debt=debt,
                shares_out=dil_shares[i],
                eps=eps,
            )
        )

    universe = {"FLAT": (flat_records, 60.0), "DIL": (dil_records, 60.0)}
    scores = score_archetypes(universe)
    flat_score = scores["FLAT"]["profitable_compounder"].score
    dil_score = scores["DIL"]["profitable_compounder"].score
    assert dil_score < flat_score, (
        f"Expected DIL ({dil_score:.2f}) < FLAT ({flat_score:.2f}), but dilution was not penalized"
    )
