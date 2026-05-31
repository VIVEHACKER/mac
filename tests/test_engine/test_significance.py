"""Tests for engine.significance — Deflated Sharpe Ratio + block bootstrap.

Math reference: Bailey & López de Prado (2012, 2014). The Probabilistic Sharpe
Ratio (PSR) and Deflated Sharpe Ratio (DSR) close the gap left by the crude
Bonferroni binomial test flagged in MASTER-REPORT-2008-2026.md.

The low-level ``*_from_stats`` helpers take pre-computed moments so the exact
closed-form values can be asserted in isolation; the return-based wrappers are
cross-checked against scipy where available.
"""

from __future__ import annotations

import math

import pytest

from engine.significance import (
    BootstrapResult,
    block_bootstrap_sharpe,
    deflated_sharpe_ratio,
    dsr_from_stats,
    expected_max_sharpe,
    minimum_track_record_length,
    mintrl_from_stats,
    normal_cdf,
    normal_ppf,
    per_period_sharpe,
    probabilistic_sharpe_ratio,
    psr_from_stats,
    sample_kurtosis,
    sample_skewness,
    sampling_sharpe_variance,
)

EULER_MASCHERONI = 0.5772156649015329


# --------------------------------------------------------------------------- #
# normal_cdf / normal_ppf                                                      #
# --------------------------------------------------------------------------- #
def test_normal_cdf_known_points() -> None:
    assert normal_cdf(0.0) == pytest.approx(0.5, abs=1e-12)
    assert normal_cdf(1.0) == pytest.approx(0.8413447460685429, abs=1e-9)
    assert normal_cdf(1.959963984540054) == pytest.approx(0.975, abs=1e-9)
    assert normal_cdf(-1.959963984540054) == pytest.approx(0.025, abs=1e-9)


def test_normal_ppf_known_points() -> None:
    assert normal_ppf(0.5) == pytest.approx(0.0, abs=1e-9)
    assert normal_ppf(0.975) == pytest.approx(1.959963984540054, abs=1e-6)
    assert normal_ppf(0.95) == pytest.approx(1.6448536269514722, abs=1e-6)
    assert normal_ppf(0.8413447460685429) == pytest.approx(1.0, abs=1e-6)


def test_normal_ppf_is_inverse_of_cdf() -> None:
    for x in (-2.5, -0.7, 0.0, 0.3, 1.4, 2.8):
        assert normal_ppf(normal_cdf(x)) == pytest.approx(x, abs=1e-6)


def test_normal_ppf_rejects_out_of_range() -> None:
    with pytest.raises(ValueError):
        normal_ppf(0.0)
    with pytest.raises(ValueError):
        normal_ppf(1.0)


# --------------------------------------------------------------------------- #
# moments                                                                      #
# --------------------------------------------------------------------------- #
def test_sample_skewness_symmetric_is_zero() -> None:
    assert sample_skewness([1, 2, 3, 4, 5]) == pytest.approx(0.0, abs=1e-12)


def test_sample_kurtosis_uniform_discrete() -> None:
    # [1..5]: m2 = 2, m4 = 6.8 -> non-excess kurtosis = 6.8 / 4 = 1.7
    assert sample_kurtosis([1, 2, 3, 4, 5]) == pytest.approx(1.7, abs=1e-12)


def test_sample_skewness_right_skewed_positive() -> None:
    assert sample_skewness([1, 1, 1, 1, 10]) > 0.0


def test_moments_match_scipy() -> None:
    scipy_stats = pytest.importorskip("scipy.stats")
    data = [0.01, -0.02, 0.03, 0.005, -0.011, 0.04, -0.003, 0.018, -0.027, 0.009]
    assert sample_skewness(data) == pytest.approx(
        float(scipy_stats.skew(data, bias=True)), abs=1e-9
    )
    assert sample_kurtosis(data) == pytest.approx(
        float(scipy_stats.kurtosis(data, bias=True, fisher=False)), abs=1e-9
    )


# --------------------------------------------------------------------------- #
# sharpe                                                                       #
# --------------------------------------------------------------------------- #
def test_per_period_sharpe_basic() -> None:
    data = [0.01, 0.02, 0.0, 0.03, -0.01]
    mean = sum(data) / len(data)
    var = sum((x - mean) ** 2 for x in data) / (len(data) - 1)
    expected = mean / math.sqrt(var)
    assert per_period_sharpe(data) == pytest.approx(expected, abs=1e-12)


def test_per_period_sharpe_zero_variance_returns_zero() -> None:
    assert per_period_sharpe([0.01, 0.01, 0.01]) == 0.0


# --------------------------------------------------------------------------- #
# PSR (from stats, exact closed form)                                         #
# --------------------------------------------------------------------------- #
def test_psr_from_stats_minimum_kurtosis_reduces_to_phi() -> None:
    # The Mertens/Lo/Bailey variance is 1 - skew*SR + ((kurt-1)/4)*SR^2.
    # Only at the theoretical minimum kurtosis (kurt=1, two-point dist) and
    # skew=0 does the denominator collapse to 1 -> PSR = Phi(SR * sqrt(n-1)).
    # SR=0.1, n=101 -> Phi(0.1 * 10) = Phi(1.0) = 0.84134...
    psr = psr_from_stats(observed_sr=0.1, benchmark_sr=0.0, n=101, skew=0.0, kurt=1.0)
    assert psr == pytest.approx(0.8413447460685429, abs=1e-9)


def test_psr_from_stats_normal_keeps_excess_term() -> None:
    # Normal returns (kurt=3) keep the (kurt-1)/4 = 0.5 term: denom = 1 + 0.5*SR^2.
    sr, n = 0.1, 101
    denom = 1.0 + 0.5 * sr**2
    expected = normal_cdf(sr * math.sqrt(n - 1) / math.sqrt(denom))
    assert psr_from_stats(sr, 0.0, n, 0.0, 3.0) == pytest.approx(expected, abs=1e-12)


def test_psr_from_stats_negative_skew_lowers_confidence() -> None:
    base = psr_from_stats(0.1, 0.0, 101, 0.0, 3.0)
    neg_skew = psr_from_stats(0.1, 0.0, 101, -1.0, 3.0)
    fat_tails = psr_from_stats(0.1, 0.0, 101, 0.0, 8.0)
    # negative skew and fat tails both inflate the denominator -> lower PSR
    assert neg_skew < base
    assert fat_tails < base


def test_probabilistic_sharpe_ratio_wrapper_matches_from_stats() -> None:
    data = [0.01, -0.02, 0.03, 0.005, -0.011, 0.04, -0.003, 0.018, -0.027, 0.009] * 5
    sr = per_period_sharpe(data)
    expected = psr_from_stats(sr, 0.0, len(data), sample_skewness(data), sample_kurtosis(data))
    assert probabilistic_sharpe_ratio(data) == pytest.approx(expected, abs=1e-12)


# --------------------------------------------------------------------------- #
# expected max sharpe / DSR                                                    #
# --------------------------------------------------------------------------- #
def test_expected_max_sharpe_zero_variance_is_zero() -> None:
    assert expected_max_sharpe(n_trials=100, trial_sr_variance=0.0) == 0.0


def test_expected_max_sharpe_single_trial_is_zero() -> None:
    # The max of one draw from a zero-mean null has expected value 0,
    # so a single trial implies no deflation (DSR == PSR).
    assert expected_max_sharpe(n_trials=1, trial_sr_variance=0.04) == 0.0


def test_expected_max_sharpe_rejects_zero_trials() -> None:
    with pytest.raises(ValueError):
        expected_max_sharpe(n_trials=0, trial_sr_variance=0.04)


def test_expected_max_sharpe_matches_gumbel_formula() -> None:
    v = 0.04  # sr std = 0.2
    n = 50
    expected = math.sqrt(v) * (
        (1 - EULER_MASCHERONI) * normal_ppf(1 - 1 / n)
        + EULER_MASCHERONI * normal_ppf(1 - 1 / (n * math.e))
    )
    assert expected_max_sharpe(n, v) == pytest.approx(expected, abs=1e-9)


def test_expected_max_sharpe_grows_with_trials() -> None:
    v = 0.04
    assert expected_max_sharpe(10, v) < expected_max_sharpe(100, v) < expected_max_sharpe(1000, v)


def test_dsr_from_stats_reduces_to_psr_when_no_variance() -> None:
    # zero trial variance -> sr_star = 0 -> DSR == PSR vs benchmark 0
    dsr = dsr_from_stats(
        observed_sr=0.1, n=101, skew=0.0, kurt=3.0, n_trials=50, trial_sr_variance=0.0
    )
    psr = psr_from_stats(0.1, 0.0, 101, 0.0, 3.0)
    assert dsr == pytest.approx(psr, abs=1e-12)


def test_dsr_decreases_with_more_trials() -> None:
    # Pass args explicitly (a mixed int/float **kwargs dict infers dict[str, float],
    # which makes mypy reject the int `n`).
    dsr_few = dsr_from_stats(
        observed_sr=0.12, n=200, skew=-0.3, kurt=5.0, n_trials=5, trial_sr_variance=0.01
    )
    dsr_many = dsr_from_stats(
        observed_sr=0.12, n=200, skew=-0.3, kurt=5.0, n_trials=500, trial_sr_variance=0.01
    )
    assert dsr_many < dsr_few


def test_deflated_sharpe_ratio_wrapper_matches_from_stats() -> None:
    data = [0.012, -0.008, 0.02, 0.004, -0.015, 0.03, -0.002, 0.011, -0.02, 0.007] * 10
    sr = per_period_sharpe(data)
    expected = dsr_from_stats(
        observed_sr=sr,
        n=len(data),
        skew=sample_skewness(data),
        kurt=sample_kurtosis(data),
        n_trials=39,
        trial_sr_variance=0.005,
    )
    assert deflated_sharpe_ratio(data, n_trials=39, trial_sr_variance=0.005) == pytest.approx(
        expected, abs=1e-12
    )


# --------------------------------------------------------------------------- #
# minimum track record length                                                 #
# --------------------------------------------------------------------------- #
def test_mintrl_from_stats_minimum_kurtosis_closed_form() -> None:
    # kurt=1, skew=0 -> denom collapses to 1 -> MinTRL = 1 + (z_0.95 / SR)^2
    sr = 0.1
    expected = 1.0 + (normal_ppf(0.95) / sr) ** 2
    assert mintrl_from_stats(
        observed_sr=sr, benchmark_sr=0.0, skew=0.0, kurt=1.0, target_prob=0.95
    ) == pytest.approx(expected, abs=1e-9)


def test_mintrl_from_stats_normal_keeps_excess_term() -> None:
    # Normal returns (kurt=3): MinTRL = 1 + (1 + 0.5*SR^2) * (z / SR)^2
    sr = 0.1
    expected = 1.0 + (1.0 + 0.5 * sr**2) * (normal_ppf(0.95) / sr) ** 2
    assert mintrl_from_stats(
        observed_sr=sr, benchmark_sr=0.0, skew=0.0, kurt=3.0, target_prob=0.95
    ) == pytest.approx(expected, abs=1e-9)


def test_mintrl_infinite_when_sr_below_benchmark() -> None:
    assert mintrl_from_stats(0.05, 0.10, 0.0, 3.0, 0.95) == math.inf


def test_minimum_track_record_length_wrapper() -> None:
    data = [0.012, -0.008, 0.02, 0.004, -0.015, 0.03, -0.002, 0.011, -0.02, 0.007] * 10
    sr = per_period_sharpe(data)
    expected = mintrl_from_stats(sr, 0.0, sample_skewness(data), sample_kurtosis(data), 0.95)
    assert minimum_track_record_length(data, target_prob=0.95) == pytest.approx(expected, abs=1e-9)


# --------------------------------------------------------------------------- #
# block bootstrap                                                             #
# --------------------------------------------------------------------------- #
def test_block_bootstrap_is_reproducible_with_seed() -> None:
    data = [0.01, -0.02, 0.03, 0.005, -0.011, 0.04, -0.003, 0.018, -0.027, 0.009] * 20
    a = block_bootstrap_sharpe(data, n_boot=500, block_size=5, seed=42)
    b = block_bootstrap_sharpe(data, n_boot=500, block_size=5, seed=42)
    assert a == b
    assert isinstance(a, BootstrapResult)


def test_block_bootstrap_strong_positive_series_significant() -> None:
    # strong positive drift, low noise -> bootstrap Sharpe distribution well above 0
    data = [0.02, 0.018, 0.022, 0.019, 0.021, 0.017, 0.023, 0.02] * 30
    res = block_bootstrap_sharpe(data, n_boot=2000, block_size=4, seed=1)
    assert res.prob_sharpe_gt_zero > 0.99
    assert res.p_value_null < 0.01
    assert res.ci_low > 0.0
    assert res.ci_low < res.ci_high


def test_block_bootstrap_zero_mean_series_not_significant() -> None:
    data = [0.02, -0.02, 0.015, -0.015, 0.01, -0.01, 0.025, -0.025] * 30
    res = block_bootstrap_sharpe(data, n_boot=2000, block_size=4, seed=7)
    assert res.prob_sharpe_gt_zero < 0.8
    # near-zero observed Sharpe -> null p-value is large (not surprising under H0)
    assert res.p_value_null > 0.2


def test_block_bootstrap_null_pvalue_recenters_to_half_on_zero_mean() -> None:
    # For a (near) zero-mean series the observed Sharpe is ~0, so after
    # recentering, ~half the null resamples exceed it -> p_value_null ~ 0.5.
    data = [0.02, -0.02, 0.015, -0.015, 0.01, -0.01, 0.025, -0.025] * 30
    res = block_bootstrap_sharpe(data, n_boot=3000, block_size=4, seed=11)
    assert 0.30 <= res.p_value_null <= 0.70


def test_block_bootstrap_ci_brackets_point_estimate() -> None:
    data = [0.012, -0.008, 0.02, 0.004, -0.015, 0.03, -0.002, 0.011, -0.02, 0.007] * 25
    point = per_period_sharpe(data) * math.sqrt(252)
    res = block_bootstrap_sharpe(data, n_boot=3000, block_size=5, seed=3, periods_per_year=252)
    assert res.ci_low <= point <= res.ci_high


# --------------------------------------------------------------------------- #
# sampling sharpe variance                                                    #
# --------------------------------------------------------------------------- #
def test_sampling_sharpe_variance_closed_form() -> None:
    data = [0.012, -0.008, 0.02, 0.004, -0.015, 0.03, -0.002, 0.011, -0.02, 0.007] * 10
    sr = per_period_sharpe(data)
    expected = (1.0 + 0.5 * sr**2) / (len(data) - 1)
    assert sampling_sharpe_variance(data) == pytest.approx(expected, abs=1e-15)


def test_sampling_sharpe_variance_shrinks_with_n() -> None:
    short = [0.01, -0.005, 0.02, -0.01, 0.015] * 4
    long = short * 10
    assert sampling_sharpe_variance(long) < sampling_sharpe_variance(short)
