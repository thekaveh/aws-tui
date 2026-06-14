"""Tests for the ToastVM + ToastStackVM (Task 3)."""

from __future__ import annotations

import asyncio
from typing import cast

import pytest
from vmx import NULL_DISPATCHER, MessageHub
from vmx.lifecycle.status import ConstructionStatus
from vmx.messages.protocols import Message

from aws_tui.vm.chrome.toast_stack_vm import ToastStackVM
from aws_tui.vm.chrome.toast_vm import ToastLevel, ToastModel, ToastVM


def _hub() -> MessageHub[Message]:
    return cast("MessageHub[Message]", MessageHub())


def _stack() -> ToastStackVM:
    return ToastStackVM(hub=_hub(), dispatcher=NULL_DISPATCHER)


def _model(*, sticky: bool = True, timeout: float | None = None) -> ToastModel:
    return ToastModel(
        id="t1",
        text="hello",
        level=ToastLevel.INFO,
        sticky=sticky,
        timeout_seconds=timeout,
        action_label=None,
        action_action=None,
    )


def test_toast_model_round_trip() -> None:
    m = ToastModel(
        id="t1",
        text="login needed",
        level=ToastLevel.WARNING,
        sticky=True,
        timeout_seconds=None,
        action_label="authenticate",
        action_action="auth.authenticate",
    )
    assert m.id == "t1"
    assert m.level is ToastLevel.WARNING
    assert m.sticky


def test_toast_vm_construct_dispose() -> None:
    hub = _hub()
    toast = ToastVM(_model(), hub=hub, dispatcher=NULL_DISPATCHER)
    toast.construct()
    assert toast.is_constructed
    toast.dispose()
    assert toast.status == ConstructionStatus.DISPOSED


def test_toast_dismiss_command() -> None:
    """dismiss_command marks the toast dismissed."""
    hub = _hub()
    dismissed: list[str] = []
    toast = ToastVM(
        _model(),
        hub=hub,
        dispatcher=NULL_DISPATCHER,
        on_dismiss=lambda t: dismissed.append(t.model.id),
    )
    toast.construct()
    assert not toast.is_dismissed
    toast.dismiss_command.execute()
    assert toast.is_dismissed
    assert dismissed == ["t1"]
    toast.dispose()


def test_toast_stack_raise_and_dismiss() -> None:
    stack = _stack()
    stack.construct()
    assert stack.count == 0
    toast = stack.raise_toast(_model())
    assert stack.count == 1
    assert stack.toasts == (toast,)
    stack.dismiss("t1")
    assert stack.count == 0
    stack.dispose()


def test_toast_stack_dismiss_unknown_is_noop() -> None:
    stack = _stack()
    stack.construct()
    stack.dismiss("does-not-exist")
    assert stack.count == 0
    stack.dispose()


def test_toast_stack_publishes_collection_changed_on_raise() -> None:
    hub = _hub()
    stack = ToastStackVM(hub=hub, dispatcher=NULL_DISPATCHER)
    stack.construct()
    events: list[object] = []
    sub = stack.on_collection_changed.subscribe(on_next=lambda e: events.append(e))
    stack.raise_toast(_model())
    assert events  # at least one collection-changed event observed
    sub.dispose()
    stack.dispose()


def test_toast_stack_dispose_cascades() -> None:
    stack = _stack()
    stack.construct()
    a = stack.raise_toast(
        ToastModel(
            id="a",
            text="a",
            level=ToastLevel.INFO,
            sticky=True,
            timeout_seconds=None,
            action_label=None,
            action_action=None,
        )
    )
    b = stack.raise_toast(
        ToastModel(
            id="b",
            text="b",
            level=ToastLevel.SUCCESS,
            sticky=True,
            timeout_seconds=None,
            action_label=None,
            action_action=None,
        )
    )
    stack.dispose()
    assert a.status == ConstructionStatus.DISPOSED
    assert b.status == ConstructionStatus.DISPOSED


async def test_non_sticky_toast_auto_dismisses() -> None:
    """A toast with sticky=False is auto-dismissed after its timeout."""
    stack = _stack()
    stack.construct()
    stack.raise_toast(
        ToastModel(
            id="auto",
            text="auto",
            level=ToastLevel.INFO,
            sticky=False,
            timeout_seconds=0.01,
            action_label=None,
            action_action=None,
        )
    )
    assert stack.count == 1
    # Give the auto-dismiss task time to fire.
    await asyncio.sleep(0.05)
    assert stack.count == 0
    stack.dispose()


async def test_sticky_toast_does_not_auto_dismiss() -> None:
    stack = _stack()
    stack.construct()
    stack.raise_toast(
        ToastModel(
            id="sticky",
            text="sticky",
            level=ToastLevel.INFO,
            sticky=True,
            timeout_seconds=0.01,
            action_label=None,
            action_action=None,
        )
    )
    await asyncio.sleep(0.05)
    assert stack.count == 1
    stack.dispose()


async def test_dispose_cancels_auto_dismiss_timer() -> None:
    """Disposing the stack mid-timer shouldn't raise / leak the asyncio task."""
    stack = _stack()
    stack.construct()
    stack.raise_toast(
        ToastModel(
            id="cancelable",
            text="x",
            level=ToastLevel.INFO,
            sticky=False,
            timeout_seconds=1.0,  # would not fire in this test window
            action_label=None,
            action_action=None,
        )
    )
    stack.dispose()
    # Give the loop a tick to settle.
    await asyncio.sleep(0)


def test_toast_level_enum_values() -> None:
    assert ToastLevel.INFO.value == "info"
    assert ToastLevel.SUCCESS.value == "success"
    assert ToastLevel.WARNING.value == "warning"
    assert ToastLevel.ERROR.value == "error"


def test_toast_model_is_frozen() -> None:
    m = _model()
    with pytest.raises(AttributeError):
        m.text = "nope"  # type: ignore[misc]
