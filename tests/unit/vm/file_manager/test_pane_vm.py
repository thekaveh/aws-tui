"""Tests for PaneVM."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterator
from typing import cast

import pytest
from vmx import NULL_DISPATCHER, MessageHub
from vmx.lifecycle.status import ConstructionStatus
from vmx.messages.protocols import Message

from aws_tui.demo.in_memory_fs import InMemoryFS
from aws_tui.domain.filesystem import (
    AuthRequiredError,
    FileEntry,
    NotFoundError,
    PathRef,
    PermissionDeniedError,
    ProgressCallback,
    ProviderError,
    ProviderUnreachableError,
    ThrottledError,
)
from aws_tui.vm.file_manager.pane_vm import PaneState, PaneVM


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

    async def delete(self, _path: PathRef, *, expected_etag: str | None = None) -> None:
        raise NotImplementedError

    async def delete_empty_directory(self, _path: PathRef) -> None:
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
        overwrite: bool = False,
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
async def test_pane_dispose_clears_filtered_projection_before_entries() -> None:
    fs = await _seed_fs()
    pane = await _make_pane(fs)
    assert pane.filtered_entries

    pane.dispose()

    assert pane.entries == ()
    assert pane.filtered_entries == ()


@pytest.mark.asyncio
async def test_stale_reload_cannot_publish_after_provider_swap() -> None:
    old = InMemoryFS()
    new = InMemoryFS()
    await old.write_stream(PathRef(("old.txt",)), _astream(b"old"))
    await new.write_stream(PathRef(("new.txt",)), _astream(b"new"))
    old_list = old.list
    started = asyncio.Event()
    release = asyncio.Event()

    async def blocked_list(path: PathRef) -> list[FileEntry]:
        started.set()
        await release.wait()
        return await old_list(path)

    old.list = blocked_list  # type: ignore[method-assign]
    pane = PaneVM(provider=old, hub=_hub(), dispatcher=NULL_DISPATCHER)
    pane.construct()
    stale = asyncio.create_task(pane.refresh())
    await asyncio.wait_for(started.wait(), timeout=1)

    await pane.swap_provider(new, identity_label="new")
    release.set()
    await stale

    assert [entry.entry.name for entry in pane.entries] == ["new.txt"]
    pane.dispose()


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

        async def delete(self, path: PathRef, *, expected_etag: str | None = None) -> None:
            self.deleted.append(path)
            await super().delete(path, expected_etag=expected_etag)

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
        async def delete(self, path: PathRef, *, expected_etag: str | None = None) -> None:
            # Refuse to delete ``a.txt`` specifically; the other
            # marked entries still complete.
            if path.name == "a.txt":
                raise PermissionDeniedError("forbidden a.txt")
            await super().delete(path, expected_etag=expected_etag)

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
async def test_delete_marked_cancellation_drains_final_reload() -> None:
    class _BlockingReloadFS(InMemoryFS):
        def __init__(self) -> None:
            super().__init__()
            self.list_calls = 0
            self.reload_started = asyncio.Event()
            self.release_reload = asyncio.Event()

        async def list(self, path: PathRef) -> list[FileEntry]:
            self.list_calls += 1
            if self.list_calls == 2:
                self.reload_started.set()
                await self.release_reload.wait()
            return await super().list(path)

    fs = _BlockingReloadFS()
    await fs.write_stream(PathRef(("delete-me.txt",)), _astream(b"payload"))
    pane = await _make_pane(fs)
    pane.toggle_select_command.execute()
    deletion = asyncio.create_task(pane.delete_marked())
    await asyncio.wait_for(fs.reload_started.wait(), timeout=1)

    deletion.cancel()
    fs.release_reload.set()
    with pytest.raises(asyncio.CancelledError):
        await deletion

    assert pane.state is PaneState.EMPTY
    assert pane.entries == ()
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


@pytest.mark.asyncio
async def test_pane_uses_vmx_filtered_composite() -> None:
    from vmx import FilteredCompositeVM

    fs = await _seed_fs()
    pane = await _make_pane(fs)
    try:
        assert isinstance(pane._filtered_composite, FilteredCompositeVM)
    finally:
        pane.dispose()


class _UnreachableFS:
    async def list(self, _path: PathRef) -> list[FileEntry]:
        raise ProviderUnreachableError("dns failure")

    async def stat(self, _path: PathRef) -> FileEntry:  # pragma: no cover
        raise NotFoundError("never")

    async def mkdir(self, _path: PathRef) -> None: ...
    async def delete(self, _path: PathRef, *, expected_etag: str | None = None) -> None: ...
    async def delete_empty_directory(self, _path: PathRef) -> None: ...
    async def rename(self, _s: PathRef, _d: PathRef) -> None: ...

    async def read_stream(
        self, _path: PathRef, *, chunk_size: int = 8 * 1024 * 1024
    ) -> AsyncIterator[bytes]:  # pragma: no cover
        raise NotFoundError("never")

    async def write_stream(  # pragma: no cover
        self,
        _path: PathRef,
        _source: AsyncIterator[bytes],
        *,
        total_size: int | None = None,
        progress: ProgressCallback | None = None,
        overwrite: bool = False,
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


class _ThrottledFS(_UnreachableFS):
    async def list(self, _path: PathRef) -> list[FileEntry]:
        raise ThrottledError("slow down")


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
async def test_pane_throttling_uses_generic_error_state() -> None:
    pane = PaneVM(provider=_ThrottledFS(), hub=_hub(), dispatcher=NULL_DISPATCHER)
    pane.construct()
    await pane.setup()
    assert pane.state == PaneState.ERROR
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


@pytest.mark.asyncio
async def test_delete_marked_stays_in_the_directory_the_marks_came_from() -> None:
    """Navigating mid-delete must not redirect the batch.

    ``delete_marked`` runs in a worker while the UI stays live. Resolving the
    pane's path per iteration let a navigation between two awaits send the
    remaining deletes into the newly-entered directory, destroying same-named
    files the user never marked — silently, with no error raised.
    """
    fs = InMemoryFS()
    await fs.mkdir(PathRef(("dirA",)))
    await fs.mkdir(PathRef(("dirB",)))
    for name in ("f1", "f2", "f3"):
        await fs.write_stream(PathRef(("dirA", name)), _astream(b"x"))
    for name in ("f2", "f3"):
        await fs.write_stream(PathRef(("dirB", name)), _astream(b"y"))

    deleted: list[str] = []
    real_delete = fs.delete
    pane = await _make_pane(fs)

    async def spy(path: PathRef, **kwargs: object) -> None:
        deleted.append(str(path))
        if len(deleted) == 1:
            await pane.navigate_to(PathRef(("dirB",)))
        await real_delete(path, **kwargs)  # type: ignore[arg-type]

    fs.delete = spy  # type: ignore[method-assign]
    await pane.navigate_to(PathRef(("dirA",)))
    for index in range(len(pane.entries)):
        pane.mark_at(index, marked=True)

    await pane.delete_marked()

    assert deleted == ["/dirA/f1", "/dirA/f2", "/dirA/f3"]
    assert [entry.name for entry in await fs.list(PathRef(("dirA",)))] == []
    # The unmarked files in the directory the pane moved to are untouched.
    assert sorted(entry.name for entry in await fs.list(PathRef(("dirB",)))) == ["f2", "f3"]


@pytest.mark.asyncio
async def test_marked_entries_are_scoped_to_the_visible_filtered_rows() -> None:
    """Destructive operations must act only on rows the pane is displaying.

    ``marked_entries`` drives copy/move/delete targets, the command predicates
    and the footer count. Deriving it from every loaded entry meant a mark on a
    row the active filter hid still participated: filter to one name, press
    delete, and files the user could not see were destroyed.
    """
    fs = await _seed_fs()
    pane = await _make_pane(fs)
    for index in range(len(pane.entries)):
        pane.mark_at(index, marked=True)
    assert len(pane.marked_entries) == len(pane.entries)

    pane.set_filter_command.execute("a.txt")

    assert [entry.entry.name for entry in pane.filtered_entries] == ["a.txt"]
    assert [entry.entry.name for entry in pane.marked_entries] == ["a.txt"]
    # The footer count follows the same property, so it matches the screen.
    assert pane.viewmodel.selection_count == 1

    pane.set_filter_command.execute("")

    # Marks on hidden rows are retained, not discarded — they simply do not
    # participate while hidden.
    assert len(pane.marked_entries) == len(pane.entries)


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["other/moved.txt", "a\\b.txt", "..", "."])
async def test_rename_rejects_names_that_would_become_paths(name: str) -> None:
    """A rename must not silently relocate the entry.

    ``PathRef.join`` splits on ``/`` by design, so ``other/moved.txt`` moved the
    file into another directory and the pane reloaded showing it as vanished.
    """
    fs = await _seed_fs()
    pane = await _make_pane(fs)
    pane.move_cursor_to(0)

    with pytest.raises(ProviderError, match="single path segment"):
        await pane.rename_cursor(name)

    with pytest.raises(ProviderError, match="single path segment"):
        await pane.make_directory(name)


@pytest.mark.asyncio
async def test_marks_are_inert_while_the_pane_is_loading() -> None:
    """No destructive operation may act on a listing being replaced.

    ``_reload`` enters ``LOADING`` without clearing ``_entries`` — every error
    branch does clear — and the view renders only the placeholder, so zero rows
    are on screen. The stale marks still drove the copy/move/delete predicates,
    the delete targets and the footer count, so on a slow listing ``d`` acted on
    an invisible selection.
    """
    fs = await _seed_fs()
    pane = await _make_pane(fs)
    for index in range(len(pane.entries)):
        pane.mark_at(index, marked=True)
    assert pane.marked_entries

    pane._set_state(PaneState.LOADING)

    assert pane.marked_entries == ()
    assert pane.viewmodel.selection_count == 0

    pane._set_state(PaneState.IDLE)

    # The marks themselves are untouched — they simply do not participate
    # while the listing is in flight.
    assert len(pane.marked_entries) == len(pane.entries)


# ── Clipboard payloads ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_copy_tooltip_advice_is_owned_by_the_view_model() -> None:
    """Both tooltip sentences, including the keys they name, live here.

    They used to be f-string literals inside ``EntryRow._sync_tooltip`` and
    ``Pane.on_mouse_move``, which made the widget decide what a label reads
    -- and made the two of them free to drift apart from the placeholder
    text two properties away, which has always named its keys here
    (``"press a to sign in"``, ``"press r to retry"``).

    The two hints must stay distinguishable, because they describe
    different affordances: a row is not a click target (clicking one moves
    the cursor), the border is.
    """
    pane = await _make_pane(await _seed_fs())
    try:
        assert "press p" in pane.entry_tooltip_hint
        assert "cursor entry" in pane.entry_tooltip_hint
        assert "click" not in pane.entry_tooltip_hint, (
            "a row is not a copy click target, so its tooltip must not offer one"
        )

        assert "press P" in pane.path_tooltip_hint
        assert "click here" in pane.path_tooltip_hint
    finally:
        pane.dispose()


@pytest.mark.asyncio
async def test_copy_payloads_are_owned_by_the_view_model() -> None:
    """The view copies prepared values, it does not reassemble paths.

    The border label is decorated with a clipboard glyph for display and the
    border truncates it on a narrow pane, so scraping the chrome back out would
    copy something the user did not ask for.
    """
    pane = await _make_pane(await _seed_fs())
    try:
        vm = pane.viewmodel
        assert vm.copy_path == "/"
        # Cursor starts on the first entry of the listing.
        assert vm.copy_selected_path is not None
        assert vm.copy_selected_path.startswith("/")
        assert not vm.copy_selected_path.endswith("/")
        # No doubled separator at the root.
        assert "//" not in vm.copy_selected_path
    finally:
        pane.dispose()


@pytest.mark.asyncio
async def test_copy_selected_path_joins_without_doubling_the_separator() -> None:
    pane = await _make_pane(await _seed_fs())
    try:
        await pane.navigate_to(PathRef(("b",)))
        assert pane.viewmodel.copy_path == "/b"
        # A subdirectory listing opens with the cursor on the ``..`` link, so
        # step onto a real entry before asking for its path.
        entries = pane.filtered_entries
        target = next(i for i, entry in enumerate(entries) if entry.name != "..")
        pane.move_cursor_command.execute(target - pane.cursor_index)

        selected = pane.viewmodel.copy_selected_path
        assert selected is not None
        assert "//" not in selected
        assert selected.startswith("/b/")
    finally:
        pane.dispose()


@pytest.mark.asyncio
async def test_copy_selected_path_is_none_on_the_parent_link() -> None:
    """``..`` is a navigation affordance, not a file the user could mean."""
    pane = await _make_pane(await _seed_fs())
    try:
        await pane.navigate_to(PathRef(("b",)))
        entries = pane.filtered_entries
        parent_index = next((i for i, entry in enumerate(entries) if entry.name == ".."), None)
        # Asserted, not skipped. The presence of ``..`` is the precondition this
        # test exists to exercise, so a PaneVM that stopped emitting the parent
        # link -- a real navigation regression -- would have turned this test
        # green by skipping instead of failing.
        assert parent_index is not None, "a subdirectory listing must expose a '..' parent link"
        pane.move_cursor_command.execute(parent_index - pane.cursor_index)
        assert pane.filtered_entries[pane.cursor_index].name == ".."

        assert pane.viewmodel.copy_selected_path is None
    finally:
        pane.dispose()


@pytest.mark.asyncio
async def test_copy_selected_path_is_none_when_the_listing_is_empty() -> None:
    pane = await _make_pane(InMemoryFS())
    try:
        assert pane.viewmodel.copy_selected_path is None
        # The location itself is still copyable.
        assert pane.viewmodel.copy_path == "/"
    finally:
        pane.dispose()


@pytest.mark.asyncio
async def test_summary_count_excludes_the_synthetic_parent_link() -> None:
    """``..`` is navigation chrome, not an object in the listing.

    ``_marked_entries`` already filters ``is_parent_link``, so counting it in
    the object total made the two halves of the same summary line disagree: a
    three-file subdirectory rendered "4 obj", and an empty subdirectory
    rendered "1 obj" rather than "empty".
    """
    pane = await _make_pane(await _seed_fs())
    try:
        await pane.navigate_to(PathRef(("b",)))
        entries = pane.filtered_entries
        real = [entry for entry in entries if entry.name != ".."]
        assert len(entries) == len(real) + 1, "expected a parent link in this listing"

        summary = pane.viewmodel.summary
        assert summary.startswith(f"{len(real)} obj"), summary
    finally:
        pane.dispose()


@pytest.mark.asyncio
async def test_set_marked_entries_marks_notifies_once_and_skips_the_parent_link() -> None:
    """``set_marked_entries`` is the only supported way to mark from outside.

    ``app.py``'s copy/delete workers flash the cursor-fallback target as
    marked for the duration of a transfer. They used to call
    ``EntryVM.set_marked`` directly, which mutates the model and emits a
    per-entry message that nothing subscribes to any more — the rows repaint
    off the pane-level ``"viewmodel"`` notify. This pins the three properties
    the pane depends on: the synthetic ``..`` row stays unmarkable, a batch
    costs exactly one notify, and a no-op batch costs none (so the ``finally``
    clear on a transfer that never marked anything cannot start a repaint).
    """
    fs = await _seed_fs()
    hub = _hub()
    notified: list[str] = []
    hub.messages.subscribe(
        on_next=lambda m: notified.append(getattr(m, "property_name", "")) if m else None
    )
    pane = await _make_pane(fs, hub=hub)
    try:
        await pane.navigate_to(PathRef(("b",)))
        entries = pane.filtered_entries
        parent = entries[0]
        assert parent.is_parent_link, "expected a parent link first in this listing"
        targets = [entry for entry in entries if not entry.is_parent_link]
        assert targets, "expected at least one real entry to mark"

        notified.clear()
        pane.set_marked_entries(entries, marked=True)

        assert parent.is_marked is False, "the .. row must stay unmarkable"
        assert all(entry.is_marked for entry in targets)
        assert notified.count("viewmodel") == 1, notified

        notified.clear()
        pane.set_marked_entries(entries, marked=True)
        assert notified.count("viewmodel") == 0, "re-marking must be a silent no-op"

        notified.clear()
        pane.set_marked_entries(entries, marked=False)
        assert all(not entry.is_marked for entry in targets)
        assert notified.count("viewmodel") == 1, notified
    finally:
        pane.dispose()


@pytest.mark.asyncio
async def test_replace_entries_publishes_one_collection_event_per_listing() -> None:
    """A listing rebuild must cost ONE collection event, not 2N.

    ``_replace_entries`` removes every old child and appends every new one.
    Unbatched that is ``2N`` ``CollectionChangedEvent``s, and the
    ``FilteredCompositeVM`` fed by this composite subscribes with
    ``lambda _: self._recompute()`` — so every one of them re-derived the
    whole visible list, making a plain directory listing quadratic in the row
    count. ``CompositeVM.batch_update()`` coalesces the burst into a single
    ``action="reset"``, which costs exactly one recompute.

    The count must be invariant in the number of entries; that invariance,
    not the absolute number, is what proves the batch is open across both
    loops rather than around one of them.
    """

    async def _events_for(count: int) -> tuple[int, str | None, int]:
        fs = InMemoryFS()
        for index in range(count):
            await fs.write_stream(PathRef((f"f{index:03d}.txt",)), _astream(b"x"))
        pane = await _make_pane(fs)
        try:
            events: list[object] = []
            sub = pane._inner.on_collection_changed.subscribe(on_next=events.append)
            try:
                await pane.refresh()
            finally:
                sub.dispose()
            first_action = getattr(events[0], "action", None) if events else None
            return len(events), first_action, len(pane.filtered_entries)
        finally:
            pane.dispose()

    small = await _events_for(4)
    large = await _events_for(40)

    # Named preconditions: the listings really did differ in size, so the
    # equal event counts below are not two empty rebuilds agreeing.
    assert small[2] == 4
    assert large[2] == 40

    assert small[0] == 1, f"expected one coalesced event, got {small[0]}"
    assert small[0] == large[0], f"event count scaled with row count: {small[0]} vs {large[0]}"
    assert small[1] == "reset"
    assert large[1] == "reset"


def test_replace_entries_writes_the_cursor_only_after_a_filter_recompute() -> None:
    """Structural guard for Constraint 20: the ordering in ``_replace_entries``.

    This one is deliberately a source-shape assertion, in the spirit of
    ``tests/docs/test_snapshot_harness.py``. The invariant cannot be pinned
    by behaviour through the public surface: leaving the batch already emits
    one coalesced ``CollectionChangedEvent(action="reset")``, which runs
    ``FilteredCompositeVM._recompute()`` → ``_sync_filtered_from_composite()``
    and re-derives ``_filtered`` from the NEW ``_entries``; and
    ``set_predicate`` calls ``_recompute()`` directly rather than through
    ``on_collection_changed``, so even a ``_recompute_filtered()`` moved
    inside the batch would still refresh it. Both regressions therefore run
    green end-to-end (verified by mutation), while still destroying the
    margin that keeps the cursor write off a stale ``_filtered`` — the
    failure mode pinned by
    ``test_a_stale_filtered_list_makes_the_cursor_write_raise``.
    """
    lines = inspect.getsource(PaneVM._replace_entries).splitlines()

    def _sole_index(statement: str) -> int:
        matches = [i for i, line in enumerate(lines) if line.strip() == statement]
        assert len(matches) == 1, f"expected exactly one {statement!r}, found {len(matches)}"
        return matches[0]

    def _indent(index: int) -> int:
        return len(lines[index]) - len(lines[index].lstrip())

    batch_at = _sole_index("with self._inner.batch_update():")
    recompute_at = _sole_index("self._recompute_filtered()")
    cursor_at = _sole_index("self._cursor_index = 0")

    for label, index in (("_recompute_filtered()", recompute_at), ("_cursor_index = 0", cursor_at)):
        assert index > batch_at, f"{label} must follow the batch block, not precede it"
        assert _indent(index) == _indent(batch_at), (
            f"{label} is nested inside `with self._inner.batch_update():` — "
            "Constraint 20 keeps that sequence outside the batch"
        )
    assert recompute_at < cursor_at, (
        "_recompute_filtered() must run BEFORE the cursor write; the setter "
        "dereferences self._entries[self._filtered[...]]"
    )


@pytest.mark.asyncio
async def test_a_stale_filtered_list_makes_the_cursor_write_raise() -> None:
    """Pin the mechanism the ordering above defends against.

    The cursor setter maps a filtered position to an entry inner through
    ``self._filtered``, which holds indices into ``self._entries``. Drive the
    exact window a cursor write inside the batch would sit in — ``_entries``
    already swapped for a shorter listing, ``_filtered`` not yet re-derived —
    and the setter indexes past the end. This is what makes the ordering
    load-bearing rather than decorative.
    """
    fs = InMemoryFS()
    for index in range(10):
        await fs.write_stream(PathRef((f"row{index}.txt",)), _astream(b"x"))
    pane = await _make_pane(fs)
    original = pane._entries
    try:
        pane.set_filter_command.execute("row7")
        assert pane._filtered == (7,), "precondition: the filter narrowed to the 8th entry"

        pane._entries = original[:2]
        with pytest.raises(IndexError):
            pane._cursor_index = 0
    finally:
        pane._entries = original
        pane.dispose()


@pytest.mark.asyncio
async def test_a_filtered_listing_survives_a_shrinking_refresh() -> None:
    """A filter that outlives its listing: refresh must not raise, and must
    re-derive the filtered view from the NEW entries.

    The filter narrows a 10-entry listing to one row, then the re-list
    returns 2 rows that the filter no longer matches. The filtered view must
    come back empty rather than still pointing at a row index that no longer
    exists. (The ordering inside ``_replace_entries`` is pinned structurally
    by ``test_replace_entries_writes_the_cursor_only_after_a_filter_recompute``;
    this test covers the end-to-end outcome, not the statement order.)
    """
    fs = InMemoryFS()
    for index in range(10):
        await fs.write_stream(PathRef((f"row{index}.txt",)), _astream(b"x"))
    pane = await _make_pane(fs)
    try:
        pane.set_filter_command.execute("row7")
        assert [entry.name for entry in pane.filtered_entries] == ["row7.txt"]
        assert pane.cursor_index == 0

        # Shrink the backing store under the pane, then re-list.
        for index in range(10):
            if index not in (0, 1):
                await fs.delete(PathRef((f"row{index}.txt",)))
        await pane.refresh()

        # No IndexError, and the filtered view is re-derived from the NEW
        # list: "row7" matches nothing in it, so it is empty rather than
        # stale.
        assert [entry.name for entry in pane.entries] == ["row0.txt", "row1.txt"]
        assert pane.filtered_entries == ()
        assert pane._filtered == ()
        assert pane.selected_entry is None
    finally:
        pane.dispose()


@pytest.mark.asyncio
async def test_a_shrinking_refresh_rederives_the_filter_from_the_new_listing() -> None:
    """The companion case where the filter still matches after the shrink.

    Here the cursor write really does dereference ``_entries`` (the previous
    test's filter matches nothing, so the setter takes its ``if not
    self._filtered`` early return). The surviving row sits at index 8 of the
    old listing and index 2 of the new one, so a filtered list carried over
    from before the refresh would either raise or select the wrong row.
    """
    fs = InMemoryFS()
    for index in range(10):
        await fs.write_stream(PathRef((f"row{index}.txt",)), _astream(b"x"))
    pane = await _make_pane(fs)
    try:
        pane.set_filter_command.execute("row8")
        assert pane._filtered == (8,)

        for index in range(10):
            if index not in (0, 1, 8):
                await fs.delete(PathRef((f"row{index}.txt",)))
        await pane.refresh()

        assert [entry.name for entry in pane.entries] == ["row0.txt", "row1.txt", "row8.txt"]
        assert pane._filtered == (2,), "the filtered index was re-derived against the new listing"
        assert [entry.name for entry in pane.filtered_entries] == ["row8.txt"]
        assert pane.cursor_index == 0
        selected = pane.selected_entry
        assert selected is not None
        assert selected.name == "row8.txt"
    finally:
        pane.dispose()
