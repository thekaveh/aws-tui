"""Tests for the QuickLookVM (Task 7)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import cast

import pytest
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.vm.chrome.quick_look_vm import QuickLookContent, QuickLookVM


def _hub() -> MessageHub[Message]:
    return cast("MessageHub[Message]", MessageHub())


def _build() -> QuickLookVM:
    vm = QuickLookVM(hub=_hub(), dispatcher=NULL_DISPATCHER)
    vm.construct()
    return vm


async def _empty_chunks() -> AsyncIterator[bytes]:
    if False:
        yield b""


async def _chunks(data: bytes) -> AsyncIterator[bytes]:
    yield data


def test_initial_state() -> None:
    vm = _build()
    assert not vm.is_open
    assert vm.content is None
    assert vm.scroll_offset == 0
    assert vm.find_query == ""
    vm.dispose()


def test_open_with_content() -> None:
    vm = _build()
    content = QuickLookContent(
        title="api-2026-06-13.json  4.2M  application/json",
        mime="application/json",
        chunks=None,
        line_count_estimate=None,
    )
    vm.open_command.execute(content)
    assert vm.is_open
    assert vm.content is content
    vm.dispose()


def test_close_clears_content() -> None:
    vm = _build()
    content = QuickLookContent(title="x", mime="text/plain", chunks=None, line_count_estimate=10)
    vm.open_command.execute(content)
    vm.close_command.execute()
    assert not vm.is_open
    assert vm.content is None
    assert vm.scroll_offset == 0
    assert vm.find_query == ""
    vm.dispose()


def test_scroll_clamped_at_zero() -> None:
    vm = _build()
    vm.open_command.execute(
        QuickLookContent(title="x", mime="text/plain", chunks=None, line_count_estimate=10)
    )
    vm.scroll_command.execute(-5)
    assert vm.scroll_offset == 0
    vm.scroll_command.execute(3)
    assert vm.scroll_offset == 3
    vm.scroll_command.execute(2)
    assert vm.scroll_offset == 5
    vm.dispose()


def test_scroll_clamped_at_line_count_estimate() -> None:
    vm = _build()
    vm.open_command.execute(
        QuickLookContent(title="x", mime="text/plain", chunks=None, line_count_estimate=4)
    )
    vm.scroll_command.execute(10)
    assert vm.scroll_offset == 4
    vm.dispose()


def test_scroll_unbounded_when_estimate_none() -> None:
    vm = _build()
    vm.open_command.execute(
        QuickLookContent(title="x", mime="text/plain", chunks=None, line_count_estimate=None)
    )
    vm.scroll_command.execute(10_000)
    assert vm.scroll_offset == 10_000
    vm.dispose()


def test_find_query_updates() -> None:
    vm = _build()
    vm.open_command.execute(
        QuickLookContent(title="x", mime="text/plain", chunks=None, line_count_estimate=10)
    )
    vm.find_command.execute("error")
    assert vm.find_query == "error"
    vm.find_command.execute("")
    assert vm.find_query == ""
    vm.dispose()


async def test_stream_chunks_collects_body() -> None:
    """Caller may stream the content body via the iterator stored on content."""
    vm = _build()
    content = QuickLookContent(
        title="x",
        mime="text/plain",
        chunks=_chunks(b"hello world"),
        line_count_estimate=1,
    )
    vm.open_command.execute(content)
    body = bytearray()
    iterator = content.chunks
    assert iterator is not None
    async for chunk in iterator:
        body.extend(chunk)
    assert bytes(body) == b"hello world"
    vm.dispose()


def test_open_replaces_existing_content() -> None:
    vm = _build()
    first = QuickLookContent(title="a", mime="text/plain", chunks=None, line_count_estimate=1)
    second = QuickLookContent(title="b", mime="text/plain", chunks=None, line_count_estimate=2)
    vm.open_command.execute(first)
    vm.scroll_command.execute(5)
    vm.find_command.execute("query")
    vm.open_command.execute(second)
    assert vm.content is second
    assert vm.scroll_offset == 0
    assert vm.find_query == ""
    vm.dispose()


def test_open_with_none_payload_is_noop() -> None:
    vm = _build()
    vm.open_command.execute(None)
    assert not vm.is_open
    vm.dispose()


def test_content_is_frozen() -> None:
    c = QuickLookContent(title="x", mime="text/plain", chunks=None, line_count_estimate=1)
    with pytest.raises(AttributeError):
        c.title = "y"  # type: ignore[misc]
