from __future__ import annotations

import math

from engine.ic import average_ranks, ic_stats, partial_spearman, spearman


def test_average_ranks_handles_ties() -> None:
    # values [10, 20, 20, 40] -> ranks 1, 2.5, 2.5, 4
    assert average_ranks([10.0, 20.0, 20.0, 40.0]) == [1.0, 2.5, 2.5, 4.0]


def test_spearman_perfect_monotonic_is_one() -> None:
    xs = [1.0, 2.0, 3.0, 4.0, 5.0]
    ys = [10.0, 20.0, 30.0, 40.0, 50.0]
    ic = spearman(xs, ys)
    assert ic is not None and abs(ic - 1.0) < 1e-9


def test_spearman_perfect_inverse_is_minus_one() -> None:
    xs = [1.0, 2.0, 3.0, 4.0, 5.0]
    ys = [50.0, 40.0, 30.0, 20.0, 10.0]
    ic = spearman(xs, ys)
    assert ic is not None and abs(ic + 1.0) < 1e-9


def test_spearman_none_on_degenerate() -> None:
    assert spearman([1.0, 2.0], [1.0, 2.0]) is None  # n < 3
    assert spearman([1.0, 1.0, 1.0], [1.0, 2.0, 3.0]) is None  # zero variance in x


def test_partial_spearman_removes_confounder() -> None:
    # x and y each track z closely but their residual swaps are INDEPENDENT (x swaps the first pair,
    # y swaps the second). Their ~0.89 raw correlation is entirely the shared z structure, so the
    # partial correlation controlling for z collapses to ~0.
    z = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    x = [2.0, 1.0, 3.0, 4.0, 5.0, 6.0]
    y = [1.0, 2.0, 4.0, 3.0, 5.0, 6.0]
    raw = spearman(x, y)
    pic = partial_spearman(x, y, z)
    assert raw is not None and raw > 0.8  # strongly correlated before controlling for z
    assert pic is not None and abs(pic) < 0.1  # ...but the correlation is the z confounder


def test_partial_spearman_keeps_independent_signal() -> None:
    # x predicts y independently of z (z is unrelated) -> partial stays high.
    z = [5.0, 1.0, 4.0, 2.0, 3.0, 6.0]
    x = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    y = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    pic = partial_spearman(x, y, z)
    assert pic is not None and pic > 0.9


def test_ic_stats_mean_t_and_positive_fraction() -> None:
    ics = [0.10, 0.20, -0.05, 0.15, 0.10]
    s = ic_stats(ics)
    assert s.n == 5
    assert s.mean is not None and abs(s.mean - 0.10) < 1e-9
    assert s.positive == 4
    # naive t = mean / (std/sqrt(n)); just assert it is finite and positive here
    assert s.t_stat is not None and s.t_stat > 0
    # effective-N haircut makes |t| no larger than the naive t for overlap < 1
    eff = ic_stats(ics, eff_n=2.0)
    assert eff.t_stat is not None and abs(eff.t_stat) <= abs(s.t_stat) + 1e-9


def test_ic_stats_empty_is_safe() -> None:
    s = ic_stats([])
    assert s.n == 0 and s.mean is None and s.t_stat is None


def test_ic_stats_single_value_no_t() -> None:
    s = ic_stats([0.1])
    assert s.n == 1 and s.mean == 0.1 and s.t_stat is None  # std undefined for n<2


def test_spearman_matches_known_value() -> None:
    # Spearman of a small set with one inversion, checked against the rank-difference formula.
    xs = [1.0, 2.0, 3.0, 4.0, 5.0]
    ys = [1.0, 2.0, 3.0, 5.0, 4.0]  # last two swapped
    # d^2 sum = 0+0+0+1+1 = 2; rho = 1 - 6*2/(5*(25-1)) = 1 - 12/120 = 0.9
    ic = spearman(xs, ys)
    assert ic is not None and abs(ic - 0.9) < 1e-9


def test_no_nan_propagation() -> None:
    s = ic_stats([0.1, 0.2, 0.3])
    assert s.t_stat is not None and not math.isnan(s.t_stat)
