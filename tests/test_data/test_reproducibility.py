from __future__ import annotations

import pytest

from data.reproducibility import ENV_VAR, assert_pinned, require_pinned


def test_require_pinned_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_VAR, raising=False)
    assert require_pinned() is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", " On "])
def test_require_pinned_truthy(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv(ENV_VAR, value)
    assert require_pinned() is True


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "2"])
def test_require_pinned_falsey(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv(ENV_VAR, value)
    assert require_pinned() is False


def test_assert_pinned_noop_when_source_pinned(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_VAR, "1")
    assert_pinned(True, "prices")  # pinned source → never raises, even under strict mode


def test_assert_pinned_noop_when_strict_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_VAR, raising=False)
    assert_pinned(False, "prices")  # exploratory run → unpinned source allowed


def test_assert_pinned_blocks_unpinned_under_strict(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_VAR, "1")
    with pytest.raises(SystemExit, match="non-reproducible"):
        assert_pinned(False, "prices (--prices omitted)")
