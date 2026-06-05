from __future__ import annotations

import pytest

from engine.significance import PBOResult, cscv_pbo, effective_n_trials


def test_effective_n_for_identical_trials_is_one() -> None:
    series = [1.0, -1.0, 1.0, -1.0]
    assert effective_n_trials([series, series, series]) == pytest.approx(1.0)


def test_effective_n_for_orthogonal_trials_is_count() -> None:
    a = [1.0, -1.0, 1.0, -1.0]
    b = [1.0, 1.0, -1.0, -1.0]  # zero correlation with a
    assert effective_n_trials([a, b]) == pytest.approx(2.0)


def test_effective_n_single_trial_is_one() -> None:
    assert effective_n_trials([[0.1, -0.2, 0.3]]) == pytest.approx(1.0)


def test_pbo_zero_when_one_config_dominates_everywhere() -> None:
    # Same volatility, strictly ordered means -> config0 is best in every split,
    # in-sample AND out-of-sample -> no overfitting.
    base = [0.01, 0.02, 0.01, 0.02, 0.01, 0.02, 0.01, 0.02]
    config0 = [x + 0.02 for x in base]
    config1 = [x + 0.01 for x in base]
    config2 = list(base)

    result = cscv_pbo([config0, config1, config2], n_splits=4)

    assert isinstance(result, PBOResult)
    assert result.n_configs == 3
    assert result.pbo == pytest.approx(0.0)
    assert result.median_logit > 0.0


def test_pbo_high_for_anti_correlated_mirror_configs() -> None:
    # Two configs that mirror each other across time: whichever wins in-sample
    # loses out-of-sample -> overfit selection -> PBO high.
    config0 = [0.03, 0.03, 0.03, 0.03, -0.01, -0.01, -0.01, -0.01]
    config1 = [-0.01, -0.01, -0.01, -0.01, 0.03, 0.03, 0.03, 0.03]

    result = cscv_pbo([config0, config1], n_splits=4)

    assert result.pbo >= 0.5
    assert result.median_logit < 0.0


def test_cscv_pbo_requires_even_splits() -> None:
    with pytest.raises(ValueError, match="even"):
        cscv_pbo([[0.1, 0.2, 0.3], [0.2, 0.1, 0.0]], n_splits=3)


def test_cscv_pbo_requires_two_configs() -> None:
    with pytest.raises(ValueError, match="two"):
        cscv_pbo([[0.1, 0.2, 0.3, 0.4]], n_splits=2)
