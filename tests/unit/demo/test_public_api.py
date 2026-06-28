"""Public API tests for the demo package: env-var detection + --demo flag."""

from __future__ import annotations

import pytest

from aws_tui.demo import DEMO_ENV_VAR, is_demo_mode_enabled


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "True", "yes", "YES"])
def test_truthy_env_values_enable_demo(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv(DEMO_ENV_VAR, value)
    assert is_demo_mode_enabled(argv=["aws-tui"]) is True


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "junk"])
def test_falsy_env_values_disable_demo(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv(DEMO_ENV_VAR, value)
    assert is_demo_mode_enabled(argv=["aws-tui"]) is False


def test_demo_cli_flag_enables_demo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(DEMO_ENV_VAR, raising=False)
    assert is_demo_mode_enabled(argv=["aws-tui", "--demo"]) is True


def test_both_env_var_and_flag_enable_demo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DEMO_ENV_VAR, "1")
    assert is_demo_mode_enabled(argv=["aws-tui", "--demo"]) is True


def test_no_signal_disables_demo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(DEMO_ENV_VAR, raising=False)
    assert is_demo_mode_enabled(argv=["aws-tui"]) is False


def test_env_var_name_constant_is_canonical() -> None:
    assert DEMO_ENV_VAR == "AWS_TUI_DEMO"
