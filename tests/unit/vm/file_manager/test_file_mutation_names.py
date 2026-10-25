"""Basenames and explicit rename targets never become paths or cursor retargets."""

import pytest

from aws_tui.domain.filesystem import NotFoundError, PathRef, ProviderError
from tests.unit.vm.file_manager.test_pane_vm import _make_pane, _seed_fs


@pytest.mark.parametrize(
    "name", ["", "   ", ".", "..", "a/b", "a\\b", "a\x00b", "a\nb", "a\x85b", "a\x9bb"]
)
def test_invalid_basename_is_rejected_without_io(name):
    from aws_tui.vm.file_manager.file_names import require_entry_name

    with pytest.raises(ProviderError):
        require_entry_name(name)


@pytest.mark.parametrize("name", ["table_1", "a b", " a ", "日本語"])
def test_valid_basename_is_preserved(name):
    from aws_tui.vm.file_manager.file_names import require_entry_name

    assert require_entry_name(name) is None


@pytest.mark.asyncio
async def test_rename_binds_explicit_marked_entry_and_preserves_unrelated_cursor():
    fs = await _seed_fs()
    pane = await _make_pane(fs)
    target = next(row for row in pane.entries if row.entry.name == "a.txt")
    pane.set_marked_entries((target,), marked=True)
    pane.move_cursor_to(
        next(i for i, row in enumerate(pane.filtered_entries) if row.entry.name == "c.json")
    )
    try:
        await pane.rename_entry(target, "z.txt")
        assert (await fs.stat(PathRef(("z.txt",)))).name == "z.txt"
        with pytest.raises(NotFoundError):
            await fs.stat(PathRef(("a.txt",)))
        assert pane.selected_entry.entry.name == "c.json"
    finally:
        pane.dispose()


@pytest.mark.asyncio
async def test_rename_rejects_parent_and_retired_listing_entry():
    fs = await _seed_fs()
    pane = await _make_pane(fs)
    original = next(row for row in pane.entries if row.entry.name == "a.txt")
    await pane.navigate_to(PathRef(("b",)))
    parent = pane.entries[0]
    assert parent.is_parent_link
    try:
        for target in (parent, original):
            with pytest.raises(ProviderError):
                await pane.rename_entry(target, "renamed")
        assert (await fs.stat(PathRef(("a.txt",)))).name == "a.txt"
    finally:
        pane.dispose()


@pytest.mark.asyncio
async def test_new_folder_refresh_preserves_unrelated_cursor():
    fs = await _seed_fs()
    pane = await _make_pane(fs)
    pane.move_cursor_to(
        next(i for i, row in enumerate(pane.filtered_entries) if row.entry.name == "c.json")
    )
    try:
        await pane.make_directory("new-folder")
        assert pane.selected_entry.entry.name == "c.json"
        assert any(row.entry.name == "new-folder" for row in pane.entries)
    finally:
        pane.dispose()


@pytest.mark.asyncio
async def test_rename_failure_refreshes_partial_provider_outcome(monkeypatch):
    fs = await _seed_fs()
    pane = await _make_pane(fs)
    target = next(row for row in pane.entries if row.entry.name == "a.txt")
    original = fs.rename

    async def partial(source, destination):
        await original(source, destination)
        raise ProviderError("controlled partial outcome")

    monkeypatch.setattr(fs, "rename", partial)
    try:
        with pytest.raises(ProviderError, match="controlled partial"):
            await pane.rename_entry(target, "renamed.txt")
        assert any(row.entry.name == "renamed.txt" for row in pane.entries)
        assert not any(row.entry.name == "a.txt" for row in pane.entries)
    finally:
        pane.dispose()


@pytest.mark.asyncio
async def test_navigation_during_rename_cannot_retarget_or_refresh_the_new_location(monkeypatch):
    import asyncio

    fs = await _seed_fs()
    pane = await _make_pane(fs)
    target = next(row for row in pane.entries if row.entry.name == "a.txt")
    original = fs.rename
    entered, release = asyncio.Event(), asyncio.Event()

    async def gated(source, destination):
        entered.set()
        await release.wait()
        await original(source, destination)

    monkeypatch.setattr(fs, "rename", gated)
    task = asyncio.create_task(pane.rename_entry(target, "renamed.txt"))
    await entered.wait()
    await pane.navigate_to(PathRef(("b",)))
    pane.move_cursor_to(1)
    selected = pane.selected_entry
    release.set()
    try:
        await task
        assert pane.path == PathRef(("b",))
        assert pane.selected_entry is selected
        assert pane.selected_entry.entry.name == "nested.bin"
        assert (await fs.stat(PathRef(("renamed.txt",)))).name == "renamed.txt"
    finally:
        release.set()
        await task
        pane.dispose()


@pytest.mark.asyncio
async def test_protection_changes_deny_captured_rename_and_folder_without_replay():
    from aws_tui.domain.filesystem import PermissionDeniedError
    from aws_tui.domain.mutation_guard import GuardedRemote, fixed_mutation_check

    protected = False

    async def check():
        await fixed_mutation_check(protected)()

    fs = await _seed_fs()
    pane = await _make_pane(GuardedRemote(fs, check))
    target = next(row for row in pane.entries if row.entry.name == "a.txt")
    protected = True
    try:
        with pytest.raises(PermissionDeniedError):
            await pane.rename_entry(target, "renamed.txt")
        with pytest.raises(PermissionDeniedError):
            await pane.make_directory("new-folder")
        protected = False
        assert (await fs.stat(PathRef(("a.txt",)))).name == "a.txt"
        with pytest.raises(NotFoundError):
            await fs.stat(PathRef(("renamed.txt",)))
        with pytest.raises(NotFoundError):
            await fs.stat(PathRef(("new-folder",)))
    finally:
        pane.dispose()


@pytest.mark.asyncio
async def test_rename_refuses_retained_source_row_during_destination_loading(monkeypatch):
    import asyncio

    fs = await _seed_fs()
    pane = await _make_pane(fs)
    target = next(row for row in pane.entries if row.name == "a.txt")
    entered, release = asyncio.Event(), asyncio.Event()
    original_list = fs.list

    async def gated_list(path):
        if path == PathRef(("b",)):
            entered.set()
            await release.wait()
        return await original_list(path)

    monkeypatch.setattr(fs, "list", gated_list)
    calls = []
    original_rename = fs.rename

    async def record(source, destination):
        calls.append((source, destination))
        return await original_rename(source, destination)

    monkeypatch.setattr(fs, "rename", record)
    navigation = asyncio.create_task(pane.navigate_to(PathRef(("b",))))
    await entered.wait()
    try:
        rename = asyncio.create_task(pane.rename_entry(target, "changed.txt"))
        await asyncio.sleep(0)
        release.set()
        with pytest.raises(ProviderError):
            await rename
        assert calls == []
    finally:
        release.set()
        await navigation
        pane.dispose()


@pytest.mark.asyncio
async def test_name_mutation_preserves_hidden_unrelated_marks():
    fs = await _seed_fs()
    pane = await _make_pane(fs)
    hidden = next(row for row in pane.entries if row.name == "c.json")
    pane.set_marked_entries((hidden,), marked=True)
    pane.set_filter_command.execute("a.txt")
    assert not pane.marked_entries
    try:
        await pane.make_directory("new-folder")
        pane.set_filter_command.execute("")
        assert {row.name for row in pane.marked_entries} == {"c.json"}
    finally:
        pane.dispose()


@pytest.mark.asyncio
async def test_refused_rename_preserves_original_cursor(monkeypatch):
    fs = await _seed_fs()
    pane = await _make_pane(fs)
    pane.move_cursor_to(
        next(i for i, row in enumerate(pane.filtered_entries) if row.name == "c.json")
    )
    target = pane.selected_entry

    async def refused(source, destination):
        raise ProviderError("controlled refusal")

    monkeypatch.setattr(fs, "rename", refused)
    try:
        with pytest.raises(ProviderError, match="controlled refusal"):
            await pane.rename_entry(target, "renamed.txt")
        assert pane.selected_entry.name == "c.json"
    finally:
        pane.dispose()


@pytest.mark.asyncio
async def test_disposed_pane_cannot_create_folder():
    fs = await _seed_fs()
    pane = await _make_pane(fs)
    pane.dispose()
    with pytest.raises(ProviderError):
        await pane.make_directory("new-folder")
    with pytest.raises(NotFoundError):
        await fs.stat(PathRef(("new-folder",)))


@pytest.mark.asyncio
async def test_move_refresh_keeps_unrelated_cursor_and_destination_marks(tmp_path):
    from tests.unit.vm.file_manager.test_dual_pane_vm import _astream, _make_dual

    dual, _ = await _make_dual(tmp_path)
    try:
        await dual.right.provider.write_stream(PathRef(("a.txt",)), _astream(b"a"))
        await dual.right.provider.write_stream(PathRef(("z.txt",)), _astream(b"z"))
        await dual.right.refresh()
        dual.left.mark_at(0)
        dual.left.move_cursor_to(1)
        dual.right.move_cursor_to(1)
        dual.right.mark_at(1)
        await dual.move_across()
        assert dual.left.selected_entry.name == "beta.txt"
        assert dual.right.selected_entry.name == "z.txt"
        assert {row.name for row in dual.right.marked_entries} == {"z.txt"}
    finally:
        dual.dispose()


@pytest.mark.asyncio
async def test_move_completion_does_not_refresh_newly_navigated_destination(tmp_path, monkeypatch):
    import asyncio

    from tests.unit.vm.file_manager.test_dual_pane_vm import _astream, _make_dual

    dual, _ = await _make_dual(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    original = dual.left.provider.read_stream

    async def gated(path, **kwargs):
        entered.set()
        await release.wait()
        return await original(path, **kwargs)

    monkeypatch.setattr(dual.left.provider, "read_stream", gated)
    await dual.right.provider.mkdir(PathRef(("folder",)))
    await dual.right.provider.write_stream(PathRef(("folder", "keep.txt")), _astream(b"keep"))
    dual.left.mark_at(0)
    task = asyncio.create_task(dual.move_across())
    await entered.wait()
    try:
        await dual.right.navigate_to(PathRef(("folder",)))
        dual.right.move_cursor_to(1)
        selected = dual.right.selected_entry
        release.set()
        await task
        assert dual.right.path == PathRef(("folder",))
        assert dual.right.selected_entry is selected
        assert dual.right.selected_entry.name == "keep.txt"
        assert (await dual.right.provider.stat(PathRef(("alpha.txt",)))).name == "alpha.txt"
    finally:
        release.set()
        await task
        dual.dispose()


@pytest.mark.asyncio
async def test_move_refreshes_partial_outcome_even_when_batch_history_settlement_fails(
    tmp_path, monkeypatch
):
    from tests.unit.vm.file_manager.test_dual_pane_vm import _make_dual

    dual, _ = await _make_dual(tmp_path)

    async def refused(*args):
        raise ProviderError("controlled settlement failure")

    monkeypatch.setattr(dual.transfer_runtime, "settle_batch", refused)
    dual.left.mark_at(0)
    try:
        with pytest.raises(ProviderError, match="controlled settlement"):
            await dual.move_across()
        assert {row.name for row in dual.left.entries} == {"beta.txt"}
        assert {row.name for row in dual.right.entries} == {"alpha.txt"}
    finally:
        dual.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["b", "a.txt"])
async def test_new_folder_refuses_existing_entry_before_mkdir(monkeypatch, name):
    from aws_tui.domain.filesystem import ConflictError

    fs = await _seed_fs()
    pane = await _make_pane(fs)
    calls = []
    original = fs.mkdir

    async def mkdir(path):
        calls.append(path)
        await original(path)

    monkeypatch.setattr(fs, "mkdir", mkdir)
    try:
        with pytest.raises(ConflictError):
            await pane.make_directory(name)
        assert calls == []
        assert (await fs.stat(PathRef((name,)))).name == name
    finally:
        pane.dispose()
