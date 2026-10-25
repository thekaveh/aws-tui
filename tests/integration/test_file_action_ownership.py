"""File actions share focused-ready admission and own confirmation listings."""

from __future__ import annotations

import asyncio
import inspect

import pytest
from textual.widgets import Input

from aws_tui.app import AwsTuiApp
from aws_tui.domain.filesystem import PathRef
from aws_tui.ui.widgets.confirm_modal import ConfirmModal
from aws_tui.ui.widgets.nav_menu import NavMenu
from aws_tui.ui.widgets.toast import Toast
from tests.helpers import drain_workers, wait_until
from tests.integration.test_copy_delete_actions import _use_injected_s3_connection
from tests.integration.test_keyboard_selection import RecordingFS, _bytes, marks, seeded

ACTIONS = [
    ("pane.copy", "c"),
    ("pane.move", "m"),
    ("pane.new", "n"),
    ("pane.delete", "d"),
    ("pane.copy_entry_path", "p"),
    ("pane.copy_path", "P"),
    ("pane.enter_multiselect", "v"),
    ("pane.toggle_select", "space"),
    ("pane.select_all", "a"),
    ("pane.clear_selection", "u"),
    ("pane.exit_multiselect", "ctrl+v"),
    ("pane.mark_up", "shift+up"),
    ("pane.mark_down", "shift+down"),
]


@pytest.mark.parametrize("route", ["key", "registry", "palette"])
@pytest.mark.parametrize("focus", ["rail", "control"])
async def test_nonpane_focus_rejects_file_actions(app_context_factory, route, focus):
    fs = await seeded()
    ctx = app_context_factory(fs=fs)
    _use_injected_s3_connection(ctx)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await drain_workers(app)
        await pilot.pause()
        pane = app._focused_file_pane()
        pane.enter_multiselect_command.execute()
        pane.mark_at(0)
        if focus == "rail":
            widget = app.query_one(NavMenu)
        else:
            widget = Input(id="unrelated-control")
            await app.screen.mount(widget)
        app.set_focus(widget)
        await pilot.pause()
        original_marks, original_cursor = marks(pane), pane.cursor_index
        original_calls = list(fs.calls)
        origin = app._capture_discovery_origin()
        rows = {row.id: row for row in app._project_discovery_actions(origin)}
        for action, key in ACTIONS:
            assert not rows[action].available
            assert rows[action].availability_reason == "focus_required"
            if route == "key":
                await pilot.press(key)
            elif route == "registry":
                result = app._actions.invoke(action)
                if inspect.isawaitable(result):
                    await result
            else:
                await app._invoke_discovery_action(action, origin)
            await pilot.pause()
            assert len(app.screen_stack) == 1, action
            assert marks(pane) == original_marks, action
            assert pane.cursor_index == original_cursor, action
            assert pane.is_multiselect_mode, action
            assert ctx.clipboard.writes == [], action
            assert fs.calls == original_calls, action


class GatedDirectoryFS(RecordingFS):
    def __init__(self):
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def list(self, path):
        if path == PathRef(("folder",)):
            self.entered.set()
            await self.release.wait()
        return await super().list(path)


@pytest.mark.parametrize("route", ["key", "registry", "palette"])
async def test_delayed_descent_never_confirms_composite_stale_row(app_context_factory, route):
    fs = GatedDirectoryFS()
    await fs.mkdir(PathRef(("folder",)))
    await fs.write_stream(PathRef(("folder", "valuable.txt")), _bytes(b"valuable"))
    ctx = app_context_factory(fs=fs)
    _use_injected_s3_connection(ctx)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await drain_workers(app)
        await pilot.pause()
        pane = app._focused_file_pane()
        await pilot.press("enter")
        await asyncio.wait_for(fs.entered.wait(), 2)
        try:
            assert pane.path == PathRef(("folder",))
            assert pane.selected_entry.name == "folder"  # retained old row
            for action, key in ACTIONS:
                assert not next(
                    row
                    for row in app._project_discovery_actions(app._capture_discovery_origin())
                    if row.id == action
                ).available
                if route == "key":
                    await pilot.press(key)
                elif route == "registry":
                    result = app._actions.invoke(action)
                    if inspect.isawaitable(result):
                        await result
                else:
                    await app._invoke_discovery_action(action, app._capture_discovery_origin())
                await pilot.pause()
                assert not isinstance(app.screen, ConfirmModal), action
                assert not ctx.clipboard.writes, action
                assert not marks(pane), action
        finally:
            fs.release.set()
        await drain_workers(app)
        assert await _read(fs, PathRef(("folder", "valuable.txt"))) == b"valuable"
        assert not [call for call in fs.calls if call[0] == "delete"]


async def _read(fs, path):
    stream = await fs.read_stream(path)
    return b"".join([chunk async for chunk in stream])


@pytest.mark.parametrize("action", ["copy", "delete"])
@pytest.mark.parametrize(
    "change", ["refresh", "different_name", "same_name_new_bytes", "source", "navigate"]
)
async def test_changed_confirmation_visibly_invalidates_and_preserves_data(
    app_context_factory, action, change
):
    fs = await seeded()
    ctx = app_context_factory(fs=fs)
    _use_injected_s3_connection(ctx)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await drain_workers(app)
        await pilot.pause()
        dual = ctx.root_vm.content_host.current
        pane = dual.focused_pane
        destination = dual.other_pane
        destination_before = await destination.provider.list(destination.path)
        await pilot.press("c" if action == "copy" else "d")
        await wait_until(lambda: isinstance(app.screen, ConfirmModal), what="owned confirmation")
        assert ctx.confirm_vm.request.paths[0].path.endswith("/alpha.txt")
        replacement = None
        if change == "different_name":
            await fs.write_stream(PathRef(("different.txt",)), _bytes(b"new valuable"))
            await pane.refresh()
        elif change == "same_name_new_bytes":
            await fs.write_stream(PathRef(("alpha.txt",)), _bytes(b"replacement valuable"))
            await pane.refresh()
        elif change == "source":
            replacement = await seeded()
            await pane.swap_provider(replacement, identity_label="replacement", path_protocol="s3:")
        elif change == "navigate":
            await fs.mkdir(PathRef(("folder",)))
            await fs.write_stream(PathRef(("folder", "alpha.txt")), _bytes(b"other valuable"))
            await pane.navigate_to(PathRef(("folder",)))
        else:
            await pane.refresh()
        if action == "delete":
            await pilot.press("right")
        await pilot.press("enter")
        await drain_workers(app)
        await pilot.pause()
        assert not isinstance(app.screen, ConfirmModal)
        assert not [call for call in fs.calls if call[0] == "delete"]
        if replacement:
            assert not [call for call in replacement.calls if call[0] == "delete"]
        expected = b"replacement valuable" if change == "same_name_new_bytes" else b"alpha"
        assert await _read(fs, PathRef(("alpha.txt",))) == expected
        assert await _read(fs, PathRef(("beta.txt",))) == b"beta"
        assert await destination.provider.list(destination.path) == destination_before
        assert any(
            toast.model.id == "file-confirmation-invalidated"
            for toast in ctx.root_vm.chrome.toast_stack.toasts
        )
        assert any(
            toast.is_attached
            and toast.visible
            and toast.region.height > 0
            and "confirmation expired" in toast.render().plain
            and "review the current entries and try again" in toast.render().plain
            for toast in app.query(Toast)
        )
        assert ctx.transfer_journal.load_history() == ()


@pytest.mark.parametrize("action", ["copy", "delete"])
async def test_changed_listing_before_confirmation_worker_starts_does_not_open_modal(
    app_context_factory, monkeypatch, action
):
    fs = await seeded()
    ctx = app_context_factory(fs=fs)
    _use_injected_s3_connection(ctx)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await drain_workers(app)
        await pilot.pause()
        source = app._focused_file_pane()
        pending = []
        original = app.run_worker

        def hold(work, **kwargs):
            if kwargs.get("group") == "confirmation":
                pending.append(work)
                return None
            return original(work, **kwargs)

        monkeypatch.setattr(app, "run_worker", hold)
        await (app.action_copy() if action == "copy" else app.action_delete())
        assert len(pending) == 1
        await source.refresh()
        requests = []

        async def ask(request, **kwargs):
            requests.append(request)
            return False

        monkeypatch.setattr(ctx.confirm_vm, "ask", ask)
        await pending.pop()
        assert not requests
        assert not app._confirmation_pending
        assert any(
            toast.model.id == "file-confirmation-invalidated"
            for toast in ctx.root_vm.chrome.toast_stack.toasts
        )


@pytest.mark.parametrize("action", ["copy", "delete"])
@pytest.mark.parametrize("change", ["refresh", "selection", "focus"])
async def test_confirmation_is_rechecked_at_transfer_worker_entry(
    app_context_factory, monkeypatch, action, change
):
    fs = await seeded()
    ctx = app_context_factory(fs=fs)
    _use_injected_s3_connection(ctx)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await drain_workers(app)
        await pilot.pause()
        dual = ctx.root_vm.content_host.current
        source = dual.focused_pane
        destination = dual.other_pane
        before = await destination.provider.list(destination.path)
        pending = []
        original = app._run_lifecycle_worker

        def hold(work, **kwargs):
            if kwargs.get("group", "").startswith("transfer-"):
                pending.append(work)
                return None
            return original(work, **kwargs)

        monkeypatch.setattr(app, "_run_lifecycle_worker", hold)
        await pilot.press("c" if action == "copy" else "d")
        await wait_until(
            lambda: isinstance(app.screen, ConfirmModal), what="worker gate confirmation"
        )
        if action == "delete":
            await pilot.press("right")
        await pilot.press("enter")
        await wait_until(lambda: bool(pending), what="accepted transfer worker queued")
        if change == "refresh":
            await source.refresh()
        elif change == "selection":
            source.mark_at(1)
        else:
            dual.switch_focus_command.execute()
        await pending.pop()()
        assert not [call for call in fs.calls if call[0] == "delete"]
        assert await _read(fs, PathRef(("alpha.txt",))) == b"alpha"
        assert await destination.provider.list(destination.path) == before
        assert ctx.transfer_journal.load_history() == ()
        assert any(
            toast.model.id == "file-confirmation-invalidated"
            for toast in ctx.root_vm.chrome.toast_stack.toasts
        )


@pytest.mark.parametrize("change", ["refresh", "navigate", "source"])
async def test_copy_confirmation_owns_destination_listing(app_context_factory, change):
    fs = await seeded()
    ctx = app_context_factory(fs=fs)
    _use_injected_s3_connection(ctx)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await drain_workers(app)
        await pilot.pause()
        destination = ctx.root_vm.content_host.current.other_pane
        replacement = GatedDirectoryFS()
        replacement.release.set()
        await replacement.mkdir(PathRef(("folder",)))
        await destination.swap_provider(replacement, identity_label="dest", path_protocol="")
        await pilot.press("c")
        await wait_until(
            lambda: isinstance(app.screen, ConfirmModal), what="destination confirmation"
        )
        if change == "refresh":
            await destination.refresh()
        elif change == "navigate":
            await destination.navigate_to(PathRef(("folder",)))
        else:
            await destination.swap_provider(await seeded(), identity_label="new", path_protocol="")
        await pilot.press("enter")
        await drain_workers(app)
        assert not [call for call in fs.calls if call[0] == "read"]
        assert not [call for call in replacement.calls if call[0] == "write"]
        assert ctx.transfer_journal.load_history() == ()
        assert any(
            toast.model.id == "file-confirmation-invalidated"
            for toast in ctx.root_vm.chrome.toast_stack.toasts
        )


@pytest.mark.parametrize("action", ["copy", "delete"])
async def test_synchronous_mark_observer_cannot_redirect_confirmed_operation(
    app_context_factory, action
):
    fs = await seeded()
    ctx = app_context_factory(fs=fs)
    _use_injected_s3_connection(ctx)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await drain_workers(app)
        await pilot.pause()
        dual = ctx.root_vm.content_host.current
        source = dual.focused_pane
        redirected = []

        def redirect(_property):
            if source.marked_entries and not redirected:
                redirected.append(True)
                dual.switch_focus_command.execute()

        subscription = source.on_property_changed.subscribe(redirect)
        try:
            await pilot.press("c" if action == "copy" else "d")
            await wait_until(
                lambda: isinstance(app.screen, ConfirmModal), what="mark observer confirmation"
            )
            if action == "delete":
                await pilot.press("right")
            await pilot.press("enter")
            await drain_workers(app)
            assert redirected
            assert not [call for call in fs.calls if call[0] in {"read", "delete"}]
            assert ctx.transfer_journal.load_history() == ()
            assert any(
                toast.model.id == "file-confirmation-invalidated"
                for toast in ctx.root_vm.chrome.toast_stack.toasts
            )
        finally:
            subscription.dispose()


async def test_copy_admission_waits_for_destination_descent(app_context_factory):
    fs = await seeded()
    ctx = app_context_factory(fs=fs)
    _use_injected_s3_connection(ctx)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await drain_workers(app)
        await pilot.pause()
        destination = ctx.root_vm.content_host.current.other_pane
        gated = GatedDirectoryFS()
        await gated.mkdir(PathRef(("folder",)))
        await destination.swap_provider(gated, identity_label="dest", path_protocol="")
        task = asyncio.create_task(destination.navigate_to(PathRef(("folder",))))
        await asyncio.wait_for(gated.entered.wait(), 2)
        try:
            origin = app._capture_discovery_origin()
            row = next(
                row for row in app._project_discovery_actions(origin) if row.id == "pane.copy"
            )
            assert not row.available
            assert row.availability_reason == "busy"
            await pilot.press("c")
            await app._actions.invoke("pane.copy")
            await app._invoke_discovery_action("pane.copy", origin)
            await pilot.pause()
            assert not isinstance(app.screen, ConfirmModal)
            assert not [call for call in fs.calls if call[0] == "read"]
        finally:
            gated.release.set()
            await task
        assert ctx.transfer_journal.load_history() == ()


async def test_pane_toolbar_control_does_not_inherit_the_row_action_context(app_context_factory):
    from aws_tui.ui.widgets.modal_button import ModalButton
    from aws_tui.ui.widgets.pane import EntryRow, Pane

    ctx = app_context_factory(fs=await seeded())
    _use_injected_s3_connection(ctx)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await drain_workers(app)
        await pilot.pause()
        dual = ctx.root_vm.content_host.current
        right = dual.right
        await right.swap_provider(await seeded(), identity_label="right", path_protocol="s3:")
        right.set_filter_command.execute("alpha")
        await pilot.pause()
        widget = next(pane for pane in app.query(Pane) if pane.vm is right)
        clear = widget.query_one(".pane-clear-filter", ModalButton)
        clear.focus()
        await pilot.pause()
        assert app.focused is clear
        origin = app._capture_discovery_origin()
        rows = {row.id: row for row in app._project_discovery_actions(origin)}
        for action, key in ACTIONS:
            assert not rows[action].available, action
            assert rows[action].availability_reason == "focus_required", action
            if key != "space":  # Space belongs to the focused button itself.
                await pilot.press(key)
                await pilot.pause()
                assert not isinstance(app.screen, ConfirmModal), action
        assert right.filter_text == "alpha"
        await pilot.press("space")
        await pilot.pause()
        assert right.filter_text == ""
        row = next(row for row in widget.query(EntryRow) if row.entry_vm.name == "alpha.txt")
        await pilot.click(row)
        await pilot.pause()
        assert dual.focused_pane is right
        await pilot.press("d")
        await wait_until(
            lambda: isinstance(app.screen, ConfirmModal), what="explicit right row confirmation"
        )
        assert ctx.confirm_vm.request.paths[0].path.endswith("/alpha.txt")
        await pilot.press("escape")
