"""Running-app contracts for loaded listing controls."""

import pytest
from textual.widgets import Input, Static

from aws_tui.app import AwsTuiApp
from aws_tui.ui.widgets.pane import EntryRow, Pane
from tests.helpers import drain_workers
from tests.integration.test_copy_delete_actions import _use_injected_s3_connection
from tests.integration.test_keyboard_selection import seeded


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [(120, 40), (80, 24)])
async def test_live_filter_and_persistent_clear(app_context_factory, size):
    fs = await seeded()
    ctx = app_context_factory(fs=fs)
    _use_injected_s3_connection(ctx)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=size) as pilot:
        await drain_workers(app)
        await pilot.pause()
        pane = app._focused_file_pane()
        widget = next(w for w in app.query(Pane) if w.vm is pane)
        baseline = list(fs.calls)
        await pilot.press("slash")
        assert isinstance(app.focused, Input)
        await pilot.press(*"ALPHA")
        await pilot.pause()
        assert [e.name for e in pane.filtered_entries] == ["alpha.txt"]
        await pilot.press("escape")
        await pilot.pause()
        assert pane.filter_text == "ALPHA"
        assert "1 / 3 matches" in str(widget.query_one(".pane-filter-status", Static).render())
        assert [r.entry_vm.name for r in widget.query(EntryRow)] == ["alpha.txt"]
        app.save_screenshot(f"/tmp/aws-tui-240-filter-{size[0]}.svg")
        await pilot.click(widget.query_one(".pane-clear-filter"))
        await pilot.pause()
        assert pane.filter_text == ""
        assert fs.calls == baseline


@pytest.mark.asyncio
@pytest.mark.parametrize("right", [False, True])
async def test_hidden_find_cancel_commit_and_stale(app_context_factory, right):
    ctx = app_context_factory(fs=await seeded())
    _use_injected_s3_connection(ctx)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await drain_workers(app)
        if right:
            await ctx.root_vm.content_host.current.right.swap_provider(await seeded())
            await pilot.press("tab")
        pane = app._focused_file_pane()
        await pilot.press("slash", *"alpha", "escape")
        pane.entries[-1].set_marked(True)
        baseline = list(pane.provider.calls)
        await pilot.press("ctrl+p", *"gamma")
        assert "reactivates retained marks" in str(
            app.screen.query_one(".listing-status", Static).render()
        )
        await pilot.press("escape")
        assert pane.filter_text == "alpha"
        await pilot.press("ctrl+p", *"notfound")
        assert "No matches" in str(app.screen.query_one("#find-empty", Static).render())
        await pilot.press("enter")
        assert len(app.screen_stack) == 2
        await pilot.press("escape", "ctrl+p", *"gamma", "enter")
        await pilot.pause()
        assert pane.filter_text == ""
        assert pane.selected_entry.name == "gamma.txt"
        assert pane.provider.calls == baseline
        await pilot.press("ctrl+p")
        stale = app.screen
        await pane.swap_provider(await seeded())
        await pilot.pause()
        assert len(app.screen_stack) == 1
        assert not stale.valid()


@pytest.mark.asyncio
@pytest.mark.parametrize("choice", range(6))
async def test_palette_sort_all_six_choices(app_context_factory, choice):
    from datetime import UTC, datetime

    from textual.widgets import OptionList

    from aws_tui.domain.filesystem import EntryKind, FileEntry
    from aws_tui.ui.widgets.pane_listing_controls import SortPaneModal
    from tests.integration.test_keyboard_selection import RecordingFS

    class DiscriminatingFS(RecordingFS):
        async def list(self, path):
            self.calls.append(("list", path))
            return [
                FileEntry(
                    name=name,
                    kind=EntryKind.FILE,
                    size=size,
                    modified=datetime(2026, 1, day, tzinfo=UTC),
                )
                for name, size, day in (
                    ("gamma.txt", 20, 1),
                    ("alpha.txt", 30, 2),
                    ("beta.txt", 10, 3),
                )
            ]

    expected_field, expected_descending, expected_rows = (
        ("name", False, ["alpha.txt", "beta.txt", "gamma.txt"]),
        ("name", True, ["gamma.txt", "beta.txt", "alpha.txt"]),
        ("size", False, ["beta.txt", "gamma.txt", "alpha.txt"]),
        ("size", True, ["alpha.txt", "gamma.txt", "beta.txt"]),
        ("modified", False, ["gamma.txt", "alpha.txt", "beta.txt"]),
        ("modified", True, ["beta.txt", "alpha.txt", "gamma.txt"]),
    )[choice]
    ctx = app_context_factory(fs=DiscriminatingFS())
    _use_injected_s3_connection(ctx)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await drain_workers(app)
        pane = app._focused_file_pane()
        original = pane.selected_entry
        baseline = list(pane.provider.calls)
        await pilot.press("colon", *"Sort loaded entries", "enter")
        await pilot.pause()
        assert isinstance(app.screen, SortPaneModal)
        assert app.screen.query_one(OptionList).option_count == 6
        await pilot.press(*(["down"] * choice), "enter")
        await pilot.pause()
        assert pane.selected_entry is original
        assert pane._sort_field.value == expected_field
        assert pane._sort_descending is expected_descending
        widget = next(w for w in app.query(Pane) if w.vm is pane)
        expected_status = (
            f"Sort: {expected_field} {'descending' if expected_descending else 'ascending'}"
        )
        assert str(widget.query_one(".pane-sort-status", Static).render()) == expected_status
        assert [row.entry_vm.name for row in widget.query(EntryRow)] == expected_rows
        assert [entry.name for entry in pane.filtered_entries] == expected_rows
        assert pane.provider.calls == baseline


@pytest.mark.asyncio
async def test_filter_editor_slash_modal_containment_literal_query(app_context_factory):
    ctx = app_context_factory(fs=await seeded())
    _use_injected_s3_connection(ctx)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(80, 24)) as pilot:
        await drain_workers(app)
        pane = app._focused_file_pane()
        await pilot.press("slash", "[", "x", "]", "slash")
        assert app.focused.value == "[x]/"
        for action in ("pane.sort", "pane.fuzzy_find", "pane.clear_filter"):
            app._actions.invoke(action)
        assert len(app.screen_stack) == 2
        assert pane.filter_text == "[x]/"
        await pilot.press("escape")
        widget = next(w for w in app.query(Pane) if w.vm is pane)
        assert "[x]/" in str(widget.query_one(".pane-filter-status", Static).render())
        assert "No matches" in pane.viewmodel.filter_status_text


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [(120, 40), (80, 24)])
async def test_parent_zero_match_and_directory_find_do_not_open(app_context_factory, size):
    from aws_tui.domain.filesystem import EntryKind, FileEntry, PathRef
    from aws_tui.ui.widgets.modal_button import ModalButton
    from tests.snapshot.apps.pane_listing_controls import FixedFS

    class DirectoryFS(FixedFS):
        async def list(self, path):
            return [
                FileEntry(name="[directory]", kind=EntryKind.DIRECTORY, size=None, modified=None)
            ]

    ctx = app_context_factory(fs=DirectoryFS())
    _use_injected_s3_connection(ctx)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=size) as pilot:
        await drain_workers(app)
        pane = app._focused_file_pane()
        await pane.navigate_to(PathRef(("loaded",)))
        await pilot.press("slash", *"missing", "escape")
        await pilot.pause()
        widget = next(w for w in app.query(Pane) if w.vm is pane)
        assert pane.filtered_entries[0].is_parent_link
        assert pane.selected_entry.is_parent_link
        assert "0 / 1 matches" in str(widget.query_one(".pane-filter-status", Static).render())
        assert widget.query_one(".pane-filter-status").region.height >= 2 if size[0] == 80 else True
        assert widget.query_one(".pane-clear-filter", ModalButton).region.width == 7
        app.save_screenshot(f"/tmp/aws-tui-240-zero-{size[0]}.svg")
        before_path = pane.path
        await pilot.press("ctrl+p", *"[directory]", "enter")
        await pilot.pause()
        assert pane.path == before_path
        assert pane.selected_entry.name == "[directory]"
        assert pane.filter_text == ""


@pytest.mark.asyncio
async def test_help_and_palette_filter_discovery_and_execution(app_context_factory):
    from aws_tui.ui.widgets.help_modal import HelpModal
    from aws_tui.ui.widgets.pane_listing_controls import FilterPaneModal

    ctx = app_context_factory(fs=await seeded())
    _use_injected_s3_connection(ctx)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await drain_workers(app)
        await pilot.press("question_mark")
        assert isinstance(app.screen, HelpModal)
        assert any(
            "Filter loaded entries" in str(row.content) for row in app.screen.query(".help-row")
        )
        await pilot.press("escape", "colon", *"Filter loaded entries")
        assert [e.id for e in ctx.command_palette_vm.filtered_entries] == ["pane.filter"]
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, FilterPaneModal)
        assert isinstance(app.focused, Input)
        pane = app._focused_file_pane()
        await pilot.press(*"beta")
        await pilot.click(app.screen.query("ModalButton").first())
        await pilot.pause()
        assert pane.filter_text == ""
        await pilot.press(*"alpha", "enter")
        await pilot.pause()
        assert len(app.screen_stack) == 1
        assert pane.filter_text == "alpha"


@pytest.mark.asyncio
async def test_find_result_keyboard_navigation_and_sort_click(app_context_factory):
    from textual.widgets import OptionList

    ctx = app_context_factory(fs=await seeded())
    _use_injected_s3_connection(ctx)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await drain_workers(app)
        pane = app._focused_file_pane()
        await pilot.press("ctrl+p", "tab", "down", "enter")
        await pilot.pause()
        assert pane.selected_entry.name == "beta.txt"
        app._actions.invoke("pane.sort")
        await pilot.pause()
        choices = app.screen.query_one(OptionList)
        await pilot.click(choices, offset=(4, 2))
        await pilot.pause()
        assert len(app.screen_stack) == 1
        assert pane.viewmodel.sort_status_text


@pytest.mark.asyncio
async def test_empty_find_has_explicit_no_match(app_context_factory):
    from aws_tui.demo.in_memory_fs import InMemoryFS

    ctx = app_context_factory(fs=InMemoryFS())
    _use_injected_s3_connection(ctx)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(80, 24)) as pilot:
        await drain_workers(app)
        await pilot.press("ctrl+p")
        assert "No matches in loaded entries" in str(
            app.screen.query_one("#find-empty", Static).render()
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["pane.filter", "pane.fuzzy_find", "pane.sort"])
async def test_refresh_invalidates_open_forms(app_context_factory, action):
    ctx = app_context_factory(fs=await seeded())
    _use_injected_s3_connection(ctx)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(80, 24)) as pilot:
        await drain_workers(app)
        pane = app._focused_file_pane()
        app._actions.invoke(action)
        await pilot.pause()
        form = app.screen
        await pane.refresh()
        await pilot.pause()
        assert len(app.screen_stack) == 1
        assert not form.valid()
        assert form._subscription is None


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["pane.filter", "pane.fuzzy_find"])
async def test_modified_control_remap_yields_to_editor_commands(app_context_factory, action):
    from aws_tui.infra.keymap_store import KeymapStore
    from aws_tui.ui.widgets.pane_listing_controls import FilterPaneModal, FindPaneModal

    ctx = app_context_factory(fs=await seeded())
    _use_injected_s3_connection(ctx)
    ctx.keymap_store = KeymapStore(overlay={action: "ctrl+u"})
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await drain_workers(app)
        await pilot.press("ctrl+u")
        expected = FilterPaneModal if action == "pane.filter" else FindPaneModal
        assert isinstance(app.screen, expected)
        editor = app.screen.query_one(Input)
        await pilot.press(*"alpha")
        assert editor.value == "alpha"
        await pilot.press("ctrl+u")
        assert editor.value == ""
        assert isinstance(app.screen, expected)
        await pilot.press("escape")
        ordinary_editor = Input(id="ordinary-listing-editor")
        await app.screen.mount(ordinary_editor)
        ordinary_editor.focus()
        await pilot.press(*"beta", "ctrl+u")
        assert ordinary_editor.value == ""
        assert len(app.screen_stack) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("selected_directory", [False, True])
async def test_persistent_clear_focused_enter_never_activates(
    app_context_factory, selected_directory
):
    from aws_tui.domain.filesystem import PathRef

    fs = await seeded()
    await fs.mkdir(PathRef(("loaded", "folder")))
    ctx = app_context_factory(fs=fs)
    _use_injected_s3_connection(ctx)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(80, 24)) as pilot:
        await drain_workers(app)
        pane = app._focused_file_pane()
        await pane.navigate_to(PathRef(("loaded",)))
        await pilot.press("slash", *("folder" if selected_directory else "missing"), "escape")
        if selected_directory:
            await pilot.press("down")
        else:
            assert pane.selected_entry.is_parent_link
        await pilot.pause()
        before_path = pane.path
        baseline = list(fs.calls)
        widget = next(w for w in app.query(Pane) if w.vm is pane)
        widget.query_one(".pane-clear-filter").focus()
        await pilot.press("enter")
        await pilot.pause()
        assert pane.filter_text == ""
        assert pane.path == before_path
        assert len(app.screen_stack) == 1
        assert fs.calls == baseline
