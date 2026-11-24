"""``guarded`` maps a Glue call's outcome to a pane state, only while current."""

from __future__ import annotations

from typing import Any

from vmx import MessageHub
from vmx.messages.protocols import Message

from aws_tui.domain.filesystem import ProviderError
from aws_tui.vm.file_manager.pane_vm import PaneState
from aws_tui.vm.glue._errors import map_provider_error
from aws_tui.vm.glue._guarded import guarded
from aws_tui.vm.glue._lifecycle import GlueOperationSuperseded
from aws_tui.vm.messages import ServiceOperationFailedMessage


def _hub() -> tuple[MessageHub[Message], list[Any]]:
    hub: MessageHub[Message] = MessageHub()
    seen: list[Any] = []
    hub.messages.subscribe(on_next=seen.append)
    return hub, seen


async def _value(value: str) -> str:
    return value


async def _raise(exc: BaseException) -> str:
    raise exc


async def test_success_current_returns_value() -> None:
    hub, _ = _hub()
    failures: list[tuple[PaneState, str | None]] = []
    ok, value = await guarded(
        lambda: _value("rows"),
        hub=hub,
        operation="list_jobs",
        is_current=lambda: True,
        on_failure=lambda state, text: failures.append((state, text)),
    )
    assert (ok, value) == (True, "rows")
    assert failures == []


async def test_success_stale_returns_false() -> None:
    hub, _ = _hub()
    ok, value = await guarded(
        lambda: _value("rows"),
        hub=hub,
        operation="list_jobs",
        is_current=lambda: False,
        on_failure=lambda state, text: None,
    )
    assert (ok, value) == (False, "rows")


async def test_superseded_is_silent() -> None:
    hub, seen = _hub()
    failures: list[tuple[PaneState, str | None]] = []
    ok, value = await guarded(
        lambda: _raise(GlueOperationSuperseded()),
        hub=hub,
        operation="list_jobs",
        is_current=lambda: True,
        on_failure=lambda state, text: failures.append((state, text)),
    )
    assert (ok, value) == (False, None)
    assert failures == []
    assert seen == []


async def test_provider_error_current_and_stale() -> None:
    exc = ProviderError("denied")
    expected = map_provider_error(exc)
    hub, _ = _hub()
    current: list[tuple[PaneState, str | None]] = []
    await guarded(
        lambda: _raise(exc),
        hub=hub,
        operation="list_jobs",
        is_current=lambda: True,
        on_failure=lambda state, text: current.append((state, text)),
    )
    assert current == [expected]
    stale: list[tuple[PaneState, str | None]] = []
    await guarded(
        lambda: _raise(exc),
        hub=hub,
        operation="list_jobs",
        is_current=lambda: False,
        on_failure=lambda state, text: stale.append((state, text)),
    )
    assert stale == []


async def test_unexpected_error_current_reports_once() -> None:
    hub, seen = _hub()
    failures: list[tuple[PaneState, str | None]] = []
    ok, value = await guarded(
        lambda: _raise(RuntimeError("boom")),
        hub=hub,
        operation="list_jobs",
        is_current=lambda: True,
        on_failure=lambda state, text: failures.append((state, text)),
    )
    assert (ok, value) == (False, None)
    assert [state for state, _text in failures] == [PaneState.ERROR]
    reports = [m for m in seen if isinstance(m, ServiceOperationFailedMessage)]
    assert len(reports) == 1


async def test_unexpected_error_stale_is_silent() -> None:
    hub, seen = _hub()
    failures: list[tuple[PaneState, str | None]] = []
    await guarded(
        lambda: _raise(RuntimeError("boom")),
        hub=hub,
        operation="list_jobs",
        is_current=lambda: False,
        on_failure=lambda state, text: failures.append((state, text)),
    )
    assert failures == []
    assert [m for m in seen if isinstance(m, ServiceOperationFailedMessage)] == []
