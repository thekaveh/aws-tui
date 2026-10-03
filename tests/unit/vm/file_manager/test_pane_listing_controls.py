"""Loaded listing controls share one ordered, actionable projection."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from aws_tui.demo.in_memory_fs import InMemoryFS
from aws_tui.domain.filesystem import EntryKind, FileEntry, PathRef
from aws_tui.vm.file_manager import pane_vm
from aws_tui.vm.file_manager.pane_vm import PaneState
from tests.unit.vm.file_manager.test_pane_vm import _make_pane


class CountingFS(InMemoryFS):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[PathRef] = []

    async def list(self, path: PathRef) -> list[FileEntry]:
        self.calls.append(path)
        return [
            FileEntry("zeta", EntryKind.FILE, 20, datetime(2026, 1, 2, tzinfo=UTC)),
            FileEntry("alpha", EntryKind.FILE, 10, datetime(2026, 1, 1)),
            FileEntry(
                "Beta",
                EntryKind.FILE,
                10,
                datetime(2026, 1, 1, 1, tzinfo=timezone(timedelta(hours=1))),
            ),
            FileEntry("unknown", EntryKind.FILE, None, None),
            FileEntry("directory", EntryKind.DIRECTORY, None, None),
        ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "descending", "expected"),
    [
        ("name", False, ["alpha", "Beta", "directory", "unknown", "zeta"]),
        ("name", True, ["zeta", "unknown", "directory", "Beta", "alpha"]),
        ("size", False, ["alpha", "Beta", "zeta", "directory", "unknown"]),
        ("size", True, ["zeta", "alpha", "Beta", "directory", "unknown"]),
        ("modified", False, ["alpha", "Beta", "zeta", "directory", "unknown"]),
        ("modified", True, ["zeta", "alpha", "Beta", "directory", "unknown"]),
    ],
)
async def test_sort_preserves_loaded_identity_marks_and_cursor(
    field: str, descending: bool, expected: list[str]
) -> None:
    fs = CountingFS()
    pane = await _make_pane(fs)
    try:
        await pane.navigate_to(PathRef(("folder",)))
        original = pane.entries
        pane.move_cursor_to(2)
        selected = pane.selected_entry
        pane.select_all_command.execute()
        calls = fs.calls.copy()
        notices: list[str] = []
        pane.on_property_changed.subscribe(notices.append)
        pane.set_sort(pane_vm.PaneSortField(field), descending=descending)
        assert [e.name for e in pane.filtered_entries] == ["..", *expected]
        assert pane.entries == original
        assert pane.selected_entry is selected
        assert all(e.is_marked for e in pane.entries if not e.is_parent_link)
        assert pane.viewmodel.selection_count == 5
        assert "filtered_entries" in notices
        assert "viewmodel" in notices
        assert field in pane.viewmodel.sort_status_text
        assert fs.calls == calls
    finally:
        pane.dispose()


@pytest.mark.asyncio
async def test_parent_filter_status_and_hidden_find_are_loaded_only() -> None:
    fs = CountingFS()
    pane = await _make_pane(fs)
    try:
        await pane.navigate_to(PathRef(("folder",)))
        calls = fs.calls.copy()
        pane.set_filter_command.execute("[literal]")
        assert [e.name for e in pane.filtered_entries] == [".."]
        assert "[literal]" in pane.viewmodel.filter_status_text
        assert "0 / 5 matches" in pane.viewmodel.filter_status_text
        assert "No matches" in pane.viewmodel.filter_status_text
        assert [e.name for e in pane.find_entries("AL")] == ["alpha"]
        assert [e.name for e in pane.find_entries("ET")] == ["Beta", "zeta", "directory"]
        assert [e.name for e in pane.find_entries("drt")] == ["directory"]
        assert pane.find_entries("absent") == ()
        assert [e.name for e in pane.find_entries("")] == [
            "alpha",
            "Beta",
            "directory",
            "unknown",
            "zeta",
        ]
        match = pane.find_entries("alpha")[0]
        revision = pane.listing_revision
        assert not pane.select_found_entry(match, revision=revision - 1)
        assert pane.filter_text == "[literal]"
        assert pane.select_found_entry(match, revision=revision)
        assert pane.filter_text == ""
        assert pane.selected_entry is match
        assert fs.calls == calls
        pane.set_filter_command.execute("alpha")
        assert pane.select_found_entry(match, revision=revision)
        assert pane.filter_text == "alpha"
        await pane.refresh()
        assert not pane.select_found_entry(match, revision=pane.listing_revision)
        assert not pane.select_found_entry(pane.entries[0], revision=revision)
        pane._set_state(PaneState.LOADING)
        assert pane.find_entries("") == ()
        assert not pane.select_found_entry(pane.entries[1], revision=pane.listing_revision)
        revision = pane.listing_revision
        pane.dispose()
        assert pane.listing_revision > revision
        assert pane.find_entries("") == ()
    finally:
        pane.dispose()


@pytest.mark.asyncio
async def test_filter_resets_on_directory_and_source_change_but_refresh_keeps_it() -> None:
    pane = await _make_pane(CountingFS())
    try:
        notices: list[str] = []
        pane.on_property_changed.subscribe(notices.append)
        pane.set_sort(pane_vm.PaneSortField.SIZE, descending=True)
        pane.set_filter_command.execute("alpha")
        await pane.refresh()
        assert pane.filter_text == "alpha"
        notices.clear()
        await pane.navigate_to(PathRef(("folder",)))
        assert pane.filter_text == ""
        assert "filter_text" in notices
        pane.set_filter_command.execute("Beta")
        notices.clear()
        await pane.swap_provider(CountingFS())
        assert pane.viewmodel.sort_status_text == "Sort: size descending"
        assert pane.filter_text == ""
        assert "filter_text" in notices
    finally:
        pane.dispose()


@pytest.mark.asyncio
async def test_credential_recovery_resets_filter_observably() -> None:
    pane = await _make_pane(CountingFS())
    try:
        pane.set_filter_command.execute("alpha")
        notices: list[str] = []
        pane.on_property_changed.subscribe(notices.append)
        staged = await pane.stage_provider_recovery(
            CountingFS(),
            path=pane.path,
            identity_label=None,
            path_protocol="",
            connection_key=("aws", "test"),
        )
        revision = pane.listing_revision
        pane.commit_provider_recovery(staged)
        assert pane.filter_text == ""
        assert "filter_text" in notices
        assert pane.listing_revision > revision
    finally:
        pane.dispose()


@pytest.mark.asyncio
async def test_controls_do_not_call_any_provider_operation(monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest.mock import Mock

    fs = CountingFS()
    operations = [
        "list",
        "stat",
        "mkdir",
        "delete",
        "delete_empty_directory",
        "rename",
        "read_stream",
        "write_stream",
    ]
    spies = [Mock(wraps=getattr(fs, name)) for name in operations]
    for name, spy in zip(operations, spies, strict=True):
        monkeypatch.setattr(fs, name, spy)
    pane = await _make_pane(fs)
    try:
        before = [spy.call_args_list.copy() for spy in spies]
        pane.set_filter_command.execute("Beta")
        pane.set_sort(pane_vm.PaneSortField.SIZE, descending=True)
        match = pane.find_entries("alpha")[0]
        assert pane.find_entries("missing") == ()
        assert not pane.select_found_entry(match, revision=pane.listing_revision - 1)
        assert pane.select_found_entry(match, revision=pane.listing_revision)
        assert [spy.call_args_list for spy in spies] == before
    finally:
        pane.dispose()


@pytest.mark.asyncio
async def test_empty_listing_status_excludes_parent_and_sort_persists() -> None:
    pane = await _make_pane(InMemoryFS())
    try:
        pane.set_sort(pane_vm.PaneSortField.NAME)
        await pane.navigate_to(PathRef(("empty",)))
        # A missing directory is an error; a real empty one has only parent chrome.
        await pane.provider.mkdir(PathRef(("empty",)))
        await pane.refresh()
        pane.set_filter_command.execute("missing")
        assert [entry.name for entry in pane.filtered_entries] == [".."]
        assert "0 / 0 matches" in pane.viewmodel.filter_status_text
        assert pane.find_entries("") == ()
        assert pane.viewmodel.sort_status_text == "Sort: name ascending"
    finally:
        pane.dispose()


@pytest.mark.asyncio
async def test_casefold_name_ties_use_original_name_for_sort_and_find() -> None:
    class CaseFS(CountingFS):
        async def list(self, path: PathRef) -> list[FileEntry]:
            self.calls.append(path)
            return [
                FileEntry(name, EntryKind.FILE, 10, None) for name in ("alpha", "Alpha", "ALPHA")
            ]

    pane = await _make_pane(CaseFS())
    try:
        assert [entry.name for entry in pane.find_entries("a")] == ["ALPHA", "Alpha", "alpha"]
        pane.set_sort(pane_vm.PaneSortField.NAME)
        assert [entry.name for entry in pane.filtered_entries] == ["ALPHA", "Alpha", "alpha"]
        pane.set_sort(pane_vm.PaneSortField.NAME, descending=True)
        assert [entry.name for entry in pane.filtered_entries] == ["alpha", "Alpha", "ALPHA"]
        pane.set_sort(pane_vm.PaneSortField.SIZE, descending=True)
        assert [entry.name for entry in pane.filtered_entries] == ["ALPHA", "Alpha", "alpha"]
    finally:
        pane.dispose()
