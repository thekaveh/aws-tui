"""The transfer-history toast shows the text of the VM that sent the change (#579)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from vmx import PropertyChangedMessage

from aws_tui import app as app_module
from aws_tui.app import AwsTuiApp


def _app(vm: SimpleNamespace) -> SimpleNamespace:
    chrome = SimpleNamespace(toast_stack="stack")
    root_vm = SimpleNamespace(chrome=chrome)
    return SimpleNamespace(_app_ctx=SimpleNamespace(transfer_history_vm=vm, root_vm=root_vm))


def _toasts(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    shown: list[str] = []

    def advise(_stack: object, **kwargs: object) -> None:
        shown.append(str(kwargs["message"]))

    monkeypatch.setattr(app_module.notifications, "advise", advise)
    return shown


def test_runtime_error_is_the_toast_text(monkeypatch: pytest.MonkeyPatch) -> None:
    shown = _toasts(monkeypatch)
    runtime = SimpleNamespace(error_text="runtime failed")
    vm = SimpleNamespace(runtime=runtime, error_text="history failed")
    message = PropertyChangedMessage.create(runtime, "transfer_history", "error_text")

    AwsTuiApp._on_history_error_changed(_app(vm), message)

    assert shown == ["runtime failed"]


def test_history_error_is_the_toast_text(monkeypatch: pytest.MonkeyPatch) -> None:
    shown = _toasts(monkeypatch)
    runtime = SimpleNamespace(error_text="runtime failed")
    vm = SimpleNamespace(runtime=runtime, error_text="history failed")
    message = PropertyChangedMessage.create(vm, "transfer_history", "error_text")

    AwsTuiApp._on_history_error_changed(_app(vm), message)

    assert shown == ["history failed"]


def test_cleared_error_shows_no_toast(monkeypatch: pytest.MonkeyPatch) -> None:
    shown = _toasts(monkeypatch)
    runtime = SimpleNamespace(error_text=None)
    vm = SimpleNamespace(runtime=runtime, error_text=None)
    message = PropertyChangedMessage.create(vm, "transfer_history", "error_text")

    AwsTuiApp._on_history_error_changed(_app(vm), message)

    assert shown == []
