from __future__ import annotations

from datetime import date, datetime

from data.models import FundamentalRecord
from engine.compounder import (
    ArchetypeScore,
    compute_metrics,
    rank_compounders,
    score_archetypes,
)
from engine.significance import normal_cdf


def _series(symbol, rev, ni, fcf, eq, debt, sh, eps):
    """4 annual records 2020-2023 with constant per-field values except revenue ramp.

    gross_profit (0.4*revenue) and total_assets (equity+debt+100) are populated so that
    gross_profitability is computable for full-data names (profitable_compounder weights it)."""
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
                total_assets=eq + debt + 100.0,
                total_equity=eq,
                total_debt=debt,
                shares_out=sh,
                eps=eps,
                gross_profit=0.4 * rev[i],
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


from engine.compounder import CandidateScore  # noqa: E402


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


# ── FIX A: z-score winsorization ──────────────────────────────────────────────


def _tight_recs(symbol: str, ni_base: float) -> list[FundamentalRecord]:
    """4 records with a small linear ni ramp (revenue=100 constant).

    Used to build a cluster of 'normal' names so that an extreme outlier's
    cross-sectional z genuinely exceeds 3.0 (requires ≥19 tight peers).
    """
    return [
        FundamentalRecord(
            symbol=symbol,
            market="us",
            period_end=date(year, 12, 31),
            asof_ts=datetime(year + 1, 3, 1),
            revenue=100.0,
            net_income=ni_base + float(i),
            free_cash_flow=None,
            total_equity=100.0,
            total_debt=0.0,
            shares_out=50.0,
            eps=None,
        )
        for i, year in enumerate((2020, 2021, 2022, 2023))
    ]


def test_zscore_clipping_caps_single_metric_score() -> None:
    """FIX A: 19 tight names + 1 extreme outlier "OUT" yields a raw cross-sectional
    z ≈ 4.36 (> 3.0) on margin_trend.  With Z_CLIP = 3.0 every component stored in
    ArchetypeScore.components must be ≤ 3.0, and OUT's score across all archetypes
    must be ≤ normal_cdf(3.0)*100 + 0.1.

    Without clipping margin_trend_signed ≈ 4.36 → score > 99.865; with clipping
    the component is pinned at 3.0 and the score is ≤ 99.865 + tolerance.
    """
    # 19 tight names: ni ramps gently (1+0.01*k, 2+0.01*k, 3+0.01*k, 4+0.01*k)
    universe: dict = {}
    for k in range(19):
        sym = f"N{k:02d}"
        universe[sym] = (_tight_recs(sym, 1.0 + 0.01 * k), 10.0)

    # OUT: massive ni ramp → margin_trend z ≈ 4.36 before clipping
    out_recs = [
        FundamentalRecord(
            symbol="OUT",
            market="us",
            period_end=date(year, 12, 31),
            asof_ts=datetime(year + 1, 3, 1),
            revenue=100.0,
            net_income=1_000_000.0 * (i + 1),
            free_cash_flow=None,
            total_equity=100.0,
            total_debt=0.0,
            shares_out=50.0,
            eps=None,
        )
        for i, year in enumerate((2020, 2021, 2022, 2023))
    ]
    universe["OUT"] = (out_recs, 10.0)

    scores = score_archetypes(universe)
    upper_bound = normal_cdf(3.0) * 100.0 + 0.1  # ≈ 99.965

    out_scores = scores.get("OUT", {})
    assert out_scores, "OUT must appear in score_archetypes output"

    for arch, arch_score in out_scores.items():
        # All stored component (signed-z) values must be clipped to ≤ 3.0
        for metric_key, comp_val in arch_score.components.items():
            assert comp_val <= 3.0 + 1e-9, (
                f"arch={arch} metric={metric_key}: component {comp_val:.6f} > Z_CLIP=3.0 "
                "— winsorization is not applied"
            )
        # The final score must also be ≤ normal_cdf(3.0)*100 + tolerance
        assert arch_score.score <= upper_bound, (
            f"arch={arch}: OUT score {arch_score.score:.4f} exceeds winsorize bound "
            f"{upper_bound:.4f} — z-score clipping is not in effect"
        )


# ── FIX B: minimum-coverage gate ──────────────────────────────────────────────


def _sparse_minimal_record(symbol: str) -> list[FundamentalRecord]:
    """4 records with ONLY total_equity + shares_out: revenue/ni/fcf/eps all None.

    This yields < 5 non-None computed metrics (only pb and share_growth can
    compute; most others need revenue/ni/fcf).
    """
    records = []
    for i, year in enumerate((2020, 2021, 2022, 2023)):
        records.append(
            FundamentalRecord(
                symbol=symbol,
                market="us",
                period_end=date(year, 12, 31),
                asof_ts=datetime(year + 1, 3, 1),
                revenue=None,
                net_income=None,
                free_cash_flow=None,
                total_equity=1.0 * (i + 1),  # contrived extreme equity drop→pb spike
                total_debt=None,
                shares_out=50.0,
                eps=None,
            )
        )
    return records


# ── NEW: per-archetype coverage gate ─────────────────────────────────────────


def _two_record_series(symbol: str) -> list[FundamentalRecord]:
    """2 annual records (2022-2023) with revenue/ni/fcf/equity present.

    2 records → revenue_cagr(3y) returns None (no record ~3y back).
    revenue_growth_acceleration also returns None (needs records at 1y and 2y).
    margin_trend CAN compute (needs ≥2 net_margin values).
    So hypergrowth_disruptor has only 1 of 3 weighted metrics present.
    """
    out = []
    for i, year in enumerate((2022, 2023)):
        out.append(
            FundamentalRecord(
                symbol=symbol,
                market="us",
                period_end=date(year, 12, 31),
                asof_ts=datetime(year + 1, 3, 1),
                revenue=100.0 * (1.5**i),
                net_income=10.0 * (1.3**i),
                free_cash_flow=8.0 * (1.3**i),
                total_equity=80.0,
                total_debt=10.0,
                shares_out=50.0,
                eps=2.0,
            )
        )
    return out


def test_archetype_coverage_field_populated() -> None:
    """A full-data name (4 records) should have profitable_compounder coverage == 1.0."""
    full = _series(
        "FULL",
        [100, 120, 150, 190],
        [10, 14, 20, 30],
        [8, 12, 18, 28],
        100.0,
        20.0,
        50.0,
        3.0,
    )
    peer1 = _series(
        "P1", [100, 110, 121, 133], [20, 24, 30, 40], [18, 22, 28, 38], 100.0, 10.0, 50.0, 5.0
    )
    peer2 = _series("P2", [100, 103, 106, 109], [5, 5, 5, 5], [4, 4, 4, 4], 80.0, 15.0, 45.0, 1.0)
    universe = {"FULL": (full, 60.0), "P1": (peer1, 50.0), "P2": (peer2, 40.0)}
    scores = score_archetypes(universe)
    pc = scores["FULL"]["profitable_compounder"]
    assert hasattr(pc, "coverage"), "ArchetypeScore must have a coverage field"
    import pytest

    assert pc.coverage == pytest.approx(1.0), (
        f"profitable_compounder has 5 weighted metrics; full-data name should have coverage=1.0, got {pc.coverage}"
    )


def test_single_metric_archetype_is_disqualified() -> None:
    """THIN has only margin_trend among hypergrowth's 3 weighted metrics.
    Its hypergrowth coverage < 0.5 and score must be 0.0."""
    import pytest

    thin = _two_record_series("THIN")
    peer1 = _series(
        "PA", [100, 160, 256, 410], [-5, -3, 0, 5], [-4, -2, 1, 6], 50.0, 0.0, 40.0, 0.5
    )
    peer2 = _series(
        "PB", [100, 110, 121, 133], [20, 24, 30, 40], [18, 22, 28, 38], 100.0, 10.0, 50.0, 5.0
    )
    universe = {"THIN": (thin, 30.0), "PA": (peer1, 30.0), "PB": (peer2, 50.0)}

    # Verify THIN's hypergrowth metrics: only margin_trend present
    thin_metrics = compute_metrics(thin, price=30.0)
    assert thin_metrics.get("revenue_cagr") is None, "revenue_cagr must be None for 2-record series"
    assert thin_metrics.get("revenue_growth_acceleration") is None, (
        "revenue_growth_acceleration must be None for 2-record series"
    )
    assert thin_metrics.get("margin_trend") is not None, (
        "margin_trend must be computable from 2 records"
    )

    scores = score_archetypes(universe)
    hg = scores["THIN"]["hypergrowth_disruptor"]
    assert hg.coverage < 0.5, (
        f"THIN hypergrowth coverage should be < 0.5 (only 1/3 metrics present), got {hg.coverage:.3f}"
    )
    assert hg.score == pytest.approx(0.0), (
        f"THIN hypergrowth score should be 0.0 (disqualified), got {hg.score:.4f}"
    )


def test_rank_excludes_name_with_no_covered_archetype() -> None:
    """A name whose present metrics give every archetype coverage < 0.5 is excluded
    from rank_compounders, while a full-data name is included."""
    # Build a name that fails coverage on ALL three archetypes.
    # Use a 2-record series but with None revenue/ni to strip away even more metrics.
    # We need: profitable_compounder coverage < 0.5 (needs roic,fcf_margin,margin_trend,revenue_cagr,share_growth)
    #          hypergrowth_disruptor coverage < 0.5 (needs revenue_cagr, rev_growth_accel, margin_trend)
    #          value_turnaround coverage < 0.5 (needs pfcf, pb, margin_trend, fcf_margin)
    #
    # Strategy: provide ONLY shares_out + total_equity (no revenue/ni/fcf/price-dependent)
    # so that only pb and share_growth can compute (~2 present metrics total),
    # meaning each archetype has very low coverage.
    nocov_records = []
    for _i, year in enumerate((2022, 2023)):
        nocov_records.append(
            FundamentalRecord(
                symbol="NOCOV",
                market="us",
                period_end=date(year, 12, 31),
                asof_ts=datetime(year + 1, 3, 1),
                revenue=None,
                net_income=None,
                free_cash_flow=None,
                total_equity=100.0,
                total_debt=None,
                shares_out=50.0,
                eps=None,
            )
        )

    full = _series(
        "GOOD",
        [100, 120, 150, 190],
        [10, 14, 20, 30],
        [8, 12, 18, 28],
        100.0,
        20.0,
        50.0,
        3.0,
    )
    peer = _series(
        "PEER", [100, 110, 121, 133], [20, 24, 30, 40], [18, 22, 28, 38], 100.0, 10.0, 50.0, 5.0
    )
    universe = {
        "NOCOV": (nocov_records, 10.0),
        "GOOD": (full, 60.0),
        "PEER": (peer, 50.0),
    }

    ranked = rank_compounders(universe, top_n=10)
    symbols = [c.symbol for c in ranked]
    assert "NOCOV" not in symbols, (
        "NOCOV has no archetype meeting coverage >= 0.5 and must be excluded from ranking"
    )
    assert "GOOD" in symbols, "GOOD (full data) must appear in ranking"


def test_coverage_gate_excludes_sparse_name() -> None:
    """FIX B: A name with fewer than MIN_PRESENT_METRICS non-None metrics must
    be excluded from rank_compounders even if it has an extreme value."""
    full1 = _series(
        "F1", [100, 120, 150, 190], [10, 14, 20, 30], [8, 12, 18, 28], 100.0, 20.0, 50.0, 3.0
    )
    full2 = _series(
        "F2", [100, 110, 121, 133], [20, 24, 30, 40], [18, 22, 28, 38], 100.0, 10.0, 50.0, 5.0
    )
    sparse = _sparse_minimal_record("SPARSE")

    universe = {
        "F1": (full1, 60.0),
        "F2": (full2, 80.0),
        "SPARSE": (sparse, 1.0),
    }

    # Verify SPARSE actually has fewer than 5 non-None metrics
    sparse_metrics = compute_metrics(sparse, price=1.0)
    present_count = sum(1 for v in sparse_metrics.values() if v is not None)
    assert present_count < 5, (
        f"Fixture error: SPARSE has {present_count} non-None metrics, expected < 5"
    )

    ranked = rank_compounders(universe, top_n=5)
    symbols = [c.symbol for c in ranked]
    assert "SPARSE" not in symbols, (
        f"SPARSE (only {present_count} non-None metrics) should be excluded but appeared in ranking"
    )
    assert "F1" in symbols, "F1 should be included in ranking"
    assert "F2" in symbols, "F2 should be included in ranking"


from engine.compounder import SECTOR_INVALID_METRICS  # noqa: E402


def test_sector_invalid_metrics_defines_financials():
    assert "fcf_margin" in SECTOR_INVALID_METRICS["financials"]
    assert "fcf_conversion" in SECTOR_INVALID_METRICS["financials"]
    assert "pfcf" in SECTOR_INVALID_METRICS["financials"]


def test_financial_sector_excludes_fcf_from_scoring():
    # A "bank" with a huge FCF artifact vs a peer; with sectors, FCF is excluded.
    bank = _series(
        "BANKX",
        [100, 110, 121, 133],
        [20, 24, 30, 40],
        [900, 900, 900, 900],
        100.0,
        10.0,
        50.0,
        5.0,
    )  # absurd FCF (artifact)
    peer = _series(
        "PEER", [100, 110, 121, 133], [20, 24, 30, 40], [18, 22, 28, 38], 100.0, 10.0, 50.0, 5.0
    )
    universe = {"BANKX": (bank, 60.0), "PEER": (peer, 60.0)}

    no_sector = score_archetypes(universe)
    with_sector = score_archetypes(universe, sectors={"BANKX": "financials", "PEER": "tech"})

    # Without sector info, the bank's profitable-compounder score uses its huge FCF margin.
    # With sector info, FCF metrics are nulled for the bank, so its components must NOT
    # include fcf_margin, and PEER (non-financial) is unaffected.
    assert "fcf_margin" in no_sector["BANKX"]["profitable_compounder"].components
    assert "fcf_margin" not in with_sector["BANKX"]["profitable_compounder"].components
    assert "fcf_margin" in with_sector["PEER"]["profitable_compounder"].components


def test_score_archetypes_sectors_default_none_unchanged():
    q = _series(
        "QLT", [100, 110, 121, 133], [20, 24, 30, 40], [18, 22, 28, 38], 100.0, 10.0, 50.0, 5.0
    )
    j = _series("JNK", [100, 101, 102, 103], [1, 1, 1, 1], [0, 0, 0, 0], 100.0, 250.0, 60.0, 0.1)
    universe = {"QLT": (q, 60.0), "JNK": (j, 5.0)}
    assert score_archetypes(universe) == score_archetypes(universe, sectors=None)
    assert score_archetypes(universe) == score_archetypes(universe, sectors={})


from engine.compounder import _WEIGHTS  # noqa: E402


def test_gross_profitability_not_weighted_pending_heldout_validation():
    """The gross_profitability ADD was REVERTED after the held-out-time OOS (action 3b) failed
    to confirm it: held-out gross_quality IC -0.014 (1/3 windows, size-partial -0.020) — the
    in-sample +0.04 did NOT generalize to 2020-2022. Validate-before-trust: an unconfirmed
    signal must not stay weighted in the live funnel. It remains MEASURED (compute_metrics) for
    diagnostics, and sector-nulled for financials, but is NOT a _WEIGHTS scoring input.
    The held-out-durable signal is VALUE (qarp +0.191, 3/3); net-margin quality is durably bad."""
    keys = [k for k, _ in _WEIGHTS["profitable_compounder"]]
    assert "gross_profitability" not in keys, "reverted: not held-out-validated"
    assert "market_cap" not in keys  # diagnostics-only, never weighted


def test_financials_sector_nulls_gross_metrics():
    """Banks/insurers have no COGS/gross profit -> gross metrics must be sector-nulled like FCF."""
    fin = SECTOR_INVALID_METRICS["financials"]
    assert "gross_profitability" in fin
    assert "gross_margin" in fin


def test_compute_metrics_surfaces_market_cap():
    """market_cap must be surfaced (size anchor for size-proxy check + size-neutral IC).

    It is NOT in _WEIGHTS (no scoring effect) — only available for diagnostics/validation."""
    recs = _series(
        "MC", [100, 120, 150, 190], [10, 14, 20, 30], [8, 12, 18, 28], 100.0, 20.0, 50.0, 3.0
    )
    m = compute_metrics(recs, price=60.0)
    assert "market_cap" in m
    assert m["market_cap"] == 60.0 * 50.0  # price * shares_out
    # market_cap is a diagnostic only — must NOT be a scoring weight in any archetype
    for arch in _WEIGHTS.values():
        assert "market_cap" not in [k for k, _ in arch]
