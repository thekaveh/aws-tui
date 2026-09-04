"""Tests for the CrashVM facade."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.vm.chrome.crash_vm import (
    SAFE_CONTINUE_ACTIONS,
    CrashChoice,
    CrashReport,
    CrashVM,
)


def _hub() -> MessageHub[Message]:
    return cast("MessageHub[Message]", MessageHub())


def _report(*, can_continue: bool = True, last_action_id: str | None = None) -> CrashReport:
    return CrashReport(
        timestamp=datetime.now(UTC),
        exception_type="TypeError",
        exception_message="unsupported operand type(s) for +: 'int' and 'str'",
        traceback_short="Traceback (most recent call last):\n  ...\nTypeError: ...",
        dump_path=Path("/tmp/aws-tui/crash/2026-06-14T10-00-00.txt"),
        can_continue=can_continue,
        last_action_id=last_action_id,
    )


def _build(report: CrashReport | None = None) -> CrashVM:
    vm = CrashVM(report or _report(), hub=_hub(), dispatcher=NULL_DISPATCHER)
    vm.construct()
    return vm


def test_initial_state() -> None:
    vm = _build()
    assert vm.is_open is False
    assert vm.can_continue is True
    assert vm.report.exception_type == "TypeError"
    vm.dispose()


async def test_ask_resolves_quit() -> None:
    from vmx import ModalVM

    vm = _build()
    task = asyncio.create_task(vm.ask())
    await asyncio.sleep(0)
    assert vm.is_open
    assert isinstance(vm._modal, ModalVM)
    vm.quit_command.execute()
    result = await task
    assert result is CrashChoice.QUIT
    assert vm.is_open is False
    vm.dispose()


async def test_ask_resolves_view_trace() -> None:
    vm = _build()
    task = asyncio.create_task(vm.ask())
    await asyncio.sleep(0)
    vm.view_trace_command.execute()
    result = await task
    assert result is CrashChoice.VIEW_TRACE
    vm.dispose()


async def test_ask_resolves_continue_when_allowed() -> None:
    vm = _build()
    task = asyncio.create_task(vm.ask())
    await asyncio.sleep(0)
    vm.continue_command.execute()
    result = await task
    assert result is CrashChoice.CONTINUE
    vm.dispose()


async def test_continue_command_disabled_when_unsafe() -> None:
    vm = _build(_report(can_continue=False))
    task = asyncio.create_task(vm.ask())
    await asyncio.sleep(0)
    # Predicate gates the relay command: execute() should be inert.
    vm.continue_command.execute()
    assert not task.done()
    vm.quit_command.execute()
    assert await task is CrashChoice.QUIT
    vm.dispose()


async def test_ask_while_open_raises() -> None:
    vm = _build()
    task = asyncio.create_task(vm.ask())
    await asyncio.sleep(0)
    with pytest.raises(RuntimeError):
        await vm.ask()
    vm.quit_command.execute()
    await task
    vm.dispose()


async def test_dispose_while_open_resolves_as_quit() -> None:
    vm = _build()
    task = asyncio.create_task(vm.ask())
    await asyncio.sleep(0)
    vm.dispose()
    result = await task
    assert result is CrashChoice.QUIT


def test_report_is_frozen() -> None:
    r = _report()
    with pytest.raises(AttributeError):
        r.exception_type = "X"  # type: ignore[misc]


def test_is_safe_to_continue_for_known_actions() -> None:
    # These must be ids ``AwsTuiApp.record_action`` actually emits. The previous
    # values (``pane.cursor_up``, ``command_palette.open``) were from a
    # vocabulary the app never records, so they asserted membership for ids that
    # could never reach ``is_safe_to_continue`` at runtime. See
    # ``test_safe_continue_actions_are_ids_the_app_actually_records``.
    for action in ("pane.refresh", "pane.move_up", "app.command_palette"):
        assert CrashReport.is_safe_to_continue(action) is True
        assert action in SAFE_CONTINUE_ACTIONS


def test_is_safe_to_continue_for_writes_and_unknowns() -> None:
    assert CrashReport.is_safe_to_continue(None) is False
    assert CrashReport.is_safe_to_continue("pane.delete_marked") is False
    assert CrashReport.is_safe_to_continue("dualpane.copy") is False
    assert CrashReport.is_safe_to_continue("pane.rename") is False


def test_safe_continue_actions_are_ids_the_app_actually_records() -> None:
    """The safe set must speak the same vocabulary as ``record_action``.

    An earlier version named a parallel vocabulary — ``pane.cursor_up``,
    ``quick_look.open``, ``theme.switch`` and 20 others the app never emits — so
    only ``pane.refresh`` and ``pane.switch_focus`` ever matched and
    ``CrashReport.can_continue`` was False after essentially every read-only
    action, leaving the crash modal effectively quit-only.
    """
    import ast
    from pathlib import Path

    from aws_tui.vm.chrome.crash_vm import SAFE_CONTINUE_ACTIONS

    app_source = Path(__file__).parents[4] / "src" / "aws_tui" / "app.py"
    tree = ast.parse(app_source.read_text(encoding="utf-8"))
    recorded = {
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "record_action"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    }

    assert recorded, "expected record_action call sites in app.py"
    orphans = sorted(set(SAFE_CONTINUE_ACTIONS) - recorded)
    assert orphans == [], f"safe-continue ids the app never records: {orphans}"
