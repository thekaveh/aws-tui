"""Mounted keyboard selection, explicit palette intent, and alias routing."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from textual.widgets import Static

from aws_tui.app import AwsTuiApp
from aws_tui.demo.in_memory_fs import InMemoryFS
from aws_tui.domain.filesystem import PathRef
from aws_tui.infra.keymap_store import KeymapStore
from aws_tui.ui.widgets.command_palette import CommandPalette
from aws_tui.ui.widgets.confirm_modal import ConfirmModal
from aws_tui.ui.widgets.pane import Pane
from aws_tui.ui.widgets.quick_look import QuickLook
from tests.helpers import drain_workers
from tests.integration.test_copy_delete_actions import _use_injected_s3_connection

SELECTION = {
    "pane.enter_multiselect": "Enter multi-select mode",
    "pane.toggle_select": "Toggle cursor selection",
    "pane.select_all": "Select all visible entries",
    "pane.clear_selection": "Clear selection",
    "pane.exit_multiselect": "Exit multi-select mode",
}


class RecordingFS(InMemoryFS):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[str, PathRef]] = []

    async def list(self, path: PathRef):  # type: ignore[no-untyped-def]
        self.calls.append(("list", path))
        return await super().list(path)

    async def stat(self, path: PathRef):  # type: ignore[no-untyped-def]
        self.calls.append(("stat", path))
        return await super().stat(path)

    async def read_stream(self, path: PathRef, *, chunk_size: int = 8 * 1024 * 1024):  # type: ignore[no-untyped-def]
        self.calls.append(("read", path))
        return await super().read_stream(path, chunk_size=chunk_size)

    async def delete(self, path: PathRef, *, expected_etag: str | None = None) -> None:
        self.calls.append(("delete", path))
        await super().delete(path, expected_etag=expected_etag)

    async def write_stream(self, path: PathRef, stream: AsyncIterator[bytes], **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(("write", path))
        await super().write_stream(path, stream, **kwargs)


async def _bytes(data: bytes) -> AsyncIterator[bytes]:
    yield data


async def seeded() -> RecordingFS:
    fs = RecordingFS()
    for name, data in (("alpha.txt", b"alpha"), ("beta.txt", b"beta"), ("gamma.txt", b"gamma!")):
        await fs.write_stream(PathRef((name,)), _bytes(data))
    return fs


def marks(pane):  # type: ignore[no-untyped-def]
    return {entry.entry.name for entry in pane.marked_entries}


def footer(app: AwsTuiApp) -> str:
    pane = next(widget for widget in app.query(Pane) if widget.vm is app._focused_file_pane())
    return str(pane.query_one(".pane-footer", Static).render())


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [(120, 40), (80, 24)])
async def test_physical_selection_and_mounted_summary(app_context_factory, size):  # type: ignore[no-untyped-def]
    fs = await seeded()
    ctx = app_context_factory(fs=fs)
    _use_injected_s3_connection(ctx)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=size) as pilot:
        await drain_workers(app)
        await pilot.pause()
        pane = app._focused_file_pane()
        await pilot.press("space")
        await pilot.pause()
        assert isinstance(app.screen, QuickLook)
        await pilot.press("escape")
        await drain_workers(app)
        await pilot.pause()
        baseline = list(fs.calls)
        await pilot.press("v")
        await pilot.pause()
        assert pane.is_multiselect_mode
        assert marks(pane) == set()
        assert "multi:" in footer(app)
        assert "0 marked" in footer(app)
        assert "0 B" in footer(app)
        await pilot.press("space")
        await pilot.pause()
        assert not isinstance(app.screen, QuickLook)
        assert marks(pane) == {"alpha.txt"}
        assert "1 marked" in footer(app)
        assert "5 B" in footer(app)
        await pilot.press("a")
        await pilot.pause()
        assert marks(pane) == {"alpha.txt", "beta.txt", "gamma.txt"}
        assert fs.calls == baseline
        widget = next(widget for widget in app.query(Pane) if widget.vm is pane)
        summary_widget = widget.query_one(".pane-footer", Static)
        assert len(footer(app)) <= summary_widget.content_region.width
        out = Path("/tmp/aws-tui-selection")
        out.mkdir(exist_ok=True)
        app.save_screenshot(str(out / f"selection-{size[0]}.svg"))
        await pilot.press("u")
        await pilot.pause()
        assert pane.is_multiselect_mode
        assert marks(pane) == set()
        assert "0 marked" in footer(app)
        await pilot.press("ctrl+v")
        await pilot.pause()
        assert not pane.is_multiselect_mode
        assert "multi:" not in footer(app)
        assert fs.calls == baseline


@pytest.mark.asyncio
@pytest.mark.parametrize("action_id", list(SELECTION))
@pytest.mark.parametrize("focus_right", [False, True])
async def test_palette_enter_executes_selection_after_dismissal(
    app_context_factory, action_id, focus_right
):  # type: ignore[no-untyped-def]
    ctx = app_context_factory(fs=await seeded())
    _use_injected_s3_connection(ctx)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await drain_workers(app)
        await pilot.pause()
        if focus_right:
            await ctx.root_vm.content_host.current.right.swap_provider(await seeded())
            await pilot.press("tab")
            await pilot.pause()
        pane = app._focused_file_pane()
        if action_id in {"pane.clear_selection", "pane.exit_multiselect"}:
            pane.toggle_select_command.execute()
        await pilot.press("colon")
        await pilot.press(*SELECTION[action_id])
        await pilot.pause()
        assert [entry.id for entry in ctx.command_palette_vm.filtered_entries] == [action_id]
        await pilot.press("enter")
        await pilot.pause()
        assert not isinstance(app.screen, CommandPalette)
        assert app._focused_file_pane() is pane
        expected = {
            "pane.enter_multiselect": set(),
            "pane.toggle_select": {"alpha.txt"},
            "pane.select_all": {"alpha.txt", "beta.txt", "gamma.txt"},
            "pane.clear_selection": set(),
            "pane.exit_multiselect": set(),
        }
        assert marks(pane) == expected[action_id]
        assert pane.is_multiselect_mode == (action_id != "pane.exit_multiselect")


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["c", "d"])
@pytest.mark.parametrize("filtered", [True, False])
async def test_confirm_contains_selection_actions_and_exact_active_targets(
    app_context_factory, operation, filtered
):  # type: ignore[no-untyped-def]
    fs = await seeded()
    ctx = app_context_factory(fs=fs)
    _use_injected_s3_connection(ctx)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await drain_workers(app)
        await pilot.pause()
        pane = app._focused_file_pane()
        destination = RecordingFS()
        await ctx.root_vm.content_host.current.right.swap_provider(destination)
        await pilot.pause()
        await pilot.press("v", "space", "down", "space")
        await pilot.press("slash", *("beta" if filtered else "gamma"), "escape")
        if not filtered:
            assert marks(pane) == set()
            pane.set_filter_command.execute("")
        expected = {"beta.txt"} if filtered else {"alpha.txt", "beta.txt"}
        await pilot.pause()
        assert marks(pane) == expected
        assert {e.entry.name for e in pane.entries if e.is_marked} == {"alpha.txt", "beta.txt"}
        baseline = list(fs.calls)
        destination_baseline = list(destination.calls)
        for key in ("v", "space", "a", "u", "ctrl+v"):
            await pilot.press(operation)
            await pilot.pause()
            assert isinstance(app.screen, ConfirmModal)
            captured_request = app.screen.request
            for action_id in SELECTION:
                app._actions.invoke(action_id)
            assert marks(pane) == expected
            if key == "space":
                # Space belongs to the modal button. Cancel this fresh modal,
                # then stop the case before any shortcut can reach the page.
                await pilot.press("left")
            await pilot.press(key)
            await pilot.pause()
            assert pane.is_multiselect_mode
            assert marks(pane) == expected
            assert fs.calls == baseline
            assert destination.calls == destination_baseline
            if key == "space":
                assert len(app.screen_stack) == 1
            else:
                assert isinstance(app.screen, ConfirmModal)
                assert app.screen.request == captured_request
                await pilot.press("escape")
                await pilot.pause()
        await pilot.press(operation)
        await pilot.pause()
        assert isinstance(app.screen, ConfirmModal)
        if operation == "d":
            await pilot.press("right")
        await pilot.press("enter")
        await drain_workers(app)
        await pilot.pause()
        calls = fs.calls[len(baseline) :]
        relevant = "read" if operation == "c" else "delete"
        assert {path for name, path in calls if name == relevant} == {
            PathRef((name,)) for name in expected
        }
        if operation == "c":
            assert {
                path
                for name, path in destination.calls[len(destination_baseline) :]
                if name == "write"
            } == {PathRef((name,)) for name in expected}
            assert {entry.name for entry in await destination.list(PathRef(()))} == expected
        else:
            remaining = {entry.name for entry in await fs.list(PathRef(()))}
            assert remaining == {"alpha.txt", "beta.txt", "gamma.txt"} - expected


@pytest.mark.asyncio
async def test_focus_and_coordinator_modal_keep_marks_independent(app_context_factory):  # type: ignore[no-untyped-def]
    ctx = app_context_factory(fs=await seeded())
    _use_injected_s3_connection(ctx)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await drain_workers(app)
        await pilot.pause()
        dual = ctx.root_vm.content_host.current
        await pilot.press("v", "space", "tab", "v", "space")
        await pilot.pause()
        left_marks, right_marks = marks(dual.left), marks(dual.right)
        assert left_marks
        assert right_marks
        await pilot.press("u")
        assert marks(dual.left) == left_marks
        assert marks(dual.right) == set()
        await pilot.press("tab")
        ctx.focus_coordinator.modal_open()
        for action_id in SELECTION:
            app.action_dispatch(action_id)
            app._actions.invoke(action_id)
        assert marks(dual.left) == left_marks
        ctx.focus_coordinator.modal_close()


@pytest.mark.asyncio
@pytest.mark.parametrize("remap_selection", [True, False])
async def test_separate_alias_overlays_preserve_explicit_meanings(
    app_context_factory, remap_selection
):  # type: ignore[no-untyped-def]
    ctx = app_context_factory(fs=await seeded())
    _use_injected_s3_connection(ctx)
    ctx.keymap_store = KeymapStore(
        overlay=(
            {"pane.toggle_select": "x", "pane.select_all": "b"}
            if remap_selection
            else {"pane.quick_look": "x", "auth.authenticate": "b"}
        )
    )
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await drain_workers(app)
        await pilot.pause()
        pane = app._focused_file_pane()
        calls: list[str] = []
        app._actions.register("auth.authenticate", lambda: calls.append("auth"))
        await pilot.press("x" if remap_selection else "space")
        assert marks(pane) == {"alpha.txt"}
        await pilot.press("space" if remap_selection else "x")
        assert isinstance(app.screen, QuickLook)
        await pilot.press("escape", "b" if remap_selection else "a")
        assert marks(pane) == {"alpha.txt", "beta.txt", "gamma.txt"}
        assert calls == []
        await pilot.press("a" if remap_selection else "b")
        assert calls == ["auth"]


@pytest.mark.asyncio
@pytest.mark.parametrize("quick_first", [True, False])
async def test_normalized_shared_aliases_dispatch_context_regardless_of_id(
    app_context_factory, quick_first
):  # type: ignore[no-untyped-def]
    ctx = app_context_factory(fs=await seeded())
    _use_injected_s3_connection(ctx)
    ctx.keymap_store = KeymapStore(
        overlay={
            "pane.quick_look": "Space",
            "pane.toggle_select": "space",
            "auth.authenticate": "a",
            "pane.select_all": "a",
        }
    )
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await drain_workers(app)
        await pilot.pause()
        pane = app._focused_file_pane()
        calls: list[str] = []
        app._actions.register("auth.authenticate", lambda: calls.append("auth"))
        # Exercise either emitted ID: behavior cannot depend on binding ordering.
        app.action_dispatch("pane.quick_look" if quick_first else "pane.toggle_select", "Space")
        assert isinstance(app.screen, QuickLook)
        await pilot.press("escape", "v")
        app.action_dispatch("pane.quick_look" if quick_first else "pane.toggle_select", "Space")
        assert marks(pane) == {"alpha.txt"}
        app.action_dispatch("auth.authenticate" if quick_first else "pane.select_all", "a")
        assert marks(pane) == {"alpha.txt", "beta.txt", "gamma.txt"}
        assert calls == []
        # Explicit credential palette actions retain recovery even when healthy.
        await pilot.press("colon")
        await pilot.press(*"Retry active source credentials")
        await pilot.press("enter")
        assert calls == ["auth"]
        from aws_tui.vm.file_manager.pane_vm import PaneState

        baseline = list(pane.provider.calls)
        pane._set_state(PaneState.LOADING)
        for action_id in SELECTION:
            app._actions.invoke(action_id)
        app.action_dispatch("auth.authenticate", "a")
        assert calls == ["auth"]
        assert pane.provider.calls == baseline
        pane._set_state(PaneState.AUTH_REQUIRED)
        app.action_dispatch("pane.select_all", "a")
        assert calls == ["auth", "auth"]


@pytest.mark.asyncio
async def test_exit_binding_yields_to_editable_input(app_context_factory):  # type: ignore[no-untyped-def]
    from textual.widgets import Input

    ctx = app_context_factory(fs=await seeded())
    _use_injected_s3_connection(ctx)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await drain_workers(app)
        await pilot.pause()
        pane = app._focused_file_pane()
        await pilot.press("v", "space")
        editor = Input(id="selection-paste-test")
        await app.screen.mount(editor)
        editor.focus()
        app.copy_to_clipboard("pasted text")
        await pilot.press("ctrl+v")
        assert editor.value == "pasted text"
        assert pane.is_multiselect_mode
        assert marks(pane) == {"alpha.txt"}


@pytest.mark.asyncio
async def test_loading_physical_selection_keys_do_not_read_stale_rows(app_context_factory):  # type: ignore[no-untyped-def]
    from aws_tui.vm.file_manager.pane_vm import PaneState

    fs = await seeded()
    ctx = app_context_factory(fs=fs)
    _use_injected_s3_connection(ctx)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await drain_workers(app)
        await pilot.pause()
        pane = app._focused_file_pane()
        pane._set_state(PaneState.LOADING)
        baseline = list(fs.calls)
        for key in ("v", "space", "a", "u", "ctrl+v"):
            await pilot.press(key)
            await pilot.pause()
            assert len(app.screen_stack) == 1
            assert not pane.is_multiselect_mode
            assert marks(pane) == set()
            assert fs.calls == baseline


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("count", "size", "expected_bytes"), [(1, 1024**3, "1.0 G"), (100, 1024**2, "100.0 M")]
)
async def test_large_selected_bytes_fit_narrow_footer(
    app_context_factory, count, size, expected_bytes
):  # type: ignore[no-untyped-def]
    from aws_tui.domain.filesystem import EntryKind, FileEntry

    class SizedListingFS(RecordingFS):
        async def list(self, path: PathRef):  # type: ignore[no-untyped-def]
            self.calls.append(("list", path))
            return [
                FileEntry(
                    name=f"object-{index:03d}.bin", kind=EntryKind.FILE, size=size, modified=None
                )
                for index in range(count)
            ]

    fs = SizedListingFS()
    ctx = app_context_factory(fs=fs)
    _use_injected_s3_connection(ctx)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(80, 24)) as pilot:
        await drain_workers(app)
        await pilot.pause()
        baseline = list(fs.calls)
        await pilot.press("v", "a")
        await pilot.pause()
        pane = app._focused_file_pane()
        widget = next(widget for widget in app.query(Pane) if widget.vm is pane)
        summary_widget = widget.query_one(".pane-footer", Static)
        assert f"{count} marked" in footer(app)
        assert expected_bytes in footer(app)
        assert len(footer(app)) <= summary_widget.content_region.width
        assert fs.calls == baseline
        out = Path("/tmp/aws-tui-selection")
        app.save_screenshot(str(out / f"selection-80-{count}-large.svg"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exclusive_action",
    ["pane.quick_look", "pane.toggle_select", "auth.authenticate", "pane.select_all"],
)
async def test_partly_overlapping_aliases_keep_exclusive_keys_explicit(
    app_context_factory, exclusive_action
):  # type: ignore[no-untyped-def]
    from aws_tui.vm.file_manager.pane_vm import PaneState

    ctx = app_context_factory(fs=await seeded())
    _use_injected_s3_connection(ctx)
    shared = (
        "Space"
        if exclusive_action.startswith("pane.") and exclusive_action != "pane.select_all"
        else "a"
    )
    ctx.keymap_store = KeymapStore(overlay={exclusive_action: [shared, "x"]})
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await drain_workers(app)
        await pilot.pause()
        pane = app._focused_file_pane()
        calls: list[str] = []
        app._actions.register("auth.authenticate", lambda: calls.append("auth"))
        if exclusive_action == "pane.quick_look":
            await pilot.press("v", "x")
            assert isinstance(app.screen, QuickLook)
            assert marks(pane) == set()
            await pilot.press("escape", "space")
            assert marks(pane) == {"alpha.txt"}
        elif exclusive_action == "pane.toggle_select":
            await pilot.press("x")
            assert not isinstance(app.screen, QuickLook)
            assert marks(pane) == {"alpha.txt"}
            await pilot.press("space")
            assert marks(pane) == set()
            assert not isinstance(app.screen, QuickLook)
        elif exclusive_action == "auth.authenticate":
            await pilot.press("x")
            assert calls == ["auth"]
            assert marks(pane) == set()
            await pilot.press("a")
            assert marks(pane) == {"alpha.txt", "beta.txt", "gamma.txt"}
            assert calls == ["auth"]
        else:
            # An exclusive select-all key must never become credential retry.
            pane._set_state(PaneState.AUTH_REQUIRED)
            await pilot.press("x")
            assert calls == []
            assert marks(pane) == set()
            pane._set_state(PaneState.IDLE)
            await pilot.press("x")
            assert marks(pane) == {"alpha.txt", "beta.txt", "gamma.txt"}
            pane._set_state(PaneState.AUTH_REQUIRED)
            await pilot.press("a")
            assert calls == ["auth"]
