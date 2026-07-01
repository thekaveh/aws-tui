"""Tests for PaneVM."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import cast

import pytest
from vmx import NULL_DISPATCHER, MessageHub
from vmx.lifecycle.status import ConstructionStatus
from vmx.messages.protocols import Message

from aws_tui.domain.filesystem import (
    AuthRequiredError,
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


class _EndpointSecretFailureFS:
    async def list(self, _path: PathRef) -> list[FileEntry]:
        raise ProviderUnreachableError(
            "failed to reach https://user:pass@example.com/bucket?X-Amz-Signature=sig token=abc123"
        )

    async def stat(self, _path: PathRef) -> FileEntry:
        raise NotImplementedError

    async def mkdir(self, _path: PathRef) -> None:
        raise NotImplementedError

    async def delete(self, _path: PathRef) -> None:
        raise NotImplementedError

    async def rename(self, _src: PathRef, _dst: PathRef) -> None:
        raise NotImplementedError

    async def read_stream(
        self, _path: PathRef, *, chunk_size: int = 8 * 1024 * 1024
    ) -> AsyncIterator[bytes]:
        raise NotImplementedError

    async def write_stream(
        self,
        _path: PathRef,
        _source: AsyncIterator[bytes],
        *,
        total_size: int | None = None,
        progress: ProgressCallback | None = None,
    ) -> None:
        raise NotImplementedError


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
async def test_pane_placeholder_redacts_endpoint_secrets() -> None:
    pane = PaneVM(
        provider=_EndpointSecretFailureFS(),
        hub=_hub(),
        dispatcher=NULL_DISPATCHER,
    )
    pane.construct()
    await pane.setup()

    placeholder = pane.viewmodel.placeholder_text or ""
    assert pane.state == PaneState.UNREACHABLE
    assert "user" not in placeholder
    assert "pass" not in placeholder
    assert "X-Amz-Signature" not in placeholder
    assert "sig" not in placeholder
    assert "abc123" not in placeholder
    assert "token=[REDACTED]" in placeholder
    pane.dispose()


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
async def test_pane_toggle_select_skips_parent_link() -> None:
    fs = await _seed_fs()
    pane = await _make_pane(fs)
    await pane.navigate_to(PathRef(("b",)))

    assert pane.selected_entry is not None
    assert pane.selected_entry.is_parent_link
    pane.toggle_select_command.execute()

    assert not pane.is_multiselect_mode
    assert pane.marked_entries == ()
    assert pane.viewmodel.selection_count == 0
    pane.dispose()


@pytest.mark.asyncio
async def test_pane_select_all_skips_parent_link() -> None:
    fs = await _seed_fs()
    pane = await _make_pane(fs)
    await pane.navigate_to(PathRef(("b",)))

    pane.select_all_command.execute()

    assert pane.is_multiselect_mode
    assert [entry.entry.name for entry in pane.marked_entries] == ["nested.bin"]
    assert pane.viewmodel.selection_count == 1
    assert not pane.entries[0].is_marked
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
async def test_pane_delete_marked_ignores_manually_marked_parent_link() -> None:
    class _RecordingFS(InMemoryFS):
        def __init__(self) -> None:
            super().__init__()
            self.deleted: list[PathRef] = []

        async def delete(self, path: PathRef) -> None:
            self.deleted.append(path)
            await super().delete(path)

    fs = _RecordingFS()
    await fs.mkdir(PathRef(("b",)))
    await fs.write_stream(PathRef(("b", "nested.bin")), _astream(b"nested"))
    pane = await _make_pane(fs)
    await pane.navigate_to(PathRef(("b",)))
    assert pane.entries[0].is_parent_link
    pane.entries[0].set_marked(True)

    await pane.delete_marked()

    assert fs.deleted == []
    assert pane.entries[0].is_parent_link
    pane.dispose()


@pytest.mark.asyncio
async def test_pane_delete_marked_partial_failure_aggregates_and_reloads() -> None:
    """Pin the post-Pass-1 ``delete_marked`` contract: a mid-batch
    failure (one of multiple marked entries can't be deleted) must
    NOT silently abort the rest of the batch AND must still reload
    the pane so the user sees the post-delete truth. Without the
    fix, the loop bailed on the first error, the surviving deletes
    never ran, and the UI showed all M entries as still-marked even
    though the first N-1 were already gone."""

    class _FailOnAlpha(InMemoryFS):
        async def delete(self, path: PathRef) -> None:
            # Refuse to delete ``a.txt`` specifically; the other
            # marked entries still complete.
            if path.name == "a.txt":
                raise PermissionDeniedError("forbidden a.txt")
            await super().delete(path)

    fs = _FailOnAlpha()
    await fs.mkdir(PathRef(("b",)))
    await fs.write_stream(PathRef(("a.txt",)), _astream(b"alpha"))
    await fs.write_stream(PathRef(("c.json",)), _astream(b'{"k":1}'))
    pane = await _make_pane(fs)
    pane.enter_multiselect_command.execute()
    pane.move_cursor_command.execute(1)
    pane.toggle_select_command.execute()
    pane.move_cursor_command.execute(1)
    pane.toggle_select_command.execute()
    assert {e.entry.name for e in pane.marked_entries} == {"a.txt", "c.json"}

    with pytest.raises(PermissionDeniedError):
        await pane.delete_marked()

    # ``c.json`` succeeded; ``a.txt`` survived. The reload in
    # ``finally`` means the pane sees the post-delete truth.
    names = [e.entry.name for e in pane.entries]
    assert "c.json" not in names
    assert "a.txt" in names
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


class _AuthRequiredFS(_UnreachableFS):
    async def list(self, _path: PathRef) -> list[FileEntry]:
        raise AuthRequiredError("login please")


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
async def test_pane_auth_required_state_from_provider() -> None:
    pane = PaneVM(provider=_AuthRequiredFS(), hub=_hub(), dispatcher=NULL_DISPATCHER)
    pane.construct()
    await pane.setup()
    assert pane.state == PaneState.AUTH_REQUIRED
    assert pane.viewmodel.error_text == "login please"
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
