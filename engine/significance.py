"""Statistical-significance tests for backtested strategies.

Implements the Probabilistic Sharpe Ratio (PSR), the Deflated Sharpe Ratio
(DSR) and a circular block bootstrap, following Bailey & López de Prado:

  * "The Sharpe Ratio Efficient Frontier" (2012)
  * "The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest
    Overfitting and Non-Normality" (2014)

These close the gap left by the crude Bonferroni binomial test that flagged
the megacap-momentum results as *borderline* in
``out/MASTER-REPORT-2008-2026.md``: the PSR/DSR account for the actual return
distribution (skew, fat tails), the sample length, and the number of trials
that produced the selected configuration.

Pure standard library only (``math``/``statistics``/``random``) to match the
rest of ``engine/`` and avoid a hard scipy dependency. ``normal_ppf`` uses
Acklam's rational approximation (accurate to ~1.15e-9 in the central region,
refined by one Halley step).
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass

EULER_MASCHERONI = 0.5772156649015329
TRADING_DAYS = 252


# --------------------------------------------------------------------------- #
# Normal distribution helpers                                                  #
# --------------------------------------------------------------------------- #
def normal_cdf(x: float) -> float:
    """Standard normal CDF via the error function."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


# Acklam's inverse normal CDF coefficients.
_A = (
    -3.969683028665376e01,
    2.209460984245205e02,
    -2.759285104469687e02,
    1.383577518672690e02,
    -3.066479806614716e01,
    2.506628277459239e00,
)
_B = (
    -5.447609879822406e01,
    1.615858368580409e02,
    -1.556989798598866e02,
    6.680131188771972e01,
    -1.328068155288572e01,
)
_C = (
    -7.784894002430293e-03,
    -3.223964580411365e-01,
    -2.400758277161838e00,
    -2.549732539343734e00,
    4.374664141464968e00,
    2.938163982698783e00,
)
_D = (
    7.784695709041462e-03,
    3.224671290700398e-01,
    2.445134137142996e00,
    3.754408661907416e00,
)
_P_LOW = 0.02425
_P_HIGH = 1.0 - _P_LOW


def normal_ppf(p: float) -> float:
    """Inverse standard normal CDF (quantile function).

    Acklam's algorithm followed by one Halley refinement step, giving full
    double precision. Defined on the open interval ``(0, 1)``.
    """
    if not 0.0 < p < 1.0:
        raise ValueError("normal_ppf requires 0 < p < 1")

    if p < _P_LOW:
        q = math.sqrt(-2.0 * math.log(p))
        x = (((((_C[0] * q + _C[1]) * q + _C[2]) * q + _C[3]) * q + _C[4]) * q + _C[5]) / (
            (((_D[0] * q + _D[1]) * q + _D[2]) * q + _D[3]) * q + 1.0
        )
    elif p <= _P_HIGH:
        q = p - 0.5
        r = q * q
        x = (
            (((((_A[0] * r + _A[1]) * r + _A[2]) * r + _A[3]) * r + _A[4]) * r + _A[5])
            * q
            / (((((_B[0] * r + _B[1]) * r + _B[2]) * r + _B[3]) * r + _B[4]) * r + 1.0)
        )
    else:
        q = math.sqrt(-2.0 * math.log(1.0 - p))
        x = -(((((_C[0] * q + _C[1]) * q + _C[2]) * q + _C[3]) * q + _C[4]) * q + _C[5]) / (
            (((_D[0] * q + _D[1]) * q + _D[2]) * q + _D[3]) * q + 1.0
        )

    # One Halley step to polish (uses the error of the Acklam estimate).
    e = normal_cdf(x) - p
    u = e * math.sqrt(2.0 * math.pi) * math.exp(x * x / 2.0)
    x = x - u / (1.0 + x * u / 2.0)
    return x


# --------------------------------------------------------------------------- #
# Sample moments (method-of-moments / biased, as used by PSR-DSR)             #
# --------------------------------------------------------------------------- #
def _central_moments(returns: Sequence[float]) -> tuple[float, float, float, float]:
    n = len(returns)
    if n == 0:
        raise ValueError("returns must be non-empty")
    mean = math.fsum(returns) / n
    m2 = math.fsum((x - mean) ** 2 for x in returns) / n
    m3 = math.fsum((x - mean) ** 3 for x in returns) / n
    m4 = math.fsum((x - mean) ** 4 for x in returns) / n
    return mean, m2, m3, m4


def sample_skewness(returns: Sequence[float]) -> float:
    """Biased (population) skewness — the third standardized moment."""
    _, m2, m3, _ = _central_moments(returns)
    if m2 <= 0.0:
        return 0.0
    return m3 / (m2**1.5)


def sample_kurtosis(returns: Sequence[float]) -> float:
    """Biased (population) **non-excess** kurtosis (normal == 3.0)."""
    _, m2, _, m4 = _central_moments(returns)
    if m2 <= 0.0:
        return 0.0
    return m4 / (m2**2)


# --------------------------------------------------------------------------- #
# Sharpe ratio                                                                 #
# --------------------------------------------------------------------------- #
def per_period_sharpe(returns: Sequence[float], ddof: int = 1) -> float:
    """Non-annualized Sharpe ratio (per-period mean / per-period std)."""
    n = len(returns)
    if n <= ddof:
        return 0.0
    mean = math.fsum(returns) / n
    var = math.fsum((x - mean) ** 2 for x in returns) / (n - ddof)
    if var <= 0.0:
        return 0.0
    return mean / math.sqrt(var)


def sharpe_ratio(
    returns: Sequence[float], periods_per_year: int = TRADING_DAYS, ddof: int = 1
) -> float:
    """Annualized Sharpe ratio."""
    return per_period_sharpe(returns, ddof=ddof) * math.sqrt(periods_per_year)


def sampling_sharpe_variance(returns: Sequence[float], ddof: int = 1) -> float:
    """Asymptotic sampling variance of the (per-period) Sharpe estimate.

    ``Var(SR) ≈ (1 + 0.5 * SR^2) / (n - 1)`` (Lo 2002, IID-normal form). This
    is the variance a single strategy's Sharpe estimate carries from estimation
    noise alone. As a Deflated-Sharpe trial variance ``V`` it is the *lower
    bound* — it assumes every trial is the same strategy re-estimated, so it
    applies the least selection-bias deflation (optimistic). Contrast with a
    cross-configuration or cross-regime dispersion estimate, which is larger.
    """
    sr = per_period_sharpe(returns, ddof=ddof)
    n = len(returns)
    if n <= 1:
        return 0.0
    return (1.0 + 0.5 * sr**2) / (n - 1)


# --------------------------------------------------------------------------- #
# Probabilistic Sharpe Ratio                                                   #
# --------------------------------------------------------------------------- #
def psr_from_stats(
    observed_sr: float, benchmark_sr: float, n: int, skew: float, kurt: float
) -> float:
    """PSR from pre-computed statistics.

    ``observed_sr``/``benchmark_sr`` are *per-period* (non-annualized) Sharpe
    ratios, ``n`` the number of returns, ``kurt`` the non-excess kurtosis.

    PSR = Phi( (SR - SR*) * sqrt(n - 1)
               / sqrt(1 - skew*SR + ((kurt - 1) / 4) * SR^2) )

    Convention note: ``kurt`` is NON-excess kurtosis (normal = 3). The
    ``(kurt - 1)/4`` term is the canonical form — it equals the Mertens/Lo
    Sharpe-ratio variance ``SR^2/2 + (kurt - 3)/4 * SR^2`` and matches López de
    Prado's reference implementation (mlfinlab ``probabilistic_sharpe_ratio``,
    which uses ``(kurtosis - 1)/4`` with ``scipy.stats.kurtosis(fisher=False)``)
    and ``sqrt(n - 1)``. It is NOT ``(kurt - 3)/4``.
    """
    if n <= 1:
        raise ValueError("PSR requires at least 2 observations")
    denom = 1.0 - skew * observed_sr + ((kurt - 1.0) / 4.0) * observed_sr**2
    if denom <= 0.0:
        # Degenerate variance estimate; treat as no statistical confidence.
        return 0.0
    z = (observed_sr - benchmark_sr) * math.sqrt(n - 1) / math.sqrt(denom)
    return normal_cdf(z)


def probabilistic_sharpe_ratio(
    returns: Sequence[float], benchmark_sr: float = 0.0, ddof: int = 1
) -> float:
    """PSR computed directly from a per-period return series."""
    sr = per_period_sharpe(returns, ddof=ddof)
    return psr_from_stats(
        sr, benchmark_sr, len(returns), sample_skewness(returns), sample_kurtosis(returns)
    )


# --------------------------------------------------------------------------- #
# Deflated Sharpe Ratio                                                        #
# --------------------------------------------------------------------------- #
def expected_max_sharpe(n_trials: int, trial_sr_variance: float) -> float:
    """Expected maximum (per-period) Sharpe under the null of zero skill.

    SR* = sqrt(V) * [ (1 - gamma) * Z^-1(1 - 1/N)
                      + gamma * Z^-1(1 - 1/(N * e)) ]

    where ``V`` is the variance of the trial Sharpe ratios, ``N`` the number of
    trials and ``gamma`` the Euler-Mascheroni constant. ``N == 1`` (a single
    trial) implies no selection bias and returns 0; ``N >= 2`` uses the Gumbel
    approximation.
    """
    if n_trials < 1:
        raise ValueError("expected_max_sharpe requires n_trials >= 1")
    if trial_sr_variance <= 0.0 or n_trials == 1:
        # No dispersion, or a single trial (max of one draw from a zero-mean
        # null has expected value 0): no selection bias to deflate.
        return 0.0
    z1 = normal_ppf(1.0 - 1.0 / n_trials)
    z2 = normal_ppf(1.0 - 1.0 / (n_trials * math.e))
    return math.sqrt(trial_sr_variance) * ((1.0 - EULER_MASCHERONI) * z1 + EULER_MASCHERONI * z2)


def dsr_from_stats(
    observed_sr: float,
    n: int,
    skew: float,
    kurt: float,
    n_trials: int,
    trial_sr_variance: float,
) -> float:
    """Deflated Sharpe Ratio from pre-computed statistics.

    DSR = PSR(observed_sr, benchmark = expected_max_sharpe).
    """
    sr_star = expected_max_sharpe(n_trials, trial_sr_variance)
    return psr_from_stats(observed_sr, sr_star, n, skew, kurt)


def deflated_sharpe_ratio(
    returns: Sequence[float], n_trials: int, trial_sr_variance: float, ddof: int = 1
) -> float:
    """DSR computed directly from a per-period return series."""
    sr = per_period_sharpe(returns, ddof=ddof)
    return dsr_from_stats(
        observed_sr=sr,
        n=len(returns),
        skew=sample_skewness(returns),
        kurt=sample_kurtosis(returns),
        n_trials=n_trials,
        trial_sr_variance=trial_sr_variance,
    )


# --------------------------------------------------------------------------- #
# Minimum Track Record Length                                                  #
# --------------------------------------------------------------------------- #
def mintrl_from_stats(
    observed_sr: float, benchmark_sr: float, skew: float, kurt: float, target_prob: float
) -> float:
    """Minimum number of observations for PSR to exceed ``target_prob``.

    MinTRL = 1 + (1 - skew*SR + ((kurt - 1)/4) * SR^2) * (Z^-1(p) / (SR - SR*))^2
    """
    if observed_sr <= benchmark_sr:
        return math.inf
    denom = 1.0 - skew * observed_sr + ((kurt - 1.0) / 4.0) * observed_sr**2
    z = normal_ppf(target_prob)
    return 1.0 + denom * (z / (observed_sr - benchmark_sr)) ** 2


def minimum_track_record_length(
    returns: Sequence[float],
    benchmark_sr: float = 0.0,
    target_prob: float = 0.95,
    ddof: int = 1,
) -> float:
    """MinTRL computed directly from a per-period return series."""
    sr = per_period_sharpe(returns, ddof=ddof)
    return mintrl_from_stats(
        sr, benchmark_sr, sample_skewness(returns), sample_kurtosis(returns), target_prob
    )


# --------------------------------------------------------------------------- #
# Circular block bootstrap                                                     #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class BootstrapResult:
    """Distribution of the annualized Sharpe under a circular block bootstrap.

    ``prob_sharpe_gt_zero`` is a *confidence* statement (fraction of resamples
    with Sharpe > 0, drawn from the observed return distribution).

    ``p_value_null`` is a proper one-sided bootstrap p-value for H0: true Sharpe
    ≤ 0 — returns are recentered to mean 0 (the null) before resampling, and it
    reports the fraction of recentered resamples whose Sharpe ≥ the observed
    Sharpe. These answer different questions; report both.
    """

    point_sharpe: float
    mean: float
    median: float
    std: float
    ci_low: float
    ci_high: float
    prob_sharpe_gt_zero: float
    p_value_null: float
    n_boot: int
    block_size: int
    confidence: float


def _percentile(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    idx = q * (len(sorted_values) - 1)
    lo = math.floor(idx)
    hi = math.ceil(idx)
    if lo == hi:
        return sorted_values[int(idx)]
    frac = idx - lo
    return sorted_values[lo] * (1.0 - frac) + sorted_values[hi] * frac


def block_bootstrap_sharpe(
    returns: Sequence[float],
    n_boot: int = 10_000,
    block_size: int = 21,
    seed: int = 0,
    periods_per_year: int = TRADING_DAYS,
    confidence: float = 0.95,
) -> BootstrapResult:
    """Circular block bootstrap of the annualized Sharpe ratio.

    Blocks of ``block_size`` consecutive returns (with wrap-around) preserve
    short-horizon autocorrelation. Reports the bootstrap distribution and a
    two-sided ``confidence`` interval (from resampling the observed returns), a
    ``prob_sharpe_gt_zero`` confidence statement, and a proper one-sided
    ``p_value_null`` for H0: Sharpe ≤ 0 (from resampling mean-0 recentered
    returns).

    ``block_size`` should approximate the decay length of return
    autocorrelation; the caller is responsible for a sensible choice (e.g.
    ~1 month of trading days for daily data, ~1 year for monthly).
    """
    data = list(returns)
    n = len(data)
    if n < 2:
        raise ValueError("block bootstrap requires at least 2 observations")
    block_size = max(1, min(block_size, n))
    n_blocks = math.ceil(n / block_size)
    rng = random.Random(seed)
    sqrt_year = math.sqrt(periods_per_year)
    observed = per_period_sharpe(data) * sqrt_year

    def resampled_sharpes(arr: list[float]) -> list[float]:
        out: list[float] = []
        for _ in range(n_boot):
            sample: list[float] = []
            for _ in range(n_blocks):
                start = rng.randrange(n)
                for k in range(block_size):
                    sample.append(arr[(start + k) % n])
            del sample[n:]
            out.append(per_period_sharpe(sample) * sqrt_year)
        return out

    samples = sorted(resampled_sharpes(data))
    mean = math.fsum(samples) / len(samples)
    median = _percentile(samples, 0.5)
    var = math.fsum((s - mean) ** 2 for s in samples) / len(samples)
    alpha = (1.0 - confidence) / 2.0
    gt_zero = sum(1 for s in samples if s > 0.0)

    # Proper null p-value: recenter to mean 0 (the Sharpe = 0 null), then count
    # recentered resamples at least as extreme as the observed Sharpe.
    data_mean = math.fsum(data) / n
    null_data = [x - data_mean for x in data]
    null_samples = resampled_sharpes(null_data)
    at_least_observed = sum(1 for s in null_samples if s >= observed)

    return BootstrapResult(
        point_sharpe=observed,
        mean=mean,
        median=median,
        std=math.sqrt(var),
        ci_low=_percentile(samples, alpha),
        ci_high=_percentile(samples, 1.0 - alpha),
        prob_sharpe_gt_zero=gt_zero / len(samples),
        p_value_null=at_least_observed / len(null_samples),
        n_boot=n_boot,
        block_size=block_size,
        confidence=confidence,
    )
