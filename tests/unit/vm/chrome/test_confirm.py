"""Tests for the ConfirmationVM."""

from __future__ import annotations

import asyncio
from typing import cast

import pytest
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.vm.chrome.confirm_vm import ConfirmationVM, ConfirmRequest


def _hub() -> MessageHub[Message]:
    return cast("MessageHub[Message]", MessageHub())


def _build() -> ConfirmationVM:
    vm = ConfirmationVM(hub=_hub(), dispatcher=NULL_DISPATCHER)
    vm.construct()
    return vm


def _req(*, danger: bool = False) -> ConfirmRequest:
    return ConfirmRequest(
        title="Delete bucket",
        body_lines=("This cannot be undone.",),
        confirm_label="OK",
        cancel_label="Cancel",
        danger=danger,
    )


def test_initial_state() -> None:
    vm = _build()
    assert not vm.is_open
    assert vm.request is None
    vm.dispose()


async def test_ask_resolves_true_on_confirm() -> None:
    from vmx import ModalVM

    vm = _build()
    task = asyncio.create_task(vm.ask(_req()))
    await asyncio.sleep(0)  # let ask() set up the future
    assert vm.is_open
    assert vm.request is not None
    assert isinstance(vm._modal, ModalVM)
    vm.confirm_command.execute()
    result = await task
    assert result is True
    assert not vm.is_open
    vm.dispose()


async def test_ask_resolves_false_on_cancel() -> None:
    vm = _build()
    task = asyncio.create_task(vm.ask(_req()))
    await asyncio.sleep(0)
    vm.cancel_command.execute()
    result = await task
    assert result is False
    assert not vm.is_open
    vm.dispose()


async def test_ask_while_open_raises() -> None:
    vm = _build()
    task = asyncio.create_task(vm.ask(_req()))
    await asyncio.sleep(0)
    with pytest.raises(RuntimeError):
        await vm.ask(_req())
    vm.cancel_command.execute()
    await task
    vm.dispose()


async def test_dispose_while_open_cancels_pending() -> None:
    vm = _build()
    task = asyncio.create_task(vm.ask(_req()))
    await asyncio.sleep(0)
    vm.dispose()
    result = await task
    # Dispose interprets the unresolved confirm as a cancel.
    assert result is False


def test_confirm_request_dangerous_flag() -> None:
    req = _req(danger=True)
    assert req.danger is True
    assert req.confirm_label == "OK"


def test_request_is_frozen() -> None:
    req = _req()
    with pytest.raises(AttributeError):
        req.title = "x"  # type: ignore[misc]


def test_commands_no_op_when_closed() -> None:
    vm = _build()
    # No pending ask: the predicate (is_open) is False so neither
    # command's task runs. Assert observable state stays at the
    # closed defaults — without these asserts a regression that
    # let execute() through despite a False predicate would
    # silently mutate is_open / request.
    assert vm.is_open is False
    vm.confirm_command.execute()
    vm.cancel_command.execute()
    assert vm.is_open is False
    assert vm.request is None
    vm.dispose()


async def test_consecutive_asks_work() -> None:
    vm = _build()
    task1 = asyncio.create_task(vm.ask(_req()))
    await asyncio.sleep(0)
    vm.confirm_command.execute()
    assert await task1 is True

    task2 = asyncio.create_task(vm.ask(_req()))
    await asyncio.sleep(0)
    vm.cancel_command.execute()
    assert await task2 is False
    vm.dispose()
