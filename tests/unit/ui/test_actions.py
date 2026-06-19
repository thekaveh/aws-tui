"""Unit tests for :mod:`aws_tui.ui.actions`."""

from __future__ import annotations

import asyncio

import pytest

from aws_tui.ui.actions import ActionRegistry, UnknownAction


def test_register_and_has() -> None:
    reg = ActionRegistry()
    assert not reg.has("app.quit")

    reg.register("app.quit", lambda: None)
    assert reg.has("app.quit")


def test_invoke_returns_sync_handler_result() -> None:
    captured: list[str] = []
    reg = ActionRegistry()
    reg.register("pane.copy", lambda: captured.append("copy"))

    result = reg.invoke("pane.copy")

    assert result is None
    assert captured == ["copy"]


def test_invoke_returns_awaitable_for_async_handler() -> None:
    async def _h() -> None:
        return None

    reg = ActionRegistry()
    reg.register("pane.copy", _h)

    result = reg.invoke("pane.copy")
    assert asyncio.iscoroutine(result)
    # Drain the coroutine to avoid warnings.
    asyncio.new_event_loop().run_until_complete(result)


def test_invoke_unknown_raises() -> None:
    reg = ActionRegistry()
    with pytest.raises(UnknownAction):
        reg.invoke("pane.copy")


def test_register_replaces_handler() -> None:
    calls: list[int] = []
    reg = ActionRegistry()
    reg.register("x", lambda: calls.append(1))
    reg.register("x", lambda: calls.append(2))

    reg.invoke("x")
    assert calls == [2]


def test_unregister() -> None:
    reg = ActionRegistry()
    reg.register("x", lambda: None)
    assert reg.has("x")
    reg.unregister("x")
    assert not reg.has("x")
    # Idempotent.
    reg.unregister("x")


def test_known_actions_returns_registration_order() -> None:
    reg = ActionRegistry()
    reg.register("a.one", lambda: None)
    reg.register("b.two", lambda: None)
    reg.register("c.three", lambda: None)

    assert reg.known_actions() == ("a.one", "b.two", "c.three")
