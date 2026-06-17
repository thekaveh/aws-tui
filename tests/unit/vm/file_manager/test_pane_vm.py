"""Tests for PaneVM."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import cast

import pytest
from vmx import NULL_DISPATCHER, MessageHub
from vmx.lifecycle.status import ConstructionStatus
from vmx.messages.protocols import Message

from aws_tui.domain.filesystem import (
    FileEntry,
    NotFoundError,
    PathRef,
    PermissionDeniedError,
    ProgressCallback,
    ProviderError,
    ProviderUnreachableError,
)
from aws_tui.vm.file_manager.pane_vm import PaneState, PaneVM
from tests.unit.domain._in_memory_fs import InMemoryFS


def _hub() -> MessageHub[Message]:
    return cast("MessageHub[Message]", MessageHub())


async def _seed_fs() -> InMemoryFS:
    fs = InMemoryFS()
    await fs.mkdir(PathRef(("b",)))
    await fs.write_stream(PathRef(("a.txt",)), _astream(b"alpha"))
    await fs.write_stream(PathRef(("c.json",)), _astream(b'{"k":1}'))
    await fs.write_stream(PathRef(("b", "nested.bin")), _astream(b"bytes"))
    return fs


async def _astream(payload: bytes) -> AsyncIterator[bytes]:
    yield payload


async def _make_pane(fs: InMemoryFS, *, hub: MessageHub[Message] | None = None) -> PaneVM:
    pane = PaneVM(
        provider=fs,
        hub=hub or _hub(),
        dispatcher=NULL_DISPATCHER,
    )
    pane.construct()
    await pane.setup()
    return pane


@pytest.mark.asyncio
async def test_pane_setup_lists_root() -> None:
    fs = await _seed_fs()
    pane = await _make_pane(fs)
    names = [e.entry.name for e in pane.entries]
    # InMemoryFS sorts directories first then files alphabetically.
    assert names == ["b", "a.txt", "c.json"]
    assert pane.state == PaneState.IDLE
    pane.dispose()
    assert pane.status == ConstructionStatus.DISPOSED


@pytest.mark.asyncio
async def test_pane_move_cursor_clamps() -> None:
    fs = await _seed_fs()
    pane = await _make_pane(fs)
    assert pane.cursor_index == 0
    pane.move_cursor_command.execute(2)
    assert pane.cursor_index == 2
    pane.move_cursor_command.execute(10)  # clamp
    assert pane.cursor_index == 2
    pane.move_cursor_command.execute(-100)  # clamp other way
    assert pane.cursor_index == 0
    pane.dispose()


@pytest.mark.asyncio
async def test_pane_toggle_select_marks_cursor() -> None:
    fs = await _seed_fs()
    pane = await _make_pane(fs)
    assert not pane.is_multiselect_mode
    pane.toggle_select_command.execute()
    assert pane.is_multiselect_mode
    marked = pane.marked_entries
    assert len(marked) == 1
    assert marked[0].entry.name == "b"
    pane.dispose()


@pytest.mark.asyncio
async def test_pane_enter_exit_multiselect() -> None:
    fs = await _seed_fs()
    pane = await _make_pane(fs)
    pane.enter_multiselect_command.execute()
    assert pane.is_multiselect_mode
    pane.select_all_command.execute()
    assert len(pane.marked_entries) == len(pane.entries)
    pane.exit_multiselect_command.execute()
    assert not pane.is_multiselect_mode
    assert pane.marked_entries == ()
    pane.dispose()


@pytest.mark.asyncio
async def test_pane_delete_marked() -> None:
    fs = await _seed_fs()
    pane = await _make_pane(fs)
    pane.enter_multiselect_command.execute()
    # mark a.txt
    pane.move_cursor_command.execute(1)
    pane.toggle_select_command.execute()
    await pane.delete_marked()
    names = [e.entry.name for e in pane.entries]
    assert "a.txt" not in names
    pane.dispose()


@pytest.mark.asyncio
async def test_pane_navigate_to_changes_breadcrumb() -> None:
    fs = await _seed_fs()
    pane = await _make_pane(fs)
    await pane.navigate_to(PathRef(("b",)))
    assert pane.path == PathRef(("b",))
    assert pane.viewmodel.breadcrumb == ("b",)
    names = [e.entry.name for e in pane.entries]
    assert "nested.bin" in names
    pane.dispose()


@pytest.mark.asyncio
async def test_pane_refresh_repopulates() -> None:
    fs = await _seed_fs()
    pane = await _make_pane(fs)
    await fs.write_stream(PathRef(("d.txt",)), _astream(b"delta"))
    await pane.refresh()
    assert any(e.entry.name == "d.txt" for e in pane.entries)
    pane.dispose()


@pytest.mark.asyncio
async def test_pane_filter_restricts_cursor_navigation() -> None:
    fs = await _seed_fs()
    pane = await _make_pane(fs)
    pane.set_filter_command.execute(".txt")
    filtered = pane.filtered_entries
    assert [e.entry.name for e in filtered] == ["a.txt"]
    pane.move_cursor_command.execute(1)
    assert pane.cursor_index == 0  # only one row visible — clamps
    pane.dispose()


class _UnreachableFS:
    async def list(self, _path: PathRef) -> list[FileEntry]:
        raise ProviderUnreachableError("dns failure")

    async def stat(self, _path: PathRef) -> FileEntry:  # pragma: no cover
        raise NotFoundError("never")

    async def mkdir(self, _path: PathRef) -> None: ...
    async def delete(self, _path: PathRef) -> None: ...
    async def rename(self, _s: PathRef, _d: PathRef) -> None: ...

    async def read_stream(
        self, _path: PathRef, *, chunk_size: int = 8 * 1024 * 1024
    ) -> AsyncIterator[bytes]:  # pragma: no cover
        raise NotFoundError("never")
        yield b""

    async def write_stream(  # pragma: no cover
        self,
        _path: PathRef,
        _source: AsyncIterator[bytes],
        *,
        total_size: int | None = None,
        progress: ProgressCallback | None = None,
    ) -> None: ...


class _ForbiddenFS(_UnreachableFS):
    async def list(self, _path: PathRef) -> list[FileEntry]:
        raise PermissionDeniedError("403")


class _ErrorFS(_UnreachableFS):
    async def list(self, _path: PathRef) -> list[FileEntry]:
        raise ProviderError("boom")


class _EmptyBucketFS(_UnreachableFS):
    async def list(self, _path: PathRef) -> list[FileEntry]:
        raise NotFoundError("bucket-empty")


@pytest.mark.asyncio
async def test_pane_unreachable_state() -> None:
    pane = PaneVM(provider=_UnreachableFS(), hub=_hub(), dispatcher=NULL_DISPATCHER)
    pane.construct()
    await pane.setup()
    assert pane.state == PaneState.UNREACHABLE
    pane.dispose()


@pytest.mark.asyncio
async def test_pane_forbidden_state() -> None:
    pane = PaneVM(provider=_ForbiddenFS(), hub=_hub(), dispatcher=NULL_DISPATCHER)
    pane.construct()
    await pane.setup()
    assert pane.state == PaneState.FORBIDDEN
    pane.dispose()


@pytest.mark.asyncio
async def test_pane_error_state_carries_text() -> None:
    pane = PaneVM(provider=_ErrorFS(), hub=_hub(), dispatcher=NULL_DISPATCHER)
    pane.construct()
    await pane.setup()
    assert pane.state == PaneState.ERROR
    assert pane.viewmodel.error_text == "boom"
    pane.dispose()


@pytest.mark.asyncio
async def test_pane_root_notfound_renders_empty() -> None:
    pane = PaneVM(provider=_EmptyBucketFS(), hub=_hub(), dispatcher=NULL_DISPATCHER)
    pane.construct()
    await pane.setup()
    assert pane.state == PaneState.EMPTY
    pane.dispose()


@pytest.mark.asyncio
async def test_pane_set_auth_required_external() -> None:
    fs = await _seed_fs()
    pane = await _make_pane(fs)
    pane.set_auth_required()
    assert pane.state == PaneState.AUTH_REQUIRED
    pane.dispose()


@pytest.mark.asyncio
async def test_pane_make_directory_then_refresh() -> None:
    fs = await _seed_fs()
    pane = await _make_pane(fs)
    await pane.make_directory("new_dir")
    assert any(e.entry.name == "new_dir" for e in pane.entries)
    pane.dispose()


@pytest.mark.asyncio
async def test_pane_rename_cursor() -> None:
    fs = await _seed_fs()
    pane = await _make_pane(fs)
    # Move cursor to a.txt (idx 1 in dir-first sort) and rename it.
    pane.move_cursor_command.execute(1)
    await pane.rename_cursor("renamed.txt")
    names = [e.entry.name for e in pane.entries]
    assert "renamed.txt" in names
    assert "a.txt" not in names
    pane.dispose()


@pytest.mark.asyncio
async def test_pane_viewmodel_summary_marks() -> None:
    fs = await _seed_fs()
    pane = await _make_pane(fs)
    pane.enter_multiselect_command.execute()
    pane.move_cursor_command.execute(1)
    pane.toggle_select_command.execute()
    vm = pane.viewmodel
    assert vm.selection_count == 1
    assert "marked" in vm.summary


@pytest.mark.asyncio
async def test_pane_emits_property_changed_on_state() -> None:
    fs = await _seed_fs()
    hub = _hub()
    received: list[str] = []
    hub.messages.subscribe(
        on_next=lambda m: received.append(getattr(m, "property_name", "")) if m else None
    )
    pane = PaneVM(provider=fs, hub=hub, dispatcher=NULL_DISPATCHER)
    pane.construct()
    await pane.setup()
    assert "state" in received
    assert "entries" in received
    pane.dispose()
